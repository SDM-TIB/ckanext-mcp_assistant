# encoding: utf-8
"""ckanext-mcp_assistant: ask questions about the LDM portal in natural language.

The plugin adds

* a page at ``/assistant`` (path set by ``ckanext.mcp_assistant.path``) and an
  "AI Assistant" item in the main navigation,
* the action ``mcp_assistant_ask`` (also usable through the CKAN API).

Questions are answered by an LLM that calls the tools of a CKAN MCP server
(ondata/ckan-mcp-server), which in turn queries this portal's CKAN API.
"""
import logging
import os

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
from ckan.lib.plugins import DefaultTranslation

from ckanext.mcp_assistant import config as cfg, helpers, views
from ckanext.mcp_assistant.logic import action, auth

log = logging.getLogger(__name__)


class McpAssistantPlugin(plugins.SingletonPlugin, DefaultTranslation):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IConfigurable)
    plugins.implements(plugins.IBlueprint)
    plugins.implements(plugins.IActions)
    plugins.implements(plugins.IAuthFunctions)
    plugins.implements(plugins.ITemplateHelpers)
    plugins.implements(plugins.ITranslation)

    # IConfigDeclaration only exists in CKAN >= 2.10; on CKAN 2.9 it is skipped.
    if hasattr(plugins, "IConfigDeclaration"):
        plugins.implements(plugins.IConfigDeclaration)

        def declare_config_options(self, declaration, key):
            import yaml  # a CKAN requirement
            path = os.path.join(os.path.dirname(__file__), "config_declaration.yaml")
            with open(path, encoding="utf-8") as f:
                declaration.load_dict(yaml.safe_load(f))

    # IConfigurer
    def update_config(self, config_):
        toolkit.add_template_directory(config_, "templates")
        toolkit.add_resource("assets", "mcp_assistant")

    # IConfigurable
    def configure(self, config_):
        if not cfg.is_configured():
            log.warning(
                "mcp_assistant: no LLM API key configured; the assistant page will show a "
                "'not configured' notice. Set ckanext.mcp_assistant.llm.api_key or OPENAI_API_KEY.")
        else:
            log.info("mcp_assistant: MCP server %s, model %s, portal %s",
                     cfg.get("mcp_url"), cfg.get("llm.model"), cfg.portal_url())

    # IBlueprint
    def get_blueprint(self):
        return views.get_blueprints()

    # IActions
    def get_actions(self):
        return action.get_actions()

    # IAuthFunctions
    def get_auth_functions(self):
        return auth.get_auth_functions()

    # ITemplateHelpers
    def get_helpers(self):
        return helpers.get_helpers()
