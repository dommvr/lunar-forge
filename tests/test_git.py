import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import lunar_forge.agent as agent_module
import lunar_forge.runtime.git as git_module
from lunar_forge.agent import CodeAgent
from lunar_forge.config import AppConfig, SubagentConfig
from lunar_forge.model_clients import ModelResponse, ToolCall
from lunar_forge.permissions import PermissionLevel
from lunar_forge.runtime.git import (
    create_git_commit,
    format_git_commit_result,
    git_diff,
    git_status,
    list_changed_files,
    prepare_git_commit,
)
from lunar_forge.tools.registry import (
    Tool,
    ToolRegistry,
    create_tool_registry,
)


class SequenceModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, messages, tools=None):
        return self.responses.pop(0)


def _result(args, *, ok=True, stdout="", stderr=""):
    return git_module._GitCommandResult(
        ok=ok,
        args=tuple(args),
        exit_code=0 if ok else 1,
        stdout=stdout,
        stderr=stderr,
        truncated=False,
        error=None if ok else stderr or "Git failed.",
    )


def _mock_git(
    monkeypatch,
    root,
    status_output,
    *,
    diff_output="1 file changed",
    detail_output="",
    fail_on=None,
):
    calls = []

    def fake_run_git(cwd, arguments, timeout_ms):
        args = tuple(arguments)
        calls.append((Path(cwd), args, timeout_ms))
        if args == ("rev-parse", "--show-toplevel"):
            return _result(args, stdout=f"{root}\n")
        if args == (
            "status",
            "--short",
            "--untracked-files=all",
            "-z",
            "--",
            ".",
        ):
            return _result(args, stdout=status_output)
        if args[:4] == ("diff", "--stat", "--no-ext-diff", "HEAD"):
            return _result(args, stdout=f"{diff_output}\n")
        if args and args[0] == "diff" and "--stat" in args:
            return _result(args, stdout=f"{diff_output}\n")
        if args and args[0] == "diff" and "--unified=3" in args:
            return _result(args, stdout=detail_output)
        if args[:2] == ("add", "--"):
            if fail_on == "add":
                return _result(args, ok=False, stderr="git add failed")
            return _result(args)
        if args[:2] == ("commit", "--only"):
            if fail_on == "commit":
                return _result(args, ok=False, stderr="git commit failed")
            return _result(args, stdout="commit created\n")
        if args == ("rev-parse", "HEAD"):
            return _result(args, stdout="abc123def456\n")
        raise AssertionError(f"Unexpected Git arguments: {args}")

    monkeypatch.setattr(git_module, "_run_git", fake_run_git)
    return calls


def test_status_outside_repository_fails_clearly(monkeypatch, tmp_path):
    def fake_run_git(cwd, arguments, timeout_ms):
        return _result(arguments, ok=False, stderr="not a git repository")

    monkeypatch.setattr(git_module, "_run_git", fake_run_git)

    result = git_status(tmp_path)

    assert result["ok"] is False
    assert result["status_short"] == []
    assert result["error"] == "Project is not inside a Git repository."


def test_status_in_repository_returns_short_status(monkeypatch, tmp_path):
    _mock_git(
        monkeypatch,
        tmp_path,
        " M README.md\0?? src/new.py\0",
    )

    result = git_status(tmp_path)

    assert result["ok"] is True
    assert result["clean"] is False
    assert result["status_short"] == [" M README.md", "?? src/new.py"]
    assert result["entries"] == [
        {"status": " M", "path": "README.md"},
        {"status": "??", "path": "src/new.py"},
    ]


def test_clean_repo_status_is_compact(monkeypatch, tmp_path):
    calls = _mock_git(monkeypatch, tmp_path, "")

    result = git_status(tmp_path)

    assert result["ok"] is True
    assert result["clean"] is True
    assert result["status_short"] == []
    assert result["modified_files"] == []
    assert result["staged_files"] == []
    assert result["untracked_files"] == []
    assert result["counts"] == {
        "changed": 0,
        "modified": 0,
        "staged": 0,
        "untracked": 0,
        "excluded": 0,
    }
    assert [args for _, args, _ in calls] == [
        ("rev-parse", "--show-toplevel"),
        (
            "status",
            "--short",
            "--untracked-files=all",
            "-z",
            "--",
            ".",
        ),
    ]


