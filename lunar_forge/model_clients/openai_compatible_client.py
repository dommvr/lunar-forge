"""OpenAI-compatible model client placeholder."""

from __future__ import annotations

from lunar_forge.model_clients.base import ModelResponse


class OpenAICompatibleClient:
    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url
        self.model = model

    def complete(self, prompt: str) -> ModelResponse:
        raise NotImplementedError("OpenAI-compatible API integration is not implemented yet.")
