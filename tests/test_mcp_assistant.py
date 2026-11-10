# encoding: utf-8
"""Standalone unit tests for ckanext-mcp_assistant core modules.

Covers agent, mcp_client, llm, and ratelimit without requiring a running
CKAN instance, Redis server, or external LLM/MCP service.
"""
import json
import unittest

from ckanext.mcp_assistant import agent, ratelimit
from ckanext.mcp_assistant.llm import LLMClient, LLMError
from ckanext.mcp_assistant.mcp_client import MCPClient, MCPError, result_text


class FakeLLM(object):
    """Deterministic stub for LLMClient.chat(messages, tools)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self.responses:
            return {"role": "assistant", "content": "Fallback answer."}
        return self.responses.pop(0)


class FakeMCPClient(object):
    """In-memory stub for MCPClient context manager used by agent.run()."""

    def __init__(self, tools=None, tool_results=None):
        self.tools = tools or [
            {
                "name": "ckan_package_search",
                "description": "Search CKAN datasets.",
                "inputSchema": {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "type": "object",
                    "properties": {
                        "server_url": {"type": "string"},
                        "q": {"type": "string"},
                    },
                },
            },
            {
                "name": "ckan_mqa_quality",
                "description": "Hidden MQA tool.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]
        self.tool_results = tool_results or {}
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list_tools(self):
        return list(self.tools)

    def call_tool(self, name, arguments=None):
        self.calls.append((name, dict(arguments or {})))
        handler = self.tool_results.get(name)
        if isinstance(handler, Exception):
            raise handler
        if callable(handler):
            return handler(arguments or {})
        if handler is not None:
            return handler
        return {
            "content": [{"type": "text", "text": "Found 2 datasets about cancer."}],
            "isError": False,
        }


class FakeHTTPResponse(object):
    """Minimal requests.Response stand-in for JSON and SSE transport tests."""

    def __init__(self, status_code=200, json_data=None, text="", headers=None, lines=None):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self.headers = headers or {"Content-Type": "application/json"}
        self._lines = lines or []
        self.closed = False

    def json(self):
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data

    def iter_lines(self, decode_unicode=True):
        for line in self._lines:
            yield line

    def close(self):
        self.closed = True


class FakeHTTPSession(object):
    """Captures HTTP POST/DELETE calls for MCPClient and LLMClient tests."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.posts = []
        self.deletes = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if not self.responses:
            raise AssertionError("Unexpected POST request to {0}".format(url))
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def delete(self, url, **kwargs):
        self.deletes.append((url, kwargs))
        return FakeHTTPResponse(status_code=204)


class FakeRedisPipeline(object):
    def __init__(self, store, fail_exc=None):
        self.store = store
        self.fail_exc = fail_exc
        self.ops = []

    def incr(self, key):
        self.ops.append(("incr", key))
        return self

    def expire(self, key, seconds):
        self.ops.append(("expire", key, seconds))
        return self

    def execute(self):
        if self.fail_exc:
            raise self.fail_exc
        results = []
        for op in self.ops:
            if op[0] == "incr":
                key = op[1]
                self.store[key] = self.store.get(key, 0) + 1
                results.append(self.store[key])
            elif op[0] == "expire":
                results.append(True)
        return results


class FakeRedis(object):
    def __init__(self, fail_exc=None):
        self.store = {}
        self.fail_exc = fail_exc

    def pipeline(self):
        return FakeRedisPipeline(self.store, fail_exc=self.fail_exc)