def test_status_is_confined_to_a_nested_project_root(monkeypatch, tmp_path):
    project = tmp_path / "packages" / "app"
    project.mkdir(parents=True)
    calls = []

    def fake_run_git(cwd, arguments, timeout_ms):
        args = tuple(arguments)
        calls.append((Path(cwd), args, timeout_ms))
        if args == ("rev-parse", "--show-toplevel"):
            return _result(args, stdout=f"{tmp_path}\n")
        if args == (
            "status",
            "--short",
            "--untracked-files=all",
            "-z",
            "--",
            "packages/app",
        ):
            return _result(
                args,
                stdout=(
                    " M packages/app/app.py\0"
                    " M packages/sibling/private.pem\0"
                ),
            )
        raise AssertionError(f"Unexpected Git arguments: {args}")

    monkeypatch.setattr(git_module, "_run_git", fake_run_git)

    result = git_status(project)

    assert result["ok"] is True
    assert result["status_short"] == [" M packages/app/app.py"]
    assert result["modified_files"] == ["packages/app/app.py"]
    assert "private.pem" not in json.dumps(result)
    assert calls[-1][0] == tmp_path


def test_dirty_repo_status_classifies_staged_untracked_and_excluded(
    monkeypatch,
    tmp_path,
):
    _mock_git(
        monkeypatch,
        tmp_path,
        "M  staged.py\0 M work.py\0?? new.py\0?? .env\0",
    )

    result = git_status(tmp_path)

    assert result["ok"] is True
    assert result["clean"] is False
    assert result["modified_files"] == ["staged.py", "work.py"]
    assert result["staged_files"] == ["staged.py"]
    assert result["untracked_files"] == ["new.py", ".env"]
    assert result["excluded_files"] == [".env"]
    assert result["counts"] == {
        "changed": 4,
        "modified": 2,
        "staged": 1,
        "untracked": 2,
        "excluded": 1,
    }


def test_commit_requires_approval_even_in_yes_mode(monkeypatch, tmp_path):
    calls = _mock_git(monkeypatch, tmp_path, " M README.md\0")
    approvals = []

    result = create_git_commit(
        tmp_path,
        "Update README",
        session_files=("README.md",),
        mode="yes",
        approval_callback=lambda request: approvals.append(request) or False,
    )

    assert result["ok"] is False
    assert result["permission_denied"] is True
    assert result["approved"] is False
    assert len(approvals) == 1
    assert approvals[0].tool_name == "git_commit"
    assert approvals[0].permission is PermissionLevel.EXECUTE
    assert "Git status --short" in approvals[0].description
    assert "Files changed by LunarForge" in approvals[0].description
    assert "Proposed commit message: Update README" in approvals[0].description
    assert "README.md" in approvals[0].description
    assert not any(args[:1] in {("add",), ("commit",)} for _, args, _ in calls)


@pytest.mark.parametrize(
    ("mode", "message"),
    (("plan", "Plan mode blocks Git commits"), ("no-command", "blocks Git")),
)
def test_plan_and_no_command_block_commit_without_git_execution(
    monkeypatch,
    tmp_path,
    mode,
    message,
):
    def unexpected(*args, **kwargs):
        raise AssertionError("Blocked modes must not execute Git")

    monkeypatch.setattr(git_module, "_run_git", unexpected)

    result = create_git_commit(tmp_path, "Blocked commit", mode=mode)

    assert result["ok"] is False
    assert result["approved"] is False
    assert message in result["error"]


def test_no_command_blocks_status_without_git_execution(monkeypatch, tmp_path):
    def unexpected(*args, **kwargs):
        raise AssertionError("No-command mode must not execute Git")

    monkeypatch.setattr(git_module, "_run_git", unexpected)

    result = git_status(tmp_path, mode="no-command")

    assert result["ok"] is False
    assert "No-command mode" in result["error"]


