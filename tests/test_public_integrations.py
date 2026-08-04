import json
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, Event, Lock

import pytest

import lunar_forge.public_api as public_api
from lunar_forge import (
    AgentRequest,
    ApprovalDecision,
    CancellationToken,
    DockerWorkspaceRuntime,
    LocalWorkspaceRuntime,
    ModelResponse,
    RuntimeCheckpoint,
    RuntimeCommandResult,
    RuntimeFileInfo,
    RuntimeNetworkPolicy,
    RuntimeOperationResult,
    RuntimePathType,
    RuntimeRollbackResult,
    RuntimeRollbackStatus,
    RuntimeTextResult,
    RuntimeWriteResult,
    NoCommandWorkspaceRuntime,
    ToolCall,
    WorkspaceRuntime,
    create_ephemeral_model_client,
    create_workspace_runtime,
    normalize_workspace_path,
    run_agent_events,
)
from lunar_forge.config import AppConfig
from lunar_forge.events import EventType


class FakeRemoteRuntime:
    """Deterministic external workspace implementing only the public contract."""

    def __init__(
        self,
        files=None,
        *,
        rollback_status=RuntimeRollbackStatus.COMPLETED,
    ):
        self.files = dict(files or {})
        self.directories = {"."}
        self.commands = []
        self.rollback_status = rollback_status
        self.command_started = Event()
        self.command_release = Event()
        self.command_cancelled = Event()
        self._command_active = False
        self._lock = Lock()
        self._checkpoint_files = None

    @property
    def workspace_id(self):
        return "remote_workspace_test"

    @property
    def local_project_root(self):
        return None

    @property
    def network_policy(self):
        return RuntimeNetworkPolicy.DENIED

    def list_directory(self, path="."):
        relative = normalize_workspace_path(path, allow_root=True)
        prefix = "" if relative == "." else f"{relative}/"
        entries = {}
        for file_path, content in self.files.items():
            if not file_path.startswith(prefix):
                continue
            remainder = file_path[len(prefix) :]
            first = remainder.partition("/")[0]
            entry_path = f"{prefix}{first}" if prefix else first
            if "/" in remainder:
                entries[entry_path] = RuntimeFileInfo(
                    entry_path,
                    RuntimePathType.DIRECTORY,
                )
            else:
                entries[entry_path] = RuntimeFileInfo(
                    entry_path,
                    RuntimePathType.FILE,
                    size_bytes=len(content.encode("utf-8")),
                )
        return tuple(entries[path] for path in sorted(entries))

    def stat(self, path):
        relative = normalize_workspace_path(path, allow_root=True)
        if relative in self.files:
            return RuntimeFileInfo(
                relative,
                RuntimePathType.FILE,
                size_bytes=len(self.files[relative].encode("utf-8")),
            )
        prefix = f"{relative}/"
        if relative == "." or any(path.startswith(prefix) for path in self.files):
            return RuntimeFileInfo(relative, RuntimePathType.DIRECTORY)
        return None

    def read_text(
        self,
        path,
        *,
        start_line=1,
        end_line=None,
        max_characters=50_000,
    ):
        relative = normalize_workspace_path(path)
        content = self.files[relative]
        lines = content.splitlines(keepends=True)
        selected = "".join(lines[start_line - 1 : end_line])
        return RuntimeTextResult(
            relative,
            selected[:max_characters],
            truncated=len(selected) > max_characters,
            start_line=start_line,
            end_line=end_line,
        )

    def write_text(self, path, content, *, overwrite=False):
        relative = normalize_workspace_path(path)
        existed = relative in self.files
        if existed and not overwrite:
            return RuntimeWriteResult(
                False,
                relative,
                error="File already exists.",
            )
        self.files[relative] = content
        return RuntimeWriteResult(
            True,
            relative,
            created=not existed,
            overwritten=existed,
            checkpoint_id="fake_write_checkpoint",
        )

    def create_directory(self, path):
        relative = normalize_workspace_path(path)
        self.directories.add(relative)
        return RuntimeOperationResult(True, relative)

    def delete_path(self, path, *, recursive=False):
        relative = normalize_workspace_path(path)
        if relative in self.files:
            del self.files[relative]
            return RuntimeOperationResult(True, relative)
        prefix = f"{relative}/"
        nested = [path for path in self.files if path.startswith(prefix)]
        if nested and not recursive:
            return RuntimeOperationResult(False, relative, error="Directory is not empty.")
        for nested_path in nested:
            del self.files[nested_path]
        self.directories.discard(relative)
        return RuntimeOperationResult(True, relative)

    def move_path(self, source, destination, *, overwrite=False):
        source_path = normalize_workspace_path(source)
        destination_path = normalize_workspace_path(destination)
        if destination_path in self.files and not overwrite:
            return RuntimeOperationResult(
                False,
                source_path,
                destination=destination_path,
                error="Destination exists.",
            )
        self.files[destination_path] = self.files.pop(source_path)
        return RuntimeOperationResult(
            True,
            source_path,
            destination=destination_path,
        )

    def execute(self, command, *, timeout_ms, max_output_characters=50_000):
        self.commands.append((command, timeout_ms))
        if command == "wait-for-cancel":
            with self._lock:
                self._command_active = True
            self.command_started.set()
            self.command_release.wait(5)
            with self._lock:
                self._command_active = False
            return RuntimeCommandResult(
                False,
                command,
                cancelled=self.command_cancelled.is_set(),
                error="Command was cancelled.",
            )
        return RuntimeCommandResult(
            True,
            command,
            exit_code=0,
            stdout="x" * 75_000 if command == "large-output" else "remote-ok\n",
        )

    def cancel_active_command(self):
        with self._lock:
            active = self._command_active
        if active:
            self.command_cancelled.set()
            self.command_release.set()
        return active

    def checkpoint_turn(self, turn_id):
        if self.rollback_status is RuntimeRollbackStatus.UNSUPPORTED:
            return RuntimeCheckpoint(False, error="Rollback is unavailable.")
        self._checkpoint_files = deepcopy(self.files)
        return RuntimeCheckpoint(True, checkpoint_id=turn_id)

    def rollback_turn(self, checkpoint_id):
        if self.rollback_status is RuntimeRollbackStatus.PARTIAL:
            return RuntimeRollbackResult(
                RuntimeRollbackStatus.PARTIAL,
                skipped_files=("created.txt",),
            )
        before = self._checkpoint_files or {}
        restored = tuple(
            path
            for path, content in before.items()
            if self.files.get(path) != content
        )
        removed = tuple(path for path in self.files if path not in before)
        self.files = deepcopy(before)
        return RuntimeRollbackResult(
            RuntimeRollbackStatus.COMPLETED,
            restored_files=restored,
            removed_files=removed,
        )


class SequenceModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, messages, tools=None):
        return self.responses.pop(0)


class CancellingWriteModel:
    def __init__(self, token):
        self.token = token
        self.calls = 0

    def complete(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        "write_remote",
                        "write_file",
                        {"path": "created.txt", "content": "new"},
                    ),
                ),
            )
        self.token.request_cancel(rollback=True)
        return ModelResponse(text="must not be emitted")


class ApproveAll:
    def __init__(self):
        self.requests = []

    def request_approval(self, request):
        self.requests.append(request)
        return ApprovalDecision.create(
            request.id,
            approved=True,
            reason="Approved by deterministic test provider.",
            source="cli",
        )


@pytest.fixture(autouse=True)
def deterministic_public_config(monkeypatch):
    monkeypatch.setattr(public_api, "load_config", lambda *args, **kwargs: AppConfig())


def test_remote_runtime_is_structural_and_supports_file_operations():
    runtime = FakeRemoteRuntime({"README.md": "hello\n"})

    assert isinstance(runtime, WorkspaceRuntime)
    assert runtime.stat("README.md").type is RuntimePathType.FILE
    assert runtime.read_text("README.md").content == "hello\n"
    assert runtime.create_directory("src").ok is True
    assert runtime.write_text("src/app.py", "print('ok')\n").created is True
    assert runtime.move_path("src/app.py", "src/main.py").ok is True
    assert runtime.delete_path("src/main.py").ok is True
    assert [entry.path for entry in runtime.list_directory(".")] == ["README.md"]


def test_remote_runtime_runs_agent_tools_without_a_local_path():
    runtime = FakeRemoteRuntime({"README.md": "remote\n"})
    approvals = ApproveAll()
    model = SequenceModel(
        (
            ModelResponse(
                text="",
                tool_calls=(ToolCall("read", "read_file", {"path": "README.md"}),),
            ),
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        "write",
                        "write_file",
                        {"path": "result.txt", "content": "done\n"},
                    ),
                ),
            ),
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        "command",
                        "run_command",
                        {"command": "echo remote"},
                    ),
                ),
            ),
            ModelResponse(text="Remote work complete."),
        )
    )

    events = list(
        run_agent_events(
            AgentRequest(None, "Read, write, and run the requested remote checks."),
            approvals,
            runtime=runtime,
            model_client=model,
        )
    )

    assert runtime.files["result.txt"] == "done\n"
    assert runtime.commands == [("echo remote", 120_000)]
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[0].payload["project_root"] is None
    assert events[0].payload["runtime_mode"] == "remote"
    assert events[0].payload["network_policy"] == "denied"
    requested = [event for event in events if event.type == "permission.requested"]
    resolved = [event for event in events if event.type == "permission.resolved"]
    assert len(requested) == len(resolved) == 2
    assert {event.payload["request_id"] for event in requested} == {
        event.payload["request_id"] for event in resolved
    }
    assert all(event.parent_event_id in {item.event_id for item in requested} for event in resolved)


