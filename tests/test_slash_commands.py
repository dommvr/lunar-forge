import pytest
import yaml

import lunar_forge.ui.textual_state as textual_state_module
from lunar_forge.approvals import ApprovalDecision, DenyApprovalProvider
from lunar_forge.config import AppConfig, PermissionConfig
from lunar_forge.events import EventType
from lunar_forge.model_clients import ModelResponse
from lunar_forge.ui.slash_commands import (
    POPULAR_SLASH_COMMANDS,
    SlashCommandParser,
    SlashCommandRouter,
    slash_command_hints,
)
from lunar_forge.ui.textual_state import (
    ChatSessionState,
    SessionConfigUpdate,
)
from lunar_forge.ui.textual_widgets import TextualChatController


class _SequenceModel:
    def __init__(self, *responses):
        self.responses = list(responses)

    def complete(self, messages, tools=None):
        return self.responses.pop(0)


class _RecordingApprovalProvider:
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
            source="auto",
        )


def _router(tmp_path):
    state = ChatSessionState.create(tmp_path, AppConfig())
    return state, SlashCommandRouter(state)


def test_slash_parser_handles_names_quotes_and_windows_paths():
    parser = SlashCommandParser()

    project = parser.parse(
        r"  /project C:\Users\tiron\Desktop\my-app  "
    )
    message = parser.parse('/commit-message "Add pricing page"')

    assert project is not None
    assert project.name == "project"
    assert project.arguments == (
        r"C:\Users\tiron\Desktop\my-app",
    )
    assert message is not None
    assert message.name == "commit-message"
    assert message.arguments == ("Add pricing page",)
    assert parser.parse("explain /status handling") is None


def test_slash_router_rejects_unknown_and_invalid_quoting(tmp_path):
    _, router = _router(tmp_path)

    unknown = router.route("/moon-mode")
    invalid_quote = router.route('/commit-message "unfinished')

    assert unknown.handled is True
    assert unknown.error is True
    assert "Unknown command: /moon-mode" in (unknown.message or "")
    assert invalid_quote.error is True
    assert "quoting" in (invalid_quote.message or "").lower()


def test_slash_command_hints_are_small_filtered_and_nested():
    assert slash_command_hints("/") == POPULAR_SLASH_COMMANDS

    session_matches = slash_command_hints("/s")
    assert "/show-usage" in session_matches
    assert "/subagents" in session_matches
    assert "/sessions" in session_matches
    assert slash_command_hints("/git") == (
        "/git status",
        "/git commit",
    )
    assert slash_command_hints("/browser") == (
        "/browser-setup",
        "/browser-validate",
    )
    assert slash_command_hints("/does-not-exist") == ()
    assert slash_command_hints("explain /status") == ()


def test_help_and_status_do_not_start_agent_turn(tmp_path):
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
    )

    help_result = controller.handle_slash_command("/help")
    status_result = controller.handle_slash_command("/status")

    assert controller.turn_count == 0
    assert "/reasoning-effort" in (help_result.message or "")
    assert "/finish" in (help_result.message or "")
    status = status_result.message or ""
    for expected in (
        f"Project: {tmp_path.resolve()}",
        "Model: openai/gpt-5.5",
        "Reasoning effort: medium",
        "Runtime mode: local",
        "Permission mode: default",
        "Network: off",
        "Subagents: off",
        "Parallel subagents: off",
        "Commit offering: off",
        "MCP: off",
        "Plugins: off",
        f"Session: {controller.session_id}",
        "Compaction status: idle",
    ):
        assert expected in status


def test_finish_is_central_and_only_finish_routes_during_active_turn(
    tmp_path,
):
    state, router = _router(tmp_path)

    finish = router.route("/finish", active_turn=True)
    blocked = router.route("/runtime docker", active_turn=True)

    assert finish.handled is True
    assert finish.finish_task is True
    assert blocked.error is True
    assert "Use /finish" in (blocked.message or "")
    assert state.config.runtime.mode == "local"


def test_clear_requires_confirmation_and_retains_session_log(tmp_path):
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
    )
    session_path = controller.session_logger.path
    before = session_path.read_text(encoding="utf-8")

    requested = controller.handle_slash_command("/clear")

    assert requested.clear_transcript is False
    assert requested.confirmation is not None
    assert session_path.exists()
    assert session_path.read_text(encoding="utf-8") == before

    confirmed = controller.confirm_slash_command(requested.confirmation)

    assert confirmed.clear_transcript is True
    assert session_path.exists()
    assert session_path.read_text(encoding="utf-8") == before