def test_git_diff_is_bounded_and_uses_only_read_only_git_commands(
    monkeypatch,
    tmp_path,
):
    detail = "\n".join(f"+line {index}" for index in range(20))
    calls = _mock_git(
        monkeypatch,
        tmp_path,
        " M app.py\0?? .env\0?? dist/bundle.js\0",
        diff_output="app.py | 20 ++++++++++++++++++++",
        detail_output=detail,
    )

    result = git_diff(tmp_path, max_lines=5)

    assert result["ok"] is True
    assert result["files"] == ["app.py"]
    assert result["excluded_files"] == []
    assert result["untracked_files"] == [".env", "dist/bundle.js"]
    assert result["summary"] == "app.py | 20 ++++++++++++++++++++"
    assert result["line_count"] == 20
    assert result["max_lines"] == 5
    assert result["truncated"] is True
    assert result["diff"].startswith("+line 0\n+line 1")
    assert "line 6" not in result["diff"]
    arguments = [args for _, args, _ in calls]
    assert all(args[0] in {"rev-parse", "status", "diff"} for args in arguments)
    assert not any(args[0] in {"add", "commit"} for args in arguments)
    diff_arguments = [args for args in arguments if args[0] == "diff"]
    assert len(diff_arguments) == 2
    assert all("app.py" in args for args in diff_arguments)
    assert all(".env" not in args for args in diff_arguments)
    assert all("dist/bundle.js" not in args for args in diff_arguments)


def test_git_diff_blocks_excluded_file_content(monkeypatch, tmp_path):
    calls = _mock_git(monkeypatch, tmp_path, " M .env\0")

    result = git_diff(tmp_path, path=".env")

    assert result["ok"] is False
    assert result["excluded_files"] == [".env"]
    assert "excluded runtime, generated, or secret-looking" in result["error"]
    assert not any(args[0] == "diff" for _, args, _ in calls)


def test_git_diff_supports_staged_changes(monkeypatch, tmp_path):
    calls = _mock_git(
        monkeypatch,
        tmp_path,
        "M  staged.py\0 M unstaged.py\0",
        diff_output="staged.py | 1 +",
        detail_output="diff --git a/staged.py b/staged.py\n+staged\n",
    )

    result = git_diff(tmp_path, staged=True)

    assert result["ok"] is True
    assert result["staged"] is True
    assert result["files"] == ["staged.py"]
    diff_arguments = [
        args for _, args, _ in calls if args and args[0] == "diff"
    ]
    assert len(diff_arguments) == 2
    assert all("--cached" in args for args in diff_arguments)
    assert all("unstaged.py" not in args for args in diff_arguments)


def test_git_diff_and_changed_files_fail_outside_repository(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        git_module,
        "_run_git",
        lambda cwd, arguments, timeout_ms: _result(
            arguments,
            ok=False,
            stderr="not a git repository",
        ),
    )

    diff_result = git_diff(tmp_path)
    changed_result = list_changed_files(tmp_path, source="git")

    assert diff_result == {
        "ok": False,
        "error": "Project is not inside a Git repository.",
    }
    assert changed_result["ok"] is False
    assert changed_result["source"] == "git"
    assert changed_result["error"] == "Project is not inside a Git repository."


def test_list_changed_files_combines_session_and_git_state(
    monkeypatch,
    tmp_path,
):
    _mock_git(
        monkeypatch,
        tmp_path,
        (
            " M app.py\0M  staged.py\0?? new.py\0?? .env\0"
            "?? dist/bundle.js\0 M unrelated.py\0"
        ),
    )

    result = list_changed_files(
        tmp_path,
        source="both",
        session_files=("app.py", "new.py", ".env", "session_only.py"),
    )
    by_path = {item["path"]: item for item in result["files"]}

    assert result["ok"] is True
    assert result["staged_files"] == ["staged.py"]
    assert result["untracked_files"] == ["new.py", ".env", "dist/bundle.js"]
    assert result["excluded_files"] == [".env", "dist/bundle.js"]
    assert result["commit_candidates"] == ["app.py", "new.py"]
    assert by_path["app.py"] == {
        "path": "app.py",
        "session_changed": True,
        "git_changed": True,
        "git_modified": True,
        "staged": False,
        "untracked": False,
        "status": " M",
        "excluded": False,
        "commit_candidate": True,
    }
    assert by_path["new.py"]["session_changed"] is True
    assert by_path["new.py"]["untracked"] is True
    assert by_path["staged.py"]["staged"] is True
    assert by_path["staged.py"]["commit_candidate"] is False
    assert by_path["session_only.py"]["git_changed"] is False
    assert by_path["session_only.py"]["commit_candidate"] is False
    assert by_path[".env"]["excluded"] is True


