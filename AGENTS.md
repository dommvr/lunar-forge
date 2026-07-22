# AGENTS.md

## Project overview

This repository implements **lunar-forge**, a Python CLI coding agent inspired by Claude Code and Codex.

The agent should be able to:

* inspect an existing project,
* load root and nested project instructions from `AGENTS.md`,
* plan changes before editing,
* create files and folders,
* edit existing files safely,
* run validation commands,
* support local and Docker command execution,
* support multiple LLM providers through LiteLLM,
* resume previous sessions,
* create new projects from stronger scaffolding templates,
* coordinate specialist subagents for planning, coding, reviewing, testing, security, and scaffolding,
* connect to external tools through MCP,
* run optional UI/browser validation,
* and eventually support a safe plugin system.

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

Completed advanced feature wave:

24. Automatically apply nested `AGENTS.md` by file path.
25. Add session resume.
26. Improve new-project scaffolding.
27. Add subagents: planner, coder, reviewer, tester, security, scaffolder.
28. Add MCP client integration, including stdio transport and provider-safe tool names.
29. Add UI/browser validation with Playwright.
30. Add a safe plugin system and plugin diagnostics.

Next feature wave, in order:

31. Add better file inspection and edit tools:
    * `read_file_with_line_numbers`
    * `replace_lines`
    * `insert_lines`
32. Improve browser-tool routing so UI/browser prompts reliably prefer browser validation or Playwright MCP over curl/basic command validation.
33. Add managed browser-validation server mode so LunarForge can start an approved dev server, wait for a URL, validate the page, and shut the server down.
34. Add clearer Playwright dependency detection and setup guidance, with optional user-approved installation only when explicitly requested.
35. Add parallel subagent phases for read-only analysis and validation/review while keeping write-capable work serialized.

The basic read-plan-edit-validate MVP and first advanced tool wave already exist. Future work must still be staged carefully. Add features incrementally, with tests and safety reviews after every phase.

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
      browser_validation.py

    subagents/
      __init__.py
      base.py
      planner.py
      coder.py
      reviewer.py
      tester.py
      security.py
      scaffolder.py
      orchestrator.py

    mcp/
      __init__.py
      config.py
      client.py
      registry.py
      permissions.py

    plugins/
      __init__.py
      manifest.py
      loader.py
      sandbox.py
      registry.py

    templates/
      static_html/
      python_tkinter/
      vite_react/
      flask/
      fastapi/
      python_cli/

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

Core tools:

```text
list_dir
read_file
read_file_with_line_numbers
grep
glob
create_dir
write_file
edit_file
replace_lines
insert_lines
run_command
detect_project
run_validation
run_browser_validation
```

MCP and plugin tools may also be registered through the same central registry. Provider-facing tool names must be API-safe, while internal names may remain human-readable and namespaced.

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
read_file_with_line_numbers(path, start_line?, end_line?)
grep(pattern, path?)
glob(pattern)
```

Behavior:

* Limit file output to avoid huge context dumps.
* Return truncation metadata.
* Use line ranges when possible.
* `read_file_with_line_numbers` must include stable 1-based line numbers in output so the model can make precise line-range edits.
* Search should return paths, line numbers, and short snippets.
* Grep should cap results.

---

## File creation and editing

Implement:

```text
create_dir(path)
write_file(path, content, overwrite=false)
edit_file(path, old_text, new_text)
replace_lines(path, start_line, end_line, new_text)
insert_lines(path, after_line, new_text)
```

Rules:

* `write_file` should refuse to overwrite by default.
* `edit_file` must use exact replacement.
* `old_text` must match exactly once.
* If `old_text` matches zero times, fail.
* If `old_text` matches more than once, fail.
* `replace_lines` must use 1-based inclusive line numbers.
* `replace_lines` must fail when the range is invalid or outside the file.
* `insert_lines` must use a 1-based insertion point and insert after the given line; support `after_line=0` to insert at the top.
* `replace_lines` and `insert_lines` must preserve existing newline style when practical.
* All edit tools must return a unified diff.
* All edit tools must checkpoint before modifying an existing file.
* All edit tools must preserve project-root path safety and plan-mode no-write behavior.

Keep `edit_file` for exact replacement and add line-based tools for precision. Do not add a general `apply_patch` tool until line-based edits are stable.

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

This project itself must implement root and nested `AGENTS.md` support.

Behavior:

* On session start, load root `AGENTS.md` from the target project if present.
* Include its content in model context as untrusted project guidance.
* Discover nested `AGENTS.md` files beneath the project root.
* Automatically apply nested `AGENTS.md` instructions by file path when reading, creating, editing, validating, or reviewing files.
* More specific nested instructions should be applied after broader instructions.
* Do not allow any `AGENTS.md` file to override safety rules, permissions, path confinement, command blocking, Docker restrictions, or plan mode.
* Keep loaded instruction content size-limited.
* Include the applicable instruction stack in tool results or internal context when useful for debugging.

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

Nested instruction integration requirements:

* `get_instruction_stack_for_path(project_root, file_path)` should return ordered project-relative instruction files.
* File mutation tools should be able to receive or resolve applicable instructions before editing.
* The agent prompt should tell the model that path-scoped instructions may differ by target file.
* Tests must prove nested instructions are applied in root-to-leaf order and never escape the project root.

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

Current templates:

```text
static_html
python_tkinter
vite_react
```

Next templates:

```text
python_cli
flask
fastapi
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