def test_project_validates_path_and_starts_fresh_context(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second project"
    first.mkdir()
    second.mkdir()
    controller = TextualChatController(
        first,
        AppConfig(),
        DenyApprovalProvider(),
        model_client=_SequenceModel(ModelResponse(text="First answer.")),
    )
    controller.send_turn(
        "Read the project. Do not edit files or run commands."
    )
    old_session_id = controller.session_id
    old_log = controller.session_logger.path

    switched = controller.handle_slash_command(
        f'/project "{second}"'
    )

    assert switched.error is False
    assert switched.project_switched is True
    assert switched.clear_transcript is True
    assert controller.project_root == second.resolve()
    assert controller.session_state.project_root == second.resolve()
    assert controller.session_id != old_session_id
    assert controller.turn_count == 0
    assert controller.conversation_messages == ()
    assert controller.resumed_session_id is None
    assert controller.session_logger.project_root == second.resolve()
    assert old_log.exists()

    missing = controller.handle_slash_command(
        f"/project {tmp_path / 'missing'}"
    )
    assert missing.error is True
    assert controller.project_root == second.resolve()


def test_project_without_argument_requests_popup_form(tmp_path):
    _, router = _router(tmp_path)

    result = router.route("/project")

    assert result.form is not None
    assert result.form.command == "project"
    assert result.form.config_backed is False


@pytest.mark.parametrize(
    ("command", "current", "choices"),
    (
        ("/plan", "false", ("false", "true")),
        ("/docker", "false", ("false", "true")),
        ("/allow-network", "false", ("false", "true")),
        ("/subagents", "false", ("false", "true")),
        ("/parallel-subagents", "false", ("false", "true")),
        (
            "/reasoning-effort",
            "medium",
            ("low", "medium", "high", "xhigh", "max"),
        ),
        ("/runtime", "local", ("local", "docker", "no-command")),
        (
            "/permissions",
            "default",
            ("default", "yes", "no-command", "plan", "docker"),
        ),
        ("/mcp", "false", ("false", "true")),
        ("/plugins", "false", ("false", "true")),
    ),
)
def test_config_popup_model_includes_current_value_and_choices(
    tmp_path,
    command,
    current,
    choices,
):
    _, router = _router(tmp_path)

    result = router.route(command)

    assert result.form is not None
    assert result.form.config_backed is True
    assert result.form.current_value == current
    assert result.form.choices == choices
    assert result.form.config_scopes == (
        "session",
        "project",
        "global",
    )
    assert f"Current: {current}" in result.form.prompt
    assert f"Choices: {', '.join(choices)}" in result.form.prompt


def test_popup_session_apply_changes_state_without_writing_config(tmp_path):
    state, router = _router(tmp_path)
    config_path = tmp_path / ".agent" / "config.yaml"
    form = router.route("/mcp").form

    result = router.submit_form(form, "true")

    assert result.error is False
    assert state.config.mcp.enabled is True
    assert not config_path.exists()


def test_typed_config_scope_distinguishes_session_project_and_global(
    tmp_path,
):
    state, router = _router(tmp_path)

    project = router.route(
        "/reasoning-effort high scope=project"
    )

    assert project.error is False
    assert project.save_config_to_project is True
    assert project.config_update == SessionConfigUpdate(
        ("model", "reasoning", "effort"),
        "high",
    )
    assert state.config.model.reasoning.effort == "medium"

    session = router.route("/runtime docker scope=session")

    assert session.error is False
    assert session.save_config_to_project is False
    assert state.config.runtime.mode == "docker"

    global_result = router.route(
        "/reasoning-effort high scope=global"
    )

    assert global_result.error is False
    assert global_result.save_config_to_project is False
    assert global_result.save_config_to_user is True
    assert global_result.config_update == SessionConfigUpdate(
        ("model", "reasoning", "effort"),
        "high",
    )
    assert state.config.model.reasoning.effort == "medium"

    invalid = router.route("/plugins true scope=forever")

    assert invalid.error is True
    assert "scope must be session, project, or global" in (
        invalid.message or ""
    )
    assert state.config.plugins.enabled is False


def test_boolean_commands_update_session_state(tmp_path):
    state, router = _router(tmp_path)

    commands = (
        "/plan on",
        "/docker on",
        "/allow-network on",
        "/subagents on",
        "/parallel-subagents on",
        "/commit on",
        "/show-usage on",
        "/mcp on",
        "/plugins on",
    )
    for command in commands:
        result = router.route(command)
        assert result.error is False, result.message

    assert state.config.permissions.mode == "plan"
    assert state.config.runtime.mode == "docker"
    assert state.config.runtime.allow_network is True
    assert state.config.subagents.enabled is True
    assert state.config.subagents.parallel is True
    assert state.offer_commit is True
    assert state.show_usage is True
    assert state.config.mcp.enabled is True
    assert state.config.plugins.enabled is True

    for command in (
        "/plan off",
        "/docker off",
        "/allow-network off",
        "/subagents off",
        "/parallel-subagents off",
        "/commit off",
        "/show-usage off",
        "/mcp off",
        "/plugins off",
    ):
        assert router.route(command).error is False
    assert state.config.permissions.mode == "default"
    assert state.config.runtime.mode == "local"
    assert state.config.runtime.allow_network is False
    assert state.config.subagents.enabled is False
    assert state.config.subagents.parallel is False
    assert state.offer_commit is False
    assert state.show_usage is False
    assert state.config.mcp.enabled is False
    assert state.config.plugins.enabled is False


@pytest.mark.parametrize(
    "effort",
    ("low", "medium", "high", "xhigh", "max"),
)
def test_reasoning_effort_accepts_only_supported_values(tmp_path, effort):
    state, router = _router(tmp_path)

    result = router.route(f"/reasoning-effort {effort}")

    assert result.error is False
    assert state.config.model.reasoning.effort == effort


def test_reasoning_effort_missing_and_invalid_values(tmp_path):
    state, router = _router(tmp_path)

    missing = router.route("/reasoning-effort")
    invalid = router.route("/reasoning-effort extreme")

    assert missing.form is not None
    assert missing.form.config_backed is True
    assert "Current: medium" in missing.form.prompt
    assert invalid.error is True
    assert "low, medium, high, xhigh, max" in (invalid.message or "")
    assert state.config.model.reasoning.effort == "medium"


@pytest.mark.parametrize("mode", ("local", "docker", "no-command"))
def test_runtime_validates_allowed_values(tmp_path, mode):
    state, router = _router(tmp_path)

    result = router.route(f"/runtime {mode}")

    assert result.error is False
    assert state.config.runtime.mode == mode


@pytest.mark.parametrize(
    "mode",
    ("default", "yes", "no-command", "plan", "docker"),
)
def test_permissions_validates_allowed_values(tmp_path, mode):
    state, router = _router(tmp_path)

    result = router.route(f"/permissions {mode}")

    assert result.error is False
    assert state.config.permissions.mode == mode


def test_runtime_permissions_and_boolean_invalid_args_do_not_mutate(tmp_path):
    state, router = _router(tmp_path)

    bad_runtime = router.route("/runtime cloud")
    bad_permissions = router.route("/permissions unrestricted")
    bad_boolean = router.route("/docker maybe")
    too_many = router.route("/runtime docker local")

    assert all(
        result.error
        for result in (
            bad_runtime,
            bad_permissions,
            bad_boolean,
            too_many,
        )
    )
    assert state.config.runtime.mode == "local"
    assert state.config.permissions.mode == "default"


def test_invalid_slash_args_do_not_start_agent_turn(tmp_path):
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
    )

    result = controller.handle_slash_command("/runtime moon")

    assert result.error is True
    assert controller.turn_count == 0
    assert controller.conversation_messages == ()


