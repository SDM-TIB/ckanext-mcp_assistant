# encoding: utf-8
import ckan.plugins.toolkit as toolkit

from ckanext.mcp_assistant import config as cfg


@toolkit.auth_allow_anonymous_access
def mcp_assistant_ask(context, data_dict):
    """Anyone may ask, unless ``ckanext.mcp_assistant.require_login`` is true."""
    if cfg.get_bool("require_login") and not context.get("auth_user_obj"):
        return {"success": False,
                "msg": toolkit._("Please log in to use the AI Assistant.")}
    return {"success": True}


def get_auth_functions():
    return {"mcp_assistant_ask": mcp_assistant_ask}