def test_session_changed_files_work_without_git_or_commands(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        git_module,
        "_run_git",
        lambda *args, **kwargs: pytest.fail("Git execution was not expected"),
    )

    result = list_changed_files(
        tmp_path,
        source="session",
        session_files=("app.py", "app.py", "../../../outside.py"),
        mode="no-command",
    )

    assert result["ok"] is True
    assert result["repository_root"] is None
    assert result["session_files"] == ["app.py"]
    assert result["git_files"] == []
    assert result["commit_candidates"] == ["app.py"]


def test_registry_tracks_successful_session_file_mutations(tmp_path):
    registry = create_tool_registry(tmp_path, mode="yes")

    write_result = registry.execute(
        "write_file",
        {"path": "created.py", "content": "value = 1\n"},
    )
    changed_result = registry.execute(
        "list_changed_files",
        {"source": "session"},
    )

    assert write_result["ok"] is True
    assert registry.session_changed_files() == ("created.py",)
    assert changed_result["ok"] is True
    assert changed_result["session_files"] == ["created.py"]
    assert changed_result["files"] == [
        {
            "path": "created.py",
            "session_changed": True,
            "git_changed": False,
            "git_modified": False,
            "staged": False,
            "untracked": False,
            "status": None,
            "excluded": False,
            "commit_candidate": True,
        }
    ]


def test_generated_runtime_and_secret_files_are_excluded(monkeypatch, tmp_path):
    status_output = "\0".join(
        (
            " M keep.py",
            "?? .agent/session.jsonl",
            "?? .agent/checkpoints/2026/note.txt",
            "?? .agent/artifacts/browser/full-page.png",
            "?? node_modules/pkg/index.js",
            "?? .venv/pyvenv.cfg",
            "?? venv/pyvenv.cfg",
            "?? pkg/__pycache__/app.pyc",
            "?? frontend/.next/cache/webpack.bin",
            "?? frontend/.nuxt/server/index.mjs",
            "?? .pytest_cache/v/cache/nodeids",
            "?? htmlcov/index.html",
            "?? dist/app.js",
            "?? build/output.txt",
            "?? coverage/index.html",
            "?? .env",
            "?? .npmrc",
            "?? deploy/credentials.json",
            "?? deploy/secrets.toml",
            "?? certs/private.pem",
            "?? certs/signing.p12",
            "",
        )
    )
    _mock_git(monkeypatch, tmp_path, status_output)

    result = prepare_git_commit(tmp_path)

    assert result["ok"] is True
    assert result["proposed_files"] == ["keep.py"]
    assert set(result["excluded_files"]) == {
        ".agent/session.jsonl",
        ".agent/checkpoints/2026/note.txt",
        ".agent/artifacts/browser/full-page.png",
        "node_modules/pkg/index.js",
        ".venv/pyvenv.cfg",
        "venv/pyvenv.cfg",
        "pkg/__pycache__/app.pyc",
        "frontend/.next/cache/webpack.bin",
        "frontend/.nuxt/server/index.mjs",
        ".pytest_cache/v/cache/nodeids",
        "htmlcov/index.html",
        "dist/app.js",
        "build/output.txt",
        "coverage/index.html",
        ".env",
        ".npmrc",
        "deploy/credentials.json",
        "deploy/secrets.toml",
        "certs/private.pem",
        "certs/signing.p12",
    }


