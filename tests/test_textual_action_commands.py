import asyncio
from datetime import datetime, timezone

import pytest

import lunar_forge.ui.textual_widgets as textual_widgets_module
from lunar_forge.approvals import ApprovalDecision, DenyApprovalProvider
from lunar_forge.config import AppConfig, PermissionConfig, RuntimeConfig
from lunar_forge.events import EventFactory, EventType
from lunar_forge.runtime.checkpoints import create_file_checkpoint
from lunar_forge.ui.slash_commands import SlashCommandRouter
from lunar_forge.ui.textual_state import ChatSessionState
from lunar_forge.ui.textual_widgets import TextualChatController


class _RecordingProvider:
    def __init__(self, approved=True):
        self.approved = approved
        self.requests = []

    def request_approval(self, request):
        self.requests.append(request)
        return ApprovalDecision.create(
            request.id,
            approved=self.approved,
            reason=(
                "Approved by test."
                if self.approved
                else "Denied by test."
            ),
            source="textual",
        )


def _router(tmp_path):
    state = ChatSessionState.create(tmp_path, AppConfig())
    return SlashCommandRouter(state)


def test_browser_validate_parses_typed_arguments(tmp_path):
    result = _router(tmp_path).route(
        "/browser-validate "
        "url=http://localhost:5173 "
        'serve="npm run dev" '
        "no-screenshot full-page "
        "width=1440 height=1200 startup-timeout-ms=45000 "
        "check=#app check=title"
    )

    assert result.error is False
    assert result.action is not None
    assert result.action.name == "browser-validate"
    assert result.action.arguments == {
        "url": "http://localhost:5173",
        "serve": "npm run dev",
        "screenshot": False,
        "full_page": True,
        "width": 1440,
        "height": 1200,
        "startup_timeout_ms": 45_000,
        "checks": ("#app", "title"),
    }


def test_browser_validate_without_arguments_opens_popup_form(tmp_path):
    result = _router(tmp_path).route("/browser-validate")

    assert result.error is False
    assert result.form is not None
    assert result.form.command == "browser-validate"
    assert result.form.parse_arguments is True
    for option in (
        "url",
        "serve",
        "screenshot",
        "no-screenshot",
        "full-page",
        "width",
        "height",
        "startup-timeout-ms",
        "check",
    ):
        assert option in result.form.prompt


def test_browser_validate_form_parses_raw_option_text(tmp_path):
    router = _router(tmp_path)
    requested = router.route("/browser-validate")

    result = router.submit_form(
        requested.form,
        "url=http://localhost:8000 screenshot=false width=1024",
    )

    assert result.action is not None
    assert result.action.arguments["url"] == "http://localhost:8000"
    assert result.action.arguments["screenshot"] is False
    assert result.action.arguments["width"] == 1024


def test_managed_browser_action_forwards_textual_approval_provider(
    monkeypatch,
    tmp_path,
):
    provider = _RecordingProvider()
    captured = {}

    def fake_managed(command, url, **kwargs):
        captured.update(command=command, url=url, **kwargs)
        return {"ok": False, "permission_denied": True, "error": "Denied."}

    monkeypatch.setattr(
        textual_widgets_module,
        "run_managed_browser_validation",
        fake_managed,
    )
    controller = TextualChatController(tmp_path, AppConfig(), provider)
    command = controller.handle_slash_command(
        '/browser-validate url=http://localhost:5173 serve="npm run dev"'
    )

    outcome = controller.run_slash_action(command.action)

    assert outcome["ok"] is False
    assert captured["command"] == "npm run dev"
    assert captured["approval_provider"] is provider
    assert captured["permission_mode"] == "default"
    assert captured["runtime_mode"] == "local"
    assert captured["project_root"] == tmp_path.resolve()
    assert "upload" not in str(captured).casefold()


@pytest.mark.parametrize(
    "config",
    (
        AppConfig(permissions=PermissionConfig(mode="plan")),
        AppConfig(permissions=PermissionConfig(mode="no-command")),
        AppConfig(runtime=RuntimeConfig(mode="no-command")),
    ),
)
def test_browser_action_preserves_plan_and_no_command_modes(
    monkeypatch,
    tmp_path,
    config,
):
    monkeypatch.setattr(
        textual_widgets_module,
        "run_browser_validation",
        lambda *args, **kwargs: pytest.fail(
            "Blocked browser action must not reach the workflow."
        ),
    )
    controller = TextualChatController(
        tmp_path,
        config,
        DenyApprovalProvider(),
    )
    command = controller.handle_slash_command(
        "/browser-validate url=http://localhost:5173 no-screenshot"
    )

    outcome = controller.run_slash_action(command.action)

    assert outcome["ok"] is False
    assert "blocks browser validation" in outcome["text"]


