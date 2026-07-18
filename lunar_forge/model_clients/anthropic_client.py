"""Anthropic model client placeholder."""

from __future__ import annotations

from lunar_forge.model_clients.base import ModelResponse


class AnthropicClient:
    def __init__(self, model: str = "claude-sonnet-4") -> None:
        self.model = model

    def complete(self, prompt: str) -> ModelResponse:
        raise NotImplementedError("Anthropic API integration is not implemented yet.")
