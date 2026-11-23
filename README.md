# ckanext-mcp_assistant

A natural-language assistant for **CKAN** portals. Visitors ask questions about the portal in
their own words ("Which datasets are about cancer?", "How many datasets does each organization
have?") and get a short answer with links to the matching datasets.

An LLM answers the questions by calling the tools of a CKAN MCP server, which queries the
portal's LDM API.

```
Browser ──► LDM /assistant ──► LLM (OpenAI-compatible API)
                 │                  │  "call ckan_package_search(q='cancer')"
                 │◄─────────────────┘
                 ├──► CKAN MCP server (ondata/ckan-mcp-server, Streamable HTTP)
                 │        └──► this portal's LDM Action API (/api/3/action/...)
                 └──► answer + list of the tool calls made ──► Browser
```

## Features

- **"AI Assistant" item in the main navigation**.
- **Assistant page** at `/assistant`: a conversation with follow-up questions, example
  questions, a "New conversation" button, and a fold-out list of the MCP tool calls behind
  each answer (can be switched off).
- **API action** `mcp_assistant_ask`, so scripts can use the assistant too.
- **Cost and abuse protection:** a rate limit per user or IP (kept in Redis),
  a maximum question length, a capped history, an optional login requirement, and tool
  output trimmed before it goes to the LLM.
- **Works on CKAN 2.9 to 2.12 / Python 3.8+.** It has a small built-in MCP client for the
  Streamable HTTP transport, written with `requests`. The official `mcp` SDK needs
  Python ≥ 3.10.
- **Any OpenAI-compatible LLM API** (OpenAI, vLLM, Ollama, LiteLLM, …).
- **English and German** (`ITranslation`). Answers come in the language of the question.

## Requirements

| Component | Version |
|---|---|
| CKAN | 2.9, 2.10, 2.11, 2.12 |
| Python | 3.8+ |
| MCP server | [ondata/ckan-mcp-server](https://github.com/ondata/ckan-mcp-server) with `TRANSPORT=http`; any MCP server with Streamable HTTP works |
| LLM | an API key for an OpenAI-compatible `/chat/completions` endpoint with tool calling |
| Redis | the CKAN Redis, for the rate limit. If Redis is unreachable, `rate_limit_fail_open` decides: `true` (default) answers without a limit, `false` refuses questions until Redis is back |

## Installation

1. Install the plugin into CKAN's virtualenv:

   ```bash
   git clone https://github.com/SDM-TIB/ckanext-mcp_assistant.git
   pip install -e ./ckanext-mcp_assistant
   ```

2. Add `mcp_assistant` to `ckan.plugins`, **before your theme plugin** (e.g. `TIBtheme`). CKAN
   uses the templates of the first plugin listed, and this plugin extends the theme's
   `header.html` rather than replacing it:

   ```ini
   ckan.plugins = mcp_assistant ... TIBtheme ...
   ```

3. Configure at least the LLM key and the MCP server URL (see below). Then restart CKAN.

4. **CKAN must be able to serve a second request while a question is answered**, because the
   MCP tools query this same CKAN over HTTP. Choose one:
   - **Normal mode:** run CKAN with several threads or workers, e.g. `ckan run --threaded`,
     `ckan.devserver.threaded = true`.
   - **Background mode:** for a CKAN that serves one request at a time (`ckan run` without
     `--threaded`). Questions are answered by a CKAN background worker; see
     [Background mode](#background-mode).

   With a single-threaded CKAN in normal mode, every tool call waits until the question's own
   request is finished, times out, and the assistant answers that the portal is unreachable.


## Configuration

Every option can go into `ckan.ini` as `ckanext.mcp_assistant.<name>`, or into the environment
as `CKANEXT__MCP_ASSISTANT__<NAME>` (dots become `__`, upper case). The environment is the easy
route in Docker set-ups where `ckan.ini` already exists. Some options also accept common
variable names (column "Also read from"). The names used before 2.0
(`ckanext.ldm_assistant.<name>`, `CKANEXT__LDM_ASSISTANT__<NAME>`) are **not** read any more.

| Option | Default | Also read from | Description |
|---|---|---|---|
| `mcp_url` | `http://127.0.0.1:3000/mcp` | `MCP_URL` | Streamable HTTP endpoint of the MCP server |
| `mcp_timeout` | `60` | | seconds per MCP request |
| `portal_url` | `ckan.site_url` + `ckan.root_path` (e.g. `https://service.tib.eu/ldmcaimed`) | `LDM_PORTAL` | portal the tools query (passed as `server_url`). Must be reachable **from the MCP server** and listed in its `CKAN_ALLOWED_DOMAINS` |
| `llm.api_key` | – (required) | `OPENAI_API_KEY` | LLM API key |
| `llm.model` | `gpt-5.4-mini` | `LLM_MODEL` | model name |
| `llm.base_url` | `https://api.openai.com/v1` | `OPENAI_BASE_URL` | any OpenAI-compatible API |
| `llm.timeout` | `180` | | seconds per LLM call |
| `max_tool_rounds` | `8` | | LLM ↔ tool round trips per question |
| `max_tool_output` | `15000` | | characters of each tool result given to the LLM |
| `max_question_chars` | `500` | | longer questions are cut |
| `max_history_messages` | `12` | | earlier messages kept for follow-ups (6 turns) |
| `max_history_chars` | `4000` | | per earlier message |
| `tools_cache_seconds` | `600` | | how long the MCP tool list is cached |
| `hidden_tools` | `mqa` | | tools whose name contains one of these (space/comma separated) are not offered. The `mqa` tools only work for dati.gov.it |
| `require_login` | `false` | | only logged-in users may ask |
| `rate_limit_per_hour` | `10` | | questions per user (or IP) per hour; `0` = no limit |
| `rate_limit_fail_open` | `true` | | when Redis is unreachable: `true` answers without a limit (and logs a warning), `false` refuses questions until Redis is back |
| `path` | `/assistant` | | URL path of the assistant page; the JSON endpoints are `<path>/ask` and `<path>/result/<job>` |
| `background` | `false` | | answer questions in a CKAN background job instead of the web request; see [Background mode](#background-mode) |
| `job_queue` | `mcp_assistant` | | background mode: name of the job queue (the worker must listen to it) |
| `job_timeout` | `300` | | background mode: seconds a question may take in the worker; a question nobody picks up within this time is dropped |
| `show_in_header` | `true` | | "AI Assistant" item in the main navigation |
| `show_tool_calls` | `true` | | show "How this answer was found" under each answer |
| `example_questions` | 4 examples | | example questions, separated by `\|` |

Example:

```ini
ckanext.mcp_assistant.mcp_url = http://ldm_mcp:3000/mcp
ckanext.mcp_assistant.llm.model = gpt-5.4-mini
ckanext.mcp_assistant.rate_limit_per_hour = 10
```

## Background mode

For CKAN instances that serve one request at a time (`ckan run` without `--threaded`).

```
Browser ── question ──► CKAN web: checks access + rate limit, puts a job in the queue  (milliseconds)
CKAN worker ── takes the job ──► LLM ⇄ MCP server ⇄ CKAN web API   (the web server is free)
            ── stores the answer in Redis (10 minutes)
Browser ── every 2 s: GET <path>/result/<job> ──► "pending" … then the answer
```

The web server only handles short requests, so it stays free for the MCP tools' API calls and
for other visitors. Each answer is only given to the visitor (user name, or IP address) who
asked the question.

1. Enable it: `ckanext.mcp_assistant.background = true` (or
   `CKANEXT__MCP_ASSISTANT__BACKGROUND=true` in the environment).
2. Run a worker that listens to the queue `mcp_assistant` (and only to it, so it never runs
   jobs of other plugins):

   ```bash
   ckan -c /etc/ckan/default/ckan.ini jobs worker mcp_assistant
   ```

   The plugin ships a [supervisor](http://supervisord.org/) configuration that starts this
   worker, restarts it if it stops, and writes its log into the container log
   (`docker logs`): [`supervisor/mcp-assistant-worker.conf`](supervisor/mcp-assistant-worker.conf).
   In a Docker image, for example:

   ```dockerfile
   RUN apt-get update && apt-get install -y supervisor
   COPY ./Plugins/ckanext-mcp_assistant/supervisor/mcp-assistant-worker.conf /etc/supervisor/conf.d/
   ```

   and in the container's entrypoint script, before CKAN itself is started:

   ```sh
   supervisord -c /etc/supervisor/supervisord.conf
   ```

3. One worker answers one question at a time; further questions wait their turn (the page shows
   "Your question is waiting for its turn…"). For more at once, raise `numprocs` in the
   supervisor file. Each worker needs about as much memory as one CKAN process.

The worker reads the same configuration as the web server (`ckan.ini` and the environment), so
the LLM key and MCP settings need no extra setup. Check it with
`supervisorctl status` and `ckan -c /etc/ckan/default/ckan.ini jobs list mcp_assistant`.

## The MCP server

The plugin talks to [ondata/ckan-mcp-server](https://github.com/ondata/ckan-mcp-server)
(npm package `@aborruso/ckan-mcp-server`) over Streamable HTTP. Its important settings are:

| Variable | Value | Why |
|---|---|---|
| `TRANSPORT` | `http` | Streamable HTTP instead of stdio |
| `PORT` | `3000` | |
| `CKAN_HTTP_HOST` | `0.0.0.0` in Docker, otherwise `127.0.0.1` | interface to listen on |
| `CKAN_HTTP_ALLOWED_HOSTS` | e.g. `ldm_mcp:3000` | the server rejects any `Host` header other than `localhost`/`127.0.0.1` with **403** (DNS-rebinding protection). Add the name CKAN uses to reach it |
| `CKAN_ALLOWED_DOMAINS` | e.g. `service.tib.eu` | the CKAN hosts the tools may query (required in HTTP mode) |



## API

```bash
curl -X POST https://<portal>/api/3/action/mcp_assistant_ask \
     -H "Content-Type: application/json" \
     -d '{"question": "Which datasets are about cancer?"}'
```

```json
{"success": true, "result": {
  "answer": "I found 3 datasets about cancer: ...",
  "steps": [{"tool": "ckan_package_search", "args": "q=\"cancer\"", "error": false,
             "chars": 5321, "summary": "# CKAN Package Search Results", "ms": 412}]}}
```

For follow-up questions, pass `history`, a list of `{"role": "user"|"assistant", "content": "..."}`.
The same authorization and rate limit apply as on the web page.

In [background mode](#background-mode), `mcp_assistant_ask` is not available. Start a question
with `mcp_assistant_ask_start` (same parameters; returns `{"job": "<id>"}`) and call
`mcp_assistant_result` with `job=<id>` until `status` is `done`:

```bash
curl -X POST https://<portal>/api/3/action/mcp_assistant_ask_start \
     -H "Content-Type: application/json" -d '{"question": "Which datasets are about cancer?"}'
curl "https://<portal>/api/3/action/mcp_assistant_result?job=<id>"
# {"success": true, "result": {"status": "pending", "state": "started"}}  ... then
# {"success": true, "result": {"status": "done", "answer": "...", "steps": [...]}}
```

## Templates and helpers

| Template | Purpose |
|---|---|
| `mcp_assistant/index.html` | the assistant page (`/assistant`, `?q=` asks right away) |
| `header.html` | adds the navigation item (block `header_site_navigation_tabs`) |

Helpers: `h.mcp_assistant_show_in_header()`,
`h.mcp_assistant_max_chars()`. The colours are CSS variables (`--mcp-assistant-accent`, …)
in `assets/css/mcp-assistant.css`.

## Privacy and cost

- Questions, earlier turns and the portal's tool results (public metadata) are sent to the
  configured LLM provider. The page tells users this and asks them not to enter personal
  data. Check that this fits your data-protection notice. Point `llm.base_url` at a
  self-hosted model to keep everything in-house.
- Every question costs tokens. The tool definitions alone add about 10k tokens per LLM
  call. The rate limit, `max_tool_rounds` and `hidden_tools` keep this in check.

## Translations


```bash
python setup.py extract_messages     # update the .pot
python setup.py update_catalog -l de # merge into de/LC_MESSAGES/ckanext-mcp_assistant.po
python setup.py compile_catalog      # build the .mo
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| "The data service behind the assistant is not reachable" | CKAN cannot reach `mcp_url`. In the CKAN log: `MCP server answered HTTP 403` means `CKAN_HTTP_ALLOWED_HOSTS` is missing the host name; `Cannot reach` means a wrong URL/network or the server is down |
| Answers say the portal is unreachable | the MCP server cannot reach `portal_url`: check `CKAN_ALLOWED_DOMAINS`, DNS and the proxy; use the public URL |
| "The assistant is not configured yet" | no `llm.api_key` / `OPENAI_API_KEY` in CKAN's environment |
| "The language model is not available" | wrong key or model, or the provider is down; details in the CKAN log |
| "The assistant is temporarily unavailable" | Redis is unreachable and `rate_limit_fail_open = false` |
| No "AI Assistant" item in the menu | `mcp_assistant` is listed after the theme in `ckan.plugins`, or `show_in_header = false` |
| The site hangs while someone asks, or every answer says the portal is unreachable | single-threaded `ckan run` in normal mode; see Installation step 4 |
| The page keeps showing "Your question is waiting for its turn…" | background mode, but no worker is running: check `supervisorctl status` and the `mcp-assistant-worker` lines in the container log |
| "This answer is no longer available" | background mode: the answer was older than 10 minutes, the question was not picked up within `job_timeout`, or it was asked from another IP address |
| "The assistant could not answer this question in time" | background mode: the worker stopped the question after `job_timeout` seconds |

## License

[AGPL v3](LICENSE).
