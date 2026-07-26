import json

import pytest

from lunar_forge.agent import CodeAgent
from lunar_forge.config import (
    AppConfig,
    MCPRuntimeConfig,
    PluginRuntimeConfig,
    SubagentConfig,
)
from lunar_forge.model_clients import ModelResponse, ToolCall
from lunar_forge.tools.registry import (
    Tool,
    ToolRegistry,
    parse_explicit_readonly_tool_request,
)


READONLY_CASES = (
    (
        "Run project_health and summarize health findings.",
        "project_health",
        {},
    ),
    (
        "Run dependency_summary and summarize dependencies.",
        "dependency_summary",
        {},
    ),
    (
        "Run git_status and tell me whether the repo is clean.",
        "git_status",
        {},
    ),
    (
        "Run git_diff and summarize current changes.",
        "git_diff",
        {},
    ),
    (
        "Run list_changed_files and group results by source.",
        "list_changed_files",
        {},
    ),
    (
        "Run read_json on package.json and summarize scripts.",
        "read_json",
        {"path": "package.json"},
    ),
    (
        "Run read_yaml on .agent/mcp.yaml.",
        "read_yaml",
        {"path": ".agent/mcp.yaml"},
    ),
    (
        "Run read_many_files on README.md and pyproject.toml.",
        "read_many_files",
        {"paths": ["README.md", "pyproject.toml"]},
    ),
    (
        "Run list_symbols on lunar_forge/cli.py.",
        "list_symbols",
        {"path": "lunar_forge/cli.py"},
    ),
    (
        "Run ci_summary and report CI commands.",
        "ci_summary",
        {},
    ),
)
READONLY_TOOL_NAMES = tuple(case[1] for case in READONLY_CASES)


class RecordingModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, tools=None):
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": list(tools or []),
            }
        )
        return self.responses.pop(0)


def _recording_registry(executions):
    def tool(name):
        def handler(**arguments):
            executions.append((name, arguments))
            return {
                "ok": True,
                "tool": name,
                "arguments": arguments,
                "summary": f"{name} completed",
            }

        return Tool(
            name=name,
            description=f"Test tool {name}.",
            parameters={"type": "object", "properties": {}},
            handler=handler,
        )

    names = (
        *READONLY_TOOL_NAMES,
        "run_validation",
        "run_browser_validation",
        "write_file",
        "git_commit",
    )
    return ToolRegistry(tool(name) for name in names)


def _events(project_root):
    session_file = next(
        (project_root / ".agent" / "sessions").glob("*.jsonl")
    )
    return [
        json.loads(line)
        for line in session_file.read_text(encoding="utf-8").splitlines()
    ]


@pytest.mark.parametrize(
    ("prompt", "tool_name", "expected_arguments"),
    READONLY_CASES,
)
def test_explicit_readonly_tools_use_one_deterministic_call_and_one_summary(
    tmp_path,
    prompt,
    tool_name,
    expected_arguments,
):
    executions = []
    registry = _recording_registry(executions)
    model = RecordingModel((ModelResponse(text="Focused summary."),))
    config = AppConfig(subagents=SubagentConfig(enabled=True))

    output = CodeAgent(config, model_client=model).run(
        prompt,
        tmp_path,
        registry=registry,
    )

    assert executions == [(tool_name, expected_arguments)]
    assert len(model.calls) == 1
    assert model.calls[0]["tools"] == []
    assert "read-only result summarizer" in (
        model.calls[0]["messages"][0]["content"]
    )
    assert output.startswith("Focused summary.")
    assert "Subagents run:" not in output
    assert "Skipped subagents" not in output
    assert not (tmp_path / ".agent" / "checkpoints").exists()

    events = _events(tmp_path)
    route = next(
        event for event in events if event["event"] == "readonly_fast_path"
    )
    selection = next(
        event
        for event in events
        if event["event"] == "tool_schema_selection"
    )
    usage = next(
        event for event in events if event["event"] == "model_usage"
    )
    assert route["data"]["tool_name"] == tool_name
    assert selection["data"]["exposed_tool_count"] == 0
    assert selection["data"]["phase"] == "readonly_fast_path"
    assert usage["data"]["tool_schema_count"] == 0
    assert usage["data"]["messages_count"] == 2
    assert usage["data"]["phase"] == "readonly_fast_path"
    assert all(
        not event["event"].startswith("subagent_")
        for event in events
    )