def test_commit_message_handles_quoted_text_and_missing_form(tmp_path):
    state, router = _router(tmp_path)

    missing = router.route("/commit-message")
    result = router.route('/commit-message "Add pricing page"')

    assert missing.form is not None
    assert result.error is False
    assert state.commit_message == "Add pricing page"


def test_session_settings_reach_future_agent_turns(tmp_path):
    captured = {}

    def event_runner(prompt, project_root, **kwargs):
        captured.update(kwargs)
        factory = kwargs["event_factory"]
        yield factory.create(
            EventType.ASSISTANT_MESSAGE_COMPLETED,
            {"text": "Configured answer."},
        )

    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
        event_runner=event_runner,
    )
    for command in (
        "/runtime docker",
        "/permissions no-command",
        "/subagents on",
        "/parallel-subagents on",
        "/commit on",
        '/commit-message "Configured commit"',
        "/show-usage on",
        "/mcp on",
        "/plugins on",
    ):
        assert controller.handle_slash_command(command).error is False

    result = controller.send_turn("Describe this project.")

    assert result.final_text == "Configured answer."
    assert captured["config"].runtime.mode == "docker"
    assert captured["config"].permissions.mode == "no-command"
    assert captured["config"].subagents.enabled is True
    assert captured["config"].subagents.parallel is True
    assert captured["config"].mcp.enabled is True
    assert captured["config"].plugins.enabled is True
    assert captured["mode"] == "no-command"
    assert captured["offer_commit"] is True
    assert captured["commit_message"] == "Configured commit"
    assert captured["show_usage"] is True
    assert controller.session_state.commit_message is None


