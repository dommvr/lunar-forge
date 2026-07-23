import json
import time
from threading import Barrier, Lock

import pytest

from lunar_forge.agent import CodeAgent
from lunar_forge.config import AppConfig, SubagentConfig
from lunar_forge.model_clients import ModelResponse, ToolCall
from lunar_forge.permissions import PermissionLevel, PermissionManager
from lunar_forge.subagents import (
    BUILTIN_SUBAGENT_TOOLS,
    CODER_ROLE,
    PLANNER_ROLE,
    REVIEWER_ROLE,
    SCAFFOLDER_ROLE,
    SECURITY_ROLE,
    SUBAGENT_ROLES,
    TESTER_ROLE,
    RestrictedToolRegistry,
    SubagentOrchestrator,
    SubagentPhase,
    SubagentPhasePlan,
    SubagentRole,
    WorkflowKind,
    build_phase_plan,
    get_subagent_role,
    requires_security_review,
)
from lunar_forge.tools.registry import Tool, ToolRegistry
from lunar_forge.workflows.new_project import (
    format_new_project_result,
    run_new_project,
)


EXPECTED_ALLOWED_TOOLS = {
    "planner": {
        "list_dir",
        "read_file",
        "read_file_with_line_numbers",
        "grep",
        "glob",
        "detect_project",
        "project_health",
        "dependency_summary",
        "git_status",
    },
    "coder": {
        "list_dir",
        "read_file",
        "read_file_with_line_numbers",
        "grep",
        "glob",
        "create_dir",
        "write_file",
        "edit_file",
        "replace_lines",
        "insert_lines",
    },
    "reviewer": {
        "read_file",
        "read_file_with_line_numbers",
        "grep",
        "glob",
        "project_health",
        "dependency_summary",
        "git_status",
        "git_diff",
        "list_changed_files",
    },
    "tester": {
        "run_command",
        "run_validation",
        "run_browser_validation",
        "run_managed_browser_validation",
        "read_file",
        "read_file_with_line_numbers",
        "grep",
        "dependency_summary",
        "git_status",
        "list_changed_files",
    },
    "security": {
        "read_file",
        "read_file_with_line_numbers",
        "grep",
        "glob",
        "project_health",
        "git_status",
        "git_diff",
        "list_changed_files",
    },
    "scaffolder": {"create_dir", "write_file", "run_command", "run_validation"},
}


def test_role_definitions_have_explicit_deny_by_default_tool_sets():
    assert set(SUBAGENT_ROLES) == set(EXPECTED_ALLOWED_TOOLS)

    for name, expected_allowed in EXPECTED_ALLOWED_TOOLS.items():
        role = get_subagent_role(name.upper())

        assert isinstance(role, SubagentRole)
        assert role.name == name
        assert role.purpose.strip()
        assert role.system_prompt_fragment.strip()
        assert role.allowed_tools == expected_allowed
        assert role.blocked_tools == BUILTIN_SUBAGENT_TOOLS - expected_allowed
        assert role.allowed_tools.isdisjoint(role.blocked_tools)
    assert TESTER_ROLE.allowed_tool_prefixes == ("mcp.playwright.",)


def test_role_prompts_use_project_intelligence_deliberately():
    planner = PLANNER_ROLE.system_prompt_fragment
    reviewer = REVIEWER_ROLE.system_prompt_fragment
    tester = TESTER_ROLE.system_prompt_fragment
    security = SECURITY_ROLE.system_prompt_fragment

    assert "broad review, onboarding, or feature-planning" in planner
    assert "dependency_summary before" in planner
    assert "tiny single-file edit" in planner
    assert "list_changed_files before opening review files" in reviewer
    assert "git_diff for relevant changed files" in reviewer
    assert "Do not reread the whole project" in reviewer
    assert "dependency_summary before selecting commands" in tester
    assert "list_changed_files when it helps" in tester
    assert "focus validation or failure inspection" in tester
    assert "project_health and git_status" in security
    assert "suspicious tracked runtime" in security
    assert "git_diff for security-sensitive changes" in security


def test_restricted_registry_exposes_only_role_allowlisted_tools():
    registry, calls = _registry_with_all_known_tools()
    restricted = RestrictedToolRegistry(registry, PLANNER_ROLE)

    assert restricted.names() == tuple(sorted(PLANNER_ROLE.allowed_tools))
    assert {
        schema["function"]["name"] for schema in restricted.schemas()
    } == PLANNER_ROLE.allowed_tools

    result = restricted.execute("read_file", {"path": "README.md"})

    assert result["ok"] is True
    assert calls == ["read_file"]


def test_tester_registry_exposes_browser_and_playwright_mcp_tools():
    calls: list[str] = []

    def handler(tool_name):
        def run(**arguments):
            calls.append(tool_name)
            return {"ok": True}

        return run

    registry = ToolRegistry(
        (
            Tool(
                name="run_managed_browser_validation",
                description="Run managed browser validation.",
                parameters={"type": "object"},
                handler=handler("run_managed_browser_validation"),
                permission=PermissionLevel.EXECUTE,
            ),
            Tool(
                name="mcp.playwright.browser_navigate",
                description="Navigate with Playwright MCP.",
                parameters={"type": "object"},
                handler=handler("mcp.playwright.browser_navigate"),
                permission=PermissionLevel.NETWORK,
            ),
            Tool(
                name="mcp.github.search_issues",
                description="Unrelated MCP tool.",
                parameters={"type": "object"},
                handler=handler("mcp.github.search_issues"),
                permission=PermissionLevel.NETWORK,
            ),
        ),
        permission_manager=PermissionManager(
            mode="default",
            approval_callback=lambda request: True,
        ),
    )

    restricted = TESTER_ROLE.restrict(registry)

    assert restricted.names() == (
        "mcp.playwright.browser_navigate",
        "run_managed_browser_validation",
    )
    assert {
        schema["function"]["name"] for schema in restricted.schemas()
    } == {
        "mcp_playwright_browser_navigate",
        "run_managed_browser_validation",
    }
    assert restricted.execute(
        "mcp_playwright_browser_navigate",
        {"url": "http://localhost:5173"},
    )["ok"] is True
    assert calls == ["mcp.playwright.browser_navigate"]


