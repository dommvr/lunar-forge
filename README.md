# LunarForge

LunarForge is a small Python CLI coding agent for inspecting, planning, editing,
and validating a single local project. It loads project guidance from
`AGENTS.md`, uses LiteLLM for model access, exposes a provider-neutral tool loop,
and keeps file operations inside the selected project root.

The current MVP supports:

- bounded project inspection and search;
- plan-only, permission-gated edit, and no-command modes;
- exact file edits with pre-change checkpoints;
- approved local or optional Docker command execution;
- project-aware Python and Node validation;
- redacted JSONL session logs;
- six declarative new-project starters;
- deterministic, optional specialist subagents;
- disabled-by-default MCP and local plugin adapters;
- optional local browser validation; and
- checkpoint, rollback, session resume, and utility commands.

## Requirements and installation

LunarForge requires Python 3.11 or newer. Create a virtual environment and
install the package from the repository:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

On POSIX shells, activate with `source .venv/bin/activate`. The `dev` extra adds
pytest; use `python -m pip install -e .` for runtime dependencies only.

Confirm the CLI is available:

```bash
lunar-forge --help
```

## Configuration

Configuration is merged in this priority order, highest first:

1. CLI flags
2. `<project>/.agent/config.yaml`
3. `~/.lunar-forge/config.yaml`
4. `LUNAR_FORGE_*` environment variables
5. built-in defaults

Example configuration:

```yaml
model:
  provider: litellm
  api: chat
  model: openai/gpt-5.5
  api_key_env: OPENAI_API_KEY
  api_base: null

runtime:
  mode: local
  allow_network: false

permissions:
  mode: default

subagents:
  enabled: false
  parallel: false

# Experimental and disabled unless explicitly enabled.
mcp:
  enabled: false

plugins:
  enabled: false
```

Set the named API-key environment variable in your shell. Do not put a raw API
key in either YAML file; `model.api_key` is rejected.

```powershell
$env:OPENAI_API_KEY = "your-key"
```

Supported environment overrides are:

- `LUNAR_FORGE_MODEL_PROVIDER`
- `LUNAR_FORGE_MODEL`
- `LUNAR_FORGE_MODEL_API`
- `LUNAR_FORGE_API_KEY_ENV`
- `LUNAR_FORGE_API_BASE`
- `LUNAR_FORGE_RUNTIME_MODE`
- `LUNAR_FORGE_ALLOW_NETWORK`
- `LUNAR_FORGE_PERMISSION_MODE`
- `LUNAR_FORGE_SUBAGENTS`
- `LUNAR_FORGE_PARALLEL_SUBAGENTS`
- `LUNAR_FORGE_MCP_ENABLED`
- `LUNAR_FORGE_PLUGINS_ENABLED`

LiteLLM model identifiers can also target providers such as Anthropic or a
local OpenAI-compatible service. For example, an Ollama model can use
`ollama/qwen2.5-coder` with `api_base: http://localhost:11434`. Local models may
have unreliable tool-calling support, so read-only planning is the safer first
test.

`model.api` defaults to `chat`, preserving the existing
`litellm.completion()` path. Set it to `responses` for models that need
LiteLLM's Responses API, including GPT-5.6 reasoning with function tools:

```yaml
model:
  provider: litellm
  api: responses
  model: openai/gpt-5.6-terra
  api_key_env: OPENAI_API_KEY
  api_base: null
```

Responses mode requires a LiteLLM release that exposes `litellm.responses`
(LiteLLM documents support in 1.63.8 and newer). If that function is unavailable,
LunarForge reports a clear upgrade-or-use-chat error.

### Experimental MCP integration

MCP support is experimental and disabled by default. It uses two explicit
opt-ins: set `mcp.enabled: true` in `.agent/config.yaml` (or user config), then
enable each server separately in `.agent/mcp.yaml` or
`~/.lunar-forge/mcp.yaml`. Enabled local servers are launched as stdio
subprocesses with `shell=False`; their executable is resolved with the same
PATH/PATHEXT handling used by local commands.

On Windows, a Playwright MCP configuration can use:

`.agent/config.yaml`:

```yaml
mcp:
  enabled: true
```

`.agent/mcp.yaml`:

```yaml
servers:
  playwright:
    command: npx.cmd
    args:
      - -y
      - "@playwright/mcp@latest"
      - "--isolated"
    enabled: true
```

Raw credentials are rejected; `env` values name environment variables using
`${NAME}` references. Discovered tools are namespaced, such as
`mcp.playwright.browser_navigate`, and every MCP call passes through the normal
approval system. Plan mode exposes only tools carrying the standard MCP
`annotations.readOnlyHint: true`, and those read-only external calls still
require approval.

Names shown by MCP diagnostics remain the dotted internal identities. Tool
schemas sent to model providers use safe aliases instead—for example,
`mcp.playwright.browser_navigate` becomes
`mcp_playwright_browser_navigate`. Returned model calls are resolved back to
the internal identity before permission checks and MCP routing.

Inspect configuration and perform bounded startup/tool discovery without a
model or API key:

```powershell
lunar-forge mcp list --project C:\path\to\project
```

The diagnostic prints loaded config files, globally and individually enabled
state, disabled servers, namespaced discovered tools, and bounded startup or
discovery errors. Running it starts servers only when both opt-ins are enabled.
An `npx -y` server may download its configured package, so review MCP config
before running discovery. Server stderr is drained but never placed in model
context or diagnostic output, and configured environment values are resolved
only for the child process. Servers inherit a small operational environment
(such as PATH, temporary-directory, and platform runtime variables) plus the
variables explicitly mapped in `env`; unrelated model/API credentials are not
forwarded automatically.

### Experimental local plugins

Plugins are experimental and disabled by default. Enabling one requires two
project-local opt-ins. First set `plugins.enabled: true` in
`.agent/config.yaml`:

```yaml
plugins:
  enabled: true
```

Then name each manifest explicitly in `.agent/plugins.yaml`:

```yaml
plugins:
  example:
    manifest: plugin_packs/example/plugin.yaml
    enabled: true
```

For example, `plugin_packs/example/plugin.yaml` keeps the model-facing schema
and capabilities explicit:

```yaml
name: example
version: 0.1.0
description: Example local tools
tools:
  - name: example.echo
    description: Echo a message
    entrypoint: example_plugin:echo
    parameters:
      type: object
      properties:
        message: {type: string}
      required: [message]
      additionalProperties: false
    permissions:
      filesystem: read
      commands: false
      network: false
```

Its bundle-local `plugin_packs/example/example_plugin.py` entrypoint can be as
small as:

```python
def echo(message):
    return {"ok": True, "echo": message}
```

The referenced module must live beneath the manifest directory. LunarForge does
not scan arbitrary directories, fetch remote plugin code, or import a handler
while discovering tools. It validates the manifest, registers the namespaced
tool, asks through the normal permission system, and only then loads and invokes
the local entrypoint. Tools declaring filesystem writes, commands, or network
access are always permission-gated. All plugin tools are omitted from plan mode
because in-process code cannot enforce a manifest's read-only claim. Plugin
arguments, results, and exceptions are contained and bounded before entering
model context.

Plugin diagnostics and permission requests use the manifest's dotted internal
name, such as `example.echo`. Model providers see its safe alias,
`example_echo`; model calls using that alias are resolved back to
`example.echo` before approval and execution.

Inspect the complete plugin configuration deterministically, without a model,
API key, permission prompt, or plugin-code import:

```powershell
lunar-forge plugins list --project C:\path\to\project
```

The JSON report includes the loaded user/project `config.yaml` files, the
project `.agent/plugins.yaml` path, global and per-plugin enablement, manifest
paths, dotted internal tool names, provider-safe model names, and bounded
config or manifest errors. Every explicitly configured manifest is validated,
including disabled plugins, so path escapes and unknown schema keys are
reported before enablement.

After reviewing the diagnostic, an enabled echo plugin can be exercised through
the normal model and permission flow:

```powershell
lunar-forge --project C:\path\to\project "Call example.echo with the message hello"
```

Plugin capability declarations are a trust contract, not an operating-system
sandbox. Enable only code you have reviewed. The loader intentionally supports
simple bundle-local Python modules; plugin dependency management and isolated
worker processes are not implemented.

## Basic usage

