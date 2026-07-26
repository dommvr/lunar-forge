"""Bounded, read-only summaries of common CI configuration files."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from lunar_forge.tools.dependencies import sanitize_command_metadata
from lunar_forge.tools.files import safe_path


MAX_CI_FILES = 20
MAX_CI_FILE_BYTES = 200_000
MAX_CI_WORKFLOWS = 20
MAX_CI_JOBS = 40
MAX_CI_COMMANDS = 50
MAX_JOB_COMMANDS = 10
MAX_CI_HINTS = 30
MAX_VALIDATION_COMMANDS = 20
MAX_PARSE_ERRORS = 10
MAX_COMMAND_CHARACTERS = 240
MAX_LABEL_CHARACTERS = 200
MAX_ERROR_CHARACTERS = 500
MAX_WALK_NODES = 5_000
MAX_WALK_DEPTH = 30
MAX_SENSITIVE_VALUES = 200

_FIXED_CI_FILES = (
    (".gitlab-ci.yml", "gitlab_ci"),
    ("azure-pipelines.yml", "azure_pipelines"),
    ("bitbucket-pipelines.yml", "bitbucket_pipelines"),
    (".circleci/config.yml", "circleci"),
)
_GITHUB_SUFFIXES = frozenset({".yml", ".yaml"})
_GITLAB_RESERVED_KEYS = frozenset(
    {
        "after_script",
        "before_script",
        "cache",
        "default",
        "image",
        "include",
        "pages",
        "services",
        "stages",
        "variables",
        "workflow",
    }
)
_SECRET_NAME_PATTERN = (
    r"(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|"
    r"PRIVATE[_-]?KEY|AUTHORIZATION|COOKIE)"
)
_CI_EXPRESSION = re.compile(
    r"\$\{\{[^}\r\n]{0,500}(?:secrets|env|vars|"
    + _SECRET_NAME_PATTERN
    + r")[^}\r\n]{0,500}\}\}",
    re.IGNORECASE,
)
_SECRET_VARIABLE = re.compile(
    r"(?i)(?:"
    r"\$\{?[A-Z0-9_]*"
    + _SECRET_NAME_PATTERN
    + r"[A-Z0-9_]*\}?"
    r"|%[A-Z0-9_]*"
    + _SECRET_NAME_PATTERN
    + r"[A-Z0-9_]*%"
    r"|\$\([A-Z0-9_]*"
    + _SECRET_NAME_PATTERN
    + r"[A-Z0-9_]*\)"
    r")"
)
_AUTHORIZATION_VALUE = re.compile(
    r"(?i)\b(Authorization\s*[:=]\s*)(?:Basic|Bearer)?\s*[^\s'\"]+"
)
_EMBEDDED_CREDENTIALS = re.compile(
    r"(?i)\b[^/\s:@]+:[^/@\s]+@"
)
_PACKAGE_MANAGER_PATTERNS = (
    ("pnpm", re.compile(r"(?i)(?:^|[\s/])pnpm(?:\s|$)")),
    ("yarn", re.compile(r"(?i)(?:^|[\s/])yarn(?:\s|$)")),
    ("npm", re.compile(r"(?i)(?:^|[\s/])(?:npm|npx)(?:\s|$)")),
    ("bun", re.compile(r"(?i)(?:^|[\s/])(?:bun|bunx)(?:\s|$)")),
    ("uv", re.compile(r"(?i)(?:^|[\s/])uv(?:\s|$)")),
    ("poetry", re.compile(r"(?i)(?:^|[\s/])poetry(?:\s|$)")),
    ("pipenv", re.compile(r"(?i)(?:^|[\s/])pipenv(?:\s|$)")),
    (
        "pip",
        re.compile(r"(?i)(?:^|[\s/])(?:pip\d*|python\s+-m\s+pip)(?:\s|$)"),
    ),
)
_VALIDATION_COMMAND = re.compile(
    r"(?ix)^"
    r"(?:python\d*(?:\.\d+)?\s+(?:-B\s+)?-m\s+"
    r"(?:pytest|compileall|unittest)|pytest|ruff\s+check|mypy|tox|nox|"
    r"python\d*(?:\.\d+)?\s+manage\.py\s+test|"
    r"(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?"
    r"(?:test|lint|build|typecheck|check)|"
    r"go\s+test|cargo\s+test|dotnet\s+test|make\s+(?:test|lint|check))"
    r"(?:\s|$)"
)
_UNSAFE_SUGGESTION_TOKENS = ("&&", "||", ";", "|", ">", "<", "`", "$(")
_SENSITIVE_CONTAINER_KEYS = frozenset(
    {"env", "environment", "secrets", "variables"}
)
_SENSITIVE_KEY_MARKERS = (
    "apikey",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "privatekey",
    "authorization",
    "cookie",
)


class _Signals:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.runtime_hints: list[str] = []
        self.setup_hints: list[str] = []
        self.package_managers: list[str] = []
        self.truncated = False

    def add_commands(self, value: Any) -> None:
        for command in _command_values(value):
            sanitized = _sanitize_ci_text(command)
            if not sanitized:
                continue
            self._add(self.commands, sanitized, MAX_CI_COMMANDS)
            for manager, pattern in _PACKAGE_MANAGER_PATTERNS:
                if pattern.search(sanitized):
                    self._add(
                        self.package_managers,
                        manager,
                        MAX_CI_HINTS,
                    )

    def add_runtime(self, value: Any) -> None:
        hint = _safe_hint(value)
        if hint is not None:
            self._add(self.runtime_hints, hint, MAX_CI_HINTS)

    def add_setup(self, value: Any) -> None:
        hint = _safe_hint(value)
        if hint is not None:
            self._add(self.setup_hints, hint, MAX_CI_HINTS)

    def add_package_manager(self, value: str) -> None:
        self._add(self.package_managers, value, MAX_CI_HINTS)

    def _add(self, output: list[str], value: str, limit: int) -> None:
        if value in output:
            return
        if len(output) >= limit:
            self.truncated = True
            return
        output.append(value)


def ci_summary(project_root: str | Path) -> dict[str, Any]:
    """Summarize supported CI YAML without executing any configured command."""
    try:
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(
                f"Project root is not a directory: {root}"
            )
        discovered, discovery_truncated = _discover_ci_files(root)
    except (OSError, PermissionError, TypeError, ValueError) as exc:
        return {
            "ok": False,
            "ci_files": [],
            "providers": [],
            "workflows": [],
            "runtime_hints": [],
            "setup_hints": [],
            "package_manager_hints": [],
            "commands": [],
            "suggested_validation_commands": [],
            "parse_errors": [],
            "truncated": False,
            "error": _bounded_text(str(exc), MAX_ERROR_CHARACTERS),
        }

    if not discovered:
        return {
            "ok": True,
            "ci_files": [],
            "providers": [],
            "workflows": [],
            "runtime_hints": [],
            "setup_hints": [],
            "package_manager_hints": [],
            "commands": [],
            "suggested_validation_commands": [],
            "parse_errors": [],
            "truncated": discovery_truncated,
            "message": "No supported CI configuration files were found.",
        }

    signals = _Signals()
    workflows: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    providers: list[str] = []
    total_jobs = 0
    truncated = discovery_truncated

    for path, relative_path, provider in discovered:
        if provider not in providers:
            providers.append(provider)
        try:
            document, file_truncated = _load_ci_yaml(path)
        except yaml.YAMLError as exc:
            _append_parse_error(parse_errors, relative_path, exc)
            truncated = truncated or len(parse_errors) >= MAX_PARSE_ERRORS
            continue
        except (OSError, RecursionError, UnicodeError, ValueError) as exc:
            _append_plain_error(parse_errors, relative_path, exc)
            truncated = truncated or "limit" in str(exc).casefold()
            continue

        if file_truncated:
            truncated = True
        try:
            document = _redact_ci_document(document)
            workflow = _provider_workflow(
                provider,
                relative_path,
                document,
                signals,
            )
        except (RecursionError, TypeError, ValueError) as exc:
            _append_plain_error(parse_errors, relative_path, exc)
            truncated = True
            continue
        jobs = workflow["jobs"]
        remaining_jobs = max(MAX_CI_JOBS - total_jobs, 0)
        if len(jobs) > remaining_jobs:
            workflow["jobs"] = jobs[:remaining_jobs]
            workflow["truncated"] = True
            truncated = True
        total_jobs += len(workflow["jobs"])
        if len(workflows) >= MAX_CI_WORKFLOWS:
            truncated = True
            continue
        workflows.append(workflow)

    truncated = truncated or signals.truncated
    validation_commands = _suggested_validation_commands(signals.commands)
    result = {
        "ok": not parse_errors,
        "ci_files": [relative_path for _, relative_path, _ in discovered],
        "providers": providers,
        "workflows": workflows,
        "runtime_hints": signals.runtime_hints,
        "setup_hints": signals.setup_hints,
        "package_manager_hints": signals.package_managers,
        "commands": signals.commands,
        "suggested_validation_commands": validation_commands,
        "parse_errors": parse_errors,
        "truncated": truncated,
        "message": (
            "CI configuration summary is incomplete because parsing failed."
            if parse_errors
            else f"Found {len(discovered)} supported CI configuration file(s)."
        ),
    }
    if parse_errors:
        result["error"] = (
            "One or more CI configuration files could not be parsed safely."
        )
    return result


def _discover_ci_files(
    root: Path,
) -> tuple[list[tuple[Path, str, str]], bool]:
    discovered: list[tuple[Path, str, str]] = []
    truncated = False
    raw_workflows_directory = root / ".github" / "workflows"
    if raw_workflows_directory.is_symlink():
        workflows_directory = None
    else:
        try:
            workflows_directory = safe_path(root, ".github/workflows")
        except PermissionError:
            workflows_directory = None
    if (
        workflows_directory is not None
        and workflows_directory.is_dir()
        and not workflows_directory.is_symlink()
    ):
        for candidate in sorted(
            workflows_directory.iterdir(),
            key=lambda item: item.name.casefold(),
        ):
            if candidate.suffix.casefold() not in _GITHUB_SUFFIXES:
                continue
            if len(discovered) >= MAX_CI_FILES:
                truncated = True
                break
            try:
                resolved = safe_path(root, candidate)
            except PermissionError:
                continue
            if not resolved.is_file() or candidate.is_symlink():
                continue
            discovered.append(
                (
                    resolved,
                    candidate.relative_to(root).as_posix(),
                    "github_actions",
                )
            )

    for relative_path, provider in _FIXED_CI_FILES:
        if len(discovered) >= MAX_CI_FILES:
            truncated = True
            break
        raw_candidate = root / Path(relative_path)
        if raw_candidate.is_symlink():
            continue
        try:
            candidate = safe_path(root, relative_path)
        except PermissionError:
            continue
        if candidate.is_file() and not candidate.is_symlink():
            discovered.append((candidate, relative_path, provider))
    return discovered, truncated


def _load_ci_yaml(path: Path) -> tuple[Mapping[str, Any], bool]:
    with path.open("rb") as handle:
        payload = handle.read(MAX_CI_FILE_BYTES + 1)
    if len(payload) > MAX_CI_FILE_BYTES:
        raise ValueError(
            f"CI configuration exceeds the {MAX_CI_FILE_BYTES}-byte parsing limit."
        )
    text = payload.decode("utf-8-sig")
    document = yaml.safe_load(text)
    if document is None:
        document = {}
    if not isinstance(document, Mapping):
        raise ValueError("CI YAML document must contain a mapping.")
    return document, False


def _redact_ci_document(
    document: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Remove env blocks and aliases of their literal values before extraction."""
    sensitive_values = _collect_sensitive_values(document)
    state = {"nodes": 0}
    redacted = _redact_ci_node(
        document,
        sensitive_values,
        state=state,
        depth=0,
        active=set(),
    )
    if not isinstance(redacted, Mapping):
        raise ValueError("CI YAML document must contain a mapping.")
    return redacted


