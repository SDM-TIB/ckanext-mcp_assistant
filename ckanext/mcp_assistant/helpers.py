# encoding: utf-8
"""Template helpers (all prefixed ``mcp_assistant_``)."""
from ckanext.mcp_assistant import config as cfg


def mcp_assistant_show_in_header():
    return cfg.get_bool("show_in_header")


def mcp_assistant_max_chars():
    return cfg.get_int("max_question_chars")


def get_helpers():
    return {
        "mcp_assistant_show_in_header": mcp_assistant_show_in_header,
        "mcp_assistant_max_chars": mcp_assistant_max_chars,
    }