class TestAgent(unittest.TestCase):
    def setUp(self):
        agent.clear_tools_cache()
        self.settings = agent.Settings(
            mcp_url="http://127.0.0.1:3000/mcp",
            portal_url="https://service.tib.eu/ldmservice/",
            max_tool_rounds=3,
            max_question_chars=50,
            max_history_messages=2,
            max_history_chars=20,
            hidden_tools=["mqa"],
        )

    def test_normalize_question_strips_collapses_and_truncates(self):
        self.assertIsNone(agent.normalize_question(None, self.settings))
        self.assertIsNone(agent.normalize_question("   \n\t  ", self.settings))
        self.assertEqual(
            agent.normalize_question("  Which   datasets\n are about COVID? ", self.settings),
            "Which datasets are about COVID?",
        )
        long_q = "a" * 100
        self.assertEqual(len(agent.normalize_question(long_q, self.settings)), 50)

    def test_clean_history_filters_roles_and_truncates(self):
        raw_history = [
            {"role": "system", "content": "ignore me"},
            {"role": "tool", "content": "ignore tool output"},
            {"role": "user", "content": "First user question"},
            {"role": "assistant", "content": "First assistant answer that is quite long"},
            {"role": "user", "content": "Second user question"},
        ]
        cleaned = agent.clean_history(raw_history, self.settings)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(cleaned[0]["role"], "assistant")
        self.assertEqual(cleaned[0]["content"], "First assistant answ")
        self.assertEqual(cleaned[1], {"role": "user", "content": "Second user question"})

    def test_get_tools_filters_hidden_tools_and_caches(self):
        fake_mcp = FakeMCPClient()
        tools = agent.get_tools(fake_mcp, self.settings)
        names = [t["function"]["name"] for t in tools]
        self.assertEqual(names, ["ckan_package_search"])
        self.assertNotIn("$schema", tools[0]["function"]["parameters"])

    def test_run_injects_portal_server_url_and_records_steps(self):
        fake_mcp = FakeMCPClient()
        fake_llm = FakeLLM([
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "ckan_package_search",
                            "arguments": json.dumps({"q": "cancer"}),
                        },
                    }
                ],
            },
            {
                "role": "assistant",
                "content": "Here are 2 datasets about cancer.",
            },
        ])

        out = agent.run(
            question="Which datasets are about cancer?",
            history=[],
            settings=self.settings,
            llm=fake_llm,
            mcp_factory=lambda url, timeout=60: fake_mcp,
        )

        self.assertEqual(out["answer"], "Here are 2 datasets about cancer.")
        self.assertEqual(len(out["steps"]), 1)
        self.assertEqual(out["steps"][0]["tool"], "ckan_package_search")
        self.assertFalse(out["steps"][0]["error"])
        # server_url must be injected into the actual MCP call but hidden from _shown_args
        self.assertEqual(
            fake_mcp.calls[0],
            ("ckan_package_search", {"q": "cancer", "server_url": "https://service.tib.eu/ldmservice"}),
        )
        self.assertEqual(out["steps"][0]["args"], 'q="cancer"')

    def test_run_handles_failing_tool_gracefully(self):
        fake_mcp = FakeMCPClient(
            tool_results={"ckan_package_search": RuntimeError("timeout talking to Solr")}
        )
        fake_llm = FakeLLM([
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_err",
                        "function": {"name": "ckan_package_search", "arguments": '{"q": "covid"}'},
                    }
                ],
            },
            {
                "role": "assistant",
                "content": "Sorry, the search tool failed.",
            },
        ])

        out = agent.run(
            question="covid",
            history=[],
            settings=self.settings,
            llm=fake_llm,
            mcp_factory=lambda url, timeout=60: fake_mcp,
        )
        self.assertEqual(out["answer"], "Sorry, the search tool failed.")
        self.assertTrue(out["steps"][0]["error"])
        self.assertIn("Tool call failed", out["steps"][0]["summary"])

    def test_run_stops_when_max_tool_rounds_exceeded(self):
        fake_mcp = FakeMCPClient()
        looping_call = {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_loop",
                    "function": {"name": "ckan_package_search", "arguments": '{"q": "loop"}'},
                }
            ],
        }
        fake_llm = FakeLLM([looping_call, looping_call, looping_call])

        out = agent.run(
            question="loop forever",
            history=[],
            settings=self.settings,
            llm=fake_llm,
            mcp_factory=lambda url, timeout=60: fake_mcp,
        )
        self.assertIn("too many tool calls", out["answer"])
        self.assertEqual(len(out["steps"]), 3)


