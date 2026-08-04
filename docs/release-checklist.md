# LunarForge core release checklist

Use this checklist for the stabilized CLI/TUI milestone. Run destructive or
stateful smoke tests only in disposable projects. Do not commit generated
`.agent` data.

## Release environment

- [ ] Python 3.11 or newer is active.
- [ ] The repository contains only intended source, test, docs, and example
  changes.
- [ ] Required model API keys exist only in environment variables.
- [ ] Docker Desktop/engine is running if Docker checks will be performed.
- [ ] Playwright and its browser are installed if browser checks will be
  performed.

## Editable install and import boundary

```powershell
python -m pip install -e ".[dev]"
python -c "from lunar_forge import AgentRequest, AgentEvent, ApprovalRequest, ApprovalDecision, CancellationToken, ModelClient, WorkspaceRuntime, SessionRef, load_config, list_sessions, resume_session, run_agent_events; print('public API OK')"
lunar-forge --help
lunar-forge chat --help
```

- [ ] The public import succeeds without importing Textual.
- [ ] `chat --help` works without eager Textual app loading.
- [ ] Without `.[tui]`, starting chat prints the documented install guidance.
- [ ] The public runtime/model/cancellation imports add no E2B, web, Rich, or
  Textual dependency.

## External runtime integration smoke

Use deterministic fakes; no provider credential or live sandbox is required:

```powershell
python -m pytest -q tests/test_public_integrations.py tests/test_public_api.py tests/test_events.py
```

- [ ] `run_agent_events(AgentRequest(...))` retains its existing behavior.
- [ ] A request with `project_root=None` runs through a fake `WorkspaceRuntime`.
- [ ] Remote file and command results are bounded and project-confined.
- [ ] Approval request/resolution IDs remain correlated.
- [ ] Cancellation from another thread cancels supported model/runtime work.
- [ ] Completed, partial, and unsupported rollback results emit the existing
  ordered rollback events.
- [ ] Concurrent runs use distinct injected clients without environment
  mutation or credential cross-contamination.

## One-shot CLI smoke

In a disposable project:

```powershell
lunar-forge --project $SmokeProject "Explain this project in one sentence. Do not edit files. Do not run commands."
lunar-forge --plan --project $SmokeProject "Plan a harmless documentation change."
lunar-forge --show-usage --reasoning-effort high --project $SmokeProject "Explain this project. Do not edit files. Do not run commands."
```

- [ ] Read-only output includes a final answer.
- [ ] Plan mode writes no project or runtime file.
- [ ] Usage output names the effective reasoning effort.
- [ ] Existing one-shot output shape remains concise.

## Textual chat and slash commands

Install the optional UI when this part of the release is being checked:

```powershell
python -m pip install -e ".[tui]"
lunar-forge chat --project $SmokeProject
```

Inside chat:

```text
/help
/status
Explain this project in one sentence. Do not edit files. Do not run commands.
/compact
/sessions
/finish
/exit
```

- [ ] The new-session greeting and `LunarForge v0.x` border are correct.
- [ ] A second turn uses the same live session.
- [ ] Temporary progress is event-driven and the final answer includes elapsed
  time.
- [ ] The ellipsis visibly cycles `.`, `..`, `...` in about two seconds.
- [ ] `/compact` uses the existing compaction flow.
- [ ] During an active fake or harmless long turn, `/finish` requests
  cancellation and does not close chat or roll back unrelated files.
- [ ] Slash commands do not accidentally start agent turns.
- [ ] Approval dialogs use approval providers rather than raw `input()`.

Resume:

```powershell
lunar-forge chat --resume latest --project $SmokeProject
```

- [ ] The resumed greeting is shown.
- [ ] Safe conversation context is restored.
- [ ] Historical tools are not replayed.
- [ ] Historical approvals are not reused.

## Docker smoke

Run only when Docker Desktop/engine is installed and running:

```powershell
docker info
docker build -t lunar-forge-sandbox -f lunar_forge/sandbox/Dockerfile .
lunar-forge --docker --project $SmokeProject "Use run_command to run python --version. Do not edit files."
```

