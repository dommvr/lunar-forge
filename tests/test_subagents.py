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
    "planner": {"list_dir", "read_file", "grep", "glob", "detect_project"},
    "coder": {
        "list_dir",
        "read_file",
        "grep",
        "glob",
        "create_dir",
        "write_file",
        "edit_file",
    },
    "reviewer": {"read_file", "grep", "glob"},
    "tester": {"run_command", "run_validation", "read_file", "grep"},
    "security": {"read_file", "grep", "glob"},
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


def test_parallel_read_only_phases_overlap_and_merge_deterministically(tmp_path):
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