def test_project_config_is_written_only_after_explicit_save(tmp_path):
    provider = _RecordingApprovalProvider()
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        provider,
    )
    config_path = tmp_path / ".agent" / "config.yaml"
    form = controller.handle_slash_command("/runtime").form

    selected = controller.validate_slash_form(form, "docker")
    assert selected.config_update is not None
    assert controller.config.runtime.mode == "local"
    assert not config_path.exists()

    saved = controller.save_project_config_update(
        selected.config_update
    )

    assert saved.path == config_path
    assert saved.created is True
    assert saved.checkpoint_path is None
    assert len(provider.requests) == 1
    assert provider.requests[0].kind == "write"
    assert provider.requests[0].file_path == ".agent/config.yaml"
    assert controller.config.runtime.mode == "docker"
    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == {
        "runtime": {"mode": "docker"}
    }
    session_text = controller.session_logger.path.read_text(encoding="utf-8")
    assert '"event":"permission.requested"' in session_text
    assert '"event":"permission.resolved"' in session_text


def test_project_config_save_preserves_keys_and_checkpoints_existing_file(
    tmp_path,
):
    config_path = tmp_path / ".agent" / "config.yaml"
    config_path.parent.mkdir()
    original = (
        "runtime:\n  allow_network: false\n"
        "model:\n  model: example/model\n"
    )
    config_path.write_text(
        original,
        encoding="utf-8",
    )
    provider = _RecordingApprovalProvider()
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        provider,
    )
    form = controller.handle_slash_command("/reasoning-effort").form
    selected = controller.validate_slash_form(form, "high")

    result = controller.save_project_config_update(
        selected.config_update
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["runtime"]["allow_network"] is False
    assert saved["model"]["model"] == "example/model"
    assert saved["model"]["reasoning"]["effort"] == "high"
    assert result.created is False
    assert result.checkpoint_path is not None
    assert result.checkpoint_path.read_text(encoding="utf-8") == original
    assert controller.config.model.reasoning.effort == "high"


def test_project_config_save_refuses_existing_raw_secrets(tmp_path):
    config_path = tmp_path / ".agent" / "config.yaml"
    config_path.parent.mkdir()
    original = "model:\n  api_key: do-not-copy-this\n"
    config_path.write_text(original, encoding="utf-8")
    provider = _RecordingApprovalProvider()
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        provider,
    )
    form = controller.handle_slash_command("/runtime").form
    selected = controller.validate_slash_form(form, "docker")

    with pytest.raises(ValueError, match="raw secret field"):
        controller.save_project_config_update(selected.config_update)

    assert config_path.read_text(encoding="utf-8") == original
    assert provider.requests == []
    assert controller.config.runtime.mode == "local"


def test_invalid_config_value_is_rejected_before_write_or_approval(tmp_path):
    provider = _RecordingApprovalProvider()
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        provider,
    )
    form = controller.handle_slash_command("/runtime").form

    invalid = controller.validate_slash_form(form, "cloud")

    assert invalid.error is True
    assert provider.requests == []
    assert controller.config.runtime.mode == "local"
    assert not (tmp_path / ".agent" / "config.yaml").exists()
    with pytest.raises(ValueError, match="Invalid runtime.mode"):
        SessionConfigUpdate(("runtime", "mode"), "cloud")


