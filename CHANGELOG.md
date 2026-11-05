# Changelog

## 2.0.0 – 2026-11-05

Breaking: the plugin was renamed from `ckanext-ldm_assistant` (plugin `ldm_assistant`) to
`ckanext-mcp_assistant` (plugin `mcp_assistant`), so it can be used by any CKAN portal. See
"Upgrading from ckanext-ldm_assistant 1.x" in the README.

- Renamed: package, plugin, config options (`ckanext.mcp_assistant.*`,
  `CKANEXT__MCP_ASSISTANT__*`), API action `mcp_assistant_ask`, blueprint and endpoints,
  template helpers, templates, assets, CSS classes and variables, JS module, Redis keys and the
  translation domain. The old option names are no longer read.
- New option `path` to change the URL of the assistant page (default `/assistant`).
- The MCP client reports the installed package version.
- Wording: "AI Assistant" instead of "LDM Assistant", "Searching the portal…";
  German translations updated.
- CKAN 2.10–2.12 support, CKAN 2.9 still supported: Bootstrap 3 and 5 class names side by
  side, icons that exist in Font Awesome 4.7 and 6, `toolkit.current_user` when available,
  config declaration (`IConfigDeclaration`) on CKAN 2.10+. Python 3.8+.
- Default `rate_limit_per_hour` is now `10` (was `30`).
- New option `rate_limit_fail_open` (default `true`, as before).

## 1.0.1 – 2026-11-02

- The default portal URL now includes `ckan.root_path` (e.g. `/ldmcaimed`), so the MCP tools
  query the right CKAN API on instances served under a sub-path.

## 1.0.0 – 2026-11-02

First release.

- Assistant page `/assistant` with follow-up questions, example questions and the list of
  MCP tool calls behind each answer.
- "AI Assistant" item in the main navigation.
- Action `mcp_assistant_ask` (web page and CKAN API) with an auth function and optional login.
- Built-in MCP Streamable HTTP client for Python 3.8 (`requests` only), tested against
  `@aborruso/ckan-mcp-server` 0.4.134.
- Rate limit per user/IP in Redis, input and history limits.
- English and German.
