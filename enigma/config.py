"""Runtime configuration for ENIGMA.

Reads provider selection from environment variables so the same code path
works for Ollama (default, local/offline), Gemini, or OpenAI.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    model_name: str
    ollama_host: str
    max_retries: int
    sandbox_backend: str  # "local" or "docker"
    sandbox_timeout_seconds: int

    @classmethod
    def load(cls) -> "Settings":
        provider = os.environ.get("LLM_PROVIDER", "ollama").strip().lower()

        default_models = {
            "ollama": "qwen2.5-coder:7b",
            "gemini": "gemini-1.5-flash",
            "openai": "gpt-4o-mini",
            "mock": "mock-deterministic",
        }
        model_name = os.environ.get("MODEL_NAME", default_models.get(provider, "qwen2.5-coder:7b"))

        return cls(
            llm_provider=provider,
            model_name=model_name,
            ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            max_retries=int(os.environ.get("ENIGMA_MAX_RETRIES", "3")),
            sandbox_backend=os.environ.get("SANDBOX_BACKEND", "local"),
            sandbox_timeout_seconds=int(os.environ.get("SANDBOX_TIMEOUT_SECONDS", "15")),
        )


SETTINGS = Settings.load()