def _collect_sensitive_values(value: Any) -> tuple[str, ...]:
    values: set[str] = set()
    state = {"nodes": 0}

    def walk(
        node: Any,
        *,
        sensitive: bool,
        depth: int,
        active: set[int],
    ) -> None:
        state["nodes"] += 1
        if state["nodes"] > MAX_WALK_NODES or depth > MAX_WALK_DEPTH:
            raise ValueError("CI YAML exceeds the safe redaction walk limit.")
        if node is None:
            return
        if isinstance(node, (str, int, float, bool)):
            if sensitive:
                rendered = str(node)
                if rendered and rendered not in values:
                    if len(values) >= MAX_SENSITIVE_VALUES:
                        raise ValueError(
                            "CI YAML contains too many sensitive values to "
                            "summarize safely."
                        )
                    values.add(rendered)
            return

        if isinstance(node, Mapping):
            identity = id(node)
            if identity in active:
                raise ValueError("CI YAML contains a recursive mapping.")
            active.add(identity)
            try:
                for key, item in node.items():
                    normalized_key = _normalized_key(key)
                    scalar_secret = (
                        _looks_sensitive_key(normalized_key)
                        and (
                            item is None
                            or isinstance(item, (str, int, float, bool))
                        )
                    )
                    walk(
                        item,
                        sensitive=(
                            sensitive
                            or normalized_key in _SENSITIVE_CONTAINER_KEYS
                            or scalar_secret
                        ),
                        depth=depth + 1,
                        active=active,
                    )
            finally:
                active.remove(identity)
            return

        if isinstance(node, Sequence) and not isinstance(
            node,
            (str, bytes, bytearray),
        ):
            identity = id(node)
            if identity in active:
                raise ValueError("CI YAML contains a recursive sequence.")
            active.add(identity)
            try:
                for item in node:
                    walk(
                        item,
                        sensitive=sensitive,
                        depth=depth + 1,
                        active=active,
                    )
            finally:
                active.remove(identity)

    walk(value, sensitive=False, depth=0, active=set())
    return tuple(sorted(values, key=lambda item: (-len(item), item)))


