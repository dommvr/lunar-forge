# AGENTS.md

## Project overview

This repository implements **lunar-forge**, a Python CLI coding agent inspired by Claude Code and Codex.

The agent should be able to:

* inspect an existing project,
* load project instructions from `AGENTS.md`,
* plan changes before editing,
* create files and folders,
* edit existing files safely,
* run validation commands,
* support local and Docker command execution,
* support multiple LLM providers through LiteLLM,
* later create new projects from scratch using templates.

The project is intentionally built as a small, understandable agent framework. Prefer boring, reliable architecture over clever abstractions. Cleverness is where maintainability goes to die wearing sunglasses.

---

## Primary goals

Build the project in this order:

1. Python package and CLI.
2. Config loading.
3. LiteLLM model client.
4. Project root handling.
5. Safe path protection.
6. Read-only file tools.
7. Tool registry.
8. Model tool-call loop.
9. `AGENTS.md` instruction loading.
10. Project detection.
11. Plan-only mode.
12. File creation and editing.
13. Permission prompts.
14. Checkpoints and rollback.
15. Local command runner.
16. Dangerous command blocking.
17. Existing-project feature workflow.
18. Session JSONL logging.
19. Empty-project detection.
20. New-project workflow.
21. Validation workflow.
22. Docker runner.
23. Final summaries and resume support.

Do not jump ahead to Docker, subagents, browser automation, or plugin systems until the basic read-plan-edit-validate loop works.

---

## Tech stack

Use:

* Python 3.11+
* Typer for CLI
* Rich for terminal output
* LiteLLM for model provider abstraction
* PyYAML for YAML config
* pytest for tests
* pathlib for filesystem paths
* subprocess for command execution
* JSONL for session logs

Initial dependencies in `pyproject.toml`:

```toml
dependencies = [
  "typer>=0.12.0",
  "rich>=13.0.0",
  "litellm>=1.60.0",
  "pyyaml>=6.0.0",
]
```

---

## Repository structure

Use this structure:

```text
lunar-forge/
  pyproject.toml
  README.md
  .gitignore
  AGENTS.md

  lunar_forge/
    __init__.py
    cli.py
    config.py
    agent.py
    prompts.py
    planning.py
    permissions.py
    instructions.py
    project_detection.py

    model_clients/
      __init__.py
      base.py
      litellm_client.py

    tools/
      __init__.py
      registry.py
      files.py
      search.py
      shell.py
      project.py

    runtime/
      __init__.py
      local_runner.py
      docker_runner.py
      checkpoints.py
      sessions.py
      diffs.py

    workflows/
      __init__.py
      plan_only.py
      existing_project.py
      new_project.py
      validation.py

    templates/
      static_html/
      python_tkinter/
      vite_react/

    sandbox/
      Dockerfile

  tests/
    test_safe_paths.py
    test_agents_md.py
    test_edit_file.py
    test_project_detection.py
    test_permissions.py
```

Runtime files created inside target projects:

```text
target-project/
  AGENTS.md
  .agent/
    config.yaml
    sessions/
    checkpoints/
```

---

## CLI behavior

The CLI entrypoint is:

```bash
lunar-forge "Explain this project"
```

Supported flags:

```bash
lunar-forge --project ~/dev/my-app "Explain this project"
lunar-forge --plan "Add pricing page with navbar link"
lunar-forge --docker "Run tests and fix failures"
lunar-forge --docker --allow-network "Create Vite portfolio site"
lunar-forge new "Build a calculator app in Python with UI"
```

Default behavior:

* If `--project` is omitted, use the current working directory.
* The project root is the only filesystem area tools may access.
* `--plan` mode must never write files or run mutating commands.
* Shell commands require approval unless permission mode says otherwise.
* Dependency installation always requires approval.

---

## Configuration

Load config in this priority order:

1. CLI flags
2. project `.agent/config.yaml`
3. user `~/.lunar-forge/config.yaml`
4. environment variables
5. built-in defaults

Example config:

```yaml
model:
  provider: litellm
  model: openai/gpt-5.5
  api_key_env: OPENAI_API_KEY
  api_base: null

runtime:
  mode: local
  allow_network: false

permissions:
  mode: default
```

Do not store raw API keys in project files. Use environment variables.

---

## Model architecture