def test_browser_setup_routes_to_existing_permission_gated_workflow(
    monkeypatch,
    tmp_path,
):
    provider = _RecordingProvider()
    captured = {}

    def fake_setup(project_root, **kwargs):
        captured.update(project_root=project_root, **kwargs)
        return {"ok": False, "permission_denied": True, "error": "Denied."}

    monkeypatch.setattr(
        textual_widgets_module,
        "run_browser_setup",
        fake_setup,
    )
    controller = TextualChatController(tmp_path, AppConfig(), provider)
    command = controller.handle_slash_command("/browser-setup")

    outcome = controller.run_slash_action(command.action)

    assert outcome["ok"] is False
    assert captured["approval_provider"] is provider
    assert captured["permission_mode"] == "default"
    assert captured["runtime_mode"] == "local"


def test_git_status_uses_existing_helper_without_agent_turn(
    monkeypatch,
    tmp_path,
):
    captured = {}

    def fake_status(project_root, *, mode):
        captured.update(project_root=project_root, mode=mode)
        return {
            "ok": True,
            "repository_root": str(tmp_path),
            "status_short": [" M README.md"],
        }

    monkeypatch.setattr(textual_widgets_module, "git_status", fake_status)
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
    )
    command = controller.handle_slash_command("/git status")

    outcome = controller.run_slash_action(command.action)

    assert outcome["ok"] is True
    assert controller.turn_count == 0
    assert captured == {
        "project_root": tmp_path.resolve(),
        "mode": "default",
    }
    assert " M README.md" in outcome["text"]


@pytest.mark.parametrize(
    ("command", "expected_message", "expected_override"),
    (
        (
            '/git commit "Add pricing page"',
            "Add pricing page",
            False,
        ),
        (
            '/git commit message="Ship release" '
            "despite-failed-validation=true",
            "Ship release",
            True,
        ),
    ),
)
def test_git_commit_message_and_override_parsing(
    tmp_path,
    command,
    expected_message,
    expected_override,
):
    result = _router(tmp_path).route(command)

    assert result.action is not None
    assert result.action.arguments["message"] == expected_message
    assert (
        result.action.arguments["despite_failed_validation"]
        is expected_override
    )


def test_git_commit_blocks_failed_validation_before_approval(
    monkeypatch,
    tmp_path,
):
    provider = _RecordingProvider()
    controller = TextualChatController(tmp_path, AppConfig(), provider)
    controller._record_public_event(
        EventFactory().create(
            EventType.VALIDATION_FINISHED,
            {"ok": False, "error": "Tests failed."},
        )
    )
    monkeypatch.setattr(
        textual_widgets_module,
        "create_git_commit",
        lambda *args, **kwargs: pytest.fail(
            "Normal commit must stop before proposal and approval."
        ),
    )
    command = controller.handle_slash_command(
        '/git commit "Do not commit this"'
    )

    outcome = controller.run_slash_action(command.action)

    assert outcome["ok"] is False
    assert outcome["result"]["result_code"] == "validation_failed"
    assert outcome["result"]["approval_requested"] is False
    assert provider.requests == []
    assert outcome["text"].startswith(
        "Validation results before commit approval:\n- Failed"
    )


def test_git_commit_explicit_failed_validation_override_keeps_context_and_approval(
    monkeypatch,
    tmp_path,
):
    provider = _RecordingProvider(approved=False)
    captured = {}
    controller = TextualChatController(tmp_path, AppConfig(), provider)
    controller._record_public_event(
        EventFactory().create(
            EventType.VALIDATION_FINISHED,
            {"ok": False, "error": "Tests failed."},
        )
    )
    monkeypatch.setattr(
        textual_widgets_module,
        "list_changed_files",
        lambda *args, **kwargs: {
            "ok": True,
            "commit_candidates": ["README.md"],
        },
    )

    def fake_commit(project_root, message, **kwargs):
        captured.update(
            project_root=project_root,
            message=message,
            **kwargs,
        )
        return {
            "ok": False,
            "result_code": "approval_denied",
            "error": "Denied.",
        }

    monkeypatch.setattr(
        textual_widgets_module,
        "create_git_commit",
        fake_commit,
    )
    command = controller.handle_slash_command(
        '/git commit message="Commit anyway" '
        "despite-failed-validation=true"
    )

    outcome = controller.run_slash_action(command.action)

    assert outcome["ok"] is False
    assert captured["approval_provider"] is provider
    assert captured["session_files"] == ("README.md",)
    context = captured["approval_context"]
    assert context.startswith(
        "Validation results before commit approval:\n- Failed"
    )
    assert "explicitly requested a commit despite failed validation" in context