def _redact_ci_node(
    value: Any,
    sensitive_values: tuple[str, ...],
    *,
    state: dict[str, int],
    depth: int,
    active: set[int],
) -> Any:
    state["nodes"] += 1
    if state["nodes"] > MAX_WALK_NODES or depth > MAX_WALK_DEPTH:
        raise ValueError("CI YAML exceeds the safe redaction walk limit.")
    if isinstance(value, str):
        return _redact_sensitive_literals(
            _redact_ci_text(value),
            sensitive_values,
        )
    if value is None or isinstance(value, (int, float, bool)):
        if str(value) in sensitive_values:
            return "[REDACTED]"
        return value
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ValueError("CI YAML contains a recursive mapping.")
        active.add(identity)
        try:
            output: dict[Any, Any] = {}
            for key, item in value.items():
                safe_key = (
                    _redact_sensitive_literals(
                        _redact_ci_text(key),
                        sensitive_values,
                    )
                    if isinstance(key, str)
                    else key
                )
                if safe_key in output:
                    raise ValueError(
                        "CI YAML mapping keys collide after redaction."
                    )
                if _normalized_key(key) in _SENSITIVE_CONTAINER_KEYS:
                    output[safe_key] = "[REDACTED]"
                else:
                    output[safe_key] = _redact_ci_node(
                        item,
                        sensitive_values,
                        state=state,
                        depth=depth + 1,
                        active=active,
                    )
            return output
        finally:
            active.remove(identity)
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        identity = id(value)
        if identity in active:
            raise ValueError("CI YAML contains a recursive sequence.")
        active.add(identity)
        try:
            return [
                _redact_ci_node(
                    item,
                    sensitive_values,
                    state=state,
                    depth=depth + 1,
                    active=active,
                )
                for item in value
            ]
        finally:
            active.remove(identity)
    return value


