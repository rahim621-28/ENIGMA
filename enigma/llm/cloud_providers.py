"""Thin wrappers for cloud LLM providers. Both require the corresponding
SDK to be installed and an API key present in the environment; imports are
deferred so the package works fine offline if you never select these.
"""
from __future__ import annotations

import os

from enigma.llm.base import BaseLLMProvider


class GeminiProvider(BaseLLMProvider):
    def __init__(self, model_name: str):
        self.model_name = model_name
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set.")
        try:
            import google.generativeai as genai
        except ImportError as e:
            raise RuntimeError(
                "google-generativeai is not installed. `pip install google-generativeai`"
            ) from e
        genai.configure(api_key=api_key)
        self._client = genai.GenerativeModel(model_name)

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = self._client.generate_content(f"{system_prompt}\n\n{user_prompt}")
        return response.text


class OpenAIProvider(BaseLLMProvider):
    def __init__(self, model_name: str):
        self.model_name = model_name
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError("openai package is not installed. `pip install openai`") from e
        self._client = OpenAI(api_key=api_key)

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or ""