def test_runtime_paths_are_confined_and_command_output_is_bounded(tmp_path):
    runtime = LocalWorkspaceRuntime(tmp_path)

    with pytest.raises(PermissionError):
        normalize_workspace_path("../outside.txt")
    with pytest.raises(PermissionError):
        runtime.write_text("../outside.txt", "blocked")

    remote = FakeRemoteRuntime()
    result = remote.execute("large-output", timeout_ms=1_000)
    assert result.ok is True
    assert result.stdout_truncated is True
    assert len(result.stdout) <= 50_000


def test_built_in_runtime_factory_preserves_existing_execution_modes(tmp_path):
    local = create_workspace_runtime(tmp_path, mode="local")
    docker = create_workspace_runtime(tmp_path, mode="docker")
    no_command = create_workspace_runtime(tmp_path, mode="no-command")

    assert isinstance(local, LocalWorkspaceRuntime)
    assert isinstance(docker, DockerWorkspaceRuntime)
    assert isinstance(no_command, NoCommandWorkspaceRuntime)
    denied = no_command.execute("python -V", timeout_ms=1_000)
    assert denied.ok is False
    assert "disabled" in denied.error


def test_runtime_command_can_be_cancelled_from_another_thread():
    runtime = FakeRemoteRuntime(rollback_status=RuntimeRollbackStatus.UNSUPPORTED)
    token = CancellationToken()
    approvals = ApproveAll()
    model = SequenceModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        "wait",
                        "run_command",
                        {"command": "wait-for-cancel", "timeout_ms": 30_000},
                    ),
                ),
            ),
        )
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            lambda: list(
                run_agent_events(
                    AgentRequest(None, "Run the requested bounded command."),
                    approvals,
                    runtime=runtime,
                    model_client=model,
                    cancellation_token=token,
                )
            )
        )
        assert runtime.command_started.wait(2)
        assert token.request_cancel(rollback=True) is True
        assert token.request_cancel(rollback=True) is False
        events = future.result(timeout=5)

    result = token.wait_result(1)
    assert result is not None
    assert result.cancelled is True
    assert result.runtime_command_cancelled is True
    assert result.rollback.status is RuntimeRollbackStatus.UNSUPPORTED
    types = [event.type for event in events]
    assert EventType.TURN_CANCELLED.value in types
    assert EventType.ROLLBACK_STARTED.value in types
    assert EventType.ROLLBACK_FINISHED.value in types
    assert types[-1] == EventType.TURN_FINISHED.value


@pytest.mark.parametrize(
    ("status", "file_retained"),
    (
        (RuntimeRollbackStatus.COMPLETED, False),
        (RuntimeRollbackStatus.PARTIAL, True),
        (RuntimeRollbackStatus.UNSUPPORTED, True),
    ),
)
def test_public_cancellation_reports_rollback_outcomes(status, file_retained):
    runtime = FakeRemoteRuntime(rollback_status=status)
    token = CancellationToken()
    approvals = ApproveAll()

    events = list(
        run_agent_events(
            AgentRequest(None, "Create the remote file, then stop."),
            approvals,
            runtime=runtime,
            model_client=CancellingWriteModel(token),
            cancellation_token=token,
        )
    )

    result = token.result
    assert result is not None
    assert result.rollback.status is status
    assert ("created.txt" in runtime.files) is file_retained
    finished = next(
        event for event in events if event.type == EventType.ROLLBACK_FINISHED.value
    )
    assert finished.payload["status"] == status.value


