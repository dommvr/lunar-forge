import json
import re
import sys
from types import ModuleType, SimpleNamespace

from lunar_forge.agent import CodeAgent
from lunar_forge.config import AppConfig
from lunar_forge.model_clients import (
    LiteLLMClient,
    LiteLLMResponsesClient,
    ModelResponse,
    ModelUsage,
)
from lunar_forge.runtime.sessions import load_session, summarize_session


class SingleResponseModel:
    def __init__(self, response):
        self.response = response

    def complete(self, messages, tools=None):
        return self.response


def _fake_litellm(monkeypatch, *, completion=None, responses=None):
    module = ModuleType("litellm")
    if completion is not None:
        module.completion = completion
    if responses is not None:
        module.responses = responses
    monkeypatch.setitem(sys.modules, "litellm", module)


def _session_file(project_root):
    files = list((project_root / ".agent" / "sessions").glob("*.jsonl"))
    assert len(files) == 1
    return files[0]


def _events(session_file):
    return [
        json.loads(line)
        for line in session_file.read_text(encoding="utf-8").splitlines()
    ]


def test_chat_client_normalizes_provider_reported_usage(monkeypatch):
    def completion(**request):
        return {
            "model": "gpt-5.5",
            "choices": [{"message": {"content": "Done."}}],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 15,
                "total_tokens": 135,
            },
            "_hidden_params": {"custom_llm_provider": "openai"},
        }

    _fake_litellm(monkeypatch, completion=completion)

    response = LiteLLMClient("openai/gpt-5.5").complete(
        [{"role": "user", "content": "Hello"}]
    )

    assert response.text == "Done."
    assert response.usage == ModelUsage(
        input_tokens=120,
        output_tokens=15,
        total_tokens=135,
        model="gpt-5.5",
        provider="openai",
        exact=True,
    )


def test_responses_client_normalizes_provider_reported_usage(monkeypatch):
    response = SimpleNamespace(
        id="resp_usage",
        model="gpt-5.6-terra",
        output=[
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Done."}],
            }
        ],
        usage=SimpleNamespace(
            input_tokens=80,
            output_tokens=20,
            total_tokens=100,
        ),
        _hidden_params={"custom_llm_provider": "openai"},
    )
    _fake_litellm(monkeypatch, responses=lambda **request: response)

    result = LiteLLMResponsesClient("openai/gpt-5.6-terra").complete(
        [{"role": "user", "content": "Hello"}]
    )

    assert result.usage == ModelUsage(
        input_tokens=80,
        output_tokens=20,
        total_tokens=100,
        model="gpt-5.6-terra",
        provider="openai",
        exact=True,
    )


def test_agent_logs_and_aggregates_exact_usage_without_secrets(
    monkeypatch,
    tmp_path,
):
    secret = "telemetry-environment-secret-123"
    monkeypatch.setenv("TELEMETRY_SECRET", secret)
    model = SingleResponseModel(
        ModelResponse(
            text="Inspection complete.",
            model="gpt-test",
            usage=ModelUsage(
                input_tokens=200,
                output_tokens=25,
                total_tokens=225,
                model="gpt-test",
                provider="openai",
                exact=True,
            ),
        )
    )

    output = CodeAgent(AppConfig(), model_client=model).run(
        f"Inspect the project without exposing {secret}.",
        tmp_path,
        show_usage=True,
    )

    session_file = _session_file(tmp_path)
    raw_log = session_file.read_text(encoding="utf-8")
    events = _events(session_file)
    usage_event = next(
        event for event in events if event["event"] == "model_usage"
    )
    usage = usage_event["data"]

    assert secret not in raw_log
    assert usage["input_tokens"] == 200
    assert usage["output_tokens"] == 25
    assert usage["total_tokens"] == 225
    assert usage["provider"] == "openai"
    assert usage["task_profile"] == "review_only"
    assert usage["phase"] == "agent"
    assert usage["role"] == "agent"
    assert usage["exact"] is True
    assert usage["estimated"] is False
    assert usage["usage_source"] == "provider"
    assert usage["messages_count"] == 2
    assert usage["tool_schema_count"] > 0
    assert usage["context_estimate_method"] == (
        "characters_divided_by_4_rounded_up"
    )
    assert (
        usage["context_components"][
            "system_project_instructions_token_estimate"
        ]
        > 0
    )

    loaded = load_session(tmp_path, session_file.name, environ={})
    assert summarize_session(loaded)["model_usage"] == {
        "model_calls": 1,
        "exact_calls": 1,
        "estimated_calls": 0,
        "input_tokens": 200,
        "output_tokens": 25,
        "total_tokens": 225,
    }
    assert "Model usage:" in output
    assert "- Total tokens: 225" in output