def test_unrelated_dirty_files_are_not_committed_by_default(
    monkeypatch,
    tmp_path,
):
    calls = _mock_git(
        monkeypatch,
        tmp_path,
        " M current.py\0 M unrelated.py\0?? .agent/session.jsonl\0",
    )

    result = create_git_commit(
        tmp_path,
        "Update current file",
        session_files=("current.py",),
        approval_callback=lambda request: True,
    )

    assert result["ok"] is True
    assert result["commit_hash"] == "abc123def456"
    assert result["committed_files"] == ["current.py"]
    assert result["unrelated_files"] == ["unrelated.py"]
    assert result["excluded_files"] == [".agent/session.jsonl"]
    add_args = next(args for _, args, _ in calls if args[0] == "add")
    commit_args = next(args for _, args, _ in calls if args[0] == "commit")
    assert add_args == ("add", "--", "current.py")
    assert commit_args[-2:] == ("--", "current.py")
    assert "unrelated.py" not in commit_args


def test_denied_commit_formats_full_proposal_and_clear_result(monkeypatch, tmp_path):
    _mock_git(
        monkeypatch,
        tmp_path,
        " M current.py\0 M unrelated.py\0?? .agent/artifacts/browser/page.png\0",
        diff_output="current.py | 2 +-",
    )

    result = create_git_commit(
        tmp_path,
        "Update current file",
        session_files=("current.py",),
        approval_callback=lambda request: False,
    )
    formatted = format_git_commit_result(result)

    assert "Files changed by LunarForge (proposed for commit):\n- current.py" in formatted
    assert "Unrelated dirty files (not included):\n- unrelated.py" in formatted
    assert ".agent/artifacts/browser/page.png" in formatted
    assert "Bounded diff summary:\ncurrent.py | 2 +-" in formatted
    assert "Proposed commit message: Update current file" in formatted
    assert formatted.endswith("- Commit not created: approval denied")


@pytest.mark.parametrize(
    ("fail_on", "result_code", "unexpected_command"),
    (
        ("add", "git_add_failed", "commit"),
        ("commit", "git_commit_failed", "rev-parse-head"),
    ),
)
def test_git_mutation_failures_stop_without_reporting_a_commit(
    monkeypatch,
    tmp_path,
    fail_on,
    result_code,
    unexpected_command,
):
    calls = _mock_git(
        monkeypatch,
        tmp_path,
        " M current.py\0",
        fail_on=fail_on,
    )

    result = create_git_commit(
        tmp_path,
        "Update current file",
        session_files=("current.py",),
        approval_callback=lambda request: True,
    )
    arguments = [args for _, args, _ in calls]

    assert result["ok"] is False
    assert result["result_code"] == result_code
    assert "commit_hash" not in result
    if unexpected_command == "commit":
        assert not any(args[0] == "commit" for args in arguments)
    else:
        assert ("rev-parse", "HEAD") not in arguments
    assert format_git_commit_result(result).endswith(
        f"- Commit not created: git {fail_on} failed"
    )


def test_clean_commit_request_reports_no_changes_without_a_proposal(
    monkeypatch,
    tmp_path,
):
    _mock_git(monkeypatch, tmp_path, "")

    result = create_git_commit(
        tmp_path,
        "Nothing to commit",
        approval_callback=lambda request: pytest.fail("Approval was not expected"),
    )
    formatted = format_git_commit_result(result)

    assert result["result_code"] == "no_changes"
    assert formatted == "- Commit not created: no changes"
    assert "Git status --short" not in formatted


def test_outside_repository_commit_has_stable_final_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(
        git_module,
        "_run_git",
        lambda cwd, arguments, timeout_ms: _result(
            arguments,
            ok=False,
            stderr="not a git repository",
        ),
    )

    result = create_git_commit(tmp_path, "Unavailable commit")

    assert result["result_code"] == "not_repository"
    assert format_git_commit_result(result) == "- Commit not created: not a repo"


