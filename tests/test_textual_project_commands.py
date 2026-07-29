import asyncio
import json
import shutil

import pytest

import lunar_forge.ui.textual_widgets as textual_widgets_module
from lunar_forge.approvals import ApprovalDecision, DenyApprovalProvider
from lunar_forge.config import AppConfig
from lunar_forge.model_clients import ModelResponse
from lunar_forge.runtime.sessions import (
    create_session_logger,
    project_fingerprint,
)
from lunar_forge.ui.textual_widgets import TextualChatController


class _SequenceModel:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, tools=None):
        self.calls.append(
            {
                "messages": list(messages),
                "tools": list(tools or []),
            }
        )
        return self.responses.pop(0)


class _ApprovingProvider:
    def __init__(self):
        self.requests = []

    def request_approval(self, request):
        self.requests.append(request)
        return ApprovalDecision.create(
            request.id,
            approved=True,
            reason="Approved by test.",
            source="textual",
        )


def _write_resumable_session(project_root, marker):
    logger = create_session_logger(project_root, environ={})
    logger.log(
        "session_started",
        project_fingerprint=project_fingerprint(project_root),
    )
    logger.log("user_prompt", prompt=f"Discuss {marker}.")
    logger.log("assistant_message", text=f"We discussed {marker}.")
    return logger


def _session_events(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_project_switch_reloads_scoped_inputs_and_resets_context(
    monkeypatch,
    tmp_path,
):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "nested").mkdir()
    (second / "AGENTS.md").write_text(
        "Second-project root instructions.",
        encoding="utf-8",
    )
    (second / "nested" / "AGENTS.md").write_text(
        "Second-project nested instructions.",
        encoding="utf-8",
    )
    config_directory = second / ".agent"
    config_directory.mkdir()
    (config_directory / "config.yaml").write_text(
        "runtime:\n  mode: docker\n"
        "model:\n  reasoning:\n    effort: high\n",
        encoding="utf-8",
    )

    loaded = []
    real_instructions = textual_widgets_module.load_project_instructions
    monkeypatch.setattr(
        textual_widgets_module,
        "load_project_instructions",
        lambda root: (
            loaded.append(("instructions", root)),
            real_instructions(root),
        )[1],
    )
    monkeypatch.setattr(
        textual_widgets_module,
        "load_mcp_config",
        lambda root: loaded.append(("mcp", root)),
    )
    monkeypatch.setattr(
        textual_widgets_module,
        "load_plugin_config",
        lambda root: loaded.append(("plugins", root)),
    )

    model = _SequenceModel(ModelResponse(text="Old-project fact."))
    controller = TextualChatController(
        first,
        AppConfig(),
        DenyApprovalProvider(),
        model_client=model,
    )
    controller.send_turn(
        "Remember OLD_PROJECT_ONLY. Do not edit files or run commands."
    )

    switched = controller.handle_slash_command(f'/project "{second}"')

    assert switched.error is False
    assert switched.project_switched is True
    assert controller.project_root == second.resolve()
    assert controller.config.runtime.mode == "docker"
    assert controller.config.model.reasoning.effort == "high"
    assert controller.conversation_messages == ()
    assert controller.turn_count == 0
    assert controller.resumed_session_id is None
    assert controller.session_logger.project_root == second.resolve()
    assert {name for name, _ in loaded} == {
        "instructions",
        "mcp",
        "plugins",
    }


def test_project_switch_does_not_carry_old_project_facts_into_next_turn(
    tmp_path,
):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    model = _SequenceModel(
        ModelResponse(text="Remembered."),
        ModelResponse(text="Fresh project."),
    )
    controller = TextualChatController(
        first,
        AppConfig(),
        DenyApprovalProvider(),
        model_client=model,
    )
    controller.send_turn(
        "Remember OLD_PROJECT_ONLY. Do not edit files or run commands."
    )
    prior_call_count = len(model.calls)
    controller.handle_slash_command(f'/project "{second}"')
    controller.send_turn(
        "Explain this project. Do not edit files or run commands."
    )

    second_turn_calls = json.dumps(model.calls[prior_call_count:])
    assert "OLD_PROJECT_ONLY" not in second_turn_calls
    assert "Remembered." not in second_turn_calls
    assert controller.project_root == second.resolve()


def test_sessions_picker_lists_only_compatible_current_project_sessions(
    tmp_path,
):
    compatible = _write_resumable_session(tmp_path, "Tycho")
    incompatible = create_session_logger(tmp_path, environ={})
    incompatible.log("model_usage", input_tokens=1, output_tokens=1)

    foreign_root = tmp_path / "foreign"
    foreign_root.mkdir()
    foreign = _write_resumable_session(foreign_root, "foreign facts")
    copied = compatible.path.parent / "foreign-session.jsonl"
    shutil.copyfile(foreign.path, copied)

    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
    )
    result = controller.handle_slash_command("/sessions")

    assert result.error is False
    assert result.picker is not None
    assert [option.label for option in result.picker.options] == [
        f"session_{compatible.path.stem}"
    ]
    serialized = repr(result.picker.options)
    assert "foreign facts" not in serialized