Run against the current directory:

```bash
lunar-forge "Explain this repository"
lunar-forge "Add a small feature and run validation"
```

Select another project or request a read-only plan:

```bash
lunar-forge --project ../my-app "Explain the routing structure"
lunar-forge --project ../my-app --plan "Add a pricing page"
```

`--plan` exposes only read and search tools. It does not edit files, run
commands, or create `.agent` runtime files. In the default permission mode,
LunarForge asks before each file mutation and command. `permissions.mode: yes`
auto-approves safe writes but still asks before commands and dependency
installation. `permissions.mode: no-command` disables command tools.

For an existing-project feature request, the system prompt requires inspection,
a short plan before the first edit, permission-gated changes, and validation
when practical. If validation fails, the agent is instructed to attempt at most
one focused fix.

### Bounded file inspection and precise edits

The model-facing file tools include three line-oriented operations in addition
to exact-text `edit_file`:

```text
read_file_with_line_numbers({"path": "src/app.py", "start_line": 20, "end_line": 60})
replace_lines({"path": "src/app.py", "start_line": 31, "end_line": 34, "new_text": "..."})
insert_lines({"path": "src/app.py", "after_line": 34, "new_text": "..."})
```

Line numbers are stable, one-based source line numbers. `replace_lines` uses an
inclusive range, while `insert_lines` inserts after the selected line and
accepts `after_line: 0` for insertion at file top. Numbered reads are bounded by
the same line and character limits as ordinary reads and report truncation.
Before a precise edit, the prompt tells the model to refresh the target range
with `read_file_with_line_numbers`; exact block replacement still uses
`edit_file`.

All paths pass through project-root confinement. Invalid and out-of-file ranges
fail before a write, every successful change to an existing file creates a
checkpoint, newline style is preserved when practical, and edit results include
a bounded unified diff. The mutation tools are not registered in plan mode.
Coder subagents can use all three tools through their own restricted registry;
read-only roles receive only the numbered reader.

### Optional subagent mode

Single-agent execution remains the default. Pass `--subagents`, or set
`subagents.enabled: true`, to use a finite specialist sequence. Sequential
subagent execution also remains the default. Parallel phases require the
additional `--parallel-subagents` flag or `subagents.parallel: true` setting:

```yaml
subagents:
  enabled: true
  parallel: true
```

```bash
lunar-forge --project ../my-app --parallel-subagents "Add a pricing page"
lunar-forge new "Build a Vite site" --project ../new-app --parallel-subagents
```

Existing-project work keeps Coder serialized. Planner and the read-only Security
role run together when the request names permissions, shell execution, Docker,
MCP, plugins, or configuration. After edits, Tester and Reviewer may run
together. New-project work keeps Scaffolder serialized, then may run Tester and
Reviewer together. Tester can use only its existing permission-gated validation
tools; no parallel role receives file mutation tools.

Each role receives an explicit tool allowlist and cannot obtain tools outside
it. Every concurrent role receives a separate restricted registry view and
conversation list. All allowed mutations and commands still pass through the
central registry, normal permission prompts, and session logging. Session
lifecycle events include role, phase, parallel group, and start/completion/error
state. Successful sibling results remain visible when one parallel role fails,
and final merge/report order follows the declared phase order rather than thread
completion order. Production parallel roles receive separate model-client
instances so mutable provider response state is not shared. Explicit custom
clients injected through the Python API cannot be cloned generically and must
be thread-safe. This is deterministic role handoff, not an autonomous debate or
self-spawning agent loop.

## Project instructions (`AGENTS.md`)

At session start, LunarForge loads a root `AGENTS.md` from the target project if
one exists. The content is size-limited and included as untrusted project
context. It can guide conventions and validation choices, but it cannot override
path safety, permissions, command blocking, Docker restrictions, or plan mode.

Nested `AGENTS.md` files are discovered and applied automatically by target file
path. The instruction stack is ordered from the root toward the most specific
containing directory, and file tools report the applicable project-relative
stack. Nested instructions remain untrusted and cannot expand filesystem access
or bypass safety policy. When no root instructions exist, the model receives a
clear fallback notice.

