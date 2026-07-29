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
* support a safe plugin system,
* emit structured agent events that can be rendered by multiple UIs,
* support an optional Textual terminal chat UI with continuous conversation state,
* compact long-running chat history safely when token limits are approached,
* and keep the event protocol compatible with a future web demo and cloud-sandbox agent runtime.

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

Completed second advanced feature wave:

31. Add better file inspection and edit tools:
    * `read_file_with_line_numbers`
    * `replace_lines`
    * `insert_lines`
32. Improve browser-tool routing so UI/browser prompts reliably prefer browser validation or Playwright MCP over curl/basic command validation.
33. Add managed browser-validation server mode so LunarForge can start an approved dev server, wait for a URL, validate the page, and shut the server down.
34. Add clearer Playwright dependency detection and setup guidance, with optional user-approved installation only when explicitly requested.
35. Add parallel subagent phases for read-only analysis and validation/review while keeping write-capable work serialized.

Completed docs/examples/Git feature wave:

36. Add `docs/manual-testing.md` with reproducible manual test checklists.
37. Add an `examples/` folder with small sample projects and copy-paste configs.
38. Add a browser validation demo project under `examples/`.
39. Add opt-in Git commit support with safe status/diff checks and approval.
40. Run a hardening and documentation pass for docs, examples, and Git commit support.

Completed project intelligence feature wave:

41. Add built-in project intelligence tools:
    * `project_health`
    * `dependency_summary`
    * `git_status`
    * `git_diff`
    * `list_changed_files`
42. Teach the agent and subagents to use these tools efficiently instead of dumping unnecessary context.
43. Improve final summaries, review flows, and commit proposals using project-health, dependency, Git status, diff, and changed-file data.
44. Run a hardening and documentation pass for the new tools.

Completed structured context feature wave:

45. Add second-batch read-only context tools:
    * `read_json`
    * `read_yaml`
    * `read_many_files`
    * `list_symbols`
    * `ci_summary`
46. Teach the agent and subagents to use these tools efficiently:
    * prefer `read_json` and `read_yaml` over raw config dumps,
    * prefer `read_many_files` for several small targeted files,
    * prefer `list_symbols` before reading large source files,
    * prefer `ci_summary` when choosing validation commands.
47. Integrate `ci_summary` and structured readers into planning, testing, review, security checks, and final summaries where useful.
48. Run a hardening and documentation pass for the second-batch tools.

Completed token/cost-control feature wave:

49. Add token telemetry for model calls and session totals.
50. Add task-profile based tool-schema filtering so each model call receives only relevant tools.
51. Add an explicit read-only fast path for direct inspection/tool requests.
52. Skip coder/tester/reviewer subagents for read-only tasks unless their roles are explicitly needed.
53. Run a hardening and documentation pass for cost controls and read-only routing.

Completed web-design plugin feature wave:

54. Add a real example plugin under `examples/plugins/web-design-review/`.
55. Implement a read-only website design review plugin tool named `web_design.review_files`.
56. Add example plugin configuration and manual tests that run the plugin against `examples/projects/browser-demo/`.
57. Document plugin permissions, expected findings, and safe usage in README/example docs/manual testing.
58. Run a hardening pass to prove the example plugin cannot execute commands, use network, write files, or escape the project root.

Completed Docker-mode manual validation:

59. Build the `lunar-forge-sandbox` Docker image.
60. Confirm Docker-mode validation runs inside the container at `/workspace`.
61. Confirm Docker-mode explicit `run_command` requests run through Docker, not the local runner.
62. Confirm privileged Docker commands, Docker socket mounts, and path escapes are blocked.
63. Confirm Docker Git commit support commits only LunarForge-changed files and excludes unrelated dirty files.

Completed configurable model reasoning effort:

* Add the nested `model.reasoning.effort` setting with a `medium` default.
* Accept only `low`, `medium`, `high`, `xhigh`, and `max`.
* Keep model identity independent from reasoning effort.
* Add the `--reasoning-effort` CLI override.
* Send effective effort to OpenAI Responses API calls through LiteLLM.
* Record effective effort in session usage events and `--show-usage`.

Completed local/Docker execution-safety hardening:

64. Document local execution safety clearly: local mode is useful, but it is not OS-level isolation.
65. Add targeted local command approval warnings without making every approval prompt long and tedious.
66. Keep Docker approval wording distinct and clear.
67. Add manual tests for local warnings, Docker approval text, no-edit command routing, plan mode, no-command mode, and dangerous-command blocking.
68. Clean up final-summary formatting so security-review findings do not appear under reviewer headings or duplicate empty sections.
69. Harden command safety and commit gating so validation results are visible before commit approval.
70. Preserve explicit `run_command` requests without silently rewriting them into validation commands.

Completed first event-stream phase:

71. Add a structured, versioned, bounded, and redacted agent event stream that separates agent logic from terminal rendering.
72. Add a renderer abstraction and make the existing one-shot CLI consume events through `ConsoleRenderer` while preserving the existing session JSONL format and one-shot output.

Completed first optional Textual chat phase:

73. Add an approval-provider abstraction so CLI, Textual, and future web UIs can all answer permission requests without `input()` buried inside business logic.
74. Add an optional Textual chat app for continuous in-terminal conversation.
75. Persist interactive chat turns to the existing JSONL session format and support resumable chat sessions.

Completed working-memory compaction phase:

76. Add bounded working-memory compaction and persistent safe summaries when continuous chat approaches its configured token budget.

Completed event protocol hardening phase:

77. Document and test the event protocol so it remains compatible with a future web demo and cloud-sandbox runtime.
78. Harden event and approval payloads so they are bounded, redacted, JSON-safe, and free of Rich/Textual objects.
79. Verify one-shot CLI, Textual, and future web renderers can consume the same event protocol without parsing terminal text.

Completed Textual layout and slash-command wave:

80. Redesign the Textual chat layout into a simpler Claude Code-style interface: top project card, chat transcript, temporary progress block, and input area.
81. Add reliable paste and multiline input behavior for the Textual input box.
82. Add temporary in-chat progress rendering with current work, active tool/command, and final elapsed-time reporting.
83. Add a central Textual slash-command router with typed arguments and popup forms.
84. Add popup selectors for config-backed settings with current-value previews and an explicit choice between session-only and saving to project config.
85. Add Textual project switching, session resume, new-project workflow, browser validation, Git, MCP, plugin, checkpoint, and rollback slash commands.
86. Run hardening and manual smoke tests for Textual layout, slash commands, config persistence, resume safety, event compatibility, and one-shot CLI parity.

Completed focused Textual chat-polish wave:

87. Add `/compact` as a manual entry point to the existing safe working-memory compaction flow.
88. Add lightweight, filtered slash-command hints for the chat input, including nested commands.
89. Add explicit `scope=global` persistence for config-backed chat commands through the approval provider and `~/.lunar-forge/config.yaml`.
90. Harden Textual paste, multiline input, speaker labels, and message spacing without changing core events or one-shot output.
91. Keep user/project/session config priority, event transport neutrality, and optional-Textual import boundaries covered by tests.

Completed focused Textual UX-hardening wave:

92. Replace generic temporary work text with phase-aware public descriptions and restore the active phase after approval decisions.
93. Add UI-only animated ellipsis state without changing event payloads.
94. Add a dependency-free Windows clipboard fallback while preserving Textual paste events, multiline input, and explicit submission.
95. Add a bounded, scrollable approval body with a fixed visible button footer.
96. Place the header, transcript, and input inside one accent-colored outer frame with consistent separators.

Completed focused Textual task-control and layout-hardening wave:

97. Keep long `/sessions` pickers bounded and scrollable without displacing the chat input.
98. Keep `Enter` as the supported submit key, preserve multiline paste, and remove the non-portable Shift+Enter claim and binding.
99. Add `/finish` to cancel an active agent turn, deny a pending approval, and conservatively revoke only checkpointed or provably current-turn-created files while leaving chat open.
100. Emit bounded, redacted, JSON-safe `turn.cancelled`, `rollback.started`, and `rollback.finished` events without adding UI fields or dependencies to the core protocol.

The basic read-plan-edit-validate MVP and advanced tool waves already exist. Future work must still be staged carefully. Add features incrementally, with tests and safety reviews after every phase.

---

## Tech stack

Use:

* Python 3.11+
* Typer for CLI
* Rich for existing one-shot terminal output
* Textual as an optional extra for the interactive terminal UI
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

Textual must be optional, not a core dependency, until the interactive UI is stable:

```toml
[project.optional-dependencies]
tui = [
  "textual",
]
```

The core one-shot CLI must continue to work without installing the `tui` extra.

---

## Repository structure

Use this structure:

```text
lunar-forge/
  pyproject.toml
  README.md
  .gitignore
  AGENTS.md

  docs/
    manual-testing.md

  examples/
    README.md
    projects/
      browser-demo/
      static-site/
      vite-react/
      python-cli/
      flask-api/
      fastapi-api/
    mcp/
      playwright/
    plugins/
      web-design-review/
        README.md
        plugin.yaml
        web_design_review.py
        plugins.yaml.example

  lunar_forge/
    __init__.py
    cli.py
    config.py
    agent.py
    events.py
    prompts.py
    planning.py
    permissions.py
    approvals.py
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
      project_health.py
      git.py
      dependencies.py
      structured_readers.py
      symbols.py
      ci.py

    runtime/
      __init__.py
      local_runner.py
      docker_runner.py
      checkpoints.py
      sessions.py
      conversation.py
      compaction.py
      diffs.py
      git.py

    workflows/
      __init__.py
      plan_only.py
      existing_project.py
      new_project.py
      validation.py
      browser_validation.py

    ui/
      __init__.py
      renderers.py
      console_renderer.py
      slash_commands.py
      textual_app.py
      textual_widgets.py
      textual_state.py

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
    summaries/
    checkpoints/
    artifacts/browser/
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
lunar-forge --reasoning-effort high --show-usage "Explain this project"
lunar-forge chat --project ~/dev/my-app
lunar-forge chat --resume latest --project ~/dev/my-app
lunar-forge new "Build a calculator app in Python with UI"
```

Default behavior:

* If `--project` is omitted, use the current working directory.
* The project root is the only filesystem area tools may access.
* `--plan` mode must never write files or run mutating commands.
* Shell commands require approval unless permission mode says otherwise.
* Dependency installation always requires approval.
* `lunar-forge chat` starts the optional Textual continuous chat UI when the `tui` extra is installed.
* The one-shot CLI and Textual UI must use the same agent engine and event stream.

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
  api: responses
  model: openai/gpt-5.6-sol
  api_key_env: OPENAI_API_KEY
  api_base: null
  reasoning:
    effort: medium

runtime:
  mode: local
  allow_network: false

permissions:
  mode: default

ui:
  chat:
    auto_resume: false
    compact_at_tokens: 120000
    compact_to_tokens: 12000
    show_tool_details: true
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

The model/tool loop should report public progress through structured events instead of printing directly from deep runtime code. The one-shot CLI, Textual UI, and future web UI should all consume the same event stream.

---

## LiteLLM behavior

Use LiteLLM from the beginning.

The default model config should be:

```yaml
model:
  provider: litellm
  model: openai/gpt-5.5
  api_key_env: OPENAI_API_KEY
  reasoning:
    effort: medium
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

### Model reasoning effort

Configure reasoning effort only through:

```yaml
model:
  provider: litellm
  api: responses
  model: openai/gpt-5.6-sol
  reasoning:
    effort: xhigh
```

Rules:

* `model.reasoning.effort` defaults to `medium`.
* Allowed values are `low`, `medium`, `high`, `xhigh`, and `max`.
* CLI `--reasoning-effort` overrides project, user, environment, and default values.
* Reasoning effort changes the thinking budget, not model identity.
* Never rewrite or switch model names based on effort.
* OpenAI Responses calls should use the LiteLLM-compatible Responses reasoning payload.
* Unsupported non-OpenAI or non-Responses combinations should warn clearly and continue without the reasoning parameter.
* Do not request reasoning summaries by default.
* Never expose hidden reasoning content.

---

## Token telemetry and cost controls

LunarForge should make token usage visible and measurable.

Goals:

* Record token usage per model call when provider usage metadata is available.
* Record approximate context component sizes even when the provider does not return detailed usage.
* Record the effective `model.reasoning.effort` for every model call.
* Aggregate token usage per run and per session.
* Make usage visible in debug/session logs without cluttering normal final answers.
* Use usage data to guide future context-budget and routing improvements.

Model usage records should include:

```json
{
  "event": "model_usage",
  "phase": "planner",
  "role": "planner",
  "model": "openai/gpt-5.5",
  "reasoning_effort": "medium",
  "input_tokens": 18420,
  "output_tokens": 610,
  "total_tokens": 19030,
  "tool_schema_count": 9,
  "messages_count": 7,
  "context_components": {
    "system_prompt_estimate": 2500,
    "agents_md_estimate": 12000,
    "tool_schemas_estimate": 3500,
    "tool_results_estimate": 400
  }
}
```

Token telemetry rules:

* Do not treat token usage as a secret.
* Do not log API keys, raw credentials, or secret-looking environment values.
* Prefer provider-reported token counts over estimates.
* If only estimates are available, label them as estimates.
* Session summaries may include aggregate usage when a debug/usage flag is enabled.
* Tests should use fake model clients and deterministic usage metadata.

Add CLI/config options only if needed, for example:

```bash
lunar-forge --show-usage "Explain this project"
lunar-forge sessions --usage
```

Keep normal output concise. Users need cost visibility, not another invoice-shaped wall of text.

---

## Task profiles and tool-schema filtering

LunarForge should not expose every tool to every model call.

Task profiles include:

```text
explicit_readonly
plan_only
review_only
no_edit_execution_allowed
edit_task
browser_task
commit_task
new_project
```

Tool-schema filtering rules:

* `explicit_readonly` should expose only the requested read-only tools and minimal supporting read-only tools.
* `plan_only` should expose planning and inspection tools only.
* `review_only` should expose Git/diff/project inspection tools, but no mutation tools.
* `no_edit_execution_allowed` should expose explicitly requested non-edit tools such as `run_command`, `run_validation`, browser validation, MCP read/review tools, plugin review tools, and structured read-only tools, but must not expose file mutation tools.
* `edit_task` may expose read and write tools, but commands should remain permission-gated.
* `browser_task` should expose browser validation tools only when browser intent is explicit.
* `commit_task` should expose Git commit helpers only when commit support is requested.
* Plan mode must never expose write tools, command execution tools, browser server startup, plugin tools, or commit tools.
* No-command mode must not expose shell execution, validation command execution, or Git commit execution tools.
* MCP and plugin tools should be omitted unless explicitly enabled and relevant.
* "Do not edit files" must remove mutation tools, not silently force planner-only behavior.
* If the user says both "do not edit files" and "do not run commands", commands must not be exposed or executed.

For each model call, log the selected task profile and exposed tool names in session logs. This helps debug token bloat and weird routing without making users decode the machine's grocery receipt.

---

## Explicit read-only fast path

Many user requests are direct inspection requests and should not trigger the full subagent workflow.

Examples:

```text
Run read_json on package.json and summarize scripts.
Run read_yaml on .agent/mcp.yaml.
Run list_symbols on lunar_forge/cli.py.
Use read_many_files to summarize README.md and pyproject.toml.
Run ci_summary and tell me the validation commands.
Run git_status and report whether the repo is clean.
```

For explicit read-only requests:

* Use a lightweight read-only execution path.
* Do not run coder, tester, reviewer, or security by default.
* Do not run validation unless the user explicitly asks.
* Do not create checkpoints.
* Do not write session logs in plan mode; otherwise keep session logs compact.
* Expose only the requested read-only tool or a small read-only tool set.
* Keep final output focused on the requested result.

Read-only fast path must still enforce:

* project-root path safety,
* command/no-command restrictions,
* plan-mode no-write behavior,
* bounded outputs,
* safe JSON/YAML parsing,
* no secret access outside the project root.

If the prompt is ambiguous or could require edits, fall back to the normal agent workflow. Fast paths are for obvious cases, not for pretending language is less messy than it is.

---

## Subagent skip rules

Subagents are useful for real work, but not every inspection needs a committee.

Skip coder/tester/reviewer by default when:

* the user explicitly says not to edit files,
* the task is a direct read-only tool request,
* the task is plan mode,
* no file mutation, command execution, browser validation, or commit is requested.

Use planner-only or direct read-only execution for simple inspection tasks.

Run reviewer only when:

* reviewing changed files or diffs,
* commit readiness is requested,
* genuine quality/security/maintainability judgment is needed.

Run tester only when:

* validation is requested,
* edits were made,
* commit readiness requires validation and the user approves commands.

Run security only when:

* MCP, plugins, Docker, permissions, secrets, CI, or external integrations are involved,
* or the user explicitly asks for security review.

Final summaries must accurately report which subagents actually ran. Do not list skipped subagents for decoration. The output is not a trophy case.

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
project_health
dependency_summary
git_status
git_diff
list_changed_files
read_json
read_yaml
read_many_files
list_symbols
ci_summary
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

## Local execution safety

LunarForge supports both local and Docker command execution.

Local execution is convenient, but it is not OS-level isolation. When LunarForge runs a local command, the process runs as the current user account on the host machine. The command working directory is the selected project root, path-based tools are project-confined, commands use `shell=False`, and dangerous command patterns are blocked, but this does not make local execution a sandbox.

Local command execution may still access host resources available to the current user account, depending on the command and operating system behavior. Use Docker mode for untrusted projects, generated code, dependency installs, repositories whose scripts have not been reviewed, or anything that smells like it was copied from a blog comment in 2013.

Docker mode runs commands inside the `lunar-forge-sandbox` container with the project mounted at `/workspace`. Docker mode is the recommended execution mode for untrusted projects, though it is still not a perfect security boundary against every possible Docker or host misconfiguration.

### Local command approval wording

Do not show a long warning before every local command. The full local-execution warning should only be shown when at least one of these conditions is true:

1. This is the first local command approval in the current LunarForge session.
2. A dependency-install command is requested.
3. A command looks risky but is not blocked.
4. The project is marked or inferred as untrusted or unknown.

For ordinary local commands after the first warning in a trusted session, use a short approval prompt.

Full local warning example:

```text
Run local command: npm test

