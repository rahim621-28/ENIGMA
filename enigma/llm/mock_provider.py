"""Deterministic mock provider.

Exists so the eval harness and tests can run in CI without a real model
(no Ollama daemon, no API key). It pattern-matches known scenario shapes
and returns canned-but-structured responses. This is explicitly NOT meant
to demonstrate LLM reasoning quality -- it demonstrates that the graph,
sandbox, and eval plumbing are correct end-to-end. Real reasoning quality
numbers should come from a run with LLM_PROVIDER=ollama (or gemini/openai).
"""
from __future__ import annotations

import json

from enigma.llm.base import BaseLLMProvider


class MockProvider(BaseLLMProvider):
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if "Return ONLY JSON" in system_prompt and "suspect_symbol" in user_prompt:
            return self._mock_hypothesis(user_prompt)
        if "unified diff" in system_prompt.lower() or "patch" in system_prompt.lower():
            return self._mock_patch(user_prompt)
        return "{}"

    def _mock_hypothesis(self, user_prompt: str) -> str:
        # crude but sufficient: pick the first symbol mentioned in the prompt
        import re
        match = re.search(r"Symbol: (\S+)", user_prompt)
        symbol = match.group(1) if match else "unknown"
        return json.dumps(
            {
                "suspect_symbol": symbol,
                "reasoning": "Mock provider: flagged the symbol enclosing the failing line.",
                "confidence": 0.6,
            }
        )

    def _mock_patch(self, user_prompt: str) -> str:
        # The mock cannot synthesize a real fix; scenarios relying on the
        # mock for actual patch content should supply a fixture patch instead.
        return json.dumps({"patched_content": "", "explanation": "Mock provider cannot synthesize patches."})
