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

- **"AI Assistant" item in the main navigation** (next to Organizations, Groups, About, Demo, API).
- **Assistant page** at `/assistant`: a conversation with follow-up questions, example
  questions, a "New conversation" button, and a fold-out list of the MCP tool calls behind
  each answer (can be switched off).
- **API action** `mcp_assistant_ask`, so scripts can use the assistant too.
- **Cost and abuse protection:** a rate limit per user or IP (kept in Redis),
  a maximum question length, a capped history, an optional login requirement, and tool
  output trimmed before it goes to the LLM.
- **Works on CKAN 2.9 to 2.12 / Python 3.8+.** It has a small built-in MCP client for the
  Streamable HTTP transport, written with `requests`. The official `mcp` SDK needs
  Python ≥ 3.10. There are no extra dependencies.
- **Any OpenAI-compatible LLM API** (OpenAI, vLLM, Ollama, LiteLLM, …).
- **English and German** (`ITranslation`). Answers come in the language of the question.

## Requirements

| Component | Version |
|---|---|
| CKAN | 2.9, 2.10, 2.11, 2.12 (checked with 2.9.5, 2.11.4 and 2.12.1). Works with the Bootstrap 3 and Bootstrap 5 templates and Font Awesome 4.7 and 6; CSRF tokens (2.10+) are sent automatically; on 2.10+ the options are declared (`ckan config declaration mcp_assistant`) |
| Python | 3.8+ |
| MCP server | [ondata/ckan-mcp-server](https://github.com/ondata/ckan-mcp-server) with `TRANSPORT=http`; any MCP server with Streamable HTTP works |
| LLM | an API key for an OpenAI-compatible `/chat/completions` endpoint with tool calling |
| Redis | the CKAN Redis, for the rate limit (optional: without it the limit is skipped) |

## Installation

1. Install the plugin into CKAN's virtualenv:

   ```bash
   . /usr/lib/ckan/default/bin/activate
   pip install -e /path/to/ckanext-mcp_assistant
   ```

2. Add `mcp_assistant` to `ckan.plugins`, **before your theme plugin** (e.g. `TIBtheme`). CKAN
   uses the templates of the first plugin listed, and this plugin extends the theme's
   `header.html` rather than replacing it:

   ```ini
   ckan.plugins = mcp_assistant ... TIBtheme ...
   ```

3. Configure at least the LLM key and the MCP server URL (see below). Then restart CKAN.


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
| `path` | `/assistant` | | URL path of the assistant page; the JSON endpoint is `<path>/ask` |
| `show_in_header` | `true` | | "AI Assistant" item in the main navigation |
| `show_tool_calls` | `true` | | show "How this answer was found" under each answer |
| `example_questions` | 4 examples | | example questions, separated by `\|` |

Example:

```ini
ckanext.mcp_assistant.mcp_url = http://ldm_mcp:3000/mcp
ckanext.mcp_assistant.llm.model = gpt-5.4-mini
ckanext.mcp_assistant.rate_limit_per_hour = 10
# the key is better kept in the environment: OPENAI_API_KEY=sk-...
```

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
| The site hangs while someone asks | single-threaded `ckan run`; see Installation step 4 |

## Upgrading from ckanext-ldm_assistant 1.x

Version 2.0 renamed the plugin. To upgrade an existing instance:

1. `pip uninstall ckanext-ldm_assistant`, then install this package.
2. In `ckan.plugins`, replace `ldm_assistant` with `mcp_assistant`.
3. Rename the options from `ckanext.ldm_assistant.*` to `ckanext.mcp_assistant.*` (and
   `CKANEXT__LDM_ASSISTANT__*` to `CKANEXT__MCP_ASSISTANT__*` in `.env`). The old names are
   ignored. Note that the default `rate_limit_per_hour` is now `10` (was `30`).
4. Scripts that call the API must use the new action name `mcp_assistant_ask`
   (was `ldm_assistant_ask`). Template overrides must use `mcp_assistant/index.html`, the
   endpoints `mcp_assistant.index` / `mcp_assistant.ask` and the helpers
   `h.mcp_assistant_*`; the CSS classes and variables are now `mcp-assistant*`.

## License

[AGPL v3](LICENSE), like the other LDM plugins.