def _normalized_key(value: Any) -> str:
    return "".join(
        character
        for character in str(value).casefold()
        if character.isalnum()
    )


def _looks_sensitive_key(normalized_key: str) -> bool:
    return any(marker in normalized_key for marker in _SENSITIVE_KEY_MARKERS)


def _redact_sensitive_literals(
    value: str,
    sensitive_values: tuple[str, ...],
) -> str:
    for sensitive_value in sensitive_values:
        if value == sensitive_value:
            return "[REDACTED]"
        if len(sensitive_value) >= 8:
            value = value.replace(sensitive_value, "[REDACTED]")
    return value


def _provider_workflow(
    provider: str,
    path: str,
    document: Mapping[str, Any],
    signals: _Signals,
) -> dict[str, Any]:
    if provider == "github_actions":
        return _github_workflow(path, document, signals)
    if provider == "gitlab_ci":
        return _gitlab_workflow(path, document, signals)
    if provider == "azure_pipelines":
        return _azure_workflow(path, document, signals)
    if provider == "circleci":
        return _circleci_workflow(path, document, signals)
    return _bitbucket_workflow(path, document, signals)


def _github_workflow(
    path: str,
    document: Mapping[str, Any],
    signals: _Signals,
) -> dict[str, Any]:
    jobs: list[dict[str, Any]] = []
    for job_id, raw_job in _sorted_mapping_items(document.get("jobs")):
        job = _mapping(raw_job)
        if not job:
            continue
        before = len(signals.commands)
        runner = _safe_hint(job.get("runs-on"))
        if runner is not None:
            signals.add_runtime(f"runner {runner}")
        _github_matrix_hints(job, signals)
        for raw_step in _sequence(job.get("steps")):
            step = _mapping(raw_step)
            if not step:
                continue
            uses = step.get("uses")
            if isinstance(uses, str):
                _github_setup_hints(uses, _mapping(step.get("with")), signals)
            signals.add_commands(step.get("run"))
        _add_job(
            jobs,
            _job_summary(
                job_id,
                job.get("name"),
                signals.commands[before:],
                runner=runner,
            ),
            signals,
        )
    return _workflow_summary(
        path,
        "github_actions",
        document.get("name") or Path(path).stem,
        jobs,
    )


