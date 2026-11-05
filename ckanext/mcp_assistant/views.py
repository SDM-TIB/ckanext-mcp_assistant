# encoding: utf-8
"""Flask blueprint: the assistant page and the JSON endpoint it posts to."""
import logging

from flask import Blueprint, jsonify, request

import ckan.plugins.toolkit as toolkit

from ckanext.mcp_assistant import config as cfg
from ckanext.mcp_assistant.logic import action as act

log = logging.getLogger(__name__)

_STATUS = {act.ERR_QUESTION: 400, act.ERR_RATE_LIMIT: 429,
           act.ERR_NOT_CONFIGURED: 503, act.ERR_UPSTREAM: 502}


def _current_user():
    """``(user name, user object)`` of the visitor, ``("", None)`` when anonymous.

    CKAN >= 2.10 has ``toolkit.current_user`` (Flask-Login); CKAN 2.9 only
    sets ``g.user`` / ``g.userobj``.
    """
    current = getattr(toolkit, "current_user", None)
    if current is not None:
        if getattr(current, "is_authenticated", False) and not getattr(current, "is_anonymous", False):
            return current.name, current
        return "", None
    return (getattr(toolkit.g, "user", "") or ""), getattr(toolkit.g, "userobj", None)


def _context():
    user, userobj = _current_user()
    return {"user": user, "auth_user_obj": userobj}


def index():
    """GET /assistant  (``?q=...`` asks that question right away).
    The path can be changed with ``ckanext.mcp_assistant.path``."""
    try:
        toolkit.check_access("mcp_assistant_ask", _context())
        can_ask = True
    except toolkit.NotAuthorized:
        can_ask = False
    return toolkit.render("mcp_assistant/index.html", extra_vars={
        "can_ask": can_ask,
        "initial_question": (request.args.get("q") or "")[:cfg.get_int("max_question_chars")],
        "max_chars": cfg.get_int("max_question_chars"),
        "examples": cfg.get_list("example_questions", sep="|"),
        "configured": cfg.is_configured(),
    })


def ask():
    """POST /assistant/ask  JSON ``{question, history}`` -> ``{answer, steps}``."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": toolkit._("Please type a question.")}), 400
    try:
        result = toolkit.get_action("mcp_assistant_ask")(_context(), {
            "question": data.get("question"), "history": data.get("history")})
    except toolkit.NotAuthorized as e:
        return jsonify({"error": str(e) or toolkit._("Not authorized")}), 403
    except toolkit.ValidationError as e:
        errors = e.error_dict or {}
        key = next(iter(errors), act.ERR_QUESTION)
        message = errors.get(key)
        message = message[0] if isinstance(message, list) and message else str(message or e)
        return jsonify({"error": message}), _STATUS.get(key, 400)
    except Exception:  # never show a traceback to the visitor
        log.exception("mcp_assistant: unexpected error")
        return jsonify({"error": toolkit._("Something went wrong. Please try again.")}), 500
    return jsonify(result)


def get_blueprints():
    path = cfg.route_path()
    assistant = Blueprint("mcp_assistant", __name__)
    assistant.add_url_rule(path, view_func=index, methods=["GET"])
    assistant.add_url_rule(path + "/ask", view_func=ask, methods=["POST"])
    return [assistant]
