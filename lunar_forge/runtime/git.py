"""Guarded, bounded Git status and commit helpers."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lunar_forge.permissions import (
    ApprovalCallback,
    PermissionLevel,
    PermissionManager,
)
from lunar_forge.runtime.local_runner import resolve_executable


DEFAULT_GIT_TIMEOUT_MS = 30_000
MAX_GIT_OUTPUT_CHARACTERS = 50_000
MAX_DIFF_SUMMARY_CHARACTERS = 20_000
MAX_STATUS_ENTRIES = 1_000
MAX_PROPOSED_FILES = 200
MAX_COMMIT_MESSAGE_CHARACTERS = 200
_TRUNCATION_MARKER = "\n...[git output truncated]"
_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".agent",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        "coverage",
    }
)
_SECRET_FILENAMES = frozenset(
    {
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }
)


@dataclass(frozen=True)
class GitStatusEntry:
    """One parsed ``git status --short`` record."""

    status: str
    path: str

    @property
    def line(self) -> str:
        return f"{self.status} {self.path}"


@dataclass(frozen=True)
class GitCommitProposal:
    """A bounded preview of exactly what a guarded commit would include."""

    project_root: Path
    repository_root: Path
    status_lines: tuple[str, ...]
    diff_summary: str
    proposed_files: tuple[str, ...]
    unrelated_files: tuple[str, ...]
    excluded_files: tuple[str, ...]
    session_scoped: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository_root": str(self.repository_root),
            "project_root": str(self.project_root),
            "status_short": list(self.status_lines),
            "diff_summary": self.diff_summary,
            "proposed_files": list(self.proposed_files),
            "unrelated_files": list(self.unrelated_files),
            "excluded_files": list(self.excluded_files),
            "session_scoped": self.session_scoped,
        }


@dataclass(frozen=True)
class _GitCommandResult:
    ok: bool
    args: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    truncated: bool
    error: str | None = None


def git_status(
    project_root: str | Path,
    *,
    mode: str = "default",
    timeout_ms: int = DEFAULT_GIT_TIMEOUT_MS,
) -> dict[str, Any]:
    """Return bounded repository-wide short status for a selected project."""
    normalized_mode = mode.strip().lower()
    if normalized_mode == "no-command":
        return {
            "ok": False,
            "error": "No-command mode blocks Git command execution.",
            "status_short": [],
        }
    if (
        isinstance(timeout_ms, bool)
        or not isinstance(timeout_ms, int)
        or timeout_ms <= 0
    ):
        return {
            "ok": False,
            "error": "Git timeout_ms must be a positive integer.",
            "status_short": [],
        }

    try:
        root = _validated_project_root(project_root)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "status_short": []}

    repository_root_result = _repository_root(root, timeout_ms)
    if not repository_root_result[0]:
        return {
            "ok": False,
            "error": repository_root_result[1],
            "status_short": [],
        }
    repository_root = Path(repository_root_result[1]).resolve()

    command = _run_git(
        repository_root,
        ("status", "--short", "--untracked-files=all", "-z"),
        timeout_ms,
    )
    if not command.ok:
        return {
            "ok": False,
            "error": command.error or "Could not read Git status.",
            "repository_root": str(repository_root),
            "status_short": [],
        }
    if command.truncated:
        return {
            "ok": False,
            "error": "Git status exceeded the bounded output limit.",
            "repository_root": str(repository_root),
            "status_short": [],
        }
    try:
        entries = _parse_short_status(command.stdout)
    except ValueError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "repository_root": str(repository_root),
            "status_short": [],
        }
    return {
        "ok": True,
        "repository_root": str(repository_root),
        "project_root": str(root),
        "clean": not entries,
        "status_short": [entry.line for entry in entries],
        "entries": [
            {"status": entry.status, "path": entry.path}
            for entry in entries
        ],
        "truncated": command.truncated,
    }


def prepare_git_commit(
    project_root: str | Path,
    *,
    session_files: Sequence[str] | None = None,
    mode: str = "default",
    timeout_ms: int = DEFAULT_GIT_TIMEOUT_MS,
) -> dict[str, Any]:
    """Build a safe commit proposal without mutating Git state."""
    normalized_mode = mode.strip().lower()
    if normalized_mode == "plan":
        return {"ok": False, "error": "Plan mode blocks Git commits."}
    if normalized_mode == "no-command":
        return {
            "ok": False,
            "error": "No-command mode blocks Git command execution.",
        }

    status_result = git_status(project_root, mode=mode, timeout_ms=timeout_ms)
    if status_result.get("ok") is not True:
        return status_result

    root = Path(str(status_result["project_root"])).resolve()
    repository_root = Path(str(status_result["repository_root"])).resolve()
    entries = tuple(
        GitStatusEntry(str(item["status"]), str(item["path"]))
        for item in status_result.get("entries", [])
        if isinstance(item, dict)
    )
    session_paths = (
        None
        if session_files is None
        else _session_repository_paths(root, repository_root, session_files)
    )

    proposed: list[str] = []
    unrelated: list[str] = []
    excluded: list[str] = []
    for entry in entries:
        path = entry.path
        if _is_excluded_path(path):
            excluded.append(path)
            continue
        if not _path_is_within_project(repository_root, root, path):
            unrelated.append(path)
            continue
        if session_paths is None or path in session_paths:
            proposed.append(path)
        else:
            unrelated.append(path)

    proposed_files = tuple(_stable_unique(proposed))
    if len(proposed_files) > MAX_PROPOSED_FILES:
        return {
            "ok": False,
            "error": (
                "Git commit proposal exceeds the guarded file limit of "
                f"{MAX_PROPOSED_FILES}."
            ),
            "status_short": status_result.get("status_short", []),
        }
    proposal = GitCommitProposal(
        project_root=root,
        repository_root=repository_root,
        status_lines=tuple(str(line) for line in status_result["status_short"]),
        diff_summary=_diff_summary(
            repository_root,
            proposed_files,
            entries,
            timeout_ms,
        ),
        proposed_files=proposed_files,
        unrelated_files=tuple(_stable_unique(unrelated)),
        excluded_files=tuple(_stable_unique(excluded)),
        session_scoped=session_files is not None,
    )
    return {"ok": True, "proposal": proposal, **proposal.as_dict()}


def create_git_commit(
    project_root: str | Path,
    message: str,
    *,
    session_files: Sequence[str] | None = None,
    mode: str = "default",
    approval_callback: ApprovalCallback | None = None,
    timeout_ms: int = DEFAULT_GIT_TIMEOUT_MS,
) -> dict[str, Any]:
    """Preview, approve, and create one path-limited Git commit."""
    normalized_mode = mode.strip().lower()
    if normalized_mode == "plan":
        return {
            "ok": False,
            "approved": False,
            "approval_requested": False,
            "result_code": "plan_mode",
            "error": "Plan mode blocks Git commits.",
        }
    if normalized_mode == "no-command":
        return {
            "ok": False,
            "approved": False,
            "approval_requested": False,
            "result_code": "no_command",
            "error": "No-command mode blocks Git command execution.",
        }
    try:
        normalized_message = normalize_commit_message(message)
    except ValueError as exc:
        return {
            "ok": False,
            "approved": False,
            "approval_requested": False,
            "result_code": "invalid_message",
            "error": str(exc),
        }

    prepared = prepare_git_commit(
        project_root,
        session_files=session_files,
        mode=mode,
        timeout_ms=timeout_ms,
    )
    if prepared.get("ok") is not True:
        error = str(prepared.get("error", "Could not prepare Git commit."))
        result_code = (
            "not_repository"
            if "not inside a git repository" in error.casefold()
            else "proposal_failed"
        )
        return {
            **prepared,
            "message": normalized_message,
            "approved": False,
            "approval_requested": False,
            "result_code": result_code,
        }
    proposal = prepared["proposal"]
    assert isinstance(proposal, GitCommitProposal)
    if not proposal.proposed_files:
        return {
            **proposal.as_dict(),
            "ok": False,
            "message": normalized_message,
            "approved": False,
            "approval_requested": False,
            "result_code": "no_changes",
            "error": "No eligible files were proposed for commit.",
        }

    preview = format_git_proposal(proposal, message=normalized_message)
    permission_manager = PermissionManager(
        mode=mode,
        approval_callback=approval_callback,
    )
    decision = permission_manager.authorize(
        PermissionLevel.EXECUTE,
        "git_commit",
        {
            "command": "git commit",
            "message": normalized_message,
            "preview": preview,
            "proposed_files": list(proposal.proposed_files),
        },
    )
    base_result = {
        **proposal.as_dict(),
        "message": normalized_message,
        "approved": decision.allowed,
        "approval_requested": True,
        "approval_reason": decision.reason,
    }
    if not decision.allowed:
        return {
            **base_result,
            "ok": False,
            "result_code": "approval_denied",
            "permission_denied": True,
            "error": decision.reason or "Git commit approval was denied.",
        }

    add_result = _run_git(
        proposal.repository_root,
        ("add", "--", *proposal.proposed_files),
        timeout_ms,
    )
    if not add_result.ok:
        return {
            **base_result,
            "ok": False,
            "result_code": "git_add_failed",
            "error": add_result.error or "Could not stage proposed files.",
        }
    commit_result = _run_git(
        proposal.repository_root,
        (
            "commit",
            "--only",
            "-m",
            normalized_message,
            "--",
            *proposal.proposed_files,
        ),
        timeout_ms,
    )
    if not commit_result.ok:
        return {
            **base_result,
            "ok": False,
            "result_code": "git_commit_failed",
            "error": commit_result.error or "Git commit failed.",
        }

    hash_result = _run_git(
        proposal.repository_root,
        ("rev-parse", "HEAD"),
        timeout_ms,
    )
    commit_hash = hash_result.stdout.strip() if hash_result.ok else None
    return {
        **base_result,
        "ok": True,
        "result_code": "commit_created",
        "commit_hash": commit_hash or None,
        "committed_files": list(proposal.proposed_files),
    }


def derive_commit_message(request: str) -> str:
    """Derive a concise one-line message from the completed task request."""
    normalized = " ".join(str(request).split()).strip().rstrip(".")
    if not normalized:
        return "Update project"
    if len(normalized) <= 72:
        return normalized
    shortened = normalized[:69].rsplit(" ", 1)[0].rstrip(" .,:;-")
    return f"{shortened or normalized[:69].rstrip()}..."


def normalize_commit_message(message: str) -> str:
    """Validate a user-supplied concise Git commit message."""
    if not isinstance(message, str):
        raise ValueError("Commit message must be a string.")
    normalized = " ".join(message.split()).strip()
    if not normalized:
        raise ValueError("Commit message must not be empty.")
    if len(normalized) > MAX_COMMIT_MESSAGE_CHARACTERS:
        raise ValueError(
            "Commit message exceeds the "
            f"{MAX_COMMIT_MESSAGE_CHARACTERS}-character limit."
        )
    return normalized


def format_git_status(result: dict[str, Any]) -> str:
    """Format a deterministic status result for CLI display."""
    if result.get("ok") is not True:
        return f"Git status failed: {result.get('error', 'Unknown error.')}"
    lines = [f"Git repository: {result['repository_root']}", "Git status --short:"]
    status_lines = result.get("status_short", [])
    if status_lines:
        lines.extend(str(line) for line in status_lines)
    else:
        lines.append("(clean)")
    return "\n".join(lines)


def format_git_proposal(
    proposal: GitCommitProposal,
    *,
    message: str | None = None,
) -> str:
    """Format bounded status, diff, and path groups before approval."""
    lines = ["Git status --short:"]
    lines.extend(proposal.status_lines or ("(clean)",))
    lines.extend(("", "Bounded diff summary:", proposal.diff_summary or "(none)"))
    proposed_label = (
        "Files changed by LunarForge (proposed for commit):"
        if proposal.session_scoped
        else "Files proposed for commit:"
    )
    lines.extend(("", proposed_label))
    lines.extend(f"- {path}" for path in proposal.proposed_files)
    if not proposal.proposed_files:
        lines.append("- None")
    lines.extend(("", "Unrelated dirty files (not included):"))
    lines.extend(f"- {path}" for path in proposal.unrelated_files)
    if not proposal.unrelated_files:
        lines.append("- None")
    lines.extend(("", "Excluded runtime/generated/secret files:"))
    lines.extend(f"- {path}" for path in proposal.excluded_files)
    if not proposal.excluded_files:
        lines.append("- None")
    if message is not None:
        lines.extend(("", f"Proposed commit message: {message}"))
    return "\n".join(lines)


def format_git_commit_result(result: dict[str, Any]) -> str:
    """Format a commit result, retaining its proposal and final outcome."""
    proposal_text = ""
    if "repository_root" in result and result.get("proposed_files"):
        proposal = GitCommitProposal(
            project_root=Path(str(result.get("project_root", "."))).resolve(),
            repository_root=Path(str(result["repository_root"])).resolve(),
            status_lines=tuple(str(item) for item in result.get("status_short", [])),
            diff_summary=str(result.get("diff_summary", "")),
            proposed_files=tuple(str(item) for item in result.get("proposed_files", [])),
            unrelated_files=tuple(str(item) for item in result.get("unrelated_files", [])),
            excluded_files=tuple(str(item) for item in result.get("excluded_files", [])),
            session_scoped=result.get("session_scoped") is True,
        )
        message = result.get("message")
        proposal_text = format_git_proposal(
            proposal,
            message=message if isinstance(message, str) and message else None,
        )
    if result.get("ok") is True:
        commit_hash = result.get("commit_hash") or "unavailable"
        outcome = f"- Commit created: {commit_hash}"
    else:
        result_code = str(result.get("result_code", ""))
        concise_reasons = {
            "approval_denied": "approval denied",
            "no_changes": "no changes",
            "not_repository": "not a repo",
            "plan_mode": "plan mode",
            "no_command": "no-command mode",
        }
        reason = concise_reasons.get(result_code)
        if reason is None:
            reason = str(result.get("error", "unknown Git error"))
        outcome = f"- Commit not created: {reason}"
    return f"{proposal_text}\n\n{outcome}" if proposal_text else outcome


def _validated_project_root(project_root: str | Path) -> Path:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Project root is not a directory: {root}")
    return root


def _repository_root(root: Path, timeout_ms: int) -> tuple[bool, str]:
    result = _run_git(root, ("rev-parse", "--show-toplevel"), timeout_ms)
    if not result.ok:
        error = result.error or "Git repository inspection failed."
        if "not a git repository" in error.lower():
            return False, "Project is not inside a Git repository."
        return False, f"Could not inspect Git repository: {error}"
    if not result.stdout.strip():
        return False, "Project is not inside a Git repository."
    repository_root = Path(result.stdout.strip()).expanduser().resolve()
    try:
        root.relative_to(repository_root)
    except ValueError:
        return False, "Git repository root does not contain the selected project."
    return True, str(repository_root)


def _run_git(
    cwd: Path,
    arguments: Sequence[str],
    timeout_ms: int,
) -> _GitCommandResult:
    git_executable = resolve_executable("git", cwd)
    args = tuple(str(argument) for argument in arguments)
    if git_executable is None:
        return _GitCommandResult(
            False,
            args,
            None,
            "",
            "",
            False,
            "Git executable was not found.",
        )
    try:
        completed = subprocess.run(
            [git_executable, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_ms / 1000,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return _GitCommandResult(
            False,
            args,
            None,
            "",
            "",
            False,
            f"Git command timed out after {timeout_ms} ms.",
        )
    except OSError as exc:
        return _GitCommandResult(
            False,
            args,
            None,
            "",
            "",
            False,
            f"Could not start Git: {exc}",
        )
    stdout, stdout_truncated = _bounded(completed.stdout, MAX_GIT_OUTPUT_CHARACTERS)
    stderr, stderr_truncated = _bounded(completed.stderr, MAX_GIT_OUTPUT_CHARACTERS)
    ok = completed.returncode == 0
    error = None
    if not ok:
        detail = stderr.strip() or stdout.strip()
        error = detail or f"Git command exited with code {completed.returncode}."
    return _GitCommandResult(
        ok,
        args,
        completed.returncode,
        stdout,
        stderr,
        stdout_truncated or stderr_truncated,
        error,
    )


def _parse_short_status(output: str) -> tuple[GitStatusEntry, ...]:
    if not output:
        return ()
    records = output.split("\0") if "\0" in output else output.splitlines()
    entries: list[GitStatusEntry] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            raise ValueError("Git returned an unrecognized short-status record.")
        status = record[:2]
        path = record[3:]
        if not path:
            raise ValueError("Git returned an empty path in short status.")
        entries.append(GitStatusEntry(status=status, path=Path(path).as_posix()))
        if "R" in status or "C" in status:
            index += 1
        if len(entries) > MAX_STATUS_ENTRIES:
            raise ValueError(
                f"Git status exceeds the guarded limit of {MAX_STATUS_ENTRIES} entries."
            )
    return tuple(entries)


def _session_repository_paths(
    project_root: Path,
    repository_root: Path,
    session_files: Sequence[str],
) -> frozenset[str]:
    paths: set[str] = set()
    for raw_path in session_files:
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        candidate = (project_root / raw_path).resolve()
        try:
            candidate.relative_to(project_root)
            relative = candidate.relative_to(repository_root).as_posix()
        except ValueError:
            continue
        paths.add(relative)
    return frozenset(paths)


def _path_is_within_project(
    repository_root: Path,
    project_root: Path,
    relative_path: str,
) -> bool:
    candidate = (repository_root / relative_path).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError:
        return False
    return True


def _is_excluded_path(path: str) -> bool:
    normalized = Path(path.replace("\\", "/"))
    lowered_parts = tuple(part.casefold() for part in normalized.parts)
    if any(part in _EXCLUDED_DIRECTORY_NAMES for part in lowered_parts):
        return True
    filename = normalized.name.casefold()
    if filename == ".env" or filename.startswith(".env."):
        return True
    if filename in _SECRET_FILENAMES:
        return True
    return normalized.suffix.casefold() in {".key", ".pem"}


def _diff_summary(
    repository_root: Path,
    proposed_files: Sequence[str],
    entries: Sequence[GitStatusEntry],
    timeout_ms: int,
) -> str:
    if not proposed_files:
        return "No eligible files proposed."
    result = _run_git(
        repository_root,
        ("diff", "--stat", "--no-ext-diff", "HEAD", "--", *proposed_files),
        timeout_ms,
    )
    lines: list[str] = []
    if result.ok and result.stdout.strip():
        lines.append(result.stdout.strip())
    proposed_set = set(proposed_files)
    lines.extend(
        f"Untracked: {entry.path}"
        for entry in entries
        if entry.path in proposed_set and entry.status == "??"
    )
    if not lines:
        lines.append("No tracked diff stat was available; review status paths above.")
    summary, _ = _bounded("\n".join(lines), MAX_DIFF_SUMMARY_CHARACTERS)
    return summary


def _stable_unique(paths: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result


def _bounded(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    keep = max(0, limit - len(_TRUNCATION_MARKER))
    return f"{value[:keep]}{_TRUNCATION_MARKER}", True