Local commands run as your user account on this machine. The project root is used as the working directory, but this is not OS-level isolation. Use Docker mode for untrusted projects or commands you have not reviewed.

Allow? [y/N]
```

Short local approval example:

```text
Run local command: npm test. Allow? [y/N]
```

Docker approval example:

```text
Run Docker command: python -B -m compileall .
This runs inside lunar-forge-sandbox with the project mounted at /workspace.
Allow? [y/N]
```

### Warning triggers

Dependency-install commands include package-manager and installer actions such as:

```text
npm install
npm ci
pnpm install
yarn install
pip install
python -m pip install
uv pip install
poetry install
```

Risky-but-not-blocked commands should still require approval and should include the full warning. This category is for commands that are allowed but deserve extra user attention, such as commands that execute project scripts, start dev servers, run generated scripts, or invoke interpreters on project files.

Examples of risky-but-not-blocked commands:

```text
npm run <script>
pnpm run <script>
yarn <script>
python app.py
python scripts/<file>.py
node <file>.js
npm run dev
pnpm dev
yarn dev
flask run
fastapi dev
uvicorn <module>:<app>
```

Blocked commands must remain blocked and must not be converted into warning-only prompts.

### No-edit semantics

"Do not edit files" means file mutation tools are not allowed. It does not mean planner-only mode and must not block explicitly requested non-edit tools such as `run_command`, `run_validation`, browser validation, MCP read/review tools, plugin review tools, Git inspection tools, project-intelligence tools, or structured read-only tools when permissions otherwise allow them.

If the user says both "do not edit files" and "do not run commands", commands must not run. Humanity may survive this distinction if we write enough tests.

---

## Shell command execution

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
* Use `shell=False`.
* Capture stdout and stderr.
* Apply timeouts.
* Truncate long output.
* Ask approval before execution in default mode.
* Never treat local execution as an OS-level sandbox.
* Show the full local-execution warning only for the targeted cases documented in `Local execution safety`.
* Use the short local approval prompt for ordinary local commands after the full warning has already been shown in the current trusted session.
* Do not include long approval-warning text in final summaries.
* Keep final summaries focused on commands run, validation results, changed files, and relevant notes.

`run_validation` must use the same command-safety and approval rules as `run_command`.

---

## Docker execution

Docker mode should continue to provide the safer execution path for untrusted projects and unreviewed commands.

Docker mode should:

* check Docker availability with `docker info`,
* run commands inside `lunar-forge-sandbox`,
* mount only the selected project directory,
* use `/workspace` as container workdir,
* disable network by default,
* apply CPU and memory limits,
* never mount host home directory,
* never mount Docker socket,
* never use privileged containers,
* ask approval with Docker-specific wording before executing commands.

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

Docker approval wording should be distinct from local approval wording:

```text
Run Docker command: python -B -m compileall .
This runs inside lunar-forge-sandbox with the project mounted at /workspace.
Allow? [y/N]
```

Docker mode must execute commands through the Docker runner, not the local runner. Manual tests should verify command stdout includes `/workspace` for working-directory checks. If Docker mode prints a host path such as `C:\Users\...`, something has gone deeply silly.

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

## Project intelligence tools

Add read-only built-in tools that help the agent understand project state without wasting model context.

Initial tools:

```text
project_health()
dependency_summary()
git_status()
git_diff(path?, staged=false, max_lines?)
list_changed_files(source="git|session|both")
```

### `project_health`

Purpose:

* summarize repository readiness and maintainability,
* check for README, `AGENTS.md`, tests, validation markers, package markers, `.gitignore`, CI files, runtime/generated folders, and suspicious tracked runtime files,
* return a compact score or status list, not a lecture.

Rules:

* Read-only.
* Must not run arbitrary project code.
* Must keep output bounded.
* Must not inspect secret file contents.
* May use safe filesystem inspection and, when command mode allows it, read-only Git helpers.

Use when:

* user asks to review, audit, explain, improve, onboard, or prepare a project,
* starting a broad existing-project task,
* before proposing a commit if project state looks suspicious.

Do not use for every tiny one-file edit. Tool spam is still spam, just wearing JSON.

### `dependency_summary`

Purpose:

* summarize dependency and script information from `package.json`, `pyproject.toml`, `requirements.txt`, and related project markers,
* identify package manager, framework hints, available scripts, Python dependencies, and likely validation commands.

Rules:

* Read-only.
* Parse files directly; do not install or execute dependencies.
* Bound dependency lists and lockfile information.
* Prefer this tool over dumping full `package.json` or dependency files into model context.

Use when:

* planning validation,
* choosing dev/build/test commands,
* working in Python or Node projects,
* preparing browser validation or new-project checks.

### `git_status`

Purpose:

* return compact `git status --short` style state for the project.

Rules:

* Read-only.
* Use `shell=False` through the existing Git/runtime helper.
* Must fail clearly outside a Git repository.
* Must respect no-command mode if the implementation treats Git subprocesses as command execution.
* Must not stage, commit, or mutate files.

Use when:

* reviewing changes,
* before commit proposals,
* before rollback or risky edits,
* final summaries when Git is available.

### `git_diff`

Purpose:

* return a bounded diff summary or selected diff for Git-tracked changes.

Rules:

* Read-only.
* Must cap lines/bytes.
* Must support staged and unstaged diffs where practical.
* Must not expose excluded secret/runtime files.
* Must fail clearly outside a Git repository.

Use when:

* reviewer subagent checks changed files,
* creating commit messages,
* explaining what changed,
* verifying that generated/runtime files are excluded.

### `list_changed_files`

Purpose:

* combine changed files from current LunarForge session state and/or Git state.

Rules:

* Read-only.
* Support `source="session"`, `source="git"`, and `source="both"`.
* Mark files as session-changed, Git dirty, excluded, staged, or untracked when known.
* Do not include generated/runtime/secret files as commit candidates.

Use when:

* producing final summaries,
* reviewer checks,
* commit proposals,
* deciding which files validation should focus on.

### Efficient tool-use policy

The agent should use these tools deliberately:

* For broad project tasks: start with `project_health`, `detect_project`, and `dependency_summary` before reading many files.
* For small targeted edits: avoid broad health checks unless the user asks for review or commit.
* Before validation: use `dependency_summary` when validation commands are uncertain.
* Before review/final summary/commit: use `list_changed_files` and `git_diff` when Git is available.
* Do not call `git_diff` repeatedly when no files changed.
* Do not dump large raw files when `dependency_summary` or `project_health` gives enough signal.
* Prefer concise structured results over long prose.

---

## Structured reading, symbol, and CI tools

Add read-only built-in tools that reduce raw context dumps and improve code navigation.

Second-batch tools:

```text
read_json(path, max_bytes?)
read_yaml(path, max_bytes?)
read_many_files(paths, max_bytes_per_file?, max_total_bytes?)
list_symbols(path)
ci_summary()
```

### `read_json`

Purpose:

* safely read and parse JSON files inside the project root,
* return structured data, top-level keys, and parse errors without dumping huge raw files.

Rules:

* Read-only.
* Use `safe_path`.
* Refuse paths outside the project root.
* Do not read obvious secret files or runtime folders.
* Bound file size and returned data.
* For huge arrays or objects, return a preview, counts, and truncation metadata.
* Return clear parse errors with line/column details when available.

Use when:

* inspecting `package.json`, `tsconfig.json`, config JSON, or project metadata,
* choosing scripts, dependencies, or validation commands,
* avoiding raw JSON dumps in model context.

### `read_yaml`

Purpose:

* safely read and parse YAML files inside the project root,
* return structured data, top-level keys, and parse errors without dumping huge raw files.

Rules:

* Read-only.
* Use `safe_path`.
* Use `yaml.safe_load`; never execute custom YAML constructors.
* Do not read obvious secret files or runtime folders.
* Bound file size and returned data.
* Return clear parse errors with line/column details when available.

Use when:

* inspecting `.agent/config.yaml`, `.agent/mcp.yaml`, plugin manifests, CI YAML, Docker Compose, or GitHub Actions workflows,
* validating config shape before editing,
* avoiding raw YAML dumps in model context.

### `read_many_files`

Purpose:

* batch-read several small, targeted files in one tool call,
* reduce round trips when the agent already knows exactly which files matter.

Rules:

* Read-only.
* Use `safe_path` for every path.
* Cap file count, bytes per file, and total returned bytes.
* Skip binary files and return a per-file error instead of failing the whole request.
* Preserve per-file metadata: path, line count, truncated flag, and error when present.
* Do not use this tool to dump entire projects.

Use when:

* reading a small set such as `README.md`, `pyproject.toml`, and `AGENTS.md`,
* reviewing a known changed-file set,
* gathering context after `list_changed_files` or `git_diff` identifies relevant paths.

### `list_symbols`

Purpose:

* provide lightweight code navigation without a full language server,
* list functions, classes, exports, and likely React components with line numbers.

Rules:

* Read-only.
* Do not execute project code.
* For Python, prefer `ast` parsing.
* For JavaScript/TypeScript/JSX/TSX, use conservative text parsing first.
* Return symbols with kind, name, start line, end line when practical, and parent/container when known.
* Fail gracefully for unsupported file types.

Use when:

* locating functions/classes/components before editing,
* reviewing large files,
* deciding which line ranges to read with `read_file_with_line_numbers`,
* avoiding unnecessary full-file reads.

### `ci_summary`

Purpose:

* summarize CI configuration and extract likely validation/build/test commands.

Inspect:

```text
.github/workflows/*.yml
.github/workflows/*.yaml
.gitlab-ci.yml
azure-pipelines.yml
bitbucket-pipelines.yml
.circleci/config.yml
```

Rules:

* Read-only.
* Parse YAML safely when possible and fall back to bounded text snippets if parsing fails.
* Do not run CI commands.
* Return workflow names, jobs, runtimes, package-manager hints, and commands.
* If no CI exists, return `ok: true` with an empty summary and clear message.

Use when:

* choosing validation commands,
* reviewing release readiness,
* planning changes in projects with CI,
* checking whether local validation should mirror CI.

### Efficient use of structured readers and navigation tools

The agent should use these tools deliberately:

* Prefer `read_json` for JSON config over `read_file`.
* Prefer `read_yaml` for YAML config over `read_file`.
* Prefer `read_many_files` only when a small known set of files is needed.
* Prefer `list_symbols` before reading large source files or searching for definitions.
* Prefer `ci_summary` before inventing validation commands in projects with CI.
* Do not call all tools on every prompt. Small targeted edits should stay small.
* Do not let structured readers bypass secret, runtime, or project-root restrictions.

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

Implemented utility commands:

```bash
lunar-forge sessions
lunar-forge resume <session-id>
```

Event-stream and Textual work must keep using this session system instead of creating a separate memory universe. A single project should not need one history for CLI, another for Textual, and a third one lurking behind the fridge.

Session resume requirements:

* Resume must load a previous JSONL session without exposing secrets.
* Resume must reconstruct enough conversation state to continue safely.
* Resume must validate that the session belongs to the selected project.
* Resume must support a dry-run/summary mode.
* Resume must not replay tool calls automatically.
* Resume must clearly distinguish historical tool results from new actions.
* Resume must keep plan mode no-write.
* Resume must continue logging into a new session file that references the resumed session.
* Textual chat resume should load prior safe conversation context and compacted summaries without replaying old tool calls.
* `lunar-forge chat --resume latest` should resume the latest compatible session for the selected project.
* Interactive turns should continue appending safe events to session logs.


---

## Agent event stream

The next architecture step is to separate agent logic from display.

Current one-shot behavior should be preserved, but the internal engine should emit structured events that can be consumed by:

* the existing CLI console renderer,
* the upcoming Textual chat UI,
* session JSONL logs,
* and a future web demo or cloud-sandbox UI.

Do not build Textual by parsing terminal text. Terminal-output parsing is how software develops raccoon energy.

Initial files:

```text
lunar_forge/events.py
lunar_forge/ui/renderers.py
lunar_forge/ui/console_renderer.py
```

Recommended public API:

```python
for event in run_agent_events(request):
    renderer.handle(event)
```

Keep the existing one-shot API as a wrapper:

```python
def run_agent(...):
    events = run_agent_events(...)
    return ConsoleRenderer(...).consume(events)
```

### Event schema

Use one versioned envelope for every public runtime event:

```json
{
  "schema_version": 1,
  "event_id": "evt_...",
  "session_id": "session_...",
  "turn_id": "turn_...",
  "sequence": 42,
  "timestamp": "2026-01-01T12:00:00Z",
  "type": "tool.finished",
  "parent_event_id": "evt_...",
  "payload": {}
}
```

Event rules:

* Events must be JSON-serializable.
* Events must be stable enough for both terminal and web renderers.
* Every event should include:
  * `schema_version`,
  * `event_id`,
  * `session_id`,
  * `turn_id`,
  * a monotonic per-session `sequence`,
  * `timestamp`,
  * `type`,
  * `payload`,
  * optional `parent_event_id`.
* Use UTC ISO-8601 timestamps.
* Treat `type` plus `schema_version` as the public protocol contract.
* Put event-specific data inside `payload`; do not add renderer-only fields to the envelope.
* Keep event payloads bounded.
* Redact secrets before events are logged or rendered.
* Never emit hidden chain-of-thought or private model reasoning.
* Do not expose raw provider responses unless they are already normalized and safe.
* Event names should describe public runtime facts, not internal implementation gossip.

### Event types

Initial event types:

```text
session.started
session.resumed
turn.started
turn.finished
turn.cancelled
status.updated
assistant.message.delta
assistant.message.completed
model.call.started
model.call.finished
model.usage
tool.started
tool.finished
tool.failed
permission.requested
permission.resolved
validation.started
validation.finished
browser.started
browser.finished
checkpoint.created
git.proposal
git.commit.created
git.commit.skipped
memory.compaction.started
memory.compaction.finished
rollback.started
rollback.finished
error
```

Tool events should include:

```json
{
  "tool_name": "run_command",
  "provider_tool_name": "run_command",
  "args_preview": {"command": "python -B -m compileall ."},
  "permission_level": "execute"
}
```

Tool results should include bounded, redacted summaries. Large stdout, diffs, screenshots, and logs should be referenced by artifact path or truncated preview instead of dumped into every event.

### Event compatibility with future web/cloud runtime

The event stream must not assume a terminal.

Rules:

* Do not include Rich/Textual objects in core events.
* Do not include terminal color markup in core events.
* Use plain dictionaries, dataclasses, or Pydantic-style serializable structures.
* Keep UI-specific formatting in renderers.
* Design approval events so they can be answered by a CLI prompt, Textual modal, or future web button.
* Design artifact references so local paths can later become URLs or cloud artifact IDs.
* Keep cloud sandbox execution out of scope for this wave, but do not make event payloads impossible to transport over WebSocket/SSE later.

---

## Renderer abstraction

Rendering should consume events, not agent internals.

Initial renderers:

```text
ConsoleRenderer
JsonlSessionRenderer
```

Later renderer:

```text
TextualRenderer
```

Renderer rules:

* The existing CLI output should remain mostly unchanged after the event refactor.
* Console rendering may use Rich.
* Session rendering should write the same safe JSONL logs already used by LunarForge.
* Renderers must not make agent decisions.
* Renderers must not bypass permissions.
* Renderers must not mutate project files.
* Renderers should be deterministic so tests can assert event order and output shape.

The console renderer should handle:

* status updates,
* streamed assistant text,
* tool start/finish,
* permission prompts through an approval provider,
* validation summaries,
* Git proposals,
* final summaries,
* usage output when `--show-usage` is enabled.

Do not delete the current concise final-summary format. Wrap it around events.

---

## Approval providers

Permission handling must become UI-independent.

Do not bury `input()` calls inside low-level permission or runtime helpers. That works for one-shot CLI mode and then immediately becomes a brick when Textual appears.

Initial interface:

```python
class ApprovalProvider:
    def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        ...
```

Initial implementations:

```text
CliApprovalProvider
AutoApprovalProvider
DenyApprovalProvider
TextualApprovalProvider
```

Approval request fields should include:

```json
{
  "id": "approval_...",
  "kind": "command|write|git_commit|browser_server|mcp_tool|plugin_tool",
  "title": "Run Docker command",
  "summary": "python -B -m compileall .",
  "details": "...",
  "risk": "low|medium|high",
  "mode": "local|docker|plan|no-command",
  "default": false
}
```

Rules:

* Existing permission modes must keep their behavior.
* `--plan` must still deny writes and command execution.
* `no-command` must still deny command execution and Git commit execution.
* Dependency install commands must still require approval.
* Dangerous commands must remain blocked before approval.
* Local full-warning behavior must stay targeted.
* Docker approval wording must stay distinct.
* The Textual UI must pause the current turn while approval is pending and resume with the decision.
* Approval decisions must be logged to the session JSONL.

---

## Textual chat UI

Textual is the interactive terminal UI for continuous LunarForge chat. It must remain optional and must consume the same event-driven agent engine used by the one-shot CLI.

This wave is a UI and command-surface refinement. Reuse the existing event stream, renderer seam, approval providers, JSONL session/resume system, and working-memory compaction implementation. Do not create parallel event, approval, session, or memory systems for slash commands.

Commands:

```bash
lunar-forge chat --project ~/dev/my-app
lunar-forge chat --project ~/dev/my-app --resume latest
```

Textual must be optional:

```bash
python -m pip install -e ".[tui]"
```

If Textual is missing and the user runs `lunar-forge chat`, show a clear setup message instead of crashing. The one-shot CLI must keep working without Textual installed.

### Textual layout

The Textual UI should use a simple Claude Code-style layout, not a dashboard full of panels that nobody asked for.

The header is the top section of one common chat frame:

```text
╭──────────── LunarForge v0.x ─────────────╮
│ What are we building today?              │   new session
│ Let’s get back to building.              │   resumed session
│                                          │
│ Project: C:\...\my-app                  │
│ Model: openai/gpt-5.6-sol               │
│ Effort: medium                           │
│ Mode: default / docker / no-command      │
├──────────────────────────────────────────┤
│ chat transcript                          │
├──────────────────────────────────────────┤
│ input                                    │
╰──────────────────────────────────────────╯
```

Rules:

* Put `LunarForge v0.x` in the common outer frame border/title and do not duplicate the version below it.
* Show `What are we building today?` for a new chat session.
* Show `Let’s get back to building.` for a resumed chat session.
* Show project root, model, reasoning effort, and runtime/permission mode.
* Keep the header, transcript, and input inside one outer frame using the accent color previously used by the input border.
* Use consistent horizontal separators between the header, transcript, and input instead of separate competing borders.
* Do not add permanent panels for recent activity, last sessions, changed files, tips/status, shortcuts, Docker status, tool log, or activity.
* Keep tool/activity information as temporary progress content while a turn is running.

Main body:

```text
╭──────────── LunarForge v0.x ─────────────╮
│ What are we building today?              │
│ Project: C:\...\my-app                  │
│ Model: openai/gpt-5.6-sol               │
│ Effort: high                             │
│ Mode: docker                             │
╰──────────────────────────────────────────╯

You:
Add a pricing page.

LunarForge:
I’ll inspect the routing and navbar, then make the smallest change.

[temporary while running]
Gathering information about the project...
Tool: list_dir .
Elapsed: 00:08
[/temporary]

LunarForge:
Done in 1 minute 12 seconds.

Changed files:
- app/pricing/page.tsx
- components/Navbar.tsx

Validation:
- pnpm build passed

› input
```

Layout rules:

* The chat transcript is the primary area.
* Add a blank line between user and assistant messages and between turns.
* The input area stays at the bottom.
* The progress block appears inside the chat transcript while the agent is working.
* The progress block is temporary and must disappear or be replaced when the final assistant message for that turn is shown.
* The final assistant message should include elapsed time, for example `Done in 2 minutes 4 seconds.`
* Approval prompts may appear as modal/popup UI or as an inline temporary approval block, but must not use raw `input()`.
* Do not add complex tabs in this wave. Tabs breed in captivity and then someone has to maintain them.

### Progress display

During an active turn, show a short temporary progress block in the chat transcript.

The progress block should include:

* one short sentence describing current public work,
* the currently active tool or command when applicable,
* an elapsed timer while the turn is running.

Examples:

```text
Gathering information about the project...
Tool: read_file AGENTS.md
Elapsed: 00:04
```

```text
Running validation...
Command: python -B -m compileall .
Elapsed: 01:16
```

Rules:

* Use public progress only. Do not show hidden reasoning or chain-of-thought.
* Update progress from structured events such as `status.updated`, `tool.started`, `tool.finished`, `validation.started`, `validation.finished`, and `permission.requested`.
* Prefer concise phase descriptions: gathering project information, planning implementation, applying changes, running validation, preparing a Git commit, checking the browser, compacting context, or running an external MCP/plugin review.
* Use `Working on your request` only as a fallback.
* If a phase description ends in an ellipsis, animate `.`, `..`, and `...` in Textual state only. Never mutate the underlying event.
* Keep each visible ellipsis state for about two-thirds of a second, producing a complete `.`, `..`, `...` cycle in about two seconds without slowing the separate elapsed-time refresh.
* While permission is pending, show `Waiting for approval` and the bounded command/tool/action below it.
* Approval resolution must restore the suspended active phase. `Approval granted` or `Approval denied` must not become a stuck work description.
* When the turn finishes, remove the temporary progress block and show the final assistant message.
* The final assistant message should include total elapsed time.
* One-shot CLI output should remain concise and should not be forced to mimic the Textual progress block.

### Text input and paste behavior

The input box must support real-world paste behavior, because users paste logs, code, and prompts, not tiny haiku.

Rules:

* Normal paste must work.
* Multi-line paste must work.
* `Enter` sends the current message.
* Shift+Enter is not a supported newline shortcut because terminal key reporting is inconsistent.
* `Ctrl+V` should paste when the terminal and Textual deliver clipboard or paste data.
* On Windows, a lightweight UI-only system clipboard fallback may be used when Textual's clipboard is empty.
* Pasted text must preserve newlines.
* Paste handling must never submit the message by itself.
* Very large pasted input should be bounded or warned about before submission rather than freezing the UI.
* Slash commands should be recognized only when the input begins with `/` after trimming leading whitespace.
* Terminal key reporting varies. Handle Textual paste events plus the common `Ctrl+V` binding while keeping `Enter` as the submit action.
* If clipboard contents are unavailable, show a short UI note without crashing or logging clipboard contents.

### Textual approval layout

Approval UI uses a fixed header, a bounded scrollable detail body, and a fixed footer containing Deny and Approve. Long summaries, commands, and details wrap or scroll without pushing the buttons off-screen. The panel may retain its warning border, but it must stay inside the main layout, preserve transcript scrolling, and leave the input area intact in narrow and full-screen terminals.

### Textual slash commands

Textual chat should include a slash-command router that works like the CLI command surface, adapted for continuous chat.

Slash commands must support both:

* typed arguments, for fast users,
* popup forms/selectors, for discoverability.

If a slash command that requires arguments is entered without arguments, show a popup form instead of failing with a cryptic error.

Initial commands:

```text
/help
/status
/clear
/exit
/finish
/compact
/project
/plan
/docker
/allow-network
/subagents
/parallel-subagents
/commit
/commit-message
/show-usage
/reasoning-effort
/runtime
/permissions
/mcp
/plugins
/sessions
/resume
/resume latest
/new
/browser-setup
/browser-validate
/checkpoints
/rollback
/git status
/git commit
/mcp list
/plugins list
```

Command behavior:

* `/help` shows available commands and short usage.
* `/status` shows current project, model, effort, runtime mode, permission mode, network setting, subagent setting, commit setting, MCP/plugin setting, session id, and compaction status.
* `/clear` clears the visible transcript only after confirmation; it must not delete session logs.
* `/exit` exits cleanly.
* `/finish` requests best-effort cancellation of the active agent turn, denies any pending approval, and revokes only changes proven to belong to that turn. It leaves chat open, preserves unrelated and previous-turn changes, and reports partial rollback instead of overwriting unverifiable state.
* `/compact` manually invokes the existing safe compaction flow, emits the normal compaction event pair, and reports a non-error no-op when there is not enough older context.
* `/project` switches the active project root and starts a fresh conversation context for that project.
* `/plan` toggles or sets plan mode for the active chat session.
* `/docker` toggles Docker runtime mode for the active chat session.
* `/allow-network` toggles network permission for Docker/browser workflows.
* `/subagents` toggles subagent use.
* `/parallel-subagents` toggles parallel subagent phases.
* `/commit` toggles commit offering for future turns in the active chat session.
* `/commit-message` sets the default commit message for the next commit flow.
* `/show-usage` toggles usage output in final answers.
* `/reasoning-effort` sets `model.reasoning.effort`.
* `/runtime` sets runtime mode.
* `/permissions` sets permission mode.
* `/mcp` enables/disables MCP integration.
* `/plugins` enables/disables plugin integration.
* `/sessions` opens a session list/picker for the current project. Long lists use a bounded scrollable body so the transcript and input remain intact in narrow, normal, and full-screen layouts.
* `/resume` opens a resume picker.
* `/resume latest` resumes the latest compatible session for the current project.
* `/new` asks for a project prompt and runs the existing new-project workflow.
* `/browser-setup` runs the existing browser setup helper with normal approvals.
* `/browser-validate` runs browser validation using typed args or a popup form.
* `/checkpoints` lists available checkpoints for the current project.
* `/rollback` rolls back a selected checkpoint/path after approval.
* `/git status` runs the existing Git status helper.
* `/git commit` opens a commit form or accepts a typed message argument.
* `/mcp list` runs MCP diagnostics/listing.
* `/plugins list` runs plugin diagnostics/listing.

### Slash command arguments and forms

Typed examples:

```text
/project C:\Users\tiron\Desktop\my-app
/reasoning-effort high
/reasoning-effort xhigh scope=project
/reasoning-effort medium scope=global
/runtime docker
/runtime no-command scope=session
/permissions no-command
/commit-message "Add pricing page"
/git commit message="Add pricing page"
/browser-validate url=http://localhost:5173 full-page=true width=1440 height=1200
/browser-validate url=http://localhost:5173 serve="npm run dev" screenshot=true check=console check=title
/resume latest
```

`/browser-validate` options:

```text
url
serve
screenshot
no-screenshot
full-page
width
height
startup-timeout-ms
check
```

Rules:

* Typed arguments should be parsed deterministically.
* Quoted strings must be supported for messages and serve commands.
* Boolean flags such as `full-page`, `screenshot`, and `no-screenshot` must be handled clearly.
* Repeated `check` values should be supported.
* If arguments are missing, show a popup form with the relevant fields.
* Popup forms must validate input before running actions.
* Invalid typed arguments should show a clear error and should not start an agent turn.
* For config-backed commands, `scope=session`, `scope=project`, and `scope=global` are the typed persistence choices.
* A typed config value without `scope` defaults to `scope=session`; it must never silently write project or user config.
* `scope=project` means the user explicitly chose save-to-project-config and should update the active session immediately after a successful save.
* `scope=global` means the user explicitly chose save-to-user-config. It must request approval, write `~/.lunar-forge/config.yaml` safely and atomically when practical, and update active state only after persistence succeeds.

### Slash-command hints

When the trimmed input is exactly `/`, show a small non-blocking list of common commands: `/help`, `/status`, `/compact`, `/sessions`, and `/resume`. As the user types, filter the central command registry, including nested commands such as `/git status`, `/mcp list`, and `/plugins list`.

Hide hints when input no longer begins with `/`, the message is submitted, `Escape` is pressed, or an approval/popup is visible. An unknown prefix may show a small `No matching commands.` state. This is input autocomplete, not a second command router or a permanent command palette.

### Config-backed slash commands

Some slash commands control values that also exist in config. These commands must show popup selectors with the current value and persistence choice.

Config-backed commands:

```text
/plan
/docker
/allow-network
/subagents
/parallel-subagents
/reasoning-effort
/runtime
/permissions
/mcp
/plugins
```

`/plan` is a convenience alias for the plan permission setting, and `/docker` is a convenience alias for Docker runtime mode. They follow the same session/project persistence rules as `/permissions` and `/runtime`. `/commit`, `/commit-message`, and `/show-usage` are session controls in this wave and are not persisted unless a future documented config key is introduced.

Popup requirements:

* Show the current effective value.
* Show available choices.
* Let the user choose either:
  * apply to this chat session only,
  * save to project `.agent/config.yaml`,
  * save to user `~/.lunar-forge/config.yaml`.
* If saving to project config and `.agent/config.yaml` does not exist, create it safely.
* If saving to project config and the file exists, edit only the relevant key while preserving unrelated config when practical.
* Validate config values before writing.
* Do not write secrets to project config.
* Do not bypass normal file safety or checkpoint rules.
* Route the config write through the normal approval provider and permission policy; the popup choice must not become a second approval mechanism.
* Changes applied to the current chat session should affect future turns until changed again or until the session exits.
* Saving to project config should also update the active session value immediately.
* Saving to user config should also update the active session only after the approved write succeeds. Denied or failed writes must leave active state unchanged.
* Preserve config precedence: CLI flags, project config, user config, environment, then defaults.

Suggested mapping:

```yaml
runtime:
  mode: local | docker | no-command
  allow_network: true | false

permissions:
  mode: default | yes | no-command | plan | docker

subagents:
  enabled: true | false
  parallel: true | false

model:
  reasoning:
    effort: low | medium | high | xhigh | max

mcp:
  enabled: true | false

plugins:
  enabled: true | false
```

Do not silently persist session-only choices. The user must explicitly choose the save-to-config option. Surprise config mutation is how tools become haunted.

### Project switching

`/project` changes the active project root for continuous chat.

Rules:

* Validate the selected path exists and is a directory.
* Start a fresh conversation context after switching projects.
* Start or select a session log for the new project.
* Reload project config, `AGENTS.md`, nested instructions, plugins, and MCP config for the new project.
* Reset project-specific changed-file state, checkpoints view, and compaction summary context.
* Do not carry old project file facts into the new project context.
* Keep global UI preferences only.
* Show a clear confirmation in the transcript.

### Resume behavior

`/resume` opens a picker. `/resume latest` resumes the latest compatible session for the current project.

Rules:

* Resuming must not replay old tool calls.
* Historical tool results must be marked as historical context.
* Load safe conversation turns and compacted summaries only.
* Restore visible transcript when practical.
* Keep project-root validation strict.
* If the resumed session belongs to a different project, refuse or ask the user to switch projects first.

### New project behavior

`/new` should ask for a natural-language project prompt and run the existing new-project workflow.

Rules:

* Reuse existing new-project detection and scaffolding logic.
* Do not build a complex template-picker UI in this wave.
* Empty or near-empty project checks still apply.
* Dependency install commands still require approval.
* Docker/local execution rules still apply.

### Textual UI rules

* The Textual app should submit user turns to the same event-driven agent engine used by the one-shot CLI.
* The app keeps live conversation state in memory while open.
* Each user message becomes a new turn in the same session unless `/project` switches to a new project context.
* Slash-command actions must delegate to existing workflows and runtime helpers instead of duplicating their business logic.
* Tool approvals should appear as UI prompts, not raw terminal `input()`.
* Approval providers remain the only mechanism for resolving guarded actions triggered from chat or slash commands.
* The UI must keep rendering status while long operations run.
* The UI must not parse console text to infer state.
* The UI must not expose hidden reasoning.
* The UI should gracefully show errors and let the user continue chatting when safe.
* The one-shot CLI must keep working without Textual installed.
* Session-only slash settings live only in active Textual state and must not be written to JSONL as reusable authorization or persisted into project config.
* Existing JSONL session logs and `.agent/summaries/` remain the only persistent conversation/resume sources.
* The future web UI should be able to reuse the same events and command/action semantics without depending on terminal widgets.

---

## Conversation memory and compaction

Continuous chat needs two memory levels:

```text
live memory:
  current in-process conversation for the open Textual app

persistent memory:
  session JSONL plus compacted summaries under .agent/summaries/
```

Do not build memory compaction before the event stream and Textual MVP work. First make continuous chat real; then teach it to forget responsibly, an ability humans still market as wisdom.

Memory rules:

* Live chat should preserve prior user and assistant turns while the app remains open.
* Session logs remain the source of truth for recovery.
* Resuming a session must not replay old tool calls.
* Historical tool results must be clearly marked as historical context.
* Compaction should summarize older safe conversation context when token budgets are approached.
* Compaction must preserve:
  * current task goal,
  * user constraints,
  * selected project root,
  * permission/runtime mode,
  * files changed,
  * validation results,
  * open TODOs,
  * important tool results,
  * approval decisions,
  * relevant AGENTS.md instructions,
  * unresolved errors.
* Compaction must not preserve:
  * secrets,
  * hidden reasoning,
  * raw huge command output,
  * stale file contents that may have changed,
  * unrelated old discussion.

Suggested compaction files:

```text
.agent/summaries/<session-id>.summary.json
```

Suggested structure:

```json
{
  "schema_version": 1,
  "session_id": "...",
  "source_events_through": "...",
  "created_at": "...",
  "summary": "...",
  "facts": {
    "project_root": "...",
    "runtime_mode": "docker",
    "permission_mode": "default",
    "changed_files": [],
    "validation": [],
    "open_items": []
  }
}
```

Compaction trigger:

* estimate tokens for live conversation plus current AGENTS/tool context,
* when approaching configured threshold, emit `memory.compaction.started`,
* call the model with a summarization prompt that forbids hidden reasoning,
* save the summary,
* replace older conversation turns with the compacted summary plus recent turns,
* emit `memory.compaction.finished`.

Config:

```yaml
ui:
  chat:
    compact_at_tokens: 120000
    compact_to_tokens: 12000
```

If token estimation is uncertain, err on the side of compacting earlier. Context windows are not a moral challenge.

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
read_file_with_line_numbers
read_json
read_yaml
read_many_files
list_symbols
grep
glob
detect_project
project_health
dependency_summary
ci_summary
git_status
git_diff
list_changed_files
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
read_file_with_line_numbers
read_json
read_yaml
read_many_files
list_symbols
grep
glob
create_dir
write_file
edit_file
replace_lines
insert_lines
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
read_file_with_line_numbers
read_json
read_yaml
read_many_files
list_symbols
grep
glob
project_health
dependency_summary
ci_summary
git_status
git_diff
list_changed_files
```

Reviewer should not mutate files. Reviewer should prefer `list_changed_files` and `git_diff` over rereading the whole project.

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
read_file_with_line_numbers
read_json
read_yaml
read_many_files
grep
dependency_summary
ci_summary
git_status
list_changed_files
```

### Security subagent

Purpose:

* review permissions, command safety, path safety, secrets, Docker settings, MCP tools, and plugin manifests.

Allowed tools:

```text
read_file
read_file_with_line_numbers
read_json
read_yaml
read_many_files
list_symbols
grep
glob
project_health
dependency_summary
ci_summary
git_status
git_diff
list_changed_files
```

Security subagent should be required before enabling MCP/plugin changes. It should use Git/project-health tools to detect secret-looking files, runtime artifacts, and unsafe tracked files.

### Scaffolder subagent

Purpose:

* choose new-project templates,
* produce scaffolding plans,
* create starter projects after approval.

Allowed tools:

```text
list_dir
read_file
read_json
read_yaml
read_many_files
dependency_summary
ci_summary
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


## Git commit support

Git support should be opt-in and conservative.

Goals:

* Let LunarForge optionally create a Git commit after a successful task.
* Keep Git history clean by committing only intended changes.
* Never commit automatically by default.
* Never commit unrelated dirty files without clearly showing them.
* Never commit secrets, generated artifacts, or runtime `.agent/` files.
* Never commit after failed validation unless the user explicitly requests it.

Suggested CLI behavior:

```bash
lunar-forge "Add pricing page" --commit
lunar-forge "Add pricing page" --commit --commit-message "Add pricing page"
lunar-forge git status --project ./my-app
lunar-forge git commit --project ./my-app --message "Add pricing page"
```

Rules:

* `--commit` means “offer to commit after successful work,” not “commit without asking.”
* Git commands must use `shell=False`.
* Commit support must work only inside a Git repository.
* Before committing, show `git status --short` and a bounded diff summary.
* Prefer committing files changed by the current LunarForge session.
* If unrelated dirty files exist, show them and do not include them unless the user explicitly approves.
* Commit message should be concise and derived from the completed task.
* Commit action requires approval even in permissive modes.
* Plan mode must never commit.
* No-command mode must block Git command execution.
* Session logs should record Git status, selected files, approval, and commit hash when available.

Suggested internal helpers:

```text
git_status
git_diff_summary
git_commit
```

Git commit support is useful, but it must remain a guarded finalization step. Git history is not a landfill, no matter how confidently tools keep treating it like one.

---

## Plugin system

Plugins are supported and must remain safer than convenient. Convenient plugin systems are how tools become malware with a README.

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


## Example plugin: web design review

Add a real example plugin under:

```text
examples/plugins/web-design-review/
  README.md
  plugin.yaml
  web_design_review.py
  plugins.yaml.example
```

The example plugin should expose one tool first:

```text
web_design.review_files
```

Purpose:

* Review frontend files for basic website quality.
* Demonstrate safe plugin loading, manifest validation, namespaced tools, and permission gating.
* Stay read-only and deterministic.
* Return structured findings that the normal LunarForge agent can use before making edits with built-in tools.

Tool input:

```json
{
  "files": ["index.html", "src/App.jsx", "src/App.css"],
  "focus": "accessibility responsive visual_hierarchy"
}
```

Tool output should be JSON-serializable and bounded:

```json
{
  "ok": true,
  "summary": "Short design review summary.",
  "score": {
    "accessibility": 8,
    "visual_hierarchy": 7,
    "responsive": 6,
    "content_clarity": 8
  },
  "findings": [
    {
      "severity": "info",
      "category": "accessibility",
      "file": "src/App.jsx",
      "line": 12,
      "message": "Button has clear visible text."
    }
  ]
}
```

Initial checks should be simple, local, and heuristic:

* missing `<title>`,
* missing `lang` on `<html>`,
* missing viewport meta tag,
* images without `alt`,
* buttons or links with no visible text,
* form inputs without labels or accessible names,
* no clear `h1`,
* weak heading structure,
* overuse of generic containers when obvious landmarks would help,
* missing responsive CSS/media queries,
* hardcoded tiny font sizes,
* unclear call-to-action or empty-state copy.

Plugin permissions must remain conservative:

```yaml
permissions:
  filesystem: read
  commands: false
  network: false
```

Rules:

* Do not give this example plugin write permission.
* Do not run shell commands from the plugin.
* Do not use network access.
* Do not depend on Playwright, browsers, npm, or external services.
* Do not require API keys.
* Do not auto-enable the plugin globally.
* The example should be enabled only through an explicit project `.agent/plugins.yaml` entry.
* The plugin must respect project-root path confinement through the existing plugin sandbox/loader mechanisms.
* Plugin findings are advisory. File edits must still be performed by built-in tools after normal approval.

Manual test target:

```text
examples/projects/browser-demo/
```

Example prompt:

```bash
lunar-forge --project examples/projects/browser-demo "Use web_design.review_files to review index.html, src/App.jsx, and src/App.css for accessibility, responsive layout, and visual hierarchy. Do not edit files."
```

Expected behavior:

* Plugin is listed by `lunar-forge plugins list` when enabled for the demo project.
* Plugin tool requires normal tool approval before execution when permissions require it.
* Plugin returns structured findings.
* No files are edited.
* No shell commands run.
* No network access is used.

---


## Documentation and examples

The repository should include documentation and runnable examples.

Required docs:

```text
docs/manual-testing.md
```

Manual testing docs should include:

* installation and config checks,
* plan mode,
* basic project inspection,
* line edit tools,
* new-project scaffolding,
* validation workflow,
* managed browser validation,
* Playwright setup,
* Playwright MCP,
* plugin diagnostics,
* session resume,
* checkpoints and rollback,
* parallel subagents,
* Git commit support,
* Docker execution safety and sandbox-image setup,
* local execution safety warnings,
* no-edit command-routing semantics,
* agent event stream and renderer abstraction,
* Textual chat UI setup, layout, paste behavior, progress block, elapsed timer, and manual smoke tests,
* Textual slash commands, popup selectors, session-only settings, and save-to-project-config flows,
* interactive approvals,
* resumable chat sessions,
* memory compaction and summary files,
* project intelligence tools: `project_health`, `dependency_summary`, `git_status`, `git_diff`, and `list_changed_files`.
* structured context tools: `read_json`, `read_yaml`, `read_many_files`, `list_symbols`, and `ci_summary`.

Each manual test should include:

* setup,
* command,
* expected output,
* cleanup notes.

Required examples:

```text
examples/
  README.md
  projects/
    browser-demo/
    static-site/
    vite-react/
    python-cli/
    flask-api/
    fastapi-api/
  mcp/
    playwright/
  plugins/
    web-design-review/
```

Example rules:

* Examples should be small, readable, and cheap to run.
* Examples should not require secrets.
* Examples should not include generated dependency folders such as `node_modules`.
* Browser demo should be a real Vite/React app or minimal frontend project suitable for:
  * managed server validation,
  * full-page screenshots,
  * console-error collection,
  * Playwright MCP inspection.
* MCP Playwright example should include copy-paste `.agent/config.yaml` and `.agent/mcp.yaml` examples.
* Examples should include README files with exact commands.
* Include only the `web-design-review` plugin example in this batch; do not add additional plugin examples unless explicitly requested later.

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
* `read_json` and `read_yaml` parse valid config and report bounded parse errors.
* `read_many_files` reads only safe, bounded, non-binary project files.
* `list_symbols` reports Python functions/classes and JavaScript/TypeScript symbols without executing code.
* `ci_summary` extracts CI jobs and commands without running them.
* Structured readers and symbol tools cannot escape the project root or read obvious secret/runtime files.
* "Do not edit files" removes mutation tools but does not block explicitly requested `run_command`, `run_validation`, browser validation, plugin review tools, MCP read/review tools, or structured read-only tools.
* "Do not edit files and do not run commands" blocks command execution.
* First local command approval in a session shows the full local-execution warning.
* Second ordinary local command in the same trusted session shows the short local approval prompt.
* Dependency-install commands show the full local-execution warning even after the first warning.
* Risky-but-not-blocked commands show the full local-execution warning.
* Blocked dangerous commands are blocked, not downgraded to warning-only prompts.
* Docker approvals use Docker-specific wording and execute through the Docker runner.
* Reasoning effort defaults to `medium` and accepts only `low`, `medium`, `high`, `xhigh`, and `max`.
* Project reasoning configuration overrides user configuration, and the CLI flag overrides both.
* OpenAI Responses requests include the effective reasoning effort without changing the model name or removing tools.
* Unsupported providers or API modes warn and continue without crashing.
* Session usage events and `--show-usage` include the effective reasoning effort.
* Agent runtime events are JSON-serializable, bounded, redacted, and schema-versioned.
* Existing one-shot CLI output can be produced by consuming events.
* Approval providers preserve CLI/default/yes/no-command/plan/docker behavior.
* Textual chat can run multiple turns in one process using shared conversation state.
* Textual approval prompts resolve permission requests without direct `input()` calls in runtime helpers.
* Chat resume loads previous safe context without replaying old tool calls.
* Memory compaction preserves task state while removing stale or oversized details.

Run tests with:

```bash
pytest
```

---

## Token and routing tests

Cost-control features need tests because otherwise they become inspirational comments.

Required tests:

* token telemetry records provider-reported usage when present,
* token telemetry labels estimates when exact usage is unavailable,
* session logs include aggregate token usage,
* task profiles expose only expected tools,
* `no_edit_execution_allowed` exposes explicitly requested non-edit tools while excluding mutation tools,
* plan mode never exposes write or execution tools,
* explicit read-only fast path skips coder/tester/reviewer,
* explicit `read_json`, `read_yaml`, `read_many_files`, `list_symbols`, `ci_summary`, `git_status`, `git_diff`, and `list_changed_files` prompts use compact read-only routing,
* browser paths such as `browser-demo/package.json` do not trigger browser routing by themselves,
* explicit browser/screenshot/console/UI prompts still trigger browser routing,
* event-driven one-shot CLI preserves task-profile routing and final summaries,
* final summaries report actual subagents run and do not invent skipped roles,
* final summaries render security-review findings under `Security review:` without duplicate empty sections or misleading reviewer headings.

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
* async in core agent logic until there is a real need; Textual UI may use Textual's async/message model at the UI boundary,
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

Local mode must be described honestly: it is command gating and path confinement, not OS-level isolation. Docker mode is recommended for untrusted execution.

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

## Optional usage output

When usage reporting is enabled, append a compact section:

```text
Usage:
- Reasoning effort: medium
- Model calls: 2
- Input tokens: 12,430
- Output tokens: 710
- Total tokens: 13,140
- Task profile: explicit_readonly
- Tool schemas exposed: 3
```

Do not show usage by default unless configured or requested. Normal users want answers; builders debugging the agent want receipts.

---

## Current architectural decisions

These are the defaults unless changed deliberately:

* Use Python 3.11+.
* Use Typer for CLI.
* Use Rich only in the existing one-shot console renderer, not in core events.
* Use LiteLLM as the first model provider layer.
* Emit a versioned, bounded, redacted event stream before adding another UI.
* Make the one-shot CLI consume events through a renderer and preserve its current behavior.
* Route permission decisions through approval providers instead of low-level `input()` calls.
* Keep Textual optional and route it through the event and approval seams; do not build a prompt_toolkit UI first.
* Keep the Textual layout simple: top project card, chat transcript, temporary progress block, and bottom input.
* Implement Textual slash commands through a command router, not ad-hoc widget callbacks.
* Keep core agent logic mostly sync for now; isolate Textual async behavior at the UI boundary.
* Use exact-replacement editing plus line-based edits before advanced patching.
* Use local command runner before Docker runner.
* Use YAML for config.
* Keep JSONL event/session logs as the persistent source of truth for one-shot and resumable chat sessions.
* Add working-memory compaction only after continuous chat works.
* Keep web UI, cloud execution, and cloud sandbox implementation out of this wave while making the event protocol transport-neutral.
* Use pytest for testing.
* Use Docker as the recommended execution mode for untrusted projects; local mode remains convenient but is not OS-level isolation.
* Use `AGENTS.md` as the project instruction file for this agent.
* For Claude Code compatibility, provide a `CLAUDE.md` file that imports `AGENTS.md`.

---

## Still do not build yet

These remain out of scope after the Textual layout, slash-command, and config-popup wave:

* background daemon,
* vector database,
* semantic code index,
* multi-repo workspace mode,
* cloud execution,
* browser-based web demo,
* cloud sandbox orchestration,
* real OS-level local sandboxing,
* full plugin marketplace,
* prompt_toolkit REPL as a competing intermediate UI.

Reason:

The project already has nested instructions, resume, stronger scaffolding, subagents, MCP, browser validation, plugins, better edit tools, parallel subagent phases, structured context tools, token/cost controls, Git support, Docker-mode execution, local execution warnings, configurable reasoning effort, and a hardened Textual experience built on the existing event seam. Future work should preserve the single event, approval, session, and compaction architecture. Do not turn lunar-forge into three frontends arguing over a pile of duplicated state.