- [ ] Approval wording identifies Docker and `/workspace`.
- [ ] The command runs inside the `lunar-forge-sandbox` container.
- [ ] Privileged mode, Docker socket mounts, and project-root escapes remain
  blocked.

## Browser validation smoke

Run only when Playwright is installed:

```powershell
python -m pip install -e ".[browser]"
playwright install chromium
lunar-forge browser-setup
npm --prefix examples/projects/browser-demo install
lunar-forge browser-validate --project examples/projects/browser-demo --serve "npm run dev" --url http://localhost:5173 --check "#main-heading"
```

- [ ] Missing dependencies otherwise produce setup guidance.
- [ ] Managed server startup asks for approval.
- [ ] Screenshots stay project-local and are not uploaded or committed.

## Plugin and MCP diagnostics

```powershell
lunar-forge plugins list --project examples/projects/browser-demo
lunar-forge mcp list --project $SmokeProject
```

- [ ] Plugin diagnostics remain bounded and do not auto-enable tools.
- [ ] For the configured MCP check, copy a reviewed example MCP config into the
  disposable project, explicitly enable it, rerun `mcp list`, and approve only
  the expected server start.
- [ ] MCP diagnostics work when configured and do not invoke an unrequested
  tool.
- [ ] No diagnostics output contains secrets.

## Git dry path

Use a disposable Git repository:

```powershell
git -C $SmokeProject status --short
lunar-forge git status --project $SmokeProject
```

Then exercise a harmless candidate commit through the normal agent or Textual
flow without approving the final commit:

- [ ] Validation status is visible before commit approval.
- [ ] Failed validation blocks the normal commit path.
- [ ] Committing despite failed validation is explicit and separately approved.
- [ ] Runtime, generated, secret, and unrelated dirty files are excluded.
- [ ] Denying approval creates no commit.

## Runtime-artifact audit

From the LunarForge repository root:

```powershell
$TrackedRuntime = git ls-files |
  Where-Object { $_ -match '(^|/)\.agent/(sessions|summaries|checkpoints|artifacts)(/|$)' }
if ($TrackedRuntime) {
  throw "Tracked runtime files found: $($TrackedRuntime -join ', ')"
}
git ls-files .agent
git status --short --untracked-files=all
```

- [ ] No session log, compacted summary, checkpoint, browser artifact,
  screenshot, or disposable project is tracked.
- [ ] `.agent/config.yaml` appears only when deliberately included as an
  example/config asset.
- [ ] Repository status contains only intended release changes.

## Automated validation

Run focused compatibility suites:

```powershell
python -m pytest -q tests/test_public_integrations.py tests/test_public_api.py tests/test_events.py tests/test_textual_ui.py tests/test_sessions.py tests/test_compaction.py tests/test_slash_commands.py tests/test_approvals.py
```

Run the complete gate:

```powershell
python -m pytest -q
python -B -m compileall lunar_forge
python -B -m compileall examples/plugins/web-design-review
git diff --check
git status --short
git ls-files .agent
```

- [ ] All tests pass.
- [ ] Both compile checks report no errors.
- [ ] `git diff --check` reports no whitespace errors.
- [ ] Core events remain bounded, redacted, JSON-safe, and UI-neutral.
- [ ] Core runtime, conversation, compaction, and public API modules have no
  Rich/Textual imports.
- [ ] No FastAPI, web framework, frontend, cloud runtime, auth, or deployment
  dependency was added.

## Final commit readiness

- [ ] Review `git diff --stat` and `git diff`.
- [ ] Confirm one-shot CLI behavior was not intentionally changed.
- [ ] Confirm Textual is still confined to the optional `tui` extra and lazy UI
  imports.
- [ ] Confirm public API names and event schema changes are deliberate and
  compatibility-reviewed.
- [ ] Confirm docs reflect known limitations rather than promising a hosted or
  OS-isolated product.
- [ ] Confirm the commit contains no secrets or generated runtime artifacts.