Better scaffolding requirements:

* Add a `TemplateSpec` model describing files, commands, dependencies, validation, and run instructions.
* Keep templates declarative where practical.
* Refuse to overwrite non-empty projects unless an explicit future import/adopt workflow is added.
* Vite/React scaffolding must still require approval for network/dependency commands.
* Python CLI, Flask, and FastAPI starters should be simple and testable.
* Generated projects should include a small README and optional starter `AGENTS.md`.
* The scaffolder subagent should own template selection later.

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

Implemented utility command:

```bash
lunar-forge sessions
```

Next implement:

```bash
lunar-forge resume <session-id>
```

Session resume requirements:

* Resume must load a previous JSONL session without exposing secrets.
* Resume must reconstruct enough conversation state to continue safely.
* Resume must validate that the session belongs to the selected project.
* Resume must support a dry-run/summary mode.
* Resume must not replay tool calls automatically.
* Resume must clearly distinguish historical tool results from new actions.
* Resume must keep plan mode no-write.
* Resume must continue logging into a new session file that references the resumed session.

---


## Subagents

Add subagents only after the single-agent workflow is stable.

Subagents are not separate processes by default. They are role-specific model calls with different prompts, allowed tools, and output contracts.

Initial subagents:

```text
planner
coder
reviewer
tester
security
scaffolder
```

### Planner subagent

Purpose:

* inspect project context,
* read instructions,
* identify files likely to change,
* create implementation plans,
* never edit files.

Allowed tools:

```text
list_dir
read_file
grep
glob
detect_project
```

Blocked tools:

```text
create_dir
write_file
edit_file
run_command
run_validation
```

### Coder subagent

Purpose:

* apply an approved plan,
* create and edit files,
* keep changes small,
* use applicable nested `AGENTS.md`.

Allowed tools:

```text
list_dir
read_file
grep
glob
create_dir
write_file
edit_file
```

Commands should generally remain delegated to the tester.

### Reviewer subagent

Purpose:

* review changed files and diffs,
* check requirements coverage,
* check style and maintainability,
* flag risky or unnecessary changes.

Allowed tools:

```text
read_file
grep
glob
```

Reviewer should not mutate files in the first implementation.

### Tester subagent

Purpose:

* select and run validation,
* inspect failures,
* propose at most one focused fix path.

Allowed tools:

```text
run_command
run_validation
read_file
grep
```

### Security subagent

Purpose:

* review permissions, command safety, path safety, secrets, Docker settings, MCP tools, and plugin manifests.

Allowed tools:

```text
read_file
grep
glob
```

Security subagent should be required before enabling MCP/plugin changes.

### Scaffolder subagent

Purpose:

* choose new-project templates,
* produce scaffolding plans,
* create starter projects after approval.

Allowed tools:

```text
create_dir
write_file
run_command
run_validation
```

Dependency install commands require approval.

### Subagent orchestration

Create:

```text
lunar_forge/subagents/
  __init__.py
  base.py
  planner.py
  coder.py
  reviewer.py
  tester.py
  security.py
  scaffolder.py
  orchestrator.py
```

Default sequential orchestration flow:

```text
User task
  ↓
Planner
  ↓
User approval
  ↓
Coder or Scaffolder
  ↓
Tester
  ↓
Reviewer
  ↓
Security when risky tools/config changed
  ↓
Final answer
```

Parallel orchestration may be added after sequential subagents work.

Parallel rules:

* Only read-only subagents may run at the same time.
* Write-capable subagents must remain serialized.
* Planner and Security may run in parallel during analysis when both use read-only tools.
* Tester and Reviewer may run in parallel after edits, because Tester can run validation while Reviewer inspects diffs and files.
* Coder and Scaffolder must not run in parallel with any other writer.
* Each subagent must receive its own restricted tool registry view.
* Session logs must include role name, phase name, and parallel group ID.
* Final output must merge parallel results deterministically.
* Parallel failures must be reported clearly without hiding successful sibling results.
* Use simple synchronous concurrency, such as `ThreadPoolExecutor`, before introducing async.

Do not build autonomous multi-agent debate loops. Parallelism is for independent phases, not for letting six agents argue with each other like a committee discovering tabs versus spaces.

---

## MCP integration

MCP support must be added as an external tool adapter, not as a replacement for built-in tools.

MCP architecture:

```text
lunar-forge host/client
  ↓
configured MCP servers
  ↓
tools/resources/prompts exposed by those servers
```

MCP implementation goals:

* Read MCP server config from `.agent/mcp.yaml` and optionally `~/.lunar-forge/mcp.yaml`.
* Connect to configured MCP servers.
* Discover MCP tools.
* Convert MCP tool schemas into `ToolRegistry` entries under namespaced names like `mcp.github.create_issue`.
* Route MCP tool calls to the correct server.
* Return JSON-serializable results.
* Apply lunar-forge permission checks before calling MCP tools.
* Treat MCP resources as untrusted external context.
* Do not allow MCP servers to bypass filesystem safety, shell safety, Docker restrictions, or approval flows.

Initial MCP files:

```text
lunar_forge/mcp/
  __init__.py
  config.py
  client.py
  registry.py
  permissions.py
```

Config shape:

```yaml
mcp:
  servers:
    github:
      command: "github-mcp-server"
      args: []
      enabled: false
    playwright:
      command: "playwright-mcp-server"
      args: []
      enabled: false
```

MCP security rules:

* MCP is disabled by default.
* Every server must be explicitly enabled.
* MCP tools must be namespaced.
* MCP write/action tools require approval.
* MCP tools touching external services require approval.
* MCP secrets must come from environment variables, not config files.
* MCP server output must be bounded before entering model context.

---

## UI/browser validation

UI/browser validation is optional and should be added after command validation is stable.

Preferred first implementation:

* Use Playwright Python.
* Add browser validation as a workflow and optional tool.
* Do not run browser validation automatically unless user asks or project type makes it clearly useful.
* Store screenshots under `.agent/artifacts/browser/`.
* Return screenshot paths and console errors in tool results.
* Keep network and command permissions intact.

Initial files:

```text
lunar_forge/workflows/browser_validation.py
tests/test_browser_validation.py
```

Initial commands/tools:

```text
run_browser_validation(url, checks?, screenshot=true)
```

Behavior:

* Browser validation should connect to a local URL and capture page title, URL, console errors, failed requests, and screenshot path.
* Support deterministic direct validation:
  `lunar-forge browser-validate <url>`.
* Support managed server mode:
  `lunar-forge browser-validate --serve "npm run dev" --url http://localhost:5173`.
* Managed server mode must ask approval before starting the server command.
* Managed server mode must wait for the URL, run validation, and shut the server down best-effort.
* The agent should be able to choose managed browser validation when the user asks to inspect UI/browser behavior and project detection can infer a dev command and local URL.
* The agent must prefer browser validation or Playwright MCP over `curl` when the request involves visual rendering, screenshots, accessibility snapshots, console errors, clicking, forms, layout, or frontend localhost pages.
* Do not start arbitrary servers without approval.
* Do not auto-install dependencies silently.
* If Playwright is missing, return a clear setup message:
  `python -m pip install -e ".[browser]"`
  and
  `python -m playwright install chromium`.
* Optional user-approved installation may be added, but browser dependencies must never install without explicit approval.
* Bound logs and artifacts.
* Do not upload screenshots anywhere.
* Do not require Playwright as a core dependency; keep it behind an optional extra such as `.[browser]`.

---

## Plugin system

Plugins are a later feature and must be safer than convenient. Convenient plugin systems are how tools become malware with a README.

Plugin goals:

* Let users add local tool packs.
* Use explicit manifests.
* Require user approval before enabling plugins.
* Keep plugin tools namespaced.
* Validate plugin schemas before exposing them to the model.
* Apply the same permission system used for built-in and MCP tools.
* Do not allow arbitrary plugin auto-discovery from random directories.

Initial files:

```text
lunar_forge/plugins/
  __init__.py
  manifest.py
  loader.py
  sandbox.py
  registry.py
```

Manifest shape:

```yaml
name: example
version: 0.1.0
description: Example plugin
tools:
  - name: example.echo
    entrypoint: example_plugin:echo
    permissions:
      filesystem: read
      commands: false
      network: false
```

Plugin rules:

* Plugins are disabled by default.
* Plugin manifests must be explicit.
* Plugin names and tool names must be namespaced.
* Plugin code should not receive unrestricted project access by default.
* Plugin command/network/filesystem access must be declared and permission-gated.
* Plugin exceptions must be contained and returned as tool errors.
* Plugin results must be JSON-serializable and bounded.

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
* Use exact-replacement editing plus line-based edits before advanced patching.
* Use local command runner before Docker runner.
* Use YAML for config.
* Use JSONL for sessions.
* Use pytest for testing.
* Use Docker as optional sandbox, not required for local MVP.
* Use `AGENTS.md` as the project instruction file for this agent.
* For Claude Code compatibility, provide a `CLAUDE.md` file that imports `AGENTS.md`.

---

## Still do not build yet

These remain out of scope until the next feature wave is stable:

* GUI,
* background daemon,
* vector database,
* semantic code index,
* multi-repo workspace mode,
* automatic git commits,
* cloud execution.

Reason:

The next feature wave already adds nested instructions, resume, stronger scaffolding, subagents, MCP, browser validation, and plugins. That is enough complexity. Do not turn lunar-forge into a distributed systems dissertation wearing a CLI hat.