def test_readonly_fast_path_does_not_run_validation_or_browser_tools(tmp_path):
    executions = []
    model = RecordingModel((ModelResponse(text="Scripts summarized."),))

    output = CodeAgent(AppConfig(), model_client=model).run(
        (
            "Run read_json on "
            "examples/projects/browser-demo/package.json and summarize scripts."
        ),
        tmp_path,
        registry=_recording_registry(executions),
    )

    assert executions == [
        (
            "read_json",
            {"path": "examples/projects/browser-demo/package.json"},
        )
    ]
    assert "Browser validation:" not in output
    events = _events(tmp_path)
    assert not any(
        event["event"] == "browser_intent_detected" for event in events
    )


def test_readonly_fast_path_does_not_initialize_mcp_or_plugins(tmp_path):
    executions = []
    model = RecordingModel((ModelResponse(text="Scripts summarized."),))

    def unexpected_transport(server):
        raise AssertionError(f"MCP transport initialized for {server.name}")

    config = AppConfig(
        subagents=SubagentConfig(enabled=True),
        mcp=MCPRuntimeConfig(enabled=True),
        plugins=PluginRuntimeConfig(enabled=True),
    )
    CodeAgent(
        config,
        model_client=model,
        mcp_transport_factory=unexpected_transport,
    ).run(
        "Run read_json on package.json and summarize scripts.",
        tmp_path,
        registry=_recording_registry(executions),
    )

    assert executions == [("read_json", {"path": "package.json"})]
    events = _events(tmp_path)
    assert not any(
        event["event"] in {
            "mcp_tools_registered",
            "plugin_tools_registered",
        }
        for event in events
    )


def test_explicit_validation_intent_falls_back_and_can_run_validation(tmp_path):
    executions = []
    model = RecordingModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="validate",
                        name="run_validation",
                        arguments={},
                    ),
                ),
            ),
            ModelResponse(text="Validation request handled."),
        )
    )

    CodeAgent(AppConfig(), model_client=model).run(
        "Run read_json on package.json, then run validation.",
        tmp_path,
        registry=_recording_registry(executions),
    )

    assert executions == [("run_validation", {})]
    assert len(model.calls) == 2
    assert any(
        schema["function"]["name"] == "run_validation"
        for schema in model.calls[0]["tools"]
    )
    assert not any(
        event["event"] == "readonly_fast_path"
        for event in _events(tmp_path)
    )


@pytest.mark.parametrize(
    "prompt",
    (
        "Run read_json on package.json and update README.md.",
        "Run read_json on package.json and run tests.",
        "Run read_json on package.json and open it in the browser.",
        "Run read_json and summarize it.",
        "Run read_json on package.json and read_yaml on config.yaml.",
        "Could you inspect package.json?",
    ),
)
def test_ambiguous_or_expansive_requests_do_not_parse_as_fast_path(prompt):
    assert parse_explicit_readonly_tool_request(prompt) is None


def test_readonly_fast_path_preserves_project_root_path_safety(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text('{"canary": "must-not-leak"}', encoding="utf-8")
    model = RecordingModel((ModelResponse(text="Path access was refused."),))

    CodeAgent(AppConfig(), model_client=model).run(
        f"Run read_json on ../{outside.name} and summarize it.",
        tmp_path,
    )

    summary_prompt = model.calls[0]["messages"][1]["content"]
    assert '"ok": false' in summary_prompt
    assert "outside the project root" in summary_prompt
    assert "must-not-leak" not in summary_prompt
