# encoding: utf-8
"""Client for any OpenAI-compatible ``/chat/completions`` API (OpenAI, Azure
OpenAI behind a compatible gateway, vLLM, Ollama, LiteLLM, ...)."""
import logging
from typing import Any, Dict, List

import requests

log = logging.getLogger(__name__)


class LLMError(Exception):
    """The language model API could not be reached or returned an error."""


class LLMClient(object):

    def __init__(self, api_key, model, base_url="https://api.openai.com/v1", timeout=180,
                 session=None):
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout = timeout
        self.http = session or requests.Session()

    def chat(self, messages, tools=None):
        # type: (List[Dict[str, Any]], List[Dict[str, Any]]) -> Dict[str, Any]
        """Send one chat completion request and return the assistant message."""
        if not self.api_key:
            raise LLMError("No API key is configured for the language model.")
        body = {"model": self.model, "messages": messages}  # type: Dict[str, Any]
        if tools:
            body["tools"] = tools
        try:
            r = self.http.post("{0}/chat/completions".format(self.base_url), json=body,
                               headers={"Authorization": "Bearer {0}".format(self.api_key)},
                               timeout=self.timeout)
        except requests.RequestException as e:
            raise LLMError("Cannot reach the language model API: {0}".format(e))
        if r.status_code != 200:
            raise LLMError("Language model API error {0}: {1}".format(r.status_code, r.text[:400]))
        try:
            return r.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("Unexpected answer from the language model API.")