def test_git_subprocess_uses_resolver_and_shell_false(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(
        git_module,
        "resolve_executable",
        lambda executable, cwd: "C:/Git/bin/git.exe",
    )

    def fake_run(arguments, **kwargs):
        captured["arguments"] = arguments
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = git_module._run_git(tmp_path, ("status", "--short"), 1_000)

    assert result.ok is True
    assert captured["arguments"] == ["C:/Git/bin/git.exe", "status", "--short"]
    assert captured["cwd"] == tmp_path
    assert captured["shell"] is False
    assert captured["check"] is False


def test_agent_never_prepares_a_commit_without_opt_in(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agent_module,
        "create_git_commit",
        lambda *args, **kwargs: pytest.fail("Git must remain opt-in"),
    )

    output = CodeAgent(
        AppConfig(),
        model_client=SequenceModel((ModelResponse(text="Task complete."),)),
    ).run("Inspect the project", tmp_path)

    assert "Git:" not in output


def test_failed_validation_prevents_auto_offered_commit(monkeypatch, tmp_path):
    model = SequenceModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(id="validate", name="run_validation", arguments={}),
                ),
            ),
            ModelResponse(text="Validation failed."),
        )
    )
    registry = ToolRegistry(
        (
            Tool(
                name="run_validation",
                description="Run validation.",
                parameters={"type": "object"},
                handler=lambda: {
                    "ok": False,
                    "results": [{"ok": False, "exit_code": 1}],
                    "error": "Tests failed.",
                },
                permission=PermissionLevel.EXECUTE,
            ),
        )
    )

    def unexpected_commit(*args, **kwargs):
        raise AssertionError("A failed validation must suppress the commit offer")

    monkeypatch.setattr(agent_module, "create_git_commit", unexpected_commit)

    output = CodeAgent(
        AppConfig(),
        model_client=model,
        approval_callback=lambda request: True,
    ).run(
        "Update the project",
        tmp_path,
        registry=registry,
        offer_commit=True,
    )

    assert "Git:\n- Commit not created: validation failed" in output
    session_file = next((tmp_path / ".agent" / "sessions").glob("*.jsonl"))
    events = [json.loads(line) for line in session_file.read_text().splitlines()]
    skipped = [event for event in events if event["event"] == "git_commit_skipped"]
    assert len(skipped) == 1
    results = [event for event in events if event["event"] == "git_commit_result"]
    assert results[0]["data"]["result_code"] == "validation_failed"


def test_commit_mention_alone_does_not_override_failed_validation(
    monkeypatch,
    tmp_path,
):
    evidence = agent_module.ValidationEvidence(validation_failed=True)
    agent = CodeAgent(AppConfig())
    monkeypatch.setattr(
        agent_module,
        "create_git_commit",
        lambda *args, **kwargs: pytest.fail("Commit proposal was not expected"),
    )

    output = agent._finalize_git_commit_offer(
        "Validation failed.",
        request="Update the project and commit it.",
        root=tmp_path,
        mode="default",
        session=None,
        changed_files=("app.py",),
        validation_evidence=evidence,
        offer_commit=True,
        commit_message=None,
    )

    assert output.endswith("Git:\n- Commit not created: validation failed")


def test_explicit_failed_validation_override_allows_commit_proposal(
    monkeypatch,
    tmp_path,
):
    evidence = agent_module.ValidationEvidence(validation_failed=True)
    captured = {}

    def fake_commit(project_root, message, **kwargs):
        captured.update(project_root=project_root, message=message, **kwargs)
        return {
            "ok": False,
            "result_code": "approval_denied",
            "repository_root": str(tmp_path),
            "project_root": str(tmp_path),
            "status_short": [" M app.py"],
            "diff_summary": "app.py | 1 +",
            "proposed_files": ["app.py"],
            "unrelated_files": [],
            "excluded_files": [],
            "session_scoped": True,
            "message": "Commit despite failure",
            "approved": False,
            "approval_requested": True,
            "permission_denied": True,
            "error": "Approval denied by user.",
        }

    monkeypatch.setattr(agent_module, "create_git_commit", fake_commit)

    output = CodeAgent(AppConfig())._finalize_git_commit_offer(
        "Validation failed.",
        request="Commit even if validation fails.",
        root=tmp_path,
        mode="default",
        session=None,
        changed_files=("app.py",),
        validation_evidence=evidence,
        offer_commit=True,
        commit_message="Commit despite failure",
    )

    assert captured["session_files"] == ("app.py",)
    assert "Proposed commit message: Commit despite failure" in output
    assert output.endswith("- Commit not created: approval denied")