Search and file-reading tools skip generated or sensitive runtime directories,
including `.git`, `.agent`, `node_modules`, virtual environments,
`__pycache__`, `.next`, `dist`, `build`, and `coverage`.

## Local command mode

Local mode is the default. Approved commands run with the project root as their
working directory using `subprocess.run(..., shell=False)`. Normal executables
and quoted arguments are supported; shell built-ins, pipes, redirects, and
operators such as `&&` are intentionally unsupported.

Executables are resolved from `PATH` with `shutil.which`. On Windows, LunarForge
also applies validated `PATHEXT` candidates, so commands such as `npm`, `npx`,
`pnpm`, and `yarn` resolve to their `.cmd` launchers without enabling a shell. A
missing executable reports its name, the PATH entry count, and a validated
PATHEXT candidate count without printing potentially sensitive environment
contents.

Dangerous command patterns are checked before parsing and again after argument
normalization. The denylist includes recursive destructive operations, privilege
escalation, SSH/SCP, `.env` and SSH-key access, pipe-to-shell installers, raw
Docker wrappers, privileged containers, and Docker socket access.

Working-directory scoping is not an operating-system filesystem sandbox. A
locally executed program runs with the current user's OS permissions and may be
able to access paths outside the project. Review every command approval; use
Docker mode when stronger process isolation is needed.

## Docker command mode

Docker execution is optional and is never used by the file tools. Build the
generic sandbox image manually before first use:

```bash
docker build -t lunar-forge-sandbox -f lunar_forge/sandbox/Dockerfile .
```

Run approved commands in the sandbox:

```bash
lunar-forge --docker "Run the tests and explain failures"
lunar-forge --docker --allow-network "Run an approved network-dependent task"
```

Docker mode checks availability with `docker info`. The application, not the
model, constructs the wrapper. It mounts only the project root at `/workspace`,
uses `/workspace` as the working directory, applies 2 GiB memory and 2 CPU
limits, and uses the `lunar-forge-sandbox` image. It never requests privileged
mode, mounts the host home directory, or mounts `/var/run/docker.sock`.

The default Docker network is `none`. `--allow-network` explicitly switches it
to `bridge`; it does not remove command approval requirements. The project mount
is writable so approved build and validation commands can update project files.

## New-project mode

The `new` command only operates on an empty or nearly empty target directory.
Each starter uses a small `TemplateSpec` describing its files, dependencies,
approval-gated commands, validation, and run instructions. Selection is
intentionally simple:

```bash
lunar-forge new --project ./site "Build a simple business website"
lunar-forge new --project ./calculator "Build a calculator app in Python with UI"
lunar-forge new --project ./cli "Build a Python CLI for notes"
lunar-forge new --project ./flask-api "Build a small Flask API"
lunar-forge new --project ./fastapi-api "Build a FastAPI service"
lunar-forge new --project ./frontend "Build a Vite React website"
lunar-forge new --project ./frontend --plan "Build a Vite React website"
```

- `static_html` creates `index.html`, `styles.css`, and `README.md` without
  dependencies.
- `python_tkinter` creates a standard-library `app.py` and `README.md`.
- `python_cli` creates a tested, standard-library command-line starter.
- `flask` and `fastapi` create small tested web-service starters; dependency
  installation requires approval.
- `vite_react` directly creates a React/Vite starter with `dev`, `build`, and
  `preview` scripts. `npm install` and build validation require separate
  approval, and installation may fail when network access is unavailable.

Generated projects include a README and may include starter `AGENTS.md`
guidance. The command rejects a non-empty target rather than overwriting an
existing project. Plan mode selects and describes the starter without writing
files, creating `.agent`, or running commands. Final output includes validation
and run instructions.

## Validation

The permission-gated `run_validation` tool chooses likely commands from project
markers:

- Python: `python -m compileall .`, plus `pytest` when tests or pytest
  configuration exist.
- Node: package-manager-aware `test`, `lint`, and `build` scripts when present in
  `package.json`.

Validation uses the same approved local or Docker command runner and reports
every result. No detected commands is a successful, explicit no-op.

### Optional browser validation

From a LunarForge checkout, install Playwright support and its Chromium browser
with the deterministic helper:

```bash
lunar-forge browser-setup --project .
```