def test_injected_model_credentials_are_redacted_from_events_sessions_and_errors(tmp_path):
    credential = "credential-not-pattern-matched-12345678"

    class LeakyModel:
        def sensitive_values_for_redaction(self):
            return (credential,)

        def complete(self, messages, tools=None):
            return ModelResponse(text=f"accidental echo: {credential}")

    events = list(
        run_agent_events(
            AgentRequest(tmp_path, "Return a short answer."),
            model_client=LeakyModel(),
        )
    )
    serialized = json.dumps([event.to_dict() for event in events])
    session_text = next((tmp_path / ".agent" / "sessions").glob("*.jsonl")).read_text(
        encoding="utf-8"
    )

    assert credential not in serialized
    assert credential not in session_text
    assert "[REDACTED]" in serialized

    class FailingLeakyModel(LeakyModel):
        def complete(self, messages, tools=None):
            raise RuntimeError(f"provider exposed {credential}")

    with pytest.raises(Exception) as raised:
        list(
            run_agent_events(
                AgentRequest(tmp_path, "Fail safely."),
                model_client=FailingLeakyModel(),
            )
        )
    assert credential not in str(raised.value)


def test_simultaneous_ephemeral_clients_do_not_share_credentials(monkeypatch):
    requests = []
    lock = Lock()
    environment_before = dict(os.environ)

    def completion(**request):
        with lock:
            requests.append(dict(request))
        return {
            "model": request["model"],
            "choices": [{"message": {"content": request["model"]}}],
        }

    monkeypatch.setattr(
        "lunar_forge.model_clients.litellm_client._litellm_completion",
        completion,
    )
    clients = (
        create_ephemeral_model_client(
            model="openai/gpt-test",
            api_key="openai-turn-secret-12345678",
        ),
        create_ephemeral_model_client(
            model="anthropic/claude-test",
            api_key="anthropic-turn-secret-12345678",
        ),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = tuple(
            executor.map(
                lambda client: client.complete(
                    [{"role": "user", "content": "hello"}]
                ),
                clients,
            )
        )

    by_model = {request["model"]: request["api_key"] for request in requests}
    assert by_model == {
        "openai/gpt-test": "openai-turn-secret-12345678",
        "anthropic/claude-test": "anthropic-turn-secret-12345678",
    }
    assert {response.text for response in responses} == {
        "openai/gpt-test",
        "anthropic/claude-test",
    }
    assert dict(os.environ) == environment_before
    assert "openai-turn-secret-12345678" not in repr(clients[0])
    assert "anthropic-turn-secret-12345678" not in repr(clients[1])


def test_simultaneous_agent_runs_use_isolated_injected_clients():
    rendezvous = Barrier(2)

    class IsolatedClient:
        def __init__(self, label, credential):
            self.label = label
            self._credential = credential

        def sensitive_values_for_redaction(self):
            return (self._credential,)

        def complete(self, messages, tools=None):
            rendezvous.wait(timeout=2)
            return ModelResponse(
                text=f"{self.label}:{self._credential}"
            )

    runs = (
        ("first", "first-client-secret-12345678"),
        ("second", "second-client-secret-12345678"),
    )

    def invoke(run):
        label, credential = run
        events = list(
            run_agent_events(
                AgentRequest(None, f"Complete the {label} run."),
                runtime=FakeRemoteRuntime(),
                model_client_factory=lambda: IsolatedClient(label, credential),
            )
        )
        return json.dumps([event.to_dict() for event in events])

    with ThreadPoolExecutor(max_workers=2) as executor:
        serialized_runs = tuple(executor.map(invoke, runs))

    assert "first:" in serialized_runs[0]
    assert "second:" not in serialized_runs[0]
    assert "second:" in serialized_runs[1]
    assert "first:" not in serialized_runs[1]
    assert all("client-secret" not in result for result in serialized_runs)


def test_active_model_cancellation_uses_optional_client_capability():
    started = Event()
    released = Event()

    class BlockingCancellableModel:
        def complete(self, messages, tools=None):
            started.set()
            released.wait(5)
            return ModelResponse(text="cancelled model response")

        def cancel_active(self):
            released.set()
            return True

    token = CancellationToken()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            lambda: list(
                run_agent_events(
                    AgentRequest(None, "Wait for model cancellation."),
                    runtime=FakeRemoteRuntime(
                        rollback_status=RuntimeRollbackStatus.UNSUPPORTED
                    ),
                    model_client=BlockingCancellableModel(),
                    cancellation_token=token,
                )
            )
        )
        assert started.wait(2)
        assert token.request_cancel(rollback=False) is True
        events = future.result(timeout=5)

    result = token.wait_result(1)
    assert result is not None
    assert result.model_operation_cancelled is True
    assert result.rollback.status is RuntimeRollbackStatus.NOT_REQUESTED
    assert EventType.ROLLBACK_STARTED.value not in {
        event.type for event in events
    }