def test_agent_with_no_changed_files_does_not_prepare_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agent_module,
        "create_git_commit",
        lambda *args, **kwargs: pytest.fail("Commit proposal was not expected"),
    )
    output = CodeAgent(
        AppConfig(),
        model_client=SequenceModel((ModelResponse(text="No changes were needed."),)),
    ).run("Inspect the project", tmp_path, offer_commit=True)

    assert "Git:\n- Commit not created: no changes" in output
    session_file = next((tmp_path / ".agent" / "sessions").glob("*.jsonl"))
    events = [json.loads(line) for line in session_file.read_text().splitlines()]
    assert not any(event["event"] == "git_commit_proposal" for event in events)
    result = next(event for event in events if event["event"] == "git_commit_result")
    assert result["data"]["result_code"] == "no_changes"


def test_agent_logs_git_proposal_approval_and_hash(monkeypatch, tmp_path):
    model = SequenceModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="write",
                        name="write_file",
                        arguments={"path": "app.py", "content": "updated"},
                    ),
                ),
            ),
            ModelResponse(text="Changed files:\n- app.py"),
        )
    )
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
    captured = {}

    def fake_commit(project_root, message, **kwargs):
        captured.update(project_root=project_root, message=message, **kwargs)
        return {
            "ok": True,
            "repository_root": str(tmp_path),
            "project_root": str(tmp_path),
            "status_short": [" M app.py", "?? .agent/session.jsonl"],
            "diff_summary": "app.py | 1 +",
            "proposed_files": ["app.py"],
            "unrelated_files": [],
            "excluded_files": [".agent/session.jsonl"],
            "session_scoped": True,
            "message": "Update app",
            "approved": True,
            "approval_requested": True,
            "approval_reason": "Approved by user.",
            "result_code": "commit_created",
            "commit_hash": "abc123",
            "committed_files": ["app.py"],
        }

    monkeypatch.setattr(agent_module, "create_git_commit", fake_commit)

    output = CodeAgent(
        AppConfig(),
        model_client=model,
        approval_callback=lambda request: True,
    ).run(
        "Update app",
        tmp_path,
        registry=registry,
        offer_commit=True,
    )

    assert changed_file_calls == ["session"]
    assert captured["session_files"] == ("app.py",)
    assert "Git:\n" in output
    assert "Files changed by LunarForge (proposed for commit):\n- app.py" in output
    assert "Proposed commit message: Update app" in output
    assert "- Commit created: abc123" in output
    session_file = next((tmp_path / ".agent" / "sessions").glob("*.jsonl"))
    events = [json.loads(line) for line in session_file.read_text().splitlines()]
    names = [event["event"] for event in events]
    assert "git_status_summary" in names
    assert "git_commit_proposal" in names
    assert "git_commit_approval" in names
    assert "git_commit_created" in names
    assert "git_commit_result" in names
    proposal = next(event for event in events if event["event"] == "git_commit_proposal")
    assert proposal["data"]["message"] == "Update app"
    result = next(event for event in events if event["event"] == "git_commit_result")
    assert result["data"]["commit_hash"] == "abc123"


def test_agent_commit_flow_preserves_executed_validation_summary(
    monkeypatch,
    tmp_path,
):
    validation_command = "python -B -m compileall lunar_forge"
    model = SequenceModel(
        (
            ModelResponse(text="Plan: update app.py, validate, then offer a commit."),
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="write",
                        name="write_file",
                        arguments={"path": "app.py", "content": "updated"},
                    ),
                ),
            ),
            ModelResponse(text="Implemented app.py."),
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(id="validate", name="run_validation", arguments={}),
                ),
            ),
            ModelResponse(text="Validation passed."),
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
                handler=lambda: {
                    "ok": True,
                    "commands": [validation_command],
                    "results": [
                        {
                            "ok": True,
                            "command": validation_command,
                            "exit_code": 0,
                        }
                    ],
                },
                permission=PermissionLevel.EXECUTE,
            ),
        )
    )

    def fake_commit(project_root, message, **kwargs):
        return {
            "ok": True,
            "repository_root": str(tmp_path),
            "project_root": str(tmp_path),
            "status_short": [" M app.py"],
            "diff_summary": "app.py | 1 +",
            "proposed_files": ["app.py"],
            "unrelated_files": [],
            "excluded_files": [],
            "session_scoped": True,
            "message": message,
            "approved": True,
            "approval_requested": True,
            "approval_reason": "Approved by user.",
            "result_code": "commit_created",
            "commit_hash": "abc123",
            "committed_files": ["app.py"],
        }

    monkeypatch.setattr(agent_module, "create_git_commit", fake_commit)

    output = CodeAgent(
        AppConfig(subagents=SubagentConfig(enabled=True)),
        model_client=model,
        approval_callback=lambda request: True,
    ).run(
        "Update app",
        tmp_path,
        registry=registry,
        offer_commit=True,
        commit_message="Update app",
    )

    assert (
        f"Validation:\n- {validation_command}: passed "
        "(authoritative tool result; exit code 0)"
    ) in output
    assert (
        f"Commands run:\n- {validation_command}: passed "
        "(authoritative tool result; via run_validation; exit code 0)"
    ) in output
    assert "Not run (review-only phase)" not in output
    assert "Commands run:\n- None" not in output
    assert "Proposed commit message: Update app" in output
    assert "- Commit created: abc123" in output