def test_agent_estimates_usage_when_provider_metadata_is_missing(tmp_path):
    model = SingleResponseModel(
        ModelResponse(
            text="Estimated response.",
            model="openai/gpt-test",
        )
    )

    output = CodeAgent(AppConfig(), model_client=model).run(
        "Explain the project.",
        tmp_path,
    )

    usage = next(
        event["data"]
        for event in _events(_session_file(tmp_path))
        if event["event"] == "model_usage"
    )
    assert usage["exact"] is False
    assert usage["estimated"] is True
    assert usage["usage_source"] == "estimate"
    assert usage["provider"] == "openai"
    assert usage["input_tokens"] > 0
    assert usage["output_tokens"] > 0
    assert usage["total_tokens"] == (
        usage["input_tokens"] + usage["output_tokens"]
    )
    assert "Model usage:" not in output


def test_plan_show_usage_uses_in_memory_estimates_without_runtime_files(tmp_path):
    class PlanModel:
        def __init__(self):
            self.calls = []

        def complete(self, messages, tools=None):
            self.calls.append(list(tools or []))
            return ModelResponse(
                text="Read-only implementation plan.",
                model="openai/gpt-test",
            )

    model = PlanModel()

    output = CodeAgent(AppConfig(), model_client=model).run(
        "Plan a parser cleanup.",
        tmp_path,
        mode="plan",
        show_usage=True,
    )

    assert "Model usage:" in output
    assert "- Calls: 1 (0 exact, 1 estimated)" in output
    assert re.search(r"- Input tokens: [1-9]\d*", output)
    assert re.search(r"- Output tokens: [1-9]\d*", output)
    assert re.search(r"- Total tokens: [1-9]\d*", output)
    assert "unavailable in plan mode" not in output
    assert "Session log: disabled in plan mode" in output
    assert not (tmp_path / ".agent").exists()

    exposed_names = {
        schema["function"]["name"]
        for schema in model.calls[0]
    }
    assert {
        "create_dir",
        "write_file",
        "edit_file",
        "replace_lines",
        "insert_lines",
        "run_command",
        "run_validation",
        "run_browser_validation",
        "run_managed_browser_validation",
        "git_commit",
    }.isdisjoint(exposed_names)


def test_plan_show_usage_preserves_exact_provider_totals(tmp_path):
    model = SingleResponseModel(
        ModelResponse(
            text="Exact-usage plan.",
            model="gpt-test",
            usage=ModelUsage(
                input_tokens=90,
                output_tokens=10,
                total_tokens=100,
                model="gpt-test",
                provider="openai",
                exact=True,
            ),
        )
    )

    output = CodeAgent(AppConfig(), model_client=model).run(
        "Plan a documentation cleanup.",
        tmp_path,
        mode="plan",
        show_usage=True,
    )

    assert "- Calls: 1 (1 exact, 0 estimated)" in output
    assert "- Input tokens: 90" in output
    assert "- Output tokens: 10" in output
    assert "- Total tokens: 100" in output
    assert not (tmp_path / ".agent").exists()