def test_checkpoints_action_lists_current_project_safely(tmp_path):
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("first", encoding="utf-8")
    checkpoint = create_file_checkpoint(
        tmp_path,
        source,
        created_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
    )
    command = controller.handle_slash_command("/checkpoints")

    outcome = controller.run_slash_action(command.action)

    assert outcome["ok"] is True
    assert checkpoint.parent.parent.name in outcome["text"]
    assert controller.turn_count == 0


def test_rollback_requires_approval_and_enforces_safe_path(tmp_path):
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("checkpoint", encoding="utf-8")
    checkpoint = create_file_checkpoint(
        tmp_path,
        source,
        created_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    source.write_text("current", encoding="utf-8")
    provider = _RecordingProvider(approved=False)
    controller = TextualChatController(tmp_path, AppConfig(), provider)
    command = controller.handle_slash_command(
        f"/rollback path=src/app.py checkpoint={checkpoint.parents[1].name}"
    )

    denied = controller.run_slash_action(command.action)

    assert denied["ok"] is False
    assert denied["result"]["permission_denied"] is True
    assert source.read_text(encoding="utf-8") == "current"
    assert len(provider.requests) == 1
    assert provider.requests[0].kind == "write"

    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    outside_command = controller.handle_slash_command(
        f'/rollback path="{outside}"'
    )
    with pytest.raises(PermissionError, match="outside the project root"):
        controller.run_slash_action(outside_command.action)
    assert len(provider.requests) == 1
    assert outside.read_text(encoding="utf-8") == "outside"


def test_rollback_can_restore_selected_checkpoint_after_approval(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("older", encoding="utf-8")
    selected = create_file_checkpoint(
        tmp_path,
        source,
        created_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    source.write_text("newer", encoding="utf-8")
    create_file_checkpoint(
        tmp_path,
        source,
        created_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    source.write_text("current", encoding="utf-8")
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        _RecordingProvider(approved=True),
    )
    command = controller.handle_slash_command(
        f"/rollback path=app.py checkpoint={selected.parent.name}"
    )

    outcome = controller.run_slash_action(command.action)

    assert outcome["ok"] is True
    assert source.read_text(encoding="utf-8") == "older"
    assert outcome["result"]["checkpoint_id"] == selected.parent.name


def test_mcp_and_plugin_list_use_existing_diagnostics_without_enabling(
    monkeypatch,
    tmp_path,
):
    calls = []

    def fake_mcp(project_root, *, globally_enabled):
        calls.append(("mcp", project_root, globally_enabled))
        return {"ok": True, "status": "disabled", "mcp_enabled": False}

    def fake_plugins(project_root, *, globally_enabled):
        calls.append(("plugins", project_root, globally_enabled))
        return {"ok": True, "status": "disabled", "plugins_enabled": False}

    monkeypatch.setattr(
        textual_widgets_module,
        "build_mcp_diagnostic",
        fake_mcp,
    )
    monkeypatch.setattr(
        textual_widgets_module,
        "build_plugin_diagnostic",
        fake_plugins,
    )
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
    )

    mcp = controller.run_slash_action(
        controller.handle_slash_command("/mcp list").action
    )
    plugins = controller.run_slash_action(
        controller.handle_slash_command("/plugins list").action
    )

    assert mcp["ok"] is True
    assert plugins["ok"] is True
    assert calls == [
        ("mcp", tmp_path.resolve(), False),
        ("plugins", tmp_path.resolve(), False),
    ]
    assert controller.config.mcp.enabled is False
    assert controller.config.plugins.enabled is False
    assert controller.turn_count == 0


def test_textual_action_runs_in_worker_and_returns_to_input(
    monkeypatch,
    tmp_path,
):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import ChatInput, LunarForgeTextualApp

    monkeypatch.setattr(
        textual_widgets_module,
        "git_status",
        lambda project_root, *, mode: {
            "ok": True,
            "repository_root": str(project_root),
            "status_short": [" M README.md"],
        },
    )
    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())

    async def wait_for(pilot, predicate):
        for _ in range(100):
            if predicate():
                return
            await pilot.pause(0.02)
        raise AssertionError("Timed out waiting for Textual action.")

    async def exercise():
        async with textual_app.run_test(size=(120, 40)) as pilot:
            chat_input = textual_app.query_one("#chat-input", ChatInput)
            chat_input.text = "/git status"
            await pilot.press("enter")
            await wait_for(
                pilot,
                lambda: (
                    not textual_app._turn_running
                    and " M README.md" in textual_app.transcript_plain_text
                ),
            )
            assert textual_app.controller.turn_count == 0
            assert chat_input.disabled is False
            assert "Done in " in textual_app.transcript_plain_text

    asyncio.run(exercise())
