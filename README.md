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
- six small new-project starters; and
- checkpoint, rollback, and session utility commands that do not call a model.

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

## Project instructions (`AGENTS.md`)

At session start, LunarForge loads a root `AGENTS.md` from the target project if
one exists. The content is size-limited and included as untrusted project
context. It can guide conventions and validation choices, but it cannot override
path safety, permissions, command blocking, Docker restrictions, or plan mode.

Nested `AGENTS.md` discovery is available as a helper, but nested instructions
are not yet applied automatically to individual files. When no root instructions
exist, the model receives a clear fallback notice.

Search and file-reading tools skip generated or sensitive runtime directories,
including `.git`, `.agent`, `node_modules`, virtual environments,
`__pycache__`, `.next`, `dist`, `build`, and `coverage`.

## Local command mode

Local mode is the default. Approved commands run with the project root as their
working directory using `subprocess.run(..., shell=False)`. Normal executables
and quoted arguments are supported; shell built-ins, pipes, redirects, and
operators such as `&&` are intentionally unsupported.

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

Docker mode checks availability with `docker info`. The application—not the
model—constructs the wrapper. It mounts only the project root at `/workspace`,
uses `/workspace` as the working directory, applies 2 GiB memory and 2 CPU
limits, and uses the `lunar-forge-sandbox` image. It never requests privileged
mode, mounts the host home directory, or mounts `/var/run/docker.sock`.

The default Docker network is `none`. `--allow-network` explicitly switches it
to `bridge`; it does not remove command approval requirements. The project mount
is writable so approved build and validation commands can update project files.

## New-project mode

The `new` command only operates on an empty or nearly empty target directory.
Template selection is intentionally simple:

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

The command rejects a non-empty target rather than overwriting an existing
project. Its final output includes run instructions.

## Validation

The permission-gated `run_validation` tool chooses likely commands from project
markers:

- Python: `python -m compileall .`, plus `pytest` when tests or pytest
  configuration exist.
- Node: package-manager-aware `test`, `lint`, and `build` scripts when present in
  `package.json`.

Validation uses the same approved local or Docker command runner and reports
every result. No detected commands is a successful, explicit no-op.

## Checkpoints, rollback, and sessions

Before an exact edit or an explicit overwrite of an existing file, LunarForge
copies the original to:

```text
.agent/checkpoints/<timestamp>/<project-relative-path>
```

New files do not create checkpoints. Inspect and restore project-local state
without model or API access:

```bash
lunar-forge checkpoints --project ../my-app
lunar-forge rollback src/example.py --project ../my-app
lunar-forge sessions --project ../my-app
```

`rollback` restores the newest checkpoint for the requested file. If the target
currently exists, its current state is checkpointed before restoration. Safe
path checks prevent restoring outside the project.

Non-plan agent runs write redacted JSONL events to
`.agent/sessions/<timestamp>.jsonl`. Events include prompts, assistant messages,
tool calls and results, denials, and errors. API-key-like values and environment
values are redacted, event sizes are bounded, and the `sessions` command lists
only filenames and sizes—it does not print log contents. Session resume is not
implemented yet.

## Development

Run the full test and syntax validation suite from the repository root:

```bash
python -m pytest -q
python -B -m compileall lunar_forge
```

The package layout keeps provider response parsing in `model_clients/`, tool
permissions in the central registry, filesystem operations in `tools/`, runtime
execution and state in `runtime/`, and small project workflows in `workflows/`.

## Known limitations

- Local command mode confines `cwd`, not OS-level filesystem or process access.
- Dangerous-command detection is a defense-in-depth denylist, not a complete
  parser or substitute for sandboxing and human approval.
- LiteLLM is the only active model adapter; other provider modules are
  placeholders.
- File edits use exact, single-match text replacement rather than a general
  patch engine.
- Nested `AGENTS.md` files are discovered but not yet applied by path scope.
- Docker image building, dependency downloads, and network access are never
  automatic.
- Sessions can be listed but not resumed.
- The new-project workflow intentionally supports six focused starters.