def test_blocked_tool_never_reaches_underlying_registry():
    registry, calls = _registry_with_all_known_tools()
    restricted = PLANNER_ROLE.restrict(registry)

    result = restricted.execute(
        "write_file",
        {"path": "changed.txt", "content": "should not run"},
    )

    assert result["ok"] is False
    assert result["permission_denied"] is True
    assert result["blocked_by_subagent"] is True
    assert "planner" in result["error"]
    assert calls == []


def test_unknown_tools_are_denied_even_when_not_in_explicit_blocked_set():
    registry, calls = _registry_with_all_known_tools()
    restricted = CODER_ROLE.restrict(registry)

    result = restricted.execute("future_admin_tool", {})

    assert result["ok"] is False
    assert result["blocked_by_subagent"] is True
    assert calls == []


def test_allowed_tool_still_uses_existing_registry_permissions():
    called = False

    def write_handler(**arguments):
        nonlocal called
        called = True
        return {"ok": True}

    registry = ToolRegistry(
        (
            Tool(
                name="write_file",
                description="Write a file.",
                parameters={"type": "object"},
                handler=write_handler,
                permission=PermissionLevel.WRITE,
            ),
        ),
        permission_manager=PermissionManager(
            mode="default",
            approval_callback=lambda request: False,
        ),
    )

    result = CODER_ROLE.restrict(registry).execute(
        "write_file",
        {"path": "example.txt", "content": "content"},
    )

    assert result["ok"] is False
    assert result["permission_denied"] is True
    assert "Denied by user" in result["error"]
    assert called is False


def test_every_role_blocks_tools_outside_its_allowlist():
    registry, calls = _registry_with_all_known_tools()

    for role in SUBAGENT_ROLES.values():
        restricted = role.restrict(registry)
        for tool_name in BUILTIN_SUBAGENT_TOOLS - role.allowed_tools:
            result = restricted.execute(tool_name, {})

            assert result["ok"] is False
            assert result["blocked_by_subagent"] is True

    assert calls == []


def test_existing_project_phase_plan_is_finite_and_deterministic():
    first = build_phase_plan(WorkflowKind.EXISTING_PROJECT)
    second = build_phase_plan("existing_project")

    assert first == second
    assert [phase.name for phase in first.phases] == [
        "plan",
        "approval",
        "implement",
        "test",
        "review",
    ]
    assert first.role_names == ("planner", "coder", "tester", "reviewer")
    assert first.phases[1].role is None
    assert first.phases[1].requires_user_approval is True
    assert len(set(first.role_names)) == len(first.role_names)
    json.dumps(first.as_dict())


def test_new_project_phase_plan_uses_scaffolder_and_optional_security():
    plan = SubagentOrchestrator().build_phase_plan(
        "new-project",
        include_security=True,
    )

    assert plan.workflow is WorkflowKind.NEW_PROJECT
    assert plan.role_names == (
        "scaffolder",
        "tester",
        "reviewer",
        "security",
    )
    assert plan.phases[-1].name == "security"
    assert plan.phases[-1].role is SECURITY_ROLE


def test_parallel_phase_plans_group_only_non_writer_roles():
    existing = build_phase_plan(
        WorkflowKind.EXISTING_PROJECT,
        include_security=True,
        parallel=True,
    )
    new_project = build_phase_plan(
        WorkflowKind.NEW_PROJECT,
        parallel=True,
    )

    assert [phase.name for phase in existing.phases] == [
        "plan",
        "security",
        "approval",
        "implement",
        "test",
        "review",
    ]
    assert [
        (group_id, tuple(phase.role_name for phase in phases))
        for group_id, phases in existing.parallel_groups
    ] == [
        ("analysis", ("planner", "security")),
        ("post-edit", ("tester", "reviewer")),
    ]
    assert existing.phases[3].role is CODER_ROLE
    assert existing.phases[3].parallel_group_id is None
    assert new_project.phases[0].role is SCAFFOLDER_ROLE
    assert new_project.phases[0].parallel_group_id is None
    assert [
        tuple(phase.role_name for phase in phases)
        for _, phases in new_project.parallel_groups
    ] == [("tester", "reviewer")]
    assert all(
        phase.role is not None and phase.role.can_run_in_parallel
        for plan in (existing, new_project)
        for _, phases in plan.parallel_groups
        for phase in phases
    )


def test_parallel_phase_plan_rejects_writer_roles():
    with pytest.raises(ValueError, match="Writer subagent 'coder'"):
        SubagentPhasePlan(
            workflow=WorkflowKind.EXISTING_PROJECT,
            phases=(
                SubagentPhase(
                    name="implement",
                    description="Unsafe writer group.",
                    role=CODER_ROLE,
                    parallel_group_id="unsafe-1",
                ),
                SubagentPhase(
                    name="test",
                    description="Synthetic sibling.",
                    role=TESTER_ROLE,
                    parallel_group_id="unsafe-1",
                ),
            ),
        )


@pytest.mark.parametrize(
    "path",
    (
        "lunar_forge/permissions.py",
        "lunar_forge/tools/shell.py",
        "lunar_forge/runtime/docker_runner.py",
        "lunar_forge/mcp/client.py",
        "lunar_forge/plugins/loader.py",
        "lunar_forge/config.py",
        "sandbox/Dockerfile",
    ),
)
def test_sensitive_changed_paths_require_security_review(path):
    assert requires_security_review((path,)) is True