def _github_matrix_hints(
    job: Mapping[str, Any],
    signals: _Signals,
) -> None:
    matrix = _mapping(_mapping(job.get("strategy")).get("matrix"))
    for raw_key, values in matrix.items():
        key = str(raw_key).casefold()
        if key not in {"python", "python-version", "node", "node-version"}:
            continue
        runtime = "python" if "python" in key else "node"
        for value in _sequence_or_scalar(values):
            hint = _safe_hint(value)
            signals.add_runtime(f"{runtime} {hint}" if hint else runtime)


def _github_setup_hints(
    uses: str,
    settings: Mapping[str, Any],
    signals: _Signals,
) -> None:
    normalized = uses.casefold()
    if "actions/setup-python@" in normalized:
        version = _safe_hint(settings.get("python-version"))
        signals.add_setup(
            f"setup-python {version}" if version else "setup-python"
        )
        signals.add_runtime(f"python {version}" if version else "python")
        cache = _safe_hint(settings.get("cache"))
        if cache in {"pip", "pipenv", "poetry"}:
            signals.add_package_manager(cache)
    elif "actions/setup-node@" in normalized:
        version = _safe_hint(settings.get("node-version"))
        signals.add_setup(f"setup-node {version}" if version else "setup-node")
        signals.add_runtime(f"node {version}" if version else "node")
        cache = _safe_hint(settings.get("cache"))
        if cache in {"npm", "pnpm", "yarn"}:
            signals.add_package_manager(cache)
    elif "pnpm/action-setup@" in normalized:
        signals.add_setup("pnpm/action-setup")
        signals.add_package_manager("pnpm")
    elif "oven-sh/setup-bun@" in normalized:
        signals.add_setup("setup-bun")
        signals.add_package_manager("bun")