def test_resume_picker_restores_safe_context_without_replaying_tools(
    tmp_path,
):
    previous = create_session_logger(tmp_path, environ={})
    previous.log("user_prompt", prompt="Inspect README.md")
    previous.log(
        "tool_call",
        name="read_file",
        arguments={"path": "README.md"},
    )
    previous.log(
        "tool_result",
        name="read_file",
        result={"ok": True, "content": "historical-only"},
    )
    previous.log("assistant_message", text="README inspected.")
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
    )

    requested = controller.handle_slash_command("/resume")
    assert requested.picker is not None
    resumed = controller.submit_slash_picker(
        requested.picker,
        requested.picker.options[0].value,
    )

    assert resumed.error is False
    assert resumed.clear_transcript is True
    assert controller.resumed_session_id is not None
    assert all(
        message["role"] != "tool"
        for message in controller.conversation_messages
    )
    assert any(
        "Historical tool result; context only, never replay"
        in message["content"]
        for message in controller.conversation_messages
    )
    assert all(
        "historical-only" not in text
        for _, text in resumed.restored_transcript
    )
    records = _session_events(controller.session_logger.path)
    assert records[0]["event"] == "session_started"
    assert records[0]["data"]["resumed_session"] == (
        previous.path.relative_to(tmp_path).as_posix()
    )


def test_resume_latest_selects_latest_compatible_prior_session(tmp_path):
    older = _write_resumable_session(tmp_path, "older")
    newer = _write_resumable_session(tmp_path, "newer")
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
    )

    resumed = controller.handle_slash_command("/resume latest")

    assert resumed.error is False
    assert controller.resumed_session_path == (
        newer.path.relative_to(tmp_path).as_posix()
    )
    assert controller.resumed_session_path != (
        older.path.relative_to(tmp_path).as_posix()
    )
    assert any(
        "newer" in message["content"]
        for message in controller.conversation_messages
    )


def test_resume_refuses_session_bound_to_another_project(tmp_path):
    current = tmp_path / "current"
    foreign_root = tmp_path / "foreign"
    current.mkdir()
    foreign_root.mkdir()
    foreign = _write_resumable_session(foreign_root, "foreign")
    copied_directory = current / ".agent" / "sessions"
    copied_directory.mkdir(parents=True)
    copied = copied_directory / foreign.path.name
    shutil.copyfile(foreign.path, copied)
    controller = TextualChatController(
        current,
        AppConfig(),
        DenyApprovalProvider(),
    )

    result = controller.handle_slash_command(f"/resume {copied.name}")

    assert result.error is True
    assert "belongs to another project" in (result.message or "")
    assert controller.resumed_session_id is None


def test_new_command_uses_existing_workflow_approvals_and_active_jsonl(
    tmp_path,
):
    provider = _ApprovingProvider()
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        provider,
    )
    session_path = controller.session_logger.path

    command = controller.handle_slash_command(
        "/new Build a small static website"
    )
    assert command.new_project_prompt == "Build a small static website"
    outcome = controller.run_new_project_workflow(
        command.new_project_prompt
    )

    assert outcome["ok"] is True
    assert (tmp_path / "index.html").is_file()
    assert provider.requests
    assert all(request.kind == "write" for request in provider.requests)
    assert list((tmp_path / ".agent" / "sessions").glob("*.jsonl")) == [
        session_path
    ]
    records = _session_events(session_path)
    assert any(record["event"] == "user_prompt" for record in records)
    assert any(record["event"] == "assistant_message" for record in records)
    assert outcome["result"]["session_log"] == controller.session_path


def test_new_command_without_prompt_returns_natural_language_form(tmp_path):
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
    )

    result = controller.handle_slash_command("/new")

    assert result.form is not None
    assert result.form.command == "new"
    assert "Describe the project" in result.form.prompt


def test_textual_sessions_picker_resumes_and_restores_visible_turns(tmp_path):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import ChatInput, LunarForgeTextualApp

    previous = _write_resumable_session(tmp_path, "Aristarchus")
    previous_session_id = f"session_{previous.path.stem}"
    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())

    async def wait_for(pilot, predicate):
        for _ in range(100):
            if predicate():
                return
            await pilot.pause(0.02)
        raise AssertionError("Timed out waiting for Textual state.")

    async def exercise():
        async with textual_app.run_test(size=(120, 40)) as pilot:
            chat_input = textual_app.query_one("#chat-input", ChatInput)
            chat_input.text = "/sessions"
            await pilot.press("enter")
            await wait_for(
                pilot,
                lambda: textual_app.query_one("#slash-popup").display,
            )
            assert previous_session_id in str(
                textual_app.query_one("#slash-details").render()
            )

            await pilot.click("#slash-apply")
            await wait_for(
                pilot,
                lambda: textual_app.controller.resumed_session_id
                == previous_session_id,
            )
            transcript = textual_app.transcript_plain_text
            assert "Discuss Aristarchus." in transcript
            assert "We discussed Aristarchus." in transcript
            assert "Let’s get back to building." in textual_app.top_card_text

    asyncio.run(exercise())