def test_ordinary_application_changes_do_not_require_security_review():
    assert requires_security_review(("app/page.py", "README.md")) is False


def test_orchestrator_rejects_unknown_workflows_and_duplicate_roles():
    with pytest.raises(ValueError, match="existing_project"):
        build_phase_plan("autonomous_debate")

    with pytest.raises(ValueError, match="unique"):
        SubagentOrchestrator((PLANNER_ROLE, PLANNER_ROLE))


def test_exported_roles_are_the_canonical_definitions():
    assert SUBAGENT_ROLES == {
        "planner": PLANNER_ROLE,
        "coder": CODER_ROLE,
        "reviewer": REVIEWER_ROLE,
        "tester": TESTER_ROLE,
        "security": SECURITY_ROLE,
        "scaffolder": SCAFFOLDER_ROLE,
    }


def test_single_agent_mode_remains_the_default(tmp_path):
    model = SequenceModel((ModelResponse(text="Single-agent response."),))

    output = CodeAgent(AppConfig(), model_client=model).run(
        "Explain this project",
        tmp_path,
        mode="plan",
    )

    assert len(model.calls) == 1
    assert "Subagents run:" not in output
    assert output.startswith("Single-agent response.")


def test_single_agent_browser_intent_exposes_and_runs_managed_tool(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {"devDependencies": {"vite": "latest"}, "scripts": {"dev": "vite"}}
        ),
        encoding="utf-8",
    )
    calls = []
    registry = ToolRegistry(
        (
            Tool(
                name="run_managed_browser_validation",
                description="Run managed browser validation.",
                parameters={"type": "object"},
                handler=lambda **arguments: calls.append(arguments)
                or {
                    "ok": True,
                    "status": "passed",
                    "console_errors": [],
                    "screenshot_path": ".agent/artifacts/browser/single.png",
                },
                permission=PermissionLevel.EXECUTE,
            ),
        )
    )

    class SingleAgentBrowserModel:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                assert "Application-detected browser routing" in messages[0]["content"]
                assert {
                    schema["function"]["name"] for schema in (tools or [])
                } == {"run_managed_browser_validation"}
                return ModelResponse(
                    text="",
                    tool_calls=(
                        ToolCall(
                            id="managed",
                            name="run_managed_browser_validation",
                            arguments={
                                "command": "npm run dev",
                                "url": "http://localhost:5173",
                                "full_page": True,
                            },
                        ),
                    ),
                )
            return ModelResponse(text="UI inspection complete.")

    output = CodeAgent(
        AppConfig(),
        model_client=SingleAgentBrowserModel(),
        approval_callback=lambda request: True,
    ).run(
        "Start the dev server and capture a full-page browser screenshot",
        tmp_path,
        registry=registry,
    )

    assert calls[0]["command"] == "npm run dev"
    assert calls[0]["url"] == "http://localhost:5173"
    assert "run_managed_browser_validation: passed" in output
    assert ".agent/artifacts/browser/single.png" in output
    assert "Console errors: 0" in output


def test_single_agent_browser_intent_uses_direct_tool_for_running_url(tmp_path):
    calls = []
    registry = ToolRegistry(
        (
            Tool(
                name="run_browser_validation",
                description="Run browser validation.",
                parameters={"type": "object"},
                handler=lambda **arguments: calls.append(arguments)
                or {
                    "ok": True,
                    "status": "passed",
                    "console_errors": [],
                    "screenshot_path": ".agent/artifacts/browser/direct.png",
                },
                permission=PermissionLevel.EXECUTE,
            ),
        )
    )
    model = SequenceModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="direct-browser",
                        name="run_browser_validation",
                        arguments={
                            "url": "http://localhost:5173",
                            "screenshot": True,
                        },
                    ),
                ),
            ),
            ModelResponse(text="Direct browser inspection complete."),
        )
    )

    output = CodeAgent(
        AppConfig(),
        model_client=model,
        approval_callback=lambda request: True,
    ).run(
        "Inspect page http://localhost:5173 in a browser and capture a screenshot",
        tmp_path,
        registry=registry,
    )

    assert model.calls[0]["tools"] == {"run_browser_validation"}
    assert calls == [
        {"url": "http://localhost:5173", "screenshot": True}
    ]
    assert "run_browser_validation: passed" in output
    assert ".agent/artifacts/browser/direct.png" in output


def test_existing_project_subagents_run_in_deterministic_order(tmp_path):
    model = SequenceModel(
        (
            ModelResponse(text="Plan: update app.py, then validate."),
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="write_app",
                        name="write_file",
                        arguments={"path": "app.py", "content": "updated"},
                    ),
                ),
            ),
            ModelResponse(text="Implemented app.py."),
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
            ModelResponse(text="Validation passed."),
            ModelResponse(text="Changed files:\n- app.py\n\nValidation:\n- passed"),
        )
    )
    config = AppConfig(subagents=SubagentConfig(enabled=True))
    registry = _agent_registry()

    output = CodeAgent(
        config,
        model_client=model,
        approval_callback=lambda request: True,
    ).run("Update the app", tmp_path, registry=registry)

    assert "Subagents run:\n- planner\n- coder\n- tester\n- reviewer" in output
    assert "Parallel subagent groups:\n- None" in output
    assert "Browser validation:" not in output
    assert [call["role"] for call in model.calls] == [
        "planner",
        "coder",
        "coder",
        "tester",
        "tester",
        "reviewer",
    ]
    assert "write_file" not in model.calls[0]["tools"]
    assert "run_validation" not in model.calls[1]["tools"]
    assert "write_file" not in model.calls[3]["tools"]
    assert model.calls[5]["tools"] == {"read_file"}

    session_file = next((tmp_path / ".agent" / "sessions").glob("*.jsonl"))
    events = [json.loads(line) for line in session_file.read_text().splitlines()]
    started = [
        event["data"]["role"]
        for event in events
        if event["event"] == "subagent_started"
    ]
    assert started == ["planner", "coder", "tester", "reviewer"]