def _gitlab_workflow(
    path: str,
    document: Mapping[str, Any],
    signals: _Signals,
) -> dict[str, Any]:
    jobs: list[dict[str, Any]] = []
    global_before = document.get("before_script")
    for raw_name, raw_job in _sorted_mapping_items(document):
        name = str(raw_name)
        if name.startswith(".") or name.casefold() in _GITLAB_RESERVED_KEYS:
            continue
        job = _mapping(raw_job)
        if not job or not any(
            key in job for key in ("script", "stage", "extends")
        ):
            continue
        before = len(signals.commands)
        signals.add_commands(global_before)
        signals.add_commands(job.get("before_script"))
        signals.add_commands(job.get("script"))
        signals.add_commands(job.get("after_script"))
        image = _image_name(job.get("image") or document.get("image"))
        _add_image_runtime(image, signals)
        signals.add_commands(document.get("after_script"))
        _add_job(
            jobs,
            _job_summary(
                name,
                job.get("name"),
                signals.commands[before:],
                stage=_safe_hint(job.get("stage")),
            ),
            signals,
        )
    return _workflow_summary(path, "gitlab_ci", "GitLab CI", jobs)


def _azure_workflow(
    path: str,
    document: Mapping[str, Any],
    signals: _Signals,
) -> dict[str, Any]:
    jobs: list[dict[str, Any]] = []
    raw_jobs: list[Any] = list(_sequence(document.get("jobs")))
    for stage in _sequence(document.get("stages")):
        raw_jobs.extend(_sequence(_mapping(stage).get("jobs")))
    if not raw_jobs and _sequence(document.get("steps")):
        raw_jobs.append(
            {
                "job": "pipeline",
                "steps": document.get("steps"),
                "pool": document.get("pool"),
            }
        )
    for index, raw_job in enumerate(raw_jobs, start=1):
        job = _mapping(raw_job)
        if not job:
            continue
        before = len(signals.commands)
        runner = _safe_hint(_mapping(job.get("pool")).get("vmImage"))
        if runner is not None:
            signals.add_runtime(f"runner {runner}")
        for raw_step in _sequence(job.get("steps")):
            step = _mapping(raw_step)
            if not step:
                continue
            for key in ("script", "bash", "pwsh", "powershell"):
                signals.add_commands(step.get(key))
            _azure_task_hints(step, signals)
        _add_job(
            jobs,
            _job_summary(
                job.get("job") or job.get("deployment") or f"job-{index}",
                job.get("displayName"),
                signals.commands[before:],
                runner=runner,
            ),
            signals,
        )
    return _workflow_summary(
        path,
        "azure_pipelines",
        document.get("name") or "Azure Pipelines",
        jobs,
    )


def _azure_task_hints(
    step: Mapping[str, Any],
    signals: _Signals,
) -> None:
    task = step.get("task")
    if not isinstance(task, str):
        return
    normalized = task.casefold()
    inputs = _mapping(step.get("inputs"))
    if "usepythonversion@" in normalized:
        version = _safe_hint(
            inputs.get("versionSpec") or inputs.get("pythonVersion")
        )
        signals.add_setup("UsePythonVersion")
        signals.add_runtime(f"python {version}" if version else "python")
    elif "nodetool@" in normalized or "usenode@" in normalized:
        version = _safe_hint(
            inputs.get("versionSpec") or inputs.get("nodeVersion")
        )
        signals.add_setup("NodeTool")
        signals.add_runtime(f"node {version}" if version else "node")


