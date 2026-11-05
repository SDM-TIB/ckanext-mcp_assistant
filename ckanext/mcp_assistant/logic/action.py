# encoding: utf-8
"""Action ``mcp_assistant_ask``: available to the web page and, as every CKAN
action, through the API at ``POST /api/3/action/mcp_assistant_ask``."""
import logging

import ckan.plugins.toolkit as toolkit

from ckanext.mcp_assistant import agent, config as cfg, ratelimit
from ckanext.mcp_assistant.llm import LLMClient, LLMError
from ckanext.mcp_assistant.mcp_client import MCPError

log = logging.getLogger(__name__)

#: keys of the ValidationError dict, used by the view to pick the HTTP status
ERR_QUESTION = "question"
ERR_RATE_LIMIT = "rate_limit"
ERR_NOT_CONFIGURED = "configuration"
ERR_UPSTREAM = "service"


def _client_identity(context):
    user = context.get("user")
    if user:
        return u"user:" + user
    try:
        from flask import request
        forwarded = request.headers.get("X-Forwarded-For", "")
        ip = forwarded.split(",")[0].strip() if forwarded else request.remote_addr
        return u"ip:" + (ip or "unknown")
    except RuntimeError:  # outside a request (e.g. CLI): no limit
        return None


def mcp_assistant_ask(context, data_dict):
    """Answer a natural-language question about the portal.

    The question goes to the configured LLM, which calls tools of the CKAN MCP
    server (ondata/ckan-mcp-server); those query this portal's CKAN API.

    :param question: the question (required, at most
        ``ckanext.mcp_assistant.max_question_chars`` characters)
    :type question: string
    :param history: earlier turns for follow-up questions, a list of
        ``{"role": "user" | "assistant", "content": string}`` (optional)
    :type history: list

    :returns: ``answer`` (Markdown text) and ``steps`` (the MCP tool calls
        made: ``tool``, ``args``, ``error``, ``chars``, ``summary``, ``ms``)
    :rtype: dictionary
    """
    toolkit.check_access("mcp_assistant_ask", context, data_dict)
    settings = cfg.get_settings()

    question = agent.normalize_question(data_dict.get("question"), settings)
    if not question:
        raise toolkit.ValidationError({ERR_QUESTION: [toolkit._("Please type a question.")]})

    if not cfg.is_configured():
        log.error("mcp_assistant is not configured: set ckanext.mcp_assistant.llm.api_key "
                  "(or OPENAI_API_KEY) and ckanext.mcp_assistant.mcp_url")
        raise toolkit.ValidationError({ERR_NOT_CONFIGURED: [
            toolkit._("The assistant is not configured yet. Please contact the administrator.")]})

    try:
        allowed, retry_after = ratelimit.hit(_client_identity(context),
                                             cfg.get_int("rate_limit_per_hour"),
                                             fail_open=cfg.get_bool("rate_limit_fail_open"))
    except ratelimit.RateLimitUnavailable:
        raise toolkit.ValidationError({ERR_UPSTREAM: [toolkit._(
            "The assistant is temporarily unavailable. Please try again later.")]})
    if not allowed:
        minutes = max(1, retry_after // 60)
        raise toolkit.ValidationError({ERR_RATE_LIMIT: [toolkit._(
            "You have asked many questions in a short time. "
            "Please try again in {minutes} minutes.").format(minutes=minutes)]})

    llm = LLMClient(api_key=cfg.get("llm.api_key"), model=cfg.get("llm.model"),
                    base_url=cfg.get("llm.base_url"), timeout=float(cfg.get("llm.timeout")))
    try:
        result = agent.run(question, data_dict.get("history"), settings, llm)
    except MCPError as e:
        log.error("mcp_assistant: MCP server problem: %s", e)
        raise toolkit.ValidationError({ERR_UPSTREAM: [toolkit._(
            "The data service behind the assistant is not reachable right now. "
            "Please try again later.")]})
    except LLMError as e:
        log.error("mcp_assistant: language model problem: %s", e)
        raise toolkit.ValidationError({ERR_UPSTREAM: [toolkit._(
            "The language model is not available right now. Please try again later.")]})

    log.info("mcp_assistant: answered a question with %d tool call(s)", len(result["steps"]))
    if not cfg.get_bool("show_tool_calls"):
        result["steps"] = []
    return result


def get_actions():
    return {"mcp_assistant_ask": mcp_assistant_ask}