def test_final_summary_uses_authoritative_session_changed_files(tmp_path):
    session_files: list[str] = []
    changed_file_calls: list[str] = []

    def list_session_changes(source="both"):
        changed_file_calls.append(source)
        return {
            "ok": True,
            "source": source,
            "files": [
                {
                    "path": path,
                    "session_changed": True,
                    "git_changed": False,
                    "excluded": False,
                    "commit_candidate": True,
                }
                for path in session_files
            ],
            "session_files": list(session_files),
            "git_files": [],
            "staged_files": [],
            "untracked_files": [],
            "excluded_files": [],
            "commit_candidates": list(session_files),
            "truncated": False,
        }

    registry = ToolRegistry(
        (
            Tool(
                name="write_file",
                description="Write a file.",
                parameters={"type": "object"},
                handler=lambda **arguments: {
                    "ok": True,
                    "path": arguments["path"],
                },
                permission=PermissionLevel.WRITE,
            ),
            Tool(
                name="list_changed_files",
                description="List session changes.",
                parameters={"type": "object"},
                handler=list_session_changes,
            ),
        ),
        session_changed_files=session_files,
    )
    model = SequenceModel(
        (
            ModelResponse(text="Plan: update authoritative.py."),
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="write_authoritative",
                        name="write_file",
                        arguments={
                            "path": "authoritative.py",
                            "content": "updated",
                        },
                    ),
                ),
            ),
            ModelResponse(text="Changed files:\n- coder-claimed.py"),
            ModelResponse(text="Validation was not run."),
            ModelResponse(
                text=(
                    "Changed files:\n"
                    "- reviewer-claimed.py\n\n"
                    "Validation:\n"
                    "- Not run.\n\n"
                    "Commands run:\n"
                    "- None.\n\n"
                    "Checkpoints:\n"
                    "- None."
                )
            ),
        )
    )

    output = CodeAgent(
        AppConfig(subagents=SubagentConfig(enabled=True)),
        model_client=model,
        approval_callback=lambda request: True,
    ).run("Update the app", tmp_path, registry=registry)

    assert changed_file_calls == ["session"]
    assert "Changed files:\n- authoritative.py" in output
    assert "reviewer-claimed.py" not in output
    assert "coder-claimed.py" not in output
    assert "Validation:\n- Not run." in output
    assert "Subagents run:\n- planner\n- coder\n- tester\n- reviewer" in output


def test_tester_command_results_replace_reviewer_not_run_summary(tmp_path):
    model = SequenceModel(
        (
            ModelResponse(text="Plan: update app.py, then validate."),
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="write_app",
                        name="write_file",
                        arguments={"path": "app.py", "content": "updated"},
                    ),
                ),
            ),
            ModelResponse(text="Implemented app.py."),
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="validate",
                        name="run_validation",
                        arguments={},
                    ),
                    ToolCall(
                        id="command",
                        name="run_command",
                        arguments={"command": "python -m pytest -q"},
                    ),
                ),
            ),
            ModelResponse(text="Validation and command checks passed."),
            ModelResponse(
                text=(
                    "Changed files:\n"
                    "- app.py\n\n"
                    "Validation:\n"
                    "- Not run (review-only phase).\n\n"
                    "Commands run:\n"
                    "- None."
                )
            ),
        )
    )

    def run_validation():
        command = "python -B -m compileall lunar_forge"
        return {
            "ok": True,
            "commands": [command],
            "results": [{"ok": True, "command": command, "exit_code": 0}],
        }

    def run_command(command):
        return {
            "ok": True,
            "command": command,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration_ms": 1,
            "truncated": False,
        }

    registry = ToolRegistry(
        (
            Tool(
                name="write_file",
                description="Write a file.",
                parameters={"type": "object"},
                handler=lambda **arguments: {
                    "ok": True,
                    "path": arguments["path"],
                },
                permission=PermissionLevel.WRITE,
            ),
            Tool(
                name="run_validation",
                description="Run validation.",
                parameters={"type": "object"},
                handler=run_validation,
                permission=PermissionLevel.EXECUTE,
            ),
            Tool(
                name="run_command",
                description="Run a command.",
                parameters={"type": "object"},
                handler=run_command,
                permission=PermissionLevel.EXECUTE,
            ),
        )
    )

    output = CodeAgent(
        AppConfig(subagents=SubagentConfig(enabled=True)),
        model_client=model,
        approval_callback=lambda request: True,
    ).run("Update the app", tmp_path, registry=registry)

    assert (
        "Validation:\n"
        "- python -B -m compileall lunar_forge: passed "
        "(authoritative tool result; exit code 0)"
    ) in output
    assert (
        "- python -m pytest -q: passed "
        "(authoritative tool result; via run_command; exit code 0)"
    ) in output
    assert "Not run (review-only phase)" not in output
    assert "Commands run:\n- None" not in output


