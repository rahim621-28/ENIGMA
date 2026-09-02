"""Ollama provider: talks to a local Ollama daemon over HTTP.

No API key, no network egress beyond localhost -- this is what makes the
100%-offline mode possible. Requires `ollama serve` running and the model
already pulled (e.g. `ollama pull qwen2.5-coder:7b`).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from enigma.llm.base import BaseLLMProvider


class OllamaProvider(BaseLLMProvider):
    def __init__(self, model_name: str, host: str = "http://localhost:11434", check_reachable: bool = True):
        self.model_name = model_name
        self.host = host.rstrip("/")
        if check_reachable:
            self._check_reachable()

    def _check_reachable(self) -> None:
        """Fail fast at construction time if the Ollama daemon isn't reachable.

        Without this, an unreachable Ollama only surfaces as an error deep
        inside a LangGraph node during actual inference -- too late for a
        caller (like the web server's demo-mode fallback) to catch and
        gracefully degrade. Checking here makes Ollama fail at the same
        point Gemini/OpenAI do (missing API key raises immediately), so
        callers can rely on a single try/except around provider construction.
        """
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", method="GET")
            urllib.request.urlopen(req, timeout=3)
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(
                f"Could not reach Ollama at {self.host}. Is `ollama serve` running?"
            ) from e

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
        }
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body.get("message", {}).get("content", "")
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Could not reach Ollama at {self.host}. Is `ollama serve` running and is "
                f"'{self.model_name}' pulled? (`ollama pull {self.model_name}`)"
            ) from e