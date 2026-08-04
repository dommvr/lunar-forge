# Stable public integration API

The symbols exported by `lunar_forge.public_api` and re-exported from
`lunar_forge` are the supported, UI-neutral integration boundary. External
applications should not import agent-loop, terminal UI, tool-registry, session,
or provider implementation modules directly.

Existing calls remain valid:

```python
events = run_agent_events(AgentRequest(project_root=project, message=prompt))
```

They continue to use project/user/environment configuration, the selected
local/Docker/no-command execution mode, persisted local sessions, and the same
approval defaults. The optional integration arguments below are keyword-only.

For synchronous worker integrations, `live_event_callback` observes
session-derived events while the agent call is active. Those same events remain
in the iterator afterward for backward compatibility and replay collection, so
consumers using both paths should de-duplicate by `event_id`.

## Runtime boundary

`WorkspaceRuntime` is a structural `Protocol`; an external adapter does not
subclass a core implementation. All paths passed through it are POSIX-style,
project-relative paths. Both the core and the adapter must enforce confinement.

The protocol provides:

- `workspace_id`, `local_project_root`, and `network_policy` properties;
- bounded directory listing, metadata, and UTF-8 text reads;
- create/replace text, create directory, delete, and move operations;
- bounded command execution with a mandatory timeout and bounded stdout/stderr;
- best-effort active-command cancellation; and
- optional current-turn checkpoint and conservative rollback operations.

Portable value objects are `RuntimeFileInfo`, `RuntimeTextResult`,
`RuntimeWriteResult`, `RuntimeOperationResult`, `RuntimeCommandResult`,
`RuntimeCheckpoint`, and `RuntimeRollbackResult`. The corresponding enums are
`RuntimePathType`, `RuntimeNetworkPolicy`, and `RuntimeRollbackStatus`.
`normalize_workspace_path()` and the exported `MAX_RUNTIME_*` constants define
the core bounds.

`LocalWorkspaceRuntime`, `DockerWorkspaceRuntime`, and
`NoCommandWorkspaceRuntime` are compatible built-in adapters. Use
`create_workspace_runtime()` to select one without changing existing defaults.
The core has no E2B dependency. E2B is one possible implementation of
`WorkspaceRuntime` in an external service.

A non-local runtime is invoked with `AgentRequest(project_root=None, ...)`.
Supplying a local path alongside a runtime whose `local_project_root` is `None`
is rejected to avoid accidentally reading one workspace and writing another.
Remote event records are not written to a local `.agent` directory; the caller
is responsible for bounded persistence and replay outside the core.

## Per-run model injection

`run_agent_events()` accepts either `model_client` or `model_client_factory`,
never both. `ModelClient`, `ModelClientFactory`, `ModelResponse`, `ModelUsage`,
and `ToolCall` are provider-neutral stable types. An injected factory is called
once for that public run. Existing configuration-based LiteLLM setup remains
the default when neither argument is supplied.

`create_ephemeral_model_client()` creates an OpenAI or Anthropic LiteLLM client
whose API key is held only in that client instance. It does not modify
`os.environ`, configuration, `AgentRequest`, sessions, or events. Discard the
client after the turn. Custom clients that retain a credential should implement
the optional `RedactingModelClient.sensitive_values_for_redaction()` capability
so the core can remove those values from events, session logs, final output, and
raised error messages. Model clients must not include credentials in tool
arguments or other application data.

## Cancellation and rollback

Create one `CancellationToken` per run and pass it to `run_agent_events()`.
Another task or thread may call `request_cancel(rollback=True)`. The first call
returns `True`; repeated calls return `False`. While an operation is active the
token invokes `CancellableModelClient.cancel_active()` and/or
`WorkspaceRuntime.cancel_active_command()` when supported.

Cancellation uses the existing ordered event types:

1. `turn.cancelled`;
2. `rollback.started` and `rollback.finished` when rollback was requested; and
3. `turn.finished` with status `cancelled`.

`token.wait_result()` or `token.result` returns a `CancellationResult` containing
model/runtime cancellation flags and a `RuntimeRollbackResult`. Rollback status
is one of `completed`, `partial`, `unsupported`, `not_requested`, or `failed`.
The result never claims success before the runtime confirms it.

## External application example

```python
from lunar_forge import (
    AgentRequest,
    CancellationToken,
    create_ephemeral_model_client,
    run_agent_events,
)

runtime = MyRemoteRuntime(workspace_id="sandbox_123")  # WorkspaceRuntime
token = CancellationToken()

def model_factory():
    return create_ephemeral_model_client(
        model="openai/gpt-5.6-sol",  # or anthropic/<model>
        api="responses",
        api_key=get_key_from_current_request_memory(),
        reasoning_effort="high",
    )

# A separate control handler may call this while iteration is active:
def cancel_turn():
    return token.request_cancel(rollback=True)

seen_event_ids = set()

def publish_live(event):
    if event.event_id not in seen_event_ids:
        seen_event_ids.add(event.event_id)
        websocket_send(event.to_dict())

for event in run_agent_events(
    AgentRequest(
        project_root=None,
        message="Inspect the project, make the requested change, and validate it.",
    ),
    approval_provider=my_approval_provider,
    runtime=runtime,
    model_client_factory=model_factory,
    cancellation_token=token,
    live_event_callback=publish_live,
):
    publish_live(event)

result = token.wait_result(timeout=0)
if result is not None:
    record_cancellation_metadata(result.to_dict())
```

The caller owns authentication, quotas, event-stream persistence, remote
workspace lifecycle, previewing, and provider-specific network controls. Those
are not implemented by LunarForge core.

## Stable exports

The stable public groups are:

- invocation and events: `AgentRequest`, `AgentEvent`, `run_agent_events`;
- approvals: `ApprovalRequest`, `ApprovalDecision`, `ApprovalProvider`;
- sessions: `SessionRef`, `ResumedSession`, `list_sessions`, `resume_session`;
- configuration: `load_config`;
- models: `ModelClient`, `ModelClientFactory`, `RedactingModelClient`,
  `CancellableModelClient`, `ModelResponse`, `ModelUsage`, `ToolCall`, and
  `create_ephemeral_model_client`;
- cancellation: `CancellationToken`, `CancellationResult`;
- runtime: `WorkspaceRuntime`, the runtime result/enums listed above,
  `normalize_workspace_path`, the `MAX_RUNTIME_*` constants, the three built-in
  adapters, and `create_workspace_runtime`.

No stable public symbol imports or exposes Rich or Textual types.