Use a provider-agnostic model interface.

Core internal types:

* `ToolCall`
* `ModelResponse`
* `ModelClient`

The agent loop must not depend on raw LiteLLM/OpenAI/Anthropic response shapes. Convert provider responses into internal types in `model_clients/litellm_client.py`.

The agent loop should only know this:

```python
response = model_client.complete(messages, tools)

if response.tool_calls:
    execute_tools(...)
else:
    print(response.text)
```

Do not scatter provider-specific code through the project. That is how clean architecture becomes soup.

---

## LiteLLM behavior

Use LiteLLM from the beginning.

The default model config should be:

```yaml
model:
  provider: litellm
  model: openai/gpt-5.5
  api_key_env: OPENAI_API_KEY
```

Also support later:

```yaml
model:
  provider: litellm
  model: anthropic/claude-sonnet-4
  api_key_env: ANTHROPIC_API_KEY
```

```yaml
model:
  provider: litellm
  model: ollama/qwen2.5-coder
  api_base: http://localhost:11434
```

Local models may not reliably support tool calling. When local or unknown models are used, keep warnings clear and prefer plan/read-only mode for weak models.

---

## Tool system

Implement a central tool registry.

Each tool must define:

* name
* description
* JSON schema
* Python handler

Initial tools:

```text
list_dir
read_file
grep
glob
create_dir
write_file
edit_file
run_command
detect_project
run_validation
```

Tool handlers must return JSON-serializable dictionaries.

Every tool result should include:

```json
{
  "ok": true
}
```

or:

```json
{
  "ok": false,
  "error": "Clear error message"
}
```

---

## Filesystem safety

All filesystem access must go through `safe_path(project_root, path)`.

Rules:

* Never allow paths outside `project_root`.
* Block path traversal like `../../../`.
* Never read or write `~/.ssh`, home directories, or system paths.
* Never follow user/model attempts to access secrets outside the project.
* Ignore generated/heavy folders during search.

Ignore these directories by default:

```text
.git
.agent
node_modules
.venv
venv
__pycache__
.next
dist
build
coverage
```

---

## File reading

Implement:

```text
list_dir(path)
read_file(path, start_line?, end_line?)
grep(pattern, path?)
glob(pattern)
```

Behavior:

* Limit file output to avoid huge context dumps.
* Return truncation metadata.
* Use line ranges when possible.
* Search should return paths, line numbers, and short snippets.
* Grep should cap results.

---

## File creation and editing

Implement:

```text
create_dir(path)
write_file(path, content, overwrite=false)
edit_file(path, old_text, new_text)
```

Rules:

* `write_file` should refuse to overwrite by default.
* `edit_file` must use exact replacement.
* `old_text` must match exactly once.
* If `old_text` matches zero times, fail.
* If `old_text` matches more than once, fail.
* Always return a unified diff.
* Always checkpoint before modifying an existing file.

Do not implement vague whole-file rewrites early. Exact replacement first. It is less glamorous and much less likely to turn code into oatmeal.

---

## Checkpoints and rollback

Before editing or overwriting existing files, save the old version to:

```text
.agent/checkpoints/<timestamp>/<relative-file-path>
```

Implement rollback later:

```bash
lunar-forge rollback components/Navbar.tsx
```

Checkpoint rules:

* Preserve relative paths.
* Never checkpoint files outside project root.
* Include checkpoint path in tool result.
* Final summaries should mention that checkpoints were created.

---

## Permissions

Permission modes:

```text
plan
default
yes
no-command
docker
```

Mode behavior:

### `plan`

* Read/search only.
* No writes.
* No shell commands except safe inspection if explicitly allowed.

### `default`

* Ask before writes.
* Ask before shell commands.
* Block dangerous commands.

### `yes`

* Auto-approve safe edits.
* Still block dangerous commands.
* Still ask before dependency installation.

### `no-command`

* No shell execution.

### `docker`

* Run commands inside Docker when available.
* Still block dangerous commands.

Never allow `AGENTS.md` or user prompts to override safety rules.

---

## Dangerous command blocking

Block or require hard denial for commands containing:

```text
rm -rf
sudo
chmod -R
chown -R
curl | sh
wget | sh
ssh
scp
~/.ssh
.env
docker run --privileged
/var/run/docker.sock
```