The helper does not contact a model or API. It lists these exact commands before
prompting for each one, then runs approved commands through the existing local
runner with `shell=False` and platform executable resolution:

```bash
python -m pip install -e ".[browser]"
python -m playwright install chromium
```

Setup stops on the first denial or failure and reports the bounded command
results. Configured `permissions.mode: no-command` or
`runtime.mode: no-command` blocks setup without executing anything. A configured
Docker runtime does not move browser setup into a container because direct
browser validation uses the host Python environment.

The permission-gated `run_browser_validation` tool connects to an already
running loopback HTTP(S) URL. The separate
`run_managed_browser_validation` tool may start an inferred project dev command
only after approval, wait for its local URL, validate it, and stop it
best-effort. Both capture a bounded page title, final URL, console errors,
failed requests, optional CSS selector checks, and an optional screenshot beneath
`.agent/artifacts/browser/`. Requests leaving loopback are blocked, obvious
credential query values and log assignments are redacted, screenshot paths are
project-confined, and artifacts are never uploaded.

For browser, UI, screenshot, visual rendering, console-error, accessibility,
click, form, layout, and localhost requests, LunarForge is instructed to prefer
browser validation or available Playwright MCP tools over curl or basic command
validation. Managed mode uses detected `dev_command` and `local_url` hints when
available. Browser tools are hidden in plan mode, and neither Playwright nor
project dependencies are installed automatically. In particular,
`browser-validate` only prints actionable setup instructions when Playwright is
missing; it never invokes `browser-setup` automatically.

For deterministic validation without a model or API key, run:

```bash
lunar-forge browser-validate http://127.0.0.1:8000 --project ./my-app
lunar-forge browser-validate http://127.0.0.1:5173 --project ./frontend --check "#root"
lunar-forge browser-validate http://127.0.0.1:5173 --project ./frontend --full-page --width 1440 --height 1200
lunar-forge browser-validate --serve "npm run dev" --url http://localhost:5173 --project ./frontend
```

Screenshots use a 1280x720 viewport by default. Pass `--full-page` to capture
the whole scrollable page; `--width` and `--height` control the browser
viewport used for layout.

Managed mode prompts with the exact redacted server command before starting it,
uses `shell=False`, applies a bounded startup timeout, captures bounded
stdout/stderr when startup fails, and uses a `finally` cleanup path after server
startup so polling and validation failures also trigger termination. Use
`--startup-timeout-ms` to adjust the URL wait. The command remains deterministic
and model-free in both modes.

If browser support is missing, either run the approved helper or execute its
commands manually after review:

```bash
lunar-forge browser-setup --project .
python -m pip install -e ".[browser]"
python -m playwright install chromium
```

## Checkpoints, rollback, and sessions

Before an exact edit, line replacement, line insertion, or explicit overwrite
of an existing file, LunarForge copies the original to:

```text
.agent/checkpoints/<timestamp>/<project-relative-path>
```

New files do not create checkpoints. Inspect and restore project-local state
without model or API access:

```bash
lunar-forge checkpoints --project ../my-app
lunar-forge rollback src/example.py --project ../my-app
lunar-forge sessions --project ../my-app
lunar-forge resume <session-id> --project ../my-app --summary-only
lunar-forge resume <session-id> --project ../my-app --prompt "Continue the fix"
```

`rollback` restores the newest checkpoint for the requested file. If the target
currently exists, its current state is checkpointed before restoration. Safe
path checks prevent restoring outside the project.

Non-plan agent runs write redacted JSONL events to
`.agent/sessions/<timestamp>.jsonl`. Events include prompts, assistant messages,
tool calls and results, denials, and errors. API-key-like values and environment
values are redacted, event sizes are bounded, and the `sessions` command lists
only filenames and sizes; it does not print log contents.

Resume validates that the session is project-local, loads a bounded redacted
history, and starts a new session that references the old one. Historical tool
calls are inert records and are never replayed automatically. Use
`--summary-only` for model-free inspection or `--plan` to continue without
writes.

## Development

Run the full test and syntax validation suite from the repository root:

```bash
python -m pytest -q
python -B -m compileall lunar_forge
```