def test_agent_logs_denied_commit_proposal_and_terminal_result(monkeypatch, tmp_path):
    session = agent_module.create_session_logger(tmp_path)
    monkeypatch.setattr(
        agent_module,
        "create_git_commit",
        lambda *args, **kwargs: {
            "ok": False,
            "result_code": "approval_denied",
            "repository_root": str(tmp_path),
            "project_root": str(tmp_path),
            "status_short": [" M app.py", "?? .agent/sessions/session.jsonl"],
            "diff_summary": "app.py | 1 +",
            "proposed_files": ["app.py"],
            "unrelated_files": [],
            "excluded_files": [".agent/sessions/session.jsonl"],
            "session_scoped": True,
            "message": "Update app",
            "approved": False,
            "approval_requested": True,
            "approval_reason": "Approval denied by user.",
            "permission_denied": True,
            "error": "Approval denied by user.",
        },
    )

    output = CodeAgent(AppConfig())._finalize_git_commit_offer(
        "Task complete.",
        request="Update app",
        root=tmp_path,
        mode="default",
        session=session,
        changed_files=("app.py",),
        validation_evidence=agent_module.ValidationEvidence(),
        offer_commit=True,
        commit_message="Update app",
    )

    events = [json.loads(line) for line in session.path.read_text().splitlines()]
    approval = next(
        event for event in events if event["event"] == "git_commit_approval"
    )
    result = next(event for event in events if event["event"] == "git_commit_result")
    assert "- Commit not created: approval denied" in output
    assert approval["data"]["approved"] is False
    assert result["data"]["result_code"] == "approval_denied"
    assert result["data"]["commit_created"] is False
    assert not any(event["event"] == "git_commit_created" for event in events)


def test_git_finalization_preserves_browser_and_subagent_summaries(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        agent_module,
        "create_git_commit",
        lambda *args, **kwargs: {
            "ok": False,
            "result_code": "approval_denied",
            "repository_root": str(tmp_path),
            "project_root": str(tmp_path),
            "status_short": [" M app.py"],
            "diff_summary": "app.py | 1 +",
            "proposed_files": ["app.py"],
            "unrelated_files": [],
            "excluded_files": [],
            "session_scoped": True,
            "message": "Update app",
            "approved": False,
            "approval_requested": True,
            "permission_denied": True,
            "error": "Approval denied by user.",
        },
    )
    existing_summary = (
        "Review complete.\n\n"
        "Browser validation:\n- run_browser_validation: passed\n\n"
        "Subagents run:\n- tester\n- reviewer"
    )

    output = CodeAgent(AppConfig())._finalize_git_commit_offer(
        existing_summary,
        request="Update app",
        root=tmp_path,
        mode="default",
        session=None,
        changed_files=("app.py",),
        validation_evidence=agent_module.ValidationEvidence(),
        offer_commit=True,
        commit_message="Update app",
    )

    assert "Browser validation:\n- run_browser_validation: passed" in output
    assert "Subagents run:\n- tester\n- reviewer" in output
    assert output.endswith("- Commit not created: approval denied")