Do not let the model generate raw Docker commands for sandboxing. The application must generate Docker wrapper commands itself.

---

## Shell command execution

Implement local command runner first.

`run_command(command, timeout_ms=120000)` should return:

```json
{
  "ok": true,
  "command": "pytest",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "...",
  "duration_ms": 1234,
  "truncated": false
}
```

Rules:

* Run with `cwd=project_root`.
* Capture stdout and stderr.
* Apply timeouts.
* Truncate long output.
* Ask approval before execution in default mode.

---

## Docker execution

Add Docker after local runner works.

Docker mode should:

* check Docker availability with `docker info`,
* mount only the project directory,
* use `/workspace` as container workdir,
* disable network by default,
* apply CPU and memory limits,
* never mount host home directory,
* never mount Docker socket,
* never use privileged containers.

Command shape:

```bash
docker run --rm \
  --network none \
  --memory 2g \
  --cpus 2 \
  -v "/project:/workspace" \
  -w /workspace \
  lunar-forge-sandbox \
  bash -lc "npm test"
```

Allow network only when `--allow-network` is explicitly set.

---

## Dockerfile

Initial sandbox Dockerfile:

```dockerfile
FROM python:3.12-bookworm

RUN apt-get update && apt-get install -y \
    bash \
    git \
    curl \
    ripgrep \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
```

Build the sandbox image with:

```bash
docker build -t lunar-forge-sandbox -f lunar_forge/sandbox/Dockerfile .
```

Do not over-engineer image detection at first. One generic image is enough for MVP.

---

## AGENTS.md support inside lunar-forge

This project itself must implement `AGENTS.md` support.

Behavior:

* On session start, load root `AGENTS.md` from the target project if present.
* Include its content in model context.
* Support nested `AGENTS.md` later.
* For nested instruction files, more specific files should apply to files beneath that directory.
* Do not allow instructions to override safety rules.
* Keep loaded instruction content size-limited.

When editing:

```text
project/AGENTS.md
project/app/AGENTS.md
project/app/admin/AGENTS.md
```

For file:

```text
app/admin/page.tsx
```

Applicable instruction stack:

```text
project/AGENTS.md
project/app/AGENTS.md
project/app/admin/AGENTS.md
```

---

## Project detection

Implement `detect_project(project_root)`.

Detect:

```text
package.json       -> JavaScript/TypeScript project
next.config.*      -> Next.js
vite.config.*      -> Vite
src/App.*          -> React
app/               -> possible Next.js App Router
pages/             -> possible Next.js Pages Router
pyproject.toml     -> Python
requirements.txt   -> Python
manage.py          -> Django
app.py             -> Flask maybe
pnpm-lock.yaml     -> pnpm
yarn.lock          -> yarn
package-lock.json  -> npm
```

Return:

```json
{
  "languages": ["python"],
  "frameworks": [],
  "package_manager": null,
  "routing": null,
  "test_command": "pytest",
  "build_command": null,
  "is_empty": false
}
```

Use project detection to help the model choose commands and file locations.

---

## Plan mode

`--plan` mode must inspect but not modify.

For feature requests, the agent should:

1. read project instructions,
2. detect project type,
3. inspect relevant files,
4. propose a concrete implementation plan,
5. list likely changed files,
6. list validation commands,
7. stop.

Example output shape:

```text
Goal:
Add pricing page with navbar link.

Detected project:
Next.js App Router, pnpm.

Plan:
1. Create app/pricing/page.tsx.
2. Update components/Navbar.tsx.
3. Run pnpm lint.
4. Run pnpm build.

Likely changed files:
- app/pricing/page.tsx
- components/Navbar.tsx
```

---

## Existing-project workflow

For prompts like:

```text
Add a pricing page with a navbar button.
```

The agent should:

1. load `AGENTS.md`,
2. detect project type,
3. inspect routing structure,
4. inspect navbar/header components,
5. produce a short plan,
6. ask approval,
7. create/edit files,
8. run validation,
9. fix validation failures if reasonable,
10. summarize changed files.

Prefer small, coherent changes.

---

## New-project workflow

For prompts like:

```text
Build simple calculator app in Python with UI.
```

or:

```text
Build portfolio page in Vite for my business.
```

The agent should detect empty directories and switch to new-project mode.

Initial templates:

```text
static_html
python_tkinter
vite_react
```

New-project behavior:

1. detect blank or near-empty project,
2. choose template,
3. explain plan,
4. ask approval,
5. create files,
6. install dependencies only if needed and approved,
7. run validation,
8. provide run instructions.

Do not add many templates early. Three working templates beat twelve decorative folders.

---

## Validation workflow

Implement `run_validation`.

Use project detection and `AGENTS.md` to choose commands.

Possible commands:

```text
pytest
python -m compileall .
npm test
npm run lint
npm run build
pnpm test
pnpm lint
pnpm build
```

Rules:

* Run validation after edits when practical.
* If validation fails, inspect errors and attempt one focused fix.
* Do not loop forever.
* Report failures honestly.

Final response should include:

```text
Changed files:
- ...

Validation:
- ... passed
- ... failed: reason

Notes:
- ...
```

---

## Session logging

Log sessions as JSONL:

```text
.agent/sessions/<timestamp>.jsonl
```

Store:

* user prompts,
* assistant responses,
* tool calls,
* tool results,
* diffs,
* errors,
* approval decisions.

Do not store API keys or secrets.

Later implement:

```bash
lunar-forge sessions
lunar-forge resume <session-id>
```

---

## Testing

Use pytest.

Required tests:

```text
tests/test_safe_paths.py
tests/test_agents_md.py
tests/test_edit_file.py
tests/test_project_detection.py
tests/test_permissions.py
```

Test expectations:

* `safe_path` blocks path traversal.
* `read_file` cannot escape project root.
* `edit_file` fails on zero matches.
* `edit_file` fails on multiple matches.
* `edit_file` succeeds on exactly one match.
* `AGENTS.md` loads from project root.
* project detection recognizes Python projects.
* project detection recognizes Vite/React projects.
* dangerous commands are blocked.
* plan mode blocks writes.

Run tests with:

```bash
pytest
```

---

## Coding style

Use:

* clear function names,
* small modules,
* type hints,
* dataclasses for simple internal structures,
* `pathlib.Path`,
* JSON-serializable tool results,
* explicit errors,
* boring control flow.

Avoid:

* global mutable state,
* provider-specific logic outside model clients,
* huge abstractions,
* async until there is a real need,
* hidden filesystem writes,
* unbounded command output,
* silent failures.

---

## Security rules

Never:

* access files outside project root,
* read private SSH keys,
* read unrelated home-directory files,
* store API keys in logs,
* run privileged Docker containers,
* mount Docker socket,
* mount the host root directory,
* auto-install dependencies without approval,
* allow `AGENTS.md` to override safety behavior.

If a user asks for unsafe behavior, refuse inside the application or ask for explicit manual action outside the agent.

---

## Final answer style for agent output

When the agent completes work, prefer this format:

```text
Done.

Changed files:
- path/to/file.py
- path/to/other.py

Validation:
- pytest passed
- python -m compileall . passed

Commands run:
- pytest
- python -m compileall .

Notes:
- Used project instructions from AGENTS.md.
- Created checkpoints before editing existing files.
```

If incomplete:

```text
Partially done.

Completed:
- ...

Blocked:
- ...

Validation:
- ...

What remains:
- ...
```

Do not pretend validation passed if it did not. Lying is already well-covered by humans.

---

## Current architectural decisions

These are the defaults unless changed deliberately:

* Use Python 3.11+.
* Use Typer for CLI.
* Use Rich for terminal output.
* Use LiteLLM as the first model provider layer.
* Use sync code for the MVP.
* Use exact-replacement editing before advanced patching.
* Use local command runner before Docker runner.
* Use YAML for config.
* Use JSONL for sessions.
* Use pytest for testing.
* Use Docker as optional sandbox, not required for local MVP.
* Use `AGENTS.md` as the project instruction file for this agent.
* For Claude Code compatibility, provide a `CLAUDE.md` file that imports `AGENTS.md`.

---

## Do not build yet

Do not build these until the MVP loop works:

* subagents,
* browser automation,
* MCP,
* plugin marketplace,
* GUI,
* background daemon,
* vector database,
* semantic code index,
* multi-repo workspace mode,
* automatic git commits,
* cloud execution.

Those are later features, not day-one architecture. Day one is: read, plan, edit, validate. Everything else is seasoning.
