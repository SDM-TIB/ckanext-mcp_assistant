# encoding: utf-8
"""Minimal, synchronous MCP client for the *Streamable HTTP* transport.

CKAN 2.9 runs on Python 3.8, while the official ``mcp`` Python SDK needs
Python >= 3.10 and is asyncio based. This module implements just the part of
the Model Context Protocol the assistant needs (``initialize``,
``tools/list`` and ``tools/call``) on top of :mod:`requests`, which CKAN
already ships with.

Spec: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports

Usage::

    with MCPClient("http://ldm_mcp:3000/mcp") as client:
        tools = client.list_tools()
        result = client.call_tool("ckan_package_search", {"q": "cancer"})
"""
from __future__ import annotations

import itertools
import json
import logging
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests

log = logging.getLogger(__name__)

#: Protocol version we ask for. The server answers with the version it
#: actually speaks; that one is then sent on every later request.
PROTOCOL_VERSION = "2025-06-18"


def _package_version():
    # type: () -> str
    try:
        from importlib.metadata import version  # Python >= 3.8
        return version("ckanext-mcp_assistant")
    except Exception:  # not installed as a package (e.g. plain source checkout)
        return "unknown"


CLIENT_INFO = {"name": "ckanext-mcp_assistant", "version": _package_version()}


class MCPError(Exception):
    """The MCP server could not be reached or answered with an error."""


class MCPClient(object):
    """One MCP session over Streamable HTTP. Use it as a context manager."""

    def __init__(self, url, timeout=60, headers=None, session=None):
        # type: (str, float, Optional[Dict[str, str]], Optional[requests.Session]) -> None
        self.url = url
        self.timeout = timeout
        self.extra_headers = dict(headers or {})
        self.http = session or requests.Session()
        self.session_id = None  # type: Optional[str]
        self.protocol_version = None  # type: Optional[str]
        self.server_info = {}  # type: Dict[str, Any]
        self._ids = itertools.count(1)

    # -- context manager ---------------------------------------------------
    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- protocol ----------------------------------------------------------
    def initialize(self):
        # type: () -> Dict[str, Any]
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        self.protocol_version = result.get("protocolVersion") or PROTOCOL_VERSION
        self.server_info = result.get("serverInfo") or {}
        self.notify("notifications/initialized")
        return result

    def list_tools(self):
        # type: () -> List[Dict[str, Any]]
        """All tools of the server (follows ``nextCursor`` pagination)."""
        tools, cursor = [], None  # type: List[Dict[str, Any]], Optional[str]
        for _ in range(50):  # hard stop against a server that never ends paging
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name, arguments=None):
        # type: (str, Optional[Dict[str, Any]]) -> Dict[str, Any]
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def close(self):
        # type: () -> None
        """End the session on the server (best effort, errors are ignored)."""
        if self.session_id:
            try:
                self.http.delete(self.url, headers=self._headers(), timeout=5)
            except requests.RequestException:
                pass
            self.session_id = None

    # -- JSON-RPC over HTTP ------------------------------------------------
    def request(self, method, params=None):
        # type: (str, Optional[Dict[str, Any]]) -> Dict[str, Any]
        msg_id = next(self._ids)
        payload = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params
        response = self._post(payload, stream=True)
        try:
            message = self._read_response(response, msg_id)
        finally:
            response.close()
        if "error" in message:
            err = message["error"] or {}
            raise MCPError("MCP error {0} in {1}: {2}".format(
                err.get("code"), method, err.get("message")))
        return message.get("result") or {}

    def notify(self, method, params=None):
        # type: (str, Optional[Dict[str, Any]]) -> None
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._post(payload, stream=False).close()

    def _headers(self):
        # type: () -> Dict[str, str]
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        headers.update(self.extra_headers)
        return headers

    def _post(self, payload, stream):
        # type: (Dict[str, Any], bool) -> requests.Response
        try:
            response = self.http.post(self.url, data=json.dumps(payload), headers=self._headers(),
                                      timeout=self.timeout, stream=stream)
        except requests.RequestException as e:
            raise MCPError("Cannot reach the MCP server at {0}: {1}".format(self.url, e))
        if response.status_code >= 400:
            body = response.text[:300]
            response.close()
            raise MCPError("MCP server answered HTTP {0}: {1}".format(response.status_code, body))
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self.session_id = session_id
        return response

    def _read_response(self, response, msg_id):
        # type: (requests.Response, int) -> Dict[str, Any]
        """The answer comes either as plain JSON or as a Server-Sent Events stream
        that may also carry server notifications before the actual response."""
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type == "text/event-stream":
            for message in _iter_sse_messages(response):
                for item in message if isinstance(message, list) else [message]:
                    if isinstance(item, dict) and item.get("id") == msg_id and (
                            "result" in item or "error" in item):
                        return item
            raise MCPError("The MCP event stream ended without a response.")
        try:
            data = response.json()
        except ValueError:
            raise MCPError("The MCP server sent an unreadable answer ({0}).".format(
                content_type or "no content type"))
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("id") == msg_id:
                return item
        raise MCPError("The MCP server's answer did not match the request.")


def _iter_sse_messages(response):
    # type: (requests.Response) -> Iterator[Any]
    """Yield the JSON payload of every ``message`` event of an SSE stream."""
    event, data_lines = "message", []  # type: str, List[str]
    for raw in response.iter_lines(decode_unicode=True):
        line = raw if raw is not None else ""
        if line == "":
            if data_lines and event == "message":
                try:
                    yield json.loads("\n".join(data_lines))
                except ValueError:
                    log.debug("Skipping a non-JSON SSE event")
            event, data_lines = "message", []
        elif line.startswith(":"):
            continue  # comment / keep-alive
        else:
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                event = value or "message"
            elif field == "data":
                data_lines.append(value)
    if data_lines and event == "message":
        try:
            yield json.loads("\n".join(data_lines))
        except ValueError:
            pass


def result_text(result):
    # type: (Dict[str, Any]) -> Tuple[str, bool]
    """Flatten a ``tools/call`` result to text; second value is the error flag."""
    parts = []
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text") or "")
        elif isinstance(block, dict):
            parts.append("[{0}]".format(block.get("type", "content")))
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    return "\n".join(parts), bool(result.get("isError"))
