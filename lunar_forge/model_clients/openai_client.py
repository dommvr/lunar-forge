"""OpenAI model client placeholder."""

from __future__ import annotations

from lunar_forge.model_clients.base import ModelResponse


class OpenAIClient:
    def __init__(self, model: str = "gpt-5") -> None:
        self.model = model

    def complete(self, prompt: str) -> ModelResponse:
        raise NotImplementedError("OpenAI API integration is not implemented yet.")
