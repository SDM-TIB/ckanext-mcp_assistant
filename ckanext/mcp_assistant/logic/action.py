# encoding: utf-8
"""Actions of the assistant, available to the web page and, as every CKAN
action, through the API at ``POST /api/3/action/<name>``:

* ``mcp_assistant_ask``: answer a question in the same request (normal mode;
  needs a CKAN that serves several requests at the same time, e.g.
  ``ckan run --threaded``);
* ``mcp_assistant_ask_start`` + ``mcp_assistant_result``: background mode
  (``ckanext.mcp_assistant.background = true``): put the question into a CKAN
  background job, then ask for the result until it is ready. Works with a
  single-threaded CKAN; needs a running ``ckan jobs worker mcp_assistant``.
"""
import logging

import ckan.plugins.toolkit as toolkit

from ckanext.mcp_assistant import agent, config as cfg, jobs, ratelimit

log = logging.getLogger(__name__)

#: keys of the ValidationError dict, used by the view to pick the HTTP status
ERR_QUESTION = "question"
ERR_RATE_LIMIT = "rate_limit"
ERR_NOT_CONFIGURED = "configuration"
ERR_UPSTREAM = "service"
ERR_MODE = "mode"
ERR_NOT_FOUND = "job"


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


def _prepare(context, data_dict):
    """Everything that happens before the question is answered: access check,
    question, configuration and rate limit. Returns the cleaned question."""
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
    return question


def _finish(result):
    """Turn the result of :func:`jobs.answer` into the action's return value."""
    code = result.get("error_code")
    if code == jobs.ERROR_MCP:
        raise toolkit.ValidationError({ERR_UPSTREAM: [toolkit._(
            "The data service behind the assistant is not reachable right now. "
            "Please try again later.")]})
    if code == jobs.ERROR_LLM:
        raise toolkit.ValidationError({ERR_UPSTREAM: [toolkit._(
            "The language model is not available right now. Please try again later.")]})
    result = dict(result)
    if not cfg.get_bool("show_tool_calls"):
        result["steps"] = []
    return result


def mcp_assistant_ask(context, data_dict):
    """Answer a natural-language question about the portal.

    The question goes to the configured LLM, which calls tools of the CKAN MCP
    server (ondata/ckan-mcp-server); those query this portal's CKAN API.
    Not available in background mode (use ``mcp_assistant_ask_start``).

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
    if cfg.get_bool("background"):
        # Answering here would block the (single) web worker and deadlock with
        # the MCP tools' own requests to this CKAN.
        toolkit.check_access("mcp_assistant_ask", context, data_dict)
        raise toolkit.ValidationError({ERR_MODE: [toolkit._(
            "This portal answers questions in the background: "
            "use mcp_assistant_ask_start and mcp_assistant_result.")]})
    question = _prepare(context, data_dict)
    return _finish(jobs.answer(question, data_dict.get("history")))


def mcp_assistant_ask_start(context, data_dict):
    """Background mode: put a question into the queue and return at once.

    Same parameters, access rules and rate limit as ``mcp_assistant_ask``.

    :returns: ``job``, the id to pass to ``mcp_assistant_result``
    :rtype: dictionary
    """
    question = _prepare(context, data_dict)
    timeout = cfg.get_int("job_timeout")
    job = toolkit.enqueue_job(
        jobs.answer_question, [question, data_dict.get("history")],
        title=u"mcp_assistant: {0}".format(question[:60]),
        queue=cfg.get("job_queue"),
        rq_kwargs={
            "timeout": timeout,          # the worker stops a question after this
            "ttl": timeout,              # a question nobody picked up is dropped
            "result_ttl": 600,           # the answer is kept 10 minutes
            "meta": {"owner": _client_identity(context) or u""},
        })
    return {"job": job.id}


def _job_result(job):
    """The job's return value: ``return_value()`` in RQ >= 1.12 (CKAN 2.11+),
    the ``result`` attribute in RQ 1.0 (CKAN 2.9)."""
    getter = getattr(job, "return_value", None)
    return getter() if callable(getter) else job.result


@toolkit.side_effect_free
def mcp_assistant_result(context, data_dict):
    """Background mode: the state of a question started with ``mcp_assistant_ask_start``.

    :param job: the id returned by ``mcp_assistant_ask_start``
    :type job: string

    :returns: ``{"status": "pending"}`` while the question is in the queue or
        being answered, then ``{"status": "done", "answer", "steps"}``
    :rtype: dictionary
    """
    toolkit.check_access("mcp_assistant_result", context, data_dict)
    from ckan.lib import jobs as ckan_jobs

    not_found = toolkit.ValidationError({ERR_NOT_FOUND: [toolkit._(
        "This answer is no longer available. Please ask again.")]})
    job_id = data_dict.get("job")
    if not job_id or not isinstance(job_id, str):
        raise not_found
    try:
        job = ckan_jobs.job_from_id(job_id)
    except KeyError:
        raise not_found
    # only questions of this plugin, and only to the visitor who asked
    if job.func_name != jobs.JOB_FUNCTION or \
            (job.meta or {}).get("owner", u"") != (_client_identity(context) or u""):
        raise not_found

    status = str(getattr(job.get_status(), "value", job.get_status()) or "")
    if status in ("queued", "started", "deferred", "scheduled"):
        return {"status": "pending", "state": status}
    if status == "finished":
        result = _job_result(job)
        if not isinstance(result, dict):
            raise not_found
        out = _finish(result)
        out["status"] = "done"
        return out
    # failed, stopped, canceled: the worker crashed or the time limit was reached
    log.error("mcp_assistant: background job %s ended with status %s", job_id, status)
    raise toolkit.ValidationError({ERR_UPSTREAM: [toolkit._(
        "The assistant could not answer this question in time. Please try again.")]})


def get_actions():
    return {"mcp_assistant_ask": mcp_assistant_ask,
            "mcp_assistant_ask_start": mcp_assistant_ask_start,
            "mcp_assistant_result": mcp_assistant_result}