def _circleci_workflow(
    path: str,
    document: Mapping[str, Any],
    signals: _Signals,
) -> dict[str, Any]:
    jobs: list[dict[str, Any]] = []
    for raw_name, raw_job in _sorted_mapping_items(document.get("jobs")):
        job = _mapping(raw_job)
        if not job:
            continue
        before = len(signals.commands)
        for image in _sequence(job.get("docker")):
            _add_image_runtime(_image_name(image), signals)
        for raw_step in _sequence(job.get("steps")):
            step = _mapping(raw_step)
            run = step.get("run")
            if isinstance(run, Mapping):
                signals.add_commands(run.get("command"))
            else:
                signals.add_commands(run)
        _add_job(
            jobs,
            _job_summary(
                raw_name,
                job.get("name"),
                signals.commands[before:],
            ),
            signals,
        )
    workflow_names = [
        str(name)
        for name, _ in _sorted_mapping_items(document.get("workflows"))
        if str(name).casefold() != "version"
    ]
    return _workflow_summary(
        path,
        "circleci",
        workflow_names[0] if workflow_names else "CircleCI",
        jobs,
    )


def _bitbucket_workflow(
    path: str,
    document: Mapping[str, Any],
    signals: _Signals,
) -> dict[str, Any]:
    jobs: list[dict[str, Any]] = []
    _add_image_runtime(_image_name(document.get("image")), signals)
    state = {"nodes": 0, "truncated": False}
    _collect_bitbucket_steps(
        document.get("pipelines"),
        signals,
        jobs,
        state,
        depth=0,
        active=set(),
    )
    if state["truncated"]:
        signals.truncated = True
    return _workflow_summary(
        path,
        "bitbucket_pipelines",
        "Bitbucket Pipelines",
        jobs,
    )


def _collect_bitbucket_steps(
    value: Any,
    signals: _Signals,
    jobs: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    depth: int,
    active: set[int],
) -> None:
    state["nodes"] += 1
    if state["nodes"] > MAX_WALK_NODES or depth > MAX_WALK_DEPTH:
        state["truncated"] = True
        return
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            state["truncated"] = True
            return
        active.add(identity)
        try:
            raw_step = value.get("step")
            step = _mapping(raw_step)
            if step:
                before = len(signals.commands)
                signals.add_commands(step.get("script"))
                _add_image_runtime(_image_name(step.get("image")), signals)
                _add_job(
                    jobs,
                    _job_summary(
                        step.get("name") or f"step-{len(jobs) + 1}",
                        None,
                        signals.commands[before:],
                    ),
                    signals,
                )
            for key, item in value.items():
                if str(key).casefold() in {
                    "env",
                    "environment",
                    "secrets",
                    "variables",
                }:
                    continue
                _collect_bitbucket_steps(
                    item,
                    signals,
                    jobs,
                    state,
                    depth=depth + 1,
                    active=active,
                )
        finally:
            active.remove(identity)
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        identity = id(value)
        if identity in active:
            state["truncated"] = True
            return
        active.add(identity)
        try:
            for item in value:
                _collect_bitbucket_steps(
                    item,
                    signals,
                    jobs,
                    state,
                    depth=depth + 1,
                    active=active,
                )
        finally:
            active.remove(identity)


def _workflow_summary(
    path: str,
    provider: str,
    raw_name: Any,
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "path": path,
        "provider": provider,
        "name": _safe_hint(raw_name) or Path(path).name,
        "jobs": jobs,
        "truncated": False,
    }


def _job_summary(
    raw_name: Any,
    raw_display_name: Any,
    commands: list[str],
    *,
    runner: str | None = None,
    stage: str | None = None,
) -> dict[str, Any]:
    name = _safe_hint(raw_name) or "unnamed"
    result: dict[str, Any] = {
        "name": name,
        "commands": commands[:MAX_JOB_COMMANDS],
    }
    display_name = _safe_hint(raw_display_name)
    if display_name is not None and display_name != name:
        result["display_name"] = display_name
    if runner is not None:
        result["runner"] = runner
    if stage is not None:
        result["stage"] = stage
    if len(commands) > MAX_JOB_COMMANDS:
        result["truncated"] = True
    return result


