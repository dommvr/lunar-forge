import logging
import sys
from types import ModuleType, SimpleNamespace

import pytest

from lunar_forge.agent import CodeAgent
from lunar_forge.config import AppConfig, ModelConfig
from lunar_forge.model_clients import (
    LiteLLMClient,
    LiteLLMResponsesClient,
    create_litellm_client,
)


def _fake_litellm(monkeypatch, *, completion=None, responses=None):
    module = ModuleType("litellm")
    if completion is not None:
        module.completion = completion
    if responses is not None:
        module.responses = responses
    monkeypatch.setitem(sys.modules, "litellm", module)
    return module


def _plain_response(text="Done."):
    return {
        "id": "resp_text",
        "model": "gpt-test",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }


def test_chat_client_still_calls_litellm_completion(monkeypatch):
    captured = {}

    def completion(**request):
        captured.update(request)
        return {
            "model": "gpt-chat",
            "choices": [{"message": {"content": "Chat response"}}],
        }

    def unexpected_responses(**request):
        raise AssertionError("chat mode must not call litellm.responses")

    _fake_litellm(
        monkeypatch,
        completion=completion,
        responses=unexpected_responses,
    )
    client = LiteLLMClient("openai/gpt-4.1")

    result = client.complete([{"role": "user", "content": "Hello"}])

    assert result.text == "Chat response"
    assert captured["model"] == "openai/gpt-4.1"
    assert captured["messages"] == [{"role": "user", "content": "Hello"}]


def test_responses_client_calls_responses_and_flattens_tools(monkeypatch):
    captured = {}

    def unexpected_completion(**request):
        raise AssertionError("responses mode must not call litellm.completion")

    def responses(**request):
        captured.update(request)
        return _plain_response()

    _fake_litellm(
        monkeypatch,
        completion=unexpected_completion,
        responses=responses,
    )
    client = LiteLLMResponsesClient("openai/gpt-5.6-terra")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a project file.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }
    ]

    result = client.complete(
        [{"role": "user", "content": "Inspect README.md"}],
        tools,
    )

    assert result.content == "Done."
    assert captured["model"] == "openai/gpt-5.6-terra"
    assert captured["input"] == [
        {"role": "user", "content": "Inspect README.md"}
    ]
    assert captured["tools"] == [
        {
            "type": "function",
            "name": "read_file",
            "description": "Read a project file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    ]


def test_responses_normalizes_mixed_text_and_tool_calls(monkeypatch):
    response = SimpleNamespace(
        id="resp_mixed",
        model="gpt-5.6-terra",
        output=[
            SimpleNamespace(type="reasoning", id="rs_1", summary=[]),
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "I will inspect it."}
                ],
            },
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "read_file",
                "arguments": '{"path":"README.md"}',
            },
        ],
    )
    _fake_litellm(monkeypatch, responses=lambda **request: response)

    result = LiteLLMResponsesClient("openai/gpt-5.6-terra").complete(
        [{"role": "user", "content": "Inspect the README"}]
    )

    assert result.text == "I will inspect it."
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "README.md"}


def test_responses_uses_top_level_output_text_fallback(monkeypatch):
    _fake_litellm(
        monkeypatch,
        responses=lambda **request: {
            "model": "gpt-5.6-terra",
            "output": [],
            "output_text": "Plain response text",
        },
    )

    result = LiteLLMResponsesClient("openai/gpt-5.6-terra").complete(
        [{"role": "user", "content": "Hello"}]
    )

    assert result.content == "Plain response text"


def test_responses_replays_output_items_and_tool_results(monkeypatch):
    requests = []
    responses_to_return = [
        {
            "id": "resp_tool",
            "model": "gpt-5.6-terra",
            "output": [
                {"type": "reasoning", "id": "rs_1", "summary": []},
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "read_file",
                    "arguments": '{"path":"README.md"}',
                },
            ],
        },
        _plain_response("The README is clear."),
    ]

    def responses(**request):
        requests.append(request)
        return responses_to_return.pop(0)

    _fake_litellm(monkeypatch, responses=responses)
    client = LiteLLMResponsesClient("openai/gpt-5.6-terra")
    messages = [
        {"role": "system", "content": "Use tools."},
        {"role": "user", "content": "Inspect README.md"},
    ]
    first = client.complete(messages)
    messages.extend(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": first.tool_calls[0].id,
                        "type": "function",
                        "function": {
                            "name": first.tool_calls[0].name,
                            "arguments": '{"path":"README.md"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "read_file",
                "content": '{"ok":true,"content":"README"}',
            },
        ]
    )

    final = client.complete(messages)

    assert final.text == "The README is clear."
    assert requests[1]["input"] == [
        {"role": "system", "content": "Use tools."},
        {"role": "user", "content": "Inspect README.md"},
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "read_file",
            "arguments": '{"path":"README.md"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"ok":true,"content":"README"}',
        },
    ]


def test_missing_litellm_responses_support_is_clear(monkeypatch):
    _fake_litellm(monkeypatch, completion=lambda **request: None)

    with pytest.raises(RuntimeError, match="does not provide Responses API support"):
        LiteLLMResponsesClient("openai/gpt-5.6-terra").complete(
            [{"role": "user", "content": "Hello"}]
        )


def test_responses_api_key_comes_from_environment_without_logging(
    monkeypatch,
    caplog,
):
    secret = "sk-responses-test-secret-123456789"
    captured = {}

    def responses(**request):
        captured.update(request)
        return _plain_response()

    monkeypatch.setenv("LUNAR_FORGE_TEST_API_KEY", secret)
    _fake_litellm(monkeypatch, responses=responses)
    client = LiteLLMResponsesClient(
        "openai/gpt-5.6-terra",
        api_key_env="LUNAR_FORGE_TEST_API_KEY",
    )

    with caplog.at_level(logging.DEBUG):
        client.complete([{"role": "user", "content": "Hello"}])

    assert captured["api_key"] == secret
    assert secret not in caplog.text
    assert secret not in repr(client.__dict__)


def test_litellm_client_factory_selects_api_mode():
    assert isinstance(
        create_litellm_client(api="chat", model="openai/gpt-4.1"),
        LiteLLMClient,
    )
    assert isinstance(
        create_litellm_client(
            api="responses",
            model="openai/gpt-5.6-terra",
        ),
        LiteLLMResponsesClient,
    )


def test_agent_uses_responses_path_from_config(monkeypatch, tmp_path):
    captured = {}

    def unexpected_completion(**request):
        raise AssertionError("responses mode must not call litellm.completion")

    def responses(**request):
        captured.update(request)
        return _plain_response("Repository explained.")

    _fake_litellm(
        monkeypatch,
        completion=unexpected_completion,
        responses=responses,
    )
    config = AppConfig(
        model=ModelConfig(
            model="openai/gpt-5.6-terra",
            api_key_env=None,
            api="responses",
        )
    )

    result = CodeAgent(config).run(
        "Explain this repository structure",
        tmp_path,
        mode="plan",
    )

    assert result == "Repository explained.\n\nSession log: disabled in plan mode"
    assert captured["model"] == "openai/gpt-5.6-terra"
    assert captured["input"][0]["role"] == "system"
    assert captured["input"][1]["role"] == "user"
