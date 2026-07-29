# LunarForge website copy source

This file is source material for a separate LunarForge website repository. It
does not define a website implementation, server API, or cloud runtime.

## One-sentence description

LunarForge is a safety-conscious Python coding agent with a one-shot CLI,
continuous terminal chat, structured events, resumable sessions, and approved
local or Docker execution.

## Short tagline options

- Build locally, with visible safety boundaries.
- A small coding-agent core for CLI, terminal chat, and future wrappers.
- Inspect, plan, edit, validate, and resume without hiding the machinery.
- One agent engine, multiple transport-neutral interfaces.

## Key features

- Inspect, plan, edit, and validate one selected project root.
- Apply root and nested `AGENTS.md` instructions.
- Run commands locally or in an optional Docker sandbox after approval.
- Use a continuous, resumable Textual chat UI as an optional install.
- Preserve redacted JSONL sessions, checkpoints, and bounded memory summaries.
- Route all guarded actions through UI-independent approval providers.
- Emit versioned, bounded, redacted, JSON-safe agent events.
- Support Git commit gating, browser validation, MCP, plugins, and specialist
  subagents without duplicating the core agent loop.
- Expose a small Python API for future server or web-runtime wrappers.

## Safety model

### Local mode

Local commands run as the current operating-system user with the project root
as their working directory. LunarForge blocks known-dangerous patterns, uses
argument-vector subprocess execution, and asks for targeted approvals, but
local mode is not OS-level isolation. Use it for projects and commands you
trust.

### Docker mode

Docker mode runs approved commands in `lunar-forge-sandbox` with the selected
project mounted at `/workspace`. It is the recommended mode for untrusted
projects and dependency installs. On Windows, Docker Desktop or another
compatible Docker engine must be installed and running. Docker is a stronger
boundary than local mode, but it is not a promise against every host or engine
misconfiguration.

Hard safety rules remain in force in every UI and cannot be overridden by a
prompt, project instruction, plugin, or old approval.

## Textual chat

The optional Textual interface provides continuous multi-turn chat, visible
event-driven progress, approval dialogs, session resume, memory compaction,
project switching, and slash commands. It uses the same agent, events,
approvals, and JSONL session records as the one-shot CLI.

Textual is not a core dependency. The one-shot CLI remains usable without it:

```bash
python -m pip install -e .
```

Install the terminal UI only when needed:

```bash
python -m pip install -e ".[tui]"
lunar-forge chat --project <path>
```

## Event stream and future web compatibility

`run_agent_events(...)` yields the same `AgentEvent` records consumed by the
current terminal interfaces. Every event has a stable versioned envelope and a
bounded, redacted, JSON-safe payload. Core events contain no Rich or Textual
objects, terminal styling, raw provider responses, API keys, or hidden model
reasoning.

A future server can wrap the public package API, serialize events over a
transport such as SSE or WebSocket, and resolve `ApprovalRequest` records with
fresh `ApprovalDecision` records. That server and transport belong in a
separate repository; they are not implemented by LunarForge core.

## Install and setup

Requirements:

- Python 3.11 or newer
- an API key exposed through the environment variable named by configuration
- Docker Desktop/engine only when Docker execution is requested
- the optional `tui` extra for Textual chat
- the optional `browser` extra for local Playwright validation

```bash
git clone <lunar-forge-repository>
cd lunar-forge
python -m venv .venv
python -m pip install -e .
lunar-forge --help
```

PowerShell activation:

```powershell
.\.venv\Scripts\Activate.ps1
$env:OPENAI_API_KEY = "set-this-outside-project-config"
```

Never store raw API keys in `.agent/config.yaml`.

## Quick demo commands

Read-only one-shot inspection:

```bash
lunar-forge --project <path> "Explain this project in three bullets. Do not edit files or run commands."
```

Plan without writes or commands:

```bash
lunar-forge --plan --project <path> "Plan a small accessibility improvement."
```

Optional terminal chat:

```bash
lunar-forge chat --project <path>
lunar-forge chat --resume latest --project <path>
```

Approved Docker validation:

```bash
lunar-forge --docker --project <path> "Run the existing tests and summarize failures. Do not edit files."
```

Browser demo:

After installing the demo's locked npm dependencies:

```bash
npm --prefix examples/projects/browser-demo install
lunar-forge browser-validate --project examples/projects/browser-demo --serve "npm run dev" --url http://localhost:5173 --check "#main-heading"
```

## Screenshot and demo asset checklist

Generate deliberate website assets outside this core repository unless an
asset is explicitly accepted as documentation:

- one-shot read-only result with no secrets or personal paths;
- new Textual session showing the `LunarForge v0.x` frame;
- event-driven progress and elapsed-time completion;
- approval dialog with a harmless disposable command;
- resumed-session greeting and safe historical context;
- `/status`, `/compact`, and filtered slash-command hints;
- Docker command wording and `/workspace` execution evidence;
- browser validation against the checked-in browser demo;
- short recording with API keys, usernames, project names, and session IDs
  redacted;
- alt text, terminal dimensions, command transcript, and reproducible setup for
  every captured asset.

Do not capture or commit `.agent/sessions`, summaries, checkpoints, browser
artifacts, temporary projects, environment values, or private repository
content as marketing assets.

## Short FAQ

### Is LunarForge usable now?

Yes. The current core milestone supports the one-shot CLI and optional Textual
chat. Website and cloud interfaces are future, separate projects.

### Is Textual required?

No. Install `.[tui]` only for `lunar-forge chat`; the one-shot CLI has no
Textual dependency.

### Is local execution sandboxed?

No. Local commands run with the current user's operating-system permissions.
Project-root confinement and command filtering are safety layers, not OS-level
isolation.

### When should Docker mode be used?

Use Docker for untrusted projects, unreviewed scripts, generated code, and
dependency installation. Docker Desktop/engine must already be running.

### Can a future website reuse the agent?

Yes. It should import the documented package API, stream `AgentEvent` records,
and answer transport-neutral approval requests. It should not import internal
agent, renderer, or session implementation modules.

### Are old tool calls or approvals replayed on resume?

No. Resume loads bounded historical context. Tool records are inert, and old
approval decisions never authorize new work.

## What it is not yet

LunarForge core is not a hosted website, FastAPI service, WebSocket/SSE server,
cloud sandbox, background daemon, authentication or billing platform, semantic
index, vector database, multi-repository workspace, or plugin marketplace.
Those concerns should be designed and deployed separately around the stable
package API.