def _add_job(
    jobs: list[dict[str, Any]],
    job: dict[str, Any],
    signals: _Signals,
) -> None:
    if len(jobs) >= MAX_CI_JOBS:
        signals.truncated = True
        return
    jobs.append(job)


def _add_image_runtime(image: str | None, signals: _Signals) -> None:
    if image is None:
        return
    normalized = image.casefold()
    for runtime in (
        "python",
        "node",
        "ruby",
        "golang",
        "rust",
        "dotnet",
        "java",
    ):
        if runtime in normalized:
            signals.add_runtime(f"image {image}")
            return


def _image_name(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("name")
    return _safe_hint(value)


def _command_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        for line in value.splitlines():
            command = line.strip()
            if command and not command.startswith("#"):
                yield command
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for item in value:
            yield from _command_values(item)


def _suggested_validation_commands(commands: list[str]) -> list[str]:
    suggestions: list[str] = []
    for command in commands:
        if "[REDACTED]" in command:
            continue
        if any(token in command for token in _UNSAFE_SUGGESTION_TOKENS):
            continue
        if _VALIDATION_COMMAND.search(command) is None:
            continue
        if command not in suggestions:
            suggestions.append(command)
        if len(suggestions) >= MAX_VALIDATION_COMMANDS:
            break
    return suggestions


def _sanitize_ci_text(value: str) -> str:
    return _bounded_text(
        _redact_ci_text(value).strip(),
        MAX_COMMAND_CHARACTERS,
    )


def _redact_ci_text(value: str) -> str:
    sanitized = _CI_EXPRESSION.sub("[REDACTED]", value)
    sanitized = _SECRET_VARIABLE.sub("[REDACTED]", sanitized)
    sanitized = _AUTHORIZATION_VALUE.sub(r"\1[REDACTED]", sanitized)
    sanitized = _EMBEDDED_CREDENTIALS.sub("[REDACTED]@", sanitized)
    return sanitize_command_metadata(sanitized)


def _safe_hint(value: Any) -> str | None:
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        values = [
            item
            for item in (_safe_hint(candidate) for candidate in value)
            if item is not None
        ]
        return ", ".join(values)[:MAX_LABEL_CHARACTERS] or None
    if not isinstance(value, (str, int, float, bool)):
        return None
    rendered = _sanitize_ci_text(str(value))
    return rendered[:MAX_LABEL_CHARACTERS] or None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return value
    return ()


def _sequence_or_scalar(value: Any) -> Sequence[Any]:
    sequence = _sequence(value)
    return sequence if sequence else (value,)


def _sorted_mapping_items(value: Any) -> list[tuple[Any, Any]]:
    mapping = _mapping(value)
    return sorted(
        mapping.items(),
        key=lambda item: str(item[0]).casefold(),
    )


def _append_parse_error(
    errors: list[dict[str, Any]],
    path: str,
    error: yaml.YAMLError,
) -> None:
    if len(errors) >= MAX_PARSE_ERRORS:
        return
    mark = getattr(error, "problem_mark", None)
    problem = getattr(error, "problem", None) or "Invalid YAML."
    item: dict[str, Any] = {
        "path": path,
        "error": _bounded_text(
            _sanitize_ci_text(str(problem)),
            MAX_ERROR_CHARACTERS,
        ),
    }
    if mark is not None:
        item["line"] = mark.line + 1
        item["column"] = mark.column + 1
    errors.append(item)


def _append_plain_error(
    errors: list[dict[str, Any]],
    path: str,
    error: Exception,
) -> None:
    if len(errors) >= MAX_PARSE_ERRORS:
        return
    errors.append(
        {
            "path": path,
            "error": _bounded_text(
                _sanitize_ci_text(str(error)),
                MAX_ERROR_CHARACTERS,
            ),
        }
    )


def _bounded_text(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else f"{value[: maximum - 3]}..."


__all__ = [
    "MAX_CI_COMMANDS",
    "MAX_CI_FILES",
    "MAX_CI_JOBS",
    "ci_summary",
]