@pytest.mark.parametrize(
    ("parallel", "include_reviewer_finding"),
    ((False, True), (True, True), (False, False)),
)
def test_browser_success_is_authoritative_over_reviewer_text(
    tmp_path,
    parallel,
    include_reviewer_finding,
):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "devDependencies": {"vite": "latest"},
                "scripts": {"dev": "vite"},
            }
        ),
        encoding="utf-8",
    )
    calls = []
    approvals = []

    def managed_browser_handler(**arguments):
        calls.append(arguments)
        return {
            "ok": True,
            "status": "passed",
            "title": "Vite App",
            "final_url": arguments["url"],
            "console_errors": ["one console error"],
            "failed_requests": [],
            "screenshot_path": ".agent/artifacts/browser/browser-test.png",
            "checks": [],
            "truncated": False,
            "managed_server": {
                "started": True,
                "ready": True,
                "startup_failed": False,
                "terminated_by_lunar_forge": True,
                "stopped": True,
                "stop_note": "Stopped intentionally.",
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "output_truncated": False,
            },
        }

    registry = ToolRegistry(
        (
            Tool(
                name="run_managed_browser_validation",
                description="Run managed browser validation.",
                parameters={"type": "object"},
                handler=managed_browser_handler,
                permission=PermissionLevel.EXECUTE,
            ),
        )
    )

    class BrowserRoutingModel:
        def __init__(self):
            self.tester_calls = 0

        def complete(self, messages, tools=None):
            system_prompt = str(messages[0]["content"])
            role_line = next(
                line
                for line in system_prompt.splitlines()
                if line.startswith("Active subagent role:")
            )
            role = role_line.partition(":")[2].strip()
            if role == "tester":
                self.tester_calls += 1
                if self.tester_calls == 1:
                    assert "Application-detected browser routing" in system_prompt
                    assert "inferred_dev_command: npm run dev" in system_prompt
                    assert "inferred_local_url: http://localhost:5173" in system_prompt
                    schema_names = {
                        schema["function"]["name"] for schema in (tools or [])
                    }
                    assert "run_managed_browser_validation" in schema_names
                    assert "run_validation" not in schema_names
                    return ModelResponse(
                        text="",
                        tool_calls=(
                            ToolCall(
                                id="browser",
                                name="run_managed_browser_validation",
                                arguments={
                                    "command": "npm run dev",
                                    "url": "http://localhost:5173",
                                    "screenshot": True,
                                    "full_page": True,
                                },
                            ),
                        ),
                    )
                return ModelResponse(text="Browser validation completed.")
            if role == "reviewer":
                findings = (
                    "Findings:\n"
                    "- app.jsx still has an unrelated maintainability issue.\n\n"
                    if include_reviewer_finding
                    else ""
                )
                return ModelResponse(
                    text=(
                        f"{findings}"
                        "Changed files:\n"
                        "- None.\n\n"
                        "Validation:\n"
                        "- No full-page screenshot was captured.\n"
                        "- Console errors were not inspected.\n"
                        "- Browser validation did not run; the active "
                        "reviewer role has no permission to start the dev server or "
                        "run managed browser validation."
                    )
                )
            return ModelResponse(text=f"{role.title()} completed.")

    request = (
        "Start the dev server if needed, inspect the UI in a browser, capture a "
        "full-page screenshot, and report console errors."
    )
    output = CodeAgent(
        AppConfig(subagents=SubagentConfig(enabled=True, parallel=parallel)),
        model_client=BrowserRoutingModel(),
        approval_callback=lambda approval: approvals.append(approval) or True,
    ).run(request, tmp_path, registry=registry)

    assert calls == [
        {
            "command": "npm run dev",
            "url": "http://localhost:5173",
            "screenshot": True,
            "full_page": True,
        }
    ]
    assert len(approvals) == 1
    assert approvals[0].tool_name == "run_managed_browser_validation"
    assert "Browser validation did not run" not in output
    assert "active reviewer role has no permission" not in output
    assert "No full-page screenshot was captured" not in output
    assert "Console errors were not inspected" not in output
    if include_reviewer_finding:
        assert "Reviewer findings (advisory):" in output
        assert "app.jsx still has an unrelated maintainability issue" in output
    else:
        assert "Reviewer findings (advisory):" not in output
        assert "\nFindings:\n" not in output
    assert "Changed files:\n- None." in output
    assert "Reviewer findings (advisory):\nChanged files:" not in output
    assert "Reviewer role note" not in output
    assert "Browser validation:" in output
    assert output.count("Browser validation:") == 1
    assert "run_managed_browser_validation: passed" in output
    assert "authoritative tool result" in output
    assert "Final URL: http://localhost:5173" in output
    assert "Page title: Vite App" in output
    assert ".agent/artifacts/browser/browser-test.png" in output
    assert "Console errors: 1" in output
    assert "Failed requests: 0" in output
    assert "Full-page screenshot: yes" in output
    if include_reviewer_finding:
        assert output.index("Reviewer findings (advisory):") < output.index(
            "Browser validation:"
        )
    if parallel:
        assert "Parallel subagent groups:\n- post-edit: tester, reviewer" in output
    session_file = next((tmp_path / ".agent" / "sessions").glob("*.jsonl"))
    raw_session = session_file.read_text(encoding="utf-8")
    assert "No full-page screenshot was captured" in raw_session
    # Session redaction may replace the word "Console", but the raw role
    # statement remains logged rather than being rewritten by final-display cleanup.
    assert "errors were not inspected" in raw_session


def test_browser_intent_plan_mode_does_not_start_server(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"vite": "latest"}, "scripts": {"dev": "vite"}}),
        encoding="utf-8",
    )
    model = SequenceModel((ModelResponse(text="Browser validation plan."),))

    output = CodeAgent(
        AppConfig(subagents=SubagentConfig(enabled=True)),
        model_client=model,
        approval_callback=lambda request: pytest.fail(
            "Plan mode must not request server approval"
        ),
    ).run("Start the dev server and inspect the UI", tmp_path, mode="plan")

    assert len(model.calls) == 1
    assert model.calls[0]["role"] == "planner"
    assert "run_managed_browser_validation" not in model.calls[0]["tools"]
    assert "Browser validation:\n- Not run" in output
    assert "plan mode" in output
    assert not (tmp_path / ".agent").exists()