def test_project_config_save_denial_writes_nothing_and_keeps_state(tmp_path):
    provider = _RecordingApprovalProvider(approved=False)
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        provider,
    )
    form = controller.handle_slash_command("/plugins").form
    selected = controller.validate_slash_form(form, "true")

    with pytest.raises(PermissionError, match="Denied by test"):
        controller.save_project_config_update(selected.config_update)

    assert len(provider.requests) == 1
    assert controller.config.plugins.enabled is False
    assert not (tmp_path / ".agent" / "config.yaml").exists()
    assert not (tmp_path / ".agent" / "checkpoints").exists()


def test_project_config_save_respects_plan_mode(tmp_path):
    provider = _RecordingApprovalProvider()
    controller = TextualChatController(
        tmp_path,
        AppConfig(permissions=PermissionConfig(mode="plan")),
        provider,
    )
    form = controller.handle_slash_command("/mcp").form
    selected = controller.validate_slash_form(form, "true")

    with pytest.raises(PermissionError, match="Plan mode blocks"):
        controller.save_project_config_update(selected.config_update)

    assert provider.requests == []
    assert controller.config.mcp.enabled is False
    assert not (tmp_path / ".agent" / "config.yaml").exists()


def test_project_config_save_enforces_project_safe_path(
    tmp_path,
    monkeypatch,
):
    provider = _RecordingApprovalProvider()
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        provider,
    )
    form = controller.handle_slash_command("/permissions").form
    selected = controller.validate_slash_form(form, "yes")
    real_safe_path = textual_state_module.safe_path

    def reject_config_path(project_root, path):
        if str(path).replace("\\", "/").endswith(".agent/config.yaml"):
            raise PermissionError("Synthetic project-root escape blocked.")
        return real_safe_path(project_root, path)

    monkeypatch.setattr(
        textual_state_module,
        "safe_path",
        reject_config_path,
    )

    with pytest.raises(PermissionError, match="escape blocked"):
        controller.save_project_config_update(selected.config_update)

    assert provider.requests == []
    assert controller.config.permissions.mode == "default"


def test_user_config_save_requires_approval_and_updates_after_success(
    tmp_path,
):
    home = tmp_path / "home"
    provider = _RecordingApprovalProvider()
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        provider,
        user_home=home,
    )
    form = controller.handle_slash_command("/runtime").form
    selected = controller.validate_slash_form(form, "docker")

    result = controller.save_user_config_update(selected.config_update)

    config_path = home / ".lunar-forge" / "config.yaml"
    assert result.path == config_path
    assert result.created is True
    assert len(provider.requests) == 1
    assert provider.requests[0].kind == "write"
    assert provider.requests[0].file_path == "~/.lunar-forge/config.yaml"
    assert controller.config.runtime.mode == "docker"
    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == {
        "runtime": {"mode": "docker"}
    }
    session_text = controller.session_logger.path.read_text(encoding="utf-8")
    assert '"event":"permission.requested"' in session_text
    assert '"event":"permission.resolved"' in session_text


def test_denied_user_config_save_writes_nothing_and_keeps_state(tmp_path):
    home = tmp_path / "home"
    provider = _RecordingApprovalProvider(approved=False)
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        provider,
        user_home=home,
    )
    form = controller.handle_slash_command("/plugins").form
    selected = controller.validate_slash_form(form, "true")

    with pytest.raises(PermissionError, match="Denied by test"):
        controller.save_user_config_update(selected.config_update)

    assert len(provider.requests) == 1
    assert controller.config.plugins.enabled is False
    assert not (home / ".lunar-forge" / "config.yaml").exists()


def test_user_config_save_preserves_unrelated_keys(tmp_path):
    home = tmp_path / "home"
    config_path = home / ".lunar-forge" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "runtime:\n  allow_network: false\nmodel:\n  model: example/model\n",
        encoding="utf-8",
    )
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        _RecordingApprovalProvider(),
        user_home=home,
    )
    form = controller.handle_slash_command("/reasoning-effort").form
    selected = controller.validate_slash_form(form, "high")

    controller.save_user_config_update(selected.config_update)

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["runtime"]["allow_network"] is False
    assert saved["model"]["model"] == "example/model"
    assert saved["model"]["reasoning"]["effort"] == "high"
    assert controller.config.model.reasoning.effort == "high"
