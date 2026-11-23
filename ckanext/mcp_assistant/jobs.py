# encoding: utf-8
"""Answering a question, in the web request or in a CKAN background job.

Background mode (``ckanext.mcp_assistant.background = true``) exists for CKAN
instances that serve one request at a time (``ckan run`` without
``--threaded``). There the assistant cannot answer inside the web request:
its MCP tools query this same CKAN over HTTP, and that request would have to
wait until the assistant's own request is finished (a deadlock that only
ends with a timeout). In background mode the web request only puts the
question into a queue; a separate worker process

    ckan -c /etc/ckan/default/ckan.ini jobs worker mcp_assistant

answers it, while the web server stays free for the MCP tools' API calls.
The page then polls for the result.

:func:`answer` is also used by the normal (synchronous) mode, so both modes
produce exactly the same answers.
"""
import logging

from ckanext.mcp_assistant import agent, config as cfg
from ckanext.mcp_assistant.llm import LLMClient, LLMError
from ckanext.mcp_assistant.mcp_client import MCPError

log = logging.getLogger(__name__)

#: error codes returned instead of an answer. They are turned into translated
#: messages by the web request, because a worker has no request (and no
#: language) of its own.
ERROR_MCP = "mcp"
ERROR_LLM = "llm"

#: what the web request checks before it hands out a job's result
JOB_FUNCTION = "ckanext.mcp_assistant.jobs.answer_question"


def answer(question, history):
    # type: (str, object) -> dict
    """Run the assistant. Returns ``{"answer", "steps"}`` or ``{"error_code"}``."""
    settings = cfg.get_settings()
    llm = LLMClient(api_key=cfg.get("llm.api_key"), model=cfg.get("llm.model"),
                    base_url=cfg.get("llm.base_url"), timeout=float(cfg.get("llm.timeout")))
    try:
        result = agent.run(question, history, settings, llm)
    except MCPError as e:
        log.error("mcp_assistant: MCP server problem: %s", e)
        return {"error_code": ERROR_MCP}
    except LLMError as e:
        log.error("mcp_assistant: language model problem: %s", e)
        return {"error_code": ERROR_LLM}
    log.info("mcp_assistant: answered a question with %d tool call(s)", len(result["steps"]))
    return result


def answer_question(question, history):
    # type: (str, object) -> dict
    """Entry point of the background job (runs in the ``ckan jobs worker`` process).

    Never raises for the expected problems (MCP server or LLM unreachable):
    the error code is returned as the job's result, so the page can show a
    proper message instead of "something went wrong".
    """
    return answer(question, history)