def test_browser_intent_no_command_mode_hides_managed_server_tool(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"vite": "latest"}, "scripts": {"dev": "vite"}}),
        encoding="utf-8",
    )
    model = SequenceModel(
        (
            ModelResponse(text="Plan complete."),
            ModelResponse(text="No changes needed."),
            ModelResponse(text="Browser execution is disabled."),
            ModelResponse(text="Review complete."),
        )
    )

    output = CodeAgent(
        AppConfig(subagents=SubagentConfig(enabled=True)),
        model_client=model,
        approval_callback=lambda request: pytest.fail(
            "No-command mode must not request server approval"
        ),
    ).run("Start the dev server and inspect the UI", tmp_path, mode="no-command")

    tester_call = next(call for call in model.calls if call["role"] == "tester")
    assert "run_managed_browser_validation" not in tester_call["tools"]
    assert "run_browser_validation" not in tester_call["tools"]
    assert "run_validation" not in tester_call["tools"]
    assert "Browser validation:\n- Not run" in output
    assert "no-command mode" in output


def test_browser_summary_reports_approval_denial(tmp_path):
    handler_calls = []
    registry = ToolRegistry(
        (
            Tool(
                name="run_managed_browser_validation",
                description="Run managed browser validation.",
                parameters={"type": "object"},
                handler=lambda **arguments: handler_calls.append(arguments)
                or {"ok": True},
                permission=PermissionLevel.EXECUTE,
            ),
        )
    )
    model = SequenceModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="browser-denied",
                        name="run_managed_browser_validation",
                        arguments={
                            "command": "npm run dev",
                            "url": "http://localhost:5173",
                        },
                    ),
                ),
            ),
            ModelResponse(text="The command was not approved."),
        )
    )

    output = CodeAgent(
        AppConfig(),
        model_client=model,
        approval_callback=lambda request: False,
    ).run("Start the dev server and inspect the UI", tmp_path, registry=registry)

    assert handler_calls == []
    assert "run_managed_browser_validation: not run" in output
    assert "Reason: approval denied." in output


@pytest.mark.parametrize(
    ("error", "managed_server", "expected_reason"),
    (
        (
            "Playwright is unavailable. Run python -m playwright install chromium.",
            {"started": False, "ready": False, "startup_failed": False},
            "Playwright missing",
        ),
        (
            "Managed dev server exited before the URL responded (exit code 1).",
            {"started": True, "ready": False, "startup_failed": True},
            "startup failed",
        ),
        (
            "Managed dev server did not respond within 30000 ms.",
            {"started": True, "ready": False, "startup_failed": True},
            "URL readiness timeout",
        ),
    ),
)
def test_browser_summary_reports_exact_not_run_reason(
    tmp_path,
    error,
    managed_server,
    expected_reason,
):
    registry = ToolRegistry(
        (
            Tool(
                name="run_managed_browser_validation",
                description="Run managed browser validation.",
                parameters={"type": "object"},
                handler=lambda **arguments: {
                    "ok": False,
                    "error": error,
                    "managed_server": managed_server,
                },
                permission=PermissionLevel.EXECUTE,
            ),
        )
    )
    model = SequenceModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="browser-failed",
                        name="run_managed_browser_validation",
                        arguments={
                            "command": "npm run dev",
                            "url": "http://localhost:5173",
                        },
                    ),
                ),
            ),
            ModelResponse(text="Browser validation was unavailable."),
        )
    )

    output = CodeAgent(
        AppConfig(),
        model_client=model,
        approval_callback=lambda request: True,
    ).run("Start the dev server and inspect the UI", tmp_path, registry=registry)

    assert "run_managed_browser_validation: not run" in output
    assert f"Reason: {expected_reason}." in output


def test_reviewer_prompt_defers_to_tester_browser_results():
    prompt = REVIEWER_ROLE.system_prompt_fragment

    assert "Validation status belongs to tester and tool results" in prompt
    assert "Do not make global browser-validation status claims" in prompt
    assert "Do not report this role's browser tool limitations" in prompt


def test_parallel_read_only_phases_overlap_and_merge_deterministically(
    monkeypatch,
    tmp_path,
):
    restricted_views = []
    original_restrict = SubagentRole.restrict

    def record_restricted_view(role, registry):
        view = original_restrict(role, registry)
        restricted_views.append((role.name, view))
        return view

    monkeypatch.setattr(SubagentRole, "restrict", record_restricted_view)
    model = ParallelRoleModel()
    config = AppConfig(
        subagents=SubagentConfig(enabled=True, parallel=True),
    )

    output = CodeAgent(
        config,
        model_client=model,
        approval_callback=lambda request: True,
    ).run("Update the app", tmp_path, registry=_agent_registry())

    assert "Reviewer completed first." in output
    assert "Subagents run:\n- planner\n- coder\n- tester\n- reviewer" in output
    assert "Parallel subagent groups:\n- post-edit: tester, reviewer" in output
    assert model.message_ids["tester"] != model.message_ids["reviewer"]
    assert model.tools_by_role["tester"] == {"read_file", "run_validation"}
    assert model.tools_by_role["reviewer"] == {"read_file"}
    assert model.writer_overlap is False
    views_by_role = dict(restricted_views)
    assert views_by_role["tester"] is not views_by_role["reviewer"]
    assert views_by_role["tester"].role is TESTER_ROLE
    assert views_by_role["reviewer"].role is REVIEWER_ROLE

    session_file = next((tmp_path / ".agent" / "sessions").glob("*.jsonl"))
    events = [json.loads(line) for line in session_file.read_text().splitlines()]
    lifecycle = [
        event
        for event in events
        if event["event"] in {
            "subagent_started",
            "subagent_completed",
            "subagent_error",
        }
    ]
    assert lifecycle
    assert all(
        {"role", "phase", "parallel_group_id"} <= set(event["data"])
        for event in lifecycle
    )
    post_edit_events = [
        event
        for event in lifecycle
        if event["data"]["role"] in {"tester", "reviewer"}
    ]
    assert all(
        event["data"]["parallel_group_id"] == "post-edit"
        for event in post_edit_events
    )


