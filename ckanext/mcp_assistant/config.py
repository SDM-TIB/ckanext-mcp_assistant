# encoding: utf-8
"""Configuration of ckanext-mcp_assistant.

Every option is read, in this order, from

1. the CKAN config file: ``ckanext.mcp_assistant.<name>``
2. the environment:      ``CKANEXT__MCP_ASSISTANT__<NAME>`` (dots -> ``__``)
3. a few well-known environment variables (``MCP_URL``, ``OPENAI_API_KEY``,
   ``LLM_MODEL``, ``OPENAI_BASE_URL``)
4. the default below.

Environment variables matter for the LDM Docker set-up: ``ckan.ini`` is only
written on the very first start of the container, so new settings can be
added to ``.env`` without editing an existing ``ckan.ini``.
"""
import logging
import os

import ckan.plugins.toolkit as toolkit

from ckanext.mcp_assistant.agent import Settings

log = logging.getLogger(__name__)

PREFIX = "ckanext.mcp_assistant."
ENV_PREFIX = "CKANEXT__MCP_ASSISTANT__"

# name: (default, fallback environment variable or None)
OPTIONS = {
    "mcp_url": ("http://127.0.0.1:3000/mcp", "MCP_URL"),
    "mcp_timeout": ("60", None),
    "portal_url": ("", "LDM_PORTAL"),
    "llm.api_key": ("", "OPENAI_API_KEY"),
    "llm.model": ("gpt-5.4-mini", "LLM_MODEL"),
    "llm.base_url": ("https://api.openai.com/v1", "OPENAI_BASE_URL"),
    "llm.timeout": ("180", None),
    "max_tool_rounds": ("8", None),
    "max_tool_output": ("15000", None),
    "max_question_chars": ("500", None),
    "max_history_messages": ("12", None),
    "max_history_chars": ("4000", None),
    "tools_cache_seconds": ("600", None),
    "hidden_tools": ("mqa", None),
    "require_login": ("false", None),
    "rate_limit_per_hour": ("10", None),
    "rate_limit_fail_open": ("true", None),
    "path": ("/assistant", None),
    "show_in_header": ("true", None),
    "show_tool_calls": ("true", None),
    "example_questions": (
        "Which datasets are about Long COVID?|"
        "How many datasets does each organization have?|"
        "Which file formats are most common?|"
        "Which tags are used most?", None),
}


def _env_name(prefix, name):
    return prefix + name.replace(".", "__").upper()


def _empty(value):
    return value is None or value == ""


def get(name):
    """Raw (string) value of one option."""
    default, env_alias = OPTIONS[name]
    value = toolkit.config.get(PREFIX + name)
    if _empty(value):
        value = os.environ.get(_env_name(ENV_PREFIX, name))
    if _empty(value) and env_alias:
        value = os.environ.get(env_alias)
    return default if _empty(value) else value


def get_bool(name):
    return toolkit.asbool(get(name))


def get_int(name):
    return toolkit.asint(get(name))


def get_list(name, sep=None):
    raw = get(name)
    items = raw.split(sep) if sep else raw.replace(",", " ").split()
    return [i.strip() for i in items if i.strip()]


def route_path():
    """URL path of the assistant page (``/assistant`` by default)."""
    return "/" + (get("path") or "/assistant").strip().strip("/")


def portal_url():
    """The CKAN portal the MCP tools should query: this very instance by default,
    i.e. ``ckan.site_url`` plus the path part of ``ckan.root_path``
    (``https://service.tib.eu`` + ``/ldmcaimed/{{LANG}}`` -> ``https://service.tib.eu/ldmcaimed``)."""
    explicit = get("portal_url")
    if explicit:
        return explicit.rstrip("/")
    site = (toolkit.config.get("ckan.site_url") or "").rstrip("/")
    root = (toolkit.config.get("ckan.root_path") or "").replace("{{LANG}}", "").strip("/")
    if root and not site.endswith("/" + root):
        site = "{0}/{1}".format(site, root)
    return site


def get_settings():
    # type: () -> Settings
    return Settings(
        mcp_url=get("mcp_url"),
        mcp_timeout=float(get("mcp_timeout")),
        portal_url=portal_url(),
        max_tool_rounds=get_int("max_tool_rounds"),
        max_tool_output=get_int("max_tool_output"),
        max_question_chars=get_int("max_question_chars"),
        max_history_messages=get_int("max_history_messages"),
        max_history_chars=get_int("max_history_chars"),
        tools_cache_seconds=get_int("tools_cache_seconds"),
        hidden_tools=get_list("hidden_tools"),
    )


def is_configured():
    """True when the minimum (an LLM API key and an MCP URL) is set."""
    return bool(get("llm.api_key")) and bool(get("mcp_url"))
