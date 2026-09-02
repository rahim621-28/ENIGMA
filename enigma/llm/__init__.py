from __future__ import annotations

from enigma.config import Settings
from enigma.llm.base import BaseLLMProvider


def get_provider(settings: Settings) -> BaseLLMProvider:
    if settings.llm_provider == "ollama":
        from enigma.llm.ollama_provider import OllamaProvider

        return OllamaProvider(settings.model_name, settings.ollama_host)
    if settings.llm_provider == "gemini":
        from enigma.llm.cloud_providers import GeminiProvider

        return GeminiProvider(settings.model_name)
    if settings.llm_provider == "openai":
        from enigma.llm.cloud_providers import OpenAIProvider

        return OpenAIProvider(settings.model_name)
    if settings.llm_provider == "mock":
        from enigma.llm.mock_provider import MockProvider

        return MockProvider()
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")