def test_parallel_production_roles_receive_distinct_model_clients(monkeypatch):
    agent = CodeAgent(
        AppConfig(subagents=SubagentConfig(enabled=True, parallel=True)),
    )
    created = [object(), object()]
    monkeypatch.setattr(agent, "_create_model_client", lambda: created.pop(0))

    clients = agent._model_clients_for_parallel_group(object(), 2)

    assert len(clients) == 2
    assert clients[0] is not clients[1]
    assert created == []


def test_parallel_injected_model_client_remains_supported():
    injected = object()
    agent = CodeAgent(
        AppConfig(subagents=SubagentConfig(enabled=True, parallel=True)),
        model_client=injected,
    )

    assert agent._model_clients_for_parallel_group(injected, 2) == (
        injected,
        injected,
    )


def test_parallel_failure_reports_successful_sibling(tmp_path):
    model = ParallelRoleModel(fail_role="reviewer")
    config = AppConfig(
        subagents=SubagentConfig(enabled=True, parallel=True),
    )

    output = CodeAgent(
        config,
        model_client=model,
        approval_callback=lambda request: True,
    ).run("Update the app", tmp_path, registry=_agent_registry())

    assert output.startswith("Tester completed after review.")
    assert "Subagent failures:" in output
    assert "reviewer (phase review, parallel group post-edit)" in output
    assert "RuntimeError: Subagent execution failed." in output
    assert "- tester\n- reviewer" in output

    session_file = next((tmp_path / ".agent" / "sessions").glob("*.jsonl"))
    events = [json.loads(line) for line in session_file.read_text().splitlines()]
    reviewer_errors = [
        event
        for event in events
        if event["event"] == "subagent_error"
        and event["data"]["role"] == "reviewer"
    ]
    assert len(reviewer_errors) == 1
    assert reviewer_errors[0]["data"]["phase"] == "review"
    assert reviewer_errors[0]["data"]["parallel_group_id"] == "post-edit"
    assert any(
        event["event"] == "subagent_completed"
        and event["data"]["role"] == "tester"
        for event in events
    )


def test_parallel_security_analysis_runs_with_planner_when_needed(tmp_path):
    model = ParallelRoleModel(include_security=True)
    config = AppConfig(
        subagents=SubagentConfig(enabled=True, parallel=True),
    )

    output = CodeAgent(
        config,
        model_client=model,
        approval_callback=lambda request: True,
    ).run("Update permission config", tmp_path, registry=_agent_registry())

    assert "- analysis: planner, security" in output
    assert "Security review:\nSecurity analysis complete." in output
    assert model.message_ids["planner"] != model.message_ids["security"]


def test_sensitive_existing_project_change_adds_security_subagent(tmp_path):
    model = SequenceModel(
        (
            ModelResponse(text="Plan the config change."),
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="write_config",
                        name="write_file",
                        arguments={
                            "path": "lunar_forge/config.py",
                            "content": "updated",
                        },
                    ),
                ),
            ),
            ModelResponse(text="Config updated."),
            ModelResponse(text="Validation passed."),
            ModelResponse(text="Review complete."),
            ModelResponse(text="No security findings."),
        )
    )

    output = CodeAgent(
        AppConfig(subagents=SubagentConfig(enabled=True)),
        model_client=model,
        approval_callback=lambda request: True,
    ).run("Update config loading", tmp_path, registry=_agent_registry())

    assert "Security review:\nNo security findings." in output
    assert output.index("- reviewer") < output.index("- security")
    assert [call["role"] for call in model.calls][-1] == "security"


def test_subagent_plan_mode_runs_only_planner_without_writing(tmp_path):
    model = SequenceModel((ModelResponse(text="Read-only plan."),))

    output = CodeAgent(
        AppConfig(subagents=SubagentConfig(enabled=True)),
        model_client=model,
    ).run("Plan a change", tmp_path, mode="plan")

    assert len(model.calls) == 1
    assert model.calls[0]["role"] == "planner"
    assert "Subagents run:\n- planner" in output
    assert not (tmp_path / ".agent").exists()


def test_new_project_subagents_use_scaffolder_tester_reviewer(tmp_path):
    result = run_new_project(
        "Build a simple marketing website",
        tmp_path,
        mode="yes",
        subagents_enabled=True,
    )

    assert result["ok"] is True
    assert result["subagents_run"] == ["scaffolder", "tester", "reviewer"]
    assert result["review"] == [
        "Reviewer confirmed all 3 declared starter files are readable."
    ]
    assert "Subagents run:\n- scaffolder\n- tester\n- reviewer" in (
        format_new_project_result(result)
    )
    assert "Parallel subagent groups:\n- None" in format_new_project_result(result)