class TestMCPClient(unittest.TestCase):
    def test_initialize_list_tools_pagination_and_close(self):
        session = FakeHTTPSession([
            # 1. initialize response with Mcp-Session-Id header
            FakeHTTPResponse(
                headers={"Content-Type": "application/json", "Mcp-Session-Id": "sess-42"},
                json_data={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "serverInfo": {"name": "ckan-mcp-server"},
                    },
                },
            ),
            # 2. notifications/initialized response
            FakeHTTPResponse(status_code=202, json_data={}),
            # 3. tools/list page 1 with nextCursor
            FakeHTTPResponse(
                json_data={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "tools": [{"name": "tool_a"}],
                        "nextCursor": "page_2",
                    },
                }
            ),
            # 4. tools/list page 2 final
            FakeHTTPResponse(
                json_data={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "result": {
                        "tools": [{"name": "tool_b"}],
                    },
                }
            ),
        ])

        with MCPClient("http://127.0.0.1:3000/mcp", session=session) as client:
            self.assertEqual(client.session_id, "sess-42")
            self.assertEqual(client.protocol_version, "2025-06-18")
            tools = client.list_tools()
            self.assertEqual([t["name"] for t in tools], ["tool_a", "tool_b"])

        # Verify DELETE was sent on context exit to close session
        self.assertEqual(len(session.deletes), 1)

    def test_sse_response_ignores_comments_and_notifications(self):
        sse_lines = [
            ": keep-alive comment",
            "event: message",
            'data: {"jsonrpc": "2.0", "method": "notifications/progress"}',
            "",
            "event: message",
            'data: {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "SSE hello"}]}}',
            "",
        ]
        session = FakeHTTPSession([
            FakeHTTPResponse(
                headers={"Content-Type": "text/event-stream; charset=utf-8"},
                lines=sse_lines,
            )
        ])
        client = MCPClient("http://127.0.0.1:3000/mcp", session=session)
        res = client.call_tool("ckan_status_show", {})
        text, is_error = result_text(res)
        self.assertEqual(text, "SSE hello")
        self.assertFalse(is_error)

    def test_result_text_structured_content_fallback(self):
        text, is_err = result_text({"structuredContent": {"count": 5}, "isError": True})
        self.assertEqual(json.loads(text), {"count": 5})
        self.assertTrue(is_err)

    def test_jsonrpc_error_raises_mcp_error(self):
        session = FakeHTTPSession([
            FakeHTTPResponse(
                json_data={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32601, "message": "Method not found"},
                }
            )
        ])
        client = MCPClient("http://127.0.0.1:3000/mcp", session=session)
        with self.assertRaises(MCPError):
            client.call_tool("unknown_tool", {})


class TestLLMClient(unittest.TestCase):
    def test_missing_api_key_raises_llm_error(self):
        client = LLMClient(api_key="", model="gpt-5.4-mini")
        with self.assertRaises(LLMError):
            client.chat([{"role": "user", "content": "hi"}])

    def test_chat_success_and_http_error(self):
        session = FakeHTTPSession([
            FakeHTTPResponse(
                status_code=200,
                json_data={"choices": [{"message": {"role": "assistant", "content": "Hello!"}}]},
            ),
            FakeHTTPResponse(status_code=500, text="Internal Server Error"),
        ])
        client = LLMClient(api_key="sk-test", model="gpt-5.4-mini", session=session)
        msg = client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(msg["content"], "Hello!")

        with self.assertRaises(LLMError):
            client.chat([{"role": "user", "content": "hi again"}])


class TestRateLimit(unittest.TestCase):
    def test_allows_within_limit_and_blocks_when_exceeded(self):
        redis_stub = FakeRedis()
        allowed1, retry1 = ratelimit.hit("user:alice", limit=2, redis_conn=redis_stub, now=3610)
        allowed2, retry2 = ratelimit.hit("user:alice", limit=2, redis_conn=redis_stub, now=3620)
        allowed3, retry3 = ratelimit.hit("user:alice", limit=2, redis_conn=redis_stub, now=3630)

        self.assertTrue(allowed1)
        self.assertEqual(retry1, 0)
        self.assertTrue(allowed2)
        self.assertFalse(allowed3)
        self.assertEqual(retry3, 7200 - 3630)

    def test_fail_open_vs_fail_closed_on_redis_error(self):
        broken_redis = FakeRedis(fail_exc=RuntimeError("Redis connection refused"))

        allowed, retry = ratelimit.hit(
            "user:alice", limit=5, redis_conn=broken_redis, now=100, fail_open=True
        )
        self.assertTrue(allowed)
        self.assertEqual(retry, 0)

        with self.assertRaises(ratelimit.RateLimitUnavailable):
            ratelimit.hit(
                "user:alice", limit=5, redis_conn=broken_redis, now=100, fail_open=False
            )


if __name__ == "__main__":
    unittest.main()