The package layout keeps provider response parsing in `model_clients/`, tool
permissions in the central registry, filesystem operations in `tools/`, runtime
execution and state in `runtime/`, and small project workflows in `workflows/`.

## Manual testing checklist

Use a disposable target project for checks that intentionally edit files or
start processes.

- [ ] Run the automated baseline from the LunarForge repository:

  ```bash
  python -m pytest -q
  python -B -m compileall lunar_forge
  git diff --check
  ```

- [ ] Ask LunarForge to read a disposable file with line numbers, replace an
  inclusive range, and insert once with `after_line: 0`. Confirm the reported
  diff is bounded, the file keeps its newline convention, and the original is
  present under `.agent/checkpoints/`.

- [ ] Repeat the edit request with `--plan`. Confirm no file changes, command
  execution, checkpoint, or session artifact is created.

- [ ] Review and run optional browser setup. Confirm both commands are printed
  before approval, denial stops immediately, and no install command runs under
  `permissions.mode: no-command`:

  ```bash
  lunar-forge browser-setup --project .
  ```

- [ ] With a local site already running, exercise viewport and full-page modes
  and confirm the returned screenshot stays under the selected project:

  ```bash
  lunar-forge browser-validate http://127.0.0.1:5173 --project ./frontend
  lunar-forge browser-validate http://127.0.0.1:5173 --project ./frontend --full-page --width 1440 --height 1200
  ```

- [ ] Exercise managed mode. Confirm the exact dev command requires approval,
  startup failures include only bounded stdout/stderr, and the process is no
  longer listening after success, timeout, validation failure, or interruption:

  ```bash
  lunar-forge browser-validate --serve "npm run dev" --url http://localhost:5173 --project ./frontend
  ```

- [ ] Ask the model to check a screenshot, layout, console errors, a click, and
  a localhost page. Confirm it chooses browser validation or available
  Playwright MCP tools instead of curl/basic HTTP validation. Confirm it does
  not install dependencies or start a server without approval.

- [ ] Enable parallel subagents with either configuration or the CLI flag (both
  are not required), run a disposable change, and confirm the final report lists
  serialized Coder/Scaffolder work and the `post-edit: tester, reviewer` group:

  ```bash
  lunar-forge --project ./my-app --parallel-subagents "Make a small documented change"
  ```

- [ ] Inspect the resulting project-local session JSONL. Confirm each subagent
  lifecycle event has `role`, `phase`, and `parallel_group_id`, records are valid
  one-line JSON under concurrent completion, and test environment secret values
  do not appear in the file.

## Known limitations

- Local command mode confines `cwd`, not OS-level filesystem or process access.
- Dangerous-command detection is a defense-in-depth denylist, not a complete
  parser or substitute for sandboxing and human approval.
- LiteLLM is the only active model adapter; other provider modules are
  placeholders.
- File edits support exact single-match replacement and explicit line ranges,
  but not a general patch engine or optimistic locking against simultaneous
  external edits.
- Docker image building, dependency downloads, and network access are never
  automatic.
- The new-project workflow intentionally supports six focused starters.
- Subagents are role-specific model calls, not independent processes or
  autonomous collaborators. Optional parallelism is limited to two fixed,
  synchronous thread groups; it does not schedule arbitrary roles or debate
  loops. Approved Tester commands can create build/test artifacts while the
  Reviewer reads the project, although file-writer roles never overlap.
  Explicitly injected custom model clients must tolerate concurrent requests.
- MCP currently supports local stdio servers only. Streamable HTTP, server
  sampling/elicitation requests, dynamic tool-list refresh, and OS-level server
  sandboxing are not implemented.
- Browser validation requires the optional Playwright extra. Managed process
  termination is best-effort; operating-system child processes spawned by a dev
  command may require manual cleanup if they ignore or outlive the parent.
- Browser request interception is defense in depth, not an operating-system
  network sandbox. Screenshots and logs are bounded and project-local, but a
  rendered page is still untrusted content.
- Session redaction covers configured environment values, sensitive keys,
  bearer tokens, and common API-key patterns; arbitrary secret-looking prose
  that matches none of those signals cannot be identified reliably.
- Plugins run reviewed local Python in-process after approval. Capability
  declarations and output containment do not provide OS-level isolation.