def test_new_project_parallelizes_only_test_and_review(tmp_path):
    result = run_new_project(
        "Build a simple marketing website",
        tmp_path,
        mode="yes",
        subagents_enabled=True,
        subagents_parallel=True,
    )

    assert result["ok"] is True
    assert result["subagents_run"] == ["scaffolder", "tester", "reviewer"]
    assert result["parallel_subagent_groups"] == [
        {
            "parallel_group_id": "post-edit",
            "roles": ["tester", "reviewer"],
        }
    ]
    formatted = format_new_project_result(result)
    assert "Parallel subagent groups:\n- post-edit: tester, reviewer" in formatted

    session_file = next((tmp_path / ".agent" / "sessions").glob("*.jsonl"))
    events = [json.loads(line) for line in session_file.read_text().splitlines()]
    starts = {
        event["data"]["role"]: event["data"]["parallel_group_id"]
        for event in events
        if event["event"] == "subagent_started"
    }
    assert starts == {
        "scaffolder": None,
        "tester": "post-edit",
        "reviewer": "post-edit",
    }


def test_new_project_subagent_plan_mode_remains_no_write(tmp_path):
    def unexpected_approval(request):
        raise AssertionError("Plan mode must not request approval")

    result = run_new_project(
        "Build a Vite React website",
        tmp_path,
        mode="plan",
        approval_callback=unexpected_approval,
        subagents_enabled=True,
    )

    assert result["ok"] is True
    assert result["subagents_run"] == []
    assert result["planned_subagents"] == ["scaffolder", "tester", "reviewer"]
    assert list(tmp_path.iterdir()) == []


def test_new_project_subagents_preserve_dependency_approval(tmp_path):
    requests = []

    def approve_writes_only(request):
        requests.append(request)
        return request.permission is PermissionLevel.WRITE

    result = run_new_project(
        "Build a Vite React website",
        tmp_path,
        mode="yes",
        approval_callback=approve_writes_only,
        subagents_enabled=True,
    )

    assert result["ok"] is False
    assert result["subagents_run"] == ["scaffolder"]
    assert requests[-1].permission is PermissionLevel.EXECUTE
    assert "npm install" in requests[-1].description
    assert result["command_results"][0]["permission_denied"] is True


def _registry_with_all_known_tools() -> tuple[ToolRegistry, list[str]]:
    calls: list[str] = []

    def make_handler(tool_name: str):
        def handler(**arguments):
            calls.append(tool_name)
            return {"ok": True, "tool": tool_name}

        return handler

    registry = ToolRegistry(
        Tool(
            name=tool_name,
            description=f"Synthetic {tool_name} tool.",
            parameters={"type": "object", "additionalProperties": True},
            handler=make_handler(tool_name),
        )
        for tool_name in BUILTIN_SUBAGENT_TOOLS
    )
    return registry, calls


class SequenceModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def complete(self, messages, tools=None):
        system_prompt = str(messages[0]["content"])
        role_line = next(
            line for line in system_prompt.splitlines() if line.startswith("Active subagent role:")
        ) if "Active subagent role:" in system_prompt else "Active subagent role: single"
        self.calls.append(
            {
                "role": role_line.partition(":")[2].strip(),
                "tools": {
                    schema["function"]["name"] for schema in (tools or [])
                },
            }
        )
        return self.responses.pop(0)


class ParallelRoleModel:
    def __init__(self, *, fail_role=None, include_security=False):
        self.fail_role = fail_role
        self.calls: list[str] = []
        self.message_ids: dict[str, int] = {}
        self.tools_by_role: dict[str, set[str]] = {}
        self.writer_overlap = False
        self._active_roles: set[str] = set()
        self._lock = Lock()
        self._post_edit_barrier = Barrier(2)
        self._analysis_barrier = Barrier(2) if include_security else None

    def complete(self, messages, tools=None):
        system_prompt = str(messages[0]["content"])
        role_line = next(
            line
            for line in system_prompt.splitlines()
            if line.startswith("Active subagent role:")
        )
        role = role_line.partition(":")[2].strip()
        with self._lock:
            if role in {"coder", "scaffolder"} and self._active_roles:
                self.writer_overlap = True
            if role not in {"coder", "scaffolder"} and any(
                active in {"coder", "scaffolder"}
                for active in self._active_roles
            ):
                self.writer_overlap = True
            self._active_roles.add(role)
            self.calls.append(role)
            self.message_ids[role] = id(messages)
            self.tools_by_role[role] = {
                schema["function"]["name"] for schema in (tools or [])
            }
        try:
            if role in {"tester", "reviewer"}:
                self._post_edit_barrier.wait(timeout=3)
            if role in {"planner", "security"} and self._analysis_barrier is not None:
                self._analysis_barrier.wait(timeout=3)
            if role == self.fail_role:
                raise RuntimeError(f"synthetic {role} failure")
            if role == "tester":
                time.sleep(0.05)
            return ModelResponse(
                text={
                    "planner": "Plan complete.",
                    "security": "Security analysis complete.",
                    "coder": "Implementation complete.",
                    "tester": "Tester completed after review.",
                    "reviewer": "Reviewer completed first.",
                }[role]
            )
        finally:
            with self._lock:
                self._active_roles.remove(role)


def _agent_registry() -> ToolRegistry:
    def read_handler(**arguments):
        return {"ok": True, "path": arguments.get("path", "README.md")}

    def write_handler(**arguments):
        return {"ok": True, "path": arguments["path"]}

    def validation_handler(**arguments):
        return {"ok": True, "results": []}

    return ToolRegistry(
        (
            Tool(
                name="read_file",
                description="Read a file.",
                parameters={"type": "object"},
                handler=read_handler,
            ),
            Tool(
                name="write_file",
                description="Write a file.",
                parameters={"type": "object"},
                handler=write_handler,
                permission=PermissionLevel.WRITE,
            ),
            Tool(
                name="run_validation",
                description="Run validation.",
                parameters={"type": "object"},
                handler=validation_handler,
                permission=PermissionLevel.EXECUTE,
            ),
        )
    )
