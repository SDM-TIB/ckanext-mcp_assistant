# encoding: utf-8
"""The assistant itself: question -> LLM -> MCP tools -> LDM CKAN API -> answer.

The LLM sees the tools of the MCP server (ondata/ckan-mcp-server), decides
which ones to call, this module runs them and feeds the results back until
the LLM writes a final answer. Every tool call is recorded as a "step" so the
web page can show how an answer was found.

This module does not import CKAN, so it can be unit tested on its own.
"""
import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

from ckanext.mcp_assistant.mcp_client import MCPClient, result_text

log = logging.getLogger(__name__)

SYSTEM_PROMPT = u"""You are the assistant of the LDM data portal at {portal}, a CKAN catalogue of research datasets.
Answer the user's questions by calling the available tools, which talk to that portal.

- Always pass server_url="{portal}" unless the user names another portal.
- Base your answer only on what the tools return. If they return nothing relevant, say so; do not invent datasets.
- Keep answers short. List datasets with their title and link ({portal}/dataset/<name>).
- Search tips: CKAN uses Solr; use simple keywords (e.g. q="cancer"), and facets (rows=0 with facet_field)
  for counts per organization, tag or format.
- Answer in the language of the user's question.
"""


class Settings(object):
    """Everything the agent needs. Built from the CKAN config by
    :func:`ckanext.mcp_assistant.config.get_settings`."""

    def __init__(self, **kw):
        self.mcp_url = kw.get("mcp_url", "http://127.0.0.1:3000/mcp")
        self.mcp_timeout = float(kw.get("mcp_timeout", 60))
        self.portal_url = (kw.get("portal_url") or "").rstrip("/")
        self.max_tool_rounds = int(kw.get("max_tool_rounds", 8))
        self.max_tool_output = int(kw.get("max_tool_output", 15000))
        self.max_question_chars = int(kw.get("max_question_chars", 500))
        self.max_history_messages = int(kw.get("max_history_messages", 12))
        self.max_history_chars = int(kw.get("max_history_chars", 4000))
        self.tools_cache_seconds = int(kw.get("tools_cache_seconds", 600))
        self.hidden_tools = list(kw.get("hidden_tools", ["mqa"]))
        self.system_prompt = kw.get("system_prompt") or SYSTEM_PROMPT


# -- tool list cache (shared by all requests of one CKAN process) -------------
_tools_cache = {}  # type: Dict[str, Dict[str, Any]]
_tools_lock = threading.Lock()


def to_openai_tool(tool):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    schema = dict(tool.get("inputSchema") or {})
    schema.pop("$schema", None)
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return {"type": "function", "function": {
        "name": tool["name"],
        "description": (tool.get("description") or "")[:4000],
        "parameters": schema,
    }}


def get_tools(client, settings):
    # type: (MCPClient, Settings) -> List[Dict[str, Any]]
    """The MCP server's tools in OpenAI format, re-read every ``tools_cache_seconds``."""
    with _tools_lock:
        cached = _tools_cache.get(settings.mcp_url)
        if cached and time.time() - cached["at"] < settings.tools_cache_seconds:
            return cached["tools"]
    listed = [t for t in client.list_tools()
              if not any(h and h in t.get("name", "") for h in settings.hidden_tools)]
    tools = [to_openai_tool(t) for t in listed]
    with _tools_lock:
        _tools_cache[settings.mcp_url] = {"tools": tools, "at": time.time()}
    return tools


def clear_tools_cache():
    with _tools_lock:
        _tools_cache.clear()


def clean_history(history, settings):
    # type: (Any, Settings) -> List[Dict[str, str]]
    """Earlier turns sent by the browser: only user questions and assistant
    answers (never tool results or system messages), most recent first cut."""
    if not isinstance(history, list):
        return []
    turns = [{"role": h["role"], "content": h["content"][:settings.max_history_chars]}
             for h in history
             if isinstance(h, dict) and h.get("role") in ("user", "assistant")
             and isinstance(h.get("content"), str)]
    return turns[-settings.max_history_messages:] if settings.max_history_messages > 0 else []


def _shown_args(args):
    # type: (Dict[str, Any]) -> str
    return u", ".join(u"{0}={1}".format(k, json.dumps(v, ensure_ascii=False))
                      for k, v in args.items() if k != "server_url")


def run(question, history, settings, llm, mcp_factory=None):
    # type: (str, Any, Settings, Any, Any) -> Dict[str, Any]
    """Answer one question. Returns ``{"answer": str, "steps": [...]}``.

    ``llm`` is an :class:`~ckanext.mcp_assistant.llm.LLMClient`;
    ``mcp_factory(url, timeout)`` returns an MCP client context manager
    (defaults to :class:`MCPClient`, replaceable in tests).
    Raises :class:`MCPError` / :class:`LLMError` on infrastructure problems.
    """
    factory = mcp_factory or MCPClient
    steps = []  # type: List[Dict[str, Any]]
    messages = [
        {"role": "system", "content": settings.system_prompt.format(portal=settings.portal_url)},
    ] + clean_history(history, settings) + [{"role": "user", "content": question}]

    with factory(settings.mcp_url, timeout=settings.mcp_timeout) as client:
        tools = get_tools(client, settings)
        takes_server_url = {t["function"]["name"] for t in tools
                            if "server_url" in t["function"]["parameters"].get("properties", {})}
        for _ in range(settings.max_tool_rounds):
            msg = llm.chat(messages, tools)
            messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
            calls = msg.get("tool_calls") or []
            if not calls:
                return {"answer": msg.get("content") or u"", "steps": steps}
            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                    if not isinstance(args, dict):
                        args = {}
                except ValueError:
                    args = {}
                # never let the model send the tools to another portal by accident
                if settings.portal_url and name in takes_server_url and not args.get("server_url"):
                    args["server_url"] = settings.portal_url
                started = time.time()
                try:
                    text, is_error = result_text(client.call_tool(name, args))
                except Exception as e:  # one failing tool must not end the conversation
                    log.warning("MCP tool %s failed: %s", name, e)
                    text, is_error = u"Tool call failed: {0}".format(e), True
                first = text.strip().splitlines()[0][:120] if text.strip() else u""
                steps.append({"tool": name, "args": _shown_args(args), "error": is_error,
                              "chars": len(text), "summary": first,
                              "ms": int((time.time() - started) * 1000)})
                log.debug("MCP %s(%s) -> %s chars", name, _shown_args(args), len(text))
                messages.append({"role": "tool", "tool_call_id": call.get("id"),
                                 "content": text[:settings.max_tool_output]})
    return {"answer": u"(Stopped: too many tool calls without a final answer. "
                      u"Please ask a more specific question.)", "steps": steps}


_WS = re.compile(r"\s+")


def normalize_question(question, settings):
    # type: (Any, Settings) -> Optional[str]
    if not isinstance(question, str):
        return None
    q = _WS.sub(u" ", question).strip()
    return q[:settings.max_question_chars] if q else None
