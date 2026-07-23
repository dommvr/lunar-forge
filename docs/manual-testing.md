# Manual testing checklist

This guide exercises LunarForge from a user-facing CLI on a disposable set of
projects. Run the commands from the LunarForge repository root in PowerShell.
Unless a test says otherwise, start it in a fresh PowerShell session so an
environment override from another test cannot affect the result. Rerun the
common variable snippet below when starting a fresh session.

Tests that call the coding agent require a configured model and its named API
key environment variable. The deterministic utility commands (`browser-setup`,
`browser-validate`, `mcp list`, `plugins list`, `checkpoints`, `rollback`,
`sessions`, and `resume --summary-only`) do not contact a model unless the test
also invokes the agent. Vite and Playwright MCP tests require Node.js and `npm`.

Use this disposable root for the examples:

```powershell
$RepoRoot = (Get-Location).Path
$ManualRoot = Join-Path $env:TEMP "lunar-forge-manual"
New-Item -ItemType Directory -Force -Path $ManualRoot | Out-Null
```

When a command prompts for approval, compare the displayed action with the
command described by the test before answering `y`. Paths beneath
`$ManualRoot` can be removed between tests.

## Progress

- [ ] Install and CLI availability
- [ ] Config loading and precedence
- [ ] Plan mode
- [ ] Basic project inspection
- [ ] Built-in project intelligence
- [ ] Line tools
- [ ] Static HTML starter
- [ ] Python Tkinter starter
- [ ] Python CLI starter
- [ ] Flask starter
- [ ] FastAPI starter
- [ ] Vite React starter
- [ ] Checked-in example projects
- [ ] Validation workflow
- [ ] `browser-setup`
- [ ] Managed browser validation
- [ ] Playwright MCP
- [ ] Plugin diagnostics and echo usage
- [ ] Session resume
- [ ] Checkpoints and rollback
- [ ] Parallel subagents
- [ ] No-command mode
- [ ] Guarded Git status and commit
- [ ] Repository validation after documentation changes

## 1. Install and CLI availability

**Purpose**

Confirm that the supported Python version can create an isolated environment,
install LunarForge in editable mode, and expose the console entry point.

**Setup**

Open PowerShell in the repository root. Python 3.11 or newer must be available.

**Command**

```powershell
python -m venv .venv-manual
& .\.venv-manual\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python --version
lunar-forge --help
lunar-forge run --help
```

**Expected result**

The editable install succeeds, Python reports 3.11 or newer, and both help
commands exit with code 0. Root help lists commands including `run`, `new`,
`resume`, `browser-setup`, `browser-validate`, `mcp`, and `plugins`.

**Cleanup**

Keep `.venv-manual` active for the remaining tests. When all testing is done,
run `deactivate` and remove `.venv-manual`.

## 2. Config loading and precedence

**Purpose**

Confirm that environment, project, and explicit CLI-style overrides merge in
the documented order without placing a raw API key in a config file.

**Setup**

```powershell
$ConfigProject = Join-Path $ManualRoot "config-project"
New-Item -ItemType Directory -Force -Path (Join-Path $ConfigProject ".agent") | Out-Null
@'
model:
  model: project/manual-model
permissions:
  mode: no-command
'@ | Set-Content -LiteralPath (Join-Path $ConfigProject ".agent\config.yaml") -Encoding utf8
$env:LUNAR_FORGE_MODEL = "environment/manual-model"
```

**Command**

```powershell
python -c "from pathlib import Path; from lunar_forge.config import load_config; c=load_config(Path(r'$ConfigProject')); print(c.model.model, c.permissions.mode)"
python -c "from pathlib import Path; from lunar_forge.config import load_config; c=load_config(Path(r'$ConfigProject'), {'model': {'model': 'cli/manual-model'}}); print(c.model.model, c.permissions.mode)"
```

**Expected result**

The first command prints `project/manual-model no-command`, proving that the
project file overrides the environment. The second prints
`cli/manual-model no-command`, proving that explicit overrides win over the
project file.

**Cleanup**

```powershell
Remove-Item Env:\LUNAR_FORGE_MODEL -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force -LiteralPath $ConfigProject
```

## 3. Plan mode

**Purpose**

Confirm that `--plan` can inspect a project and return a concrete plan without
editing files, running commands, or creating `.agent` runtime state.

**Setup**

This test requires a configured model.

```powershell
$PlanProject = Join-Path $ManualRoot "plan-project"
New-Item -ItemType Directory -Force -Path $PlanProject | Out-Null
"alpha" | Set-Content -LiteralPath (Join-Path $PlanProject "note.txt") -Encoding utf8
```

**Command**

```powershell
lunar-forge --project $PlanProject --plan --commit "Inspect note.txt and plan how to replace alpha with beta. List the file and validation steps, but do not edit anything."
Get-Content -LiteralPath (Join-Path $PlanProject "note.txt")
Test-Path -LiteralPath (Join-Path $PlanProject ".agent")
```

**Expected result**

LunarForge describes the goal, likely changed file, and validation approach.
`note.txt` still contains `alpha`, the final `Test-Path` prints `False`, and no
write or command approval prompt appears. The Git section says plan mode blocks
the commit, and no Git approval prompt appears.

**Cleanup**

```powershell
Remove-Item -Recurse -Force -LiteralPath $PlanProject
```

## 4. Basic project inspection

**Purpose**

Confirm that project detection, bounded file inspection, search, and root
`AGENTS.md` guidance are available during a read-only request.

**Setup**

This test requires a configured model.

```powershell
$InspectProject = Join-Path $ManualRoot "inspect-project"
New-Item -ItemType Directory -Force -Path (Join-Path $InspectProject "src") | Out-Null
@'
{
  "scripts": {"dev": "vite", "build": "vite build"},
  "dependencies": {"react": "latest"},
  "devDependencies": {"vite": "latest"}
}
'@ | Set-Content -LiteralPath (Join-Path $InspectProject "package.json") -Encoding utf8
"export default function App() { return <h1>Manual demo</h1>; }" | Set-Content -LiteralPath (Join-Path $InspectProject "src\App.jsx") -Encoding utf8
"Prefer concise explanations and mention detected validation commands." | Set-Content -LiteralPath (Join-Path $InspectProject "AGENTS.md") -Encoding utf8
```

**Command**

```powershell
lunar-forge --project $InspectProject --plan "Explain this project's language, framework, package manager, important files, and available development or build commands."
```

**Expected result**

The answer identifies a JavaScript React/Vite project using npm, mentions
`src/App.jsx`, and reports `npm run dev` and `npm run build`. The request remains
read-only and follows the concise project guidance.

**Cleanup**

```powershell
Remove-Item -Recurse -Force -LiteralPath $InspectProject
```

## Read-only project intelligence

**Purpose**

Confirm that the plan-mode registry exposes all five provider-safe,
read-permission project intelligence tools. Exercise filesystem health and
dependency parsing without running project code, reading secret files, or
parsing lockfile bodies. Git-backed behavior is exercised in sections 20 and
21.

**Setup**

```powershell
$IntelProject = Join-Path $ManualRoot "intelligence-project"
New-Item -ItemType Directory -Force -Path (Join-Path $IntelProject "tests") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $IntelProject "dist") | Out-Null
"# Intelligence demo" | Set-Content -LiteralPath (Join-Path $IntelProject "README.md") -Encoding utf8
"Use bounded inspection." | Set-Content -LiteralPath (Join-Path $IntelProject "AGENTS.md") -Encoding utf8
"dist/" | Set-Content -LiteralPath (Join-Path $IntelProject ".gitignore") -Encoding utf8
@'
{
  "scripts": {"test": "vitest run", "build": "vite build", "dev": "vite"},
  "dependencies": {"react": "^19"},
  "devDependencies": {"vite": "^7", "vitest": "^3"}
}
'@ | Set-Content -LiteralPath (Join-Path $IntelProject "package.json") -Encoding utf8
"lockfile body is intentionally not parsed" | Set-Content -LiteralPath (Join-Path $IntelProject "package-lock.json") -Encoding utf8
"manual-secret-canary" | Set-Content -LiteralPath (Join-Path $IntelProject ".env") -Encoding utf8
@'
from pathlib import Path
from setuptools import setup

Path("setup-executed.txt").write_text("setup.py ran")
setup(install_requires=["requests>=2"])
'@ | Set-Content -LiteralPath (Join-Path $IntelProject "setup.py") -Encoding utf8
```

**Command**

```powershell
$env:INTEL_PROJECT = $IntelProject
@'
import json
import os
from lunar_forge.tools.registry import create_tool_registry

registry = create_tool_registry(os.environ["INTEL_PROJECT"], mode="plan")
intelligence = {
    "project_health",
    "dependency_summary",
    "git_status",
    "git_diff",
    "list_changed_files",
}
schema_names = {
    item["function"]["name"]
    for item in registry.schemas(read_only=True, allow_execute=False)
}
print("available:", sorted(intelligence & set(registry.names())))
print("provider-safe:", sorted(intelligence & schema_names))
print(json.dumps(registry.execute("project_health", {}), indent=2))
print(json.dumps(registry.execute("dependency_summary", {}), indent=2))
'@ | python -
Test-Path -LiteralPath (Join-Path $IntelProject "setup-executed.txt")
```

**Expected result**

Both `available` and `provider-safe` list all five exact tool names. Both
filesystem results have `ok: true` and serialize as JSON. Health reports the
README, `AGENTS.md`, tests, package markers, `.gitignore`, `dist`, and validation
hints without returning `manual-secret-canary`. Dependency metadata reports
npm, React/Vite, the scripts, bounded dependency lists, the static `requests`
requirement, and likely npm commands. The invalid lockfile body causes no parse
error because lockfile contents are not read. `Test-Path` prints `False`,
proving `setup.py` was not executed. No approval prompt appears and no project
command runs. This disposable directory is not a Git repository, so invoking a
Git-backed tool here would return a clear non-repository error without mutation.

**Cleanup**

```powershell
Remove-Item Env:\INTEL_PROJECT -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force -LiteralPath $IntelProject
```

## 5. Line tools

**Purpose**

Exercise `read_file_with_line_numbers`, inclusive `replace_lines`, and
`insert_lines`, including the diff and checkpoint behavior for existing files.

**Setup**

This test requires a configured model. `permissions.mode: yes` auto-approves the
two file edits; the prompt does not request command execution.

```powershell
$LineProject = Join-Path $ManualRoot "line-tools-project"
New-Item -ItemType Directory -Force -Path (Join-Path $LineProject ".agent") | Out-Null
@'
permissions:
  mode: yes
'@ | Set-Content -LiteralPath (Join-Path $LineProject ".agent\config.yaml") -Encoding utf8
@'
one
two
three
four
'@ | Set-Content -LiteralPath (Join-Path $LineProject "sample.txt") -Encoding utf8
```

**Command**

```powershell
lunar-forge --project $LineProject "First use read_file_with_line_numbers on sample.txt. Then use replace_lines to replace inclusive lines 2 through 3 with two lines named TWO and THREE. Finally use insert_lines after line 1 to insert INSERTED. Do not use edit_file or write_file, and do not run commands."
Get-Content -LiteralPath (Join-Path $LineProject "sample.txt")
Get-ChildItem -Recurse -File -LiteralPath (Join-Path $LineProject ".agent\checkpoints")
```

**Expected result**

The tool trace shows a numbered read followed by `replace_lines` and
`insert_lines`. The file contains `one`, `INSERTED`, `TWO`, `THREE`, and `four`
in that order. Both edits return bounded unified diffs, and checkpoint copies of
the pre-edit states exist beneath `.agent\checkpoints`.

**Cleanup**

```powershell
Remove-Item -Recurse -Force -LiteralPath $LineProject
```

## New-project scaffolding

The six tests below set `LUNAR_FORGE_PERMISSION_MODE=yes` so deterministic file
creation does not prompt for every generated file. Command execution and
dependency installation still require their own approval. Each target must be
empty before `lunar-forge new` runs.

### 6. Static HTML starter

**Purpose**

Confirm selection and creation of the dependency-free `static_html` template.

**Setup**

```powershell
$StaticProject = Join-Path $ManualRoot "new-static-html"
New-Item -ItemType Directory -Force -Path $StaticProject | Out-Null
$env:LUNAR_FORGE_PERMISSION_MODE = "yes"
```

**Command**

```powershell
lunar-forge new --project $StaticProject "Build a simple static HTML business website"
Get-ChildItem -File -LiteralPath $StaticProject | Select-Object -ExpandProperty Name
```

**Expected result**

The plan names `static_html`; `index.html`, `styles.css`, and `README.md` are
created; no dependency or validation command approval is requested; and the
result includes local run instructions.

**Cleanup**

```powershell
Remove-Item Env:\LUNAR_FORGE_PERMISSION_MODE -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force -LiteralPath $StaticProject
```

### 7. Python Tkinter starter

**Purpose**

Confirm selection and creation of the standard-library `python_tkinter`
calculator template without opening the GUI during the checklist.

**Setup**

```powershell
$TkProject = Join-Path $ManualRoot "new-tkinter"
New-Item -ItemType Directory -Force -Path $TkProject | Out-Null
$env:LUNAR_FORGE_PERMISSION_MODE = "yes"
```

**Command**

```powershell
lunar-forge new --project $TkProject "Build a calculator app in Python with UI"
python -m py_compile "$TkProject\app.py"
Get-ChildItem -File -LiteralPath $TkProject | Select-Object -ExpandProperty Name
```

**Expected result**

The plan names `python_tkinter`; `app.py` and `README.md` are created; no
dependency command is requested; and `py_compile` exits successfully. The
generated README explains how to run `python app.py` when a GUI session is
available.

**Cleanup**

```powershell
Remove-Item Env:\LUNAR_FORGE_PERMISSION_MODE -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force -LiteralPath $TkProject
```

### 8. Python CLI starter

**Purpose**

Confirm the `python_cli` starter, its standard-library test, and its run
instructions.

**Setup**

```powershell
$CliProject = Join-Path $ManualRoot "new-python-cli"
New-Item -ItemType Directory -Force -Path $CliProject | Out-Null
$env:LUNAR_FORGE_PERMISSION_MODE = "yes"
```

**Command**

```powershell
lunar-forge new --project $CliProject "Build a Python CLI for notes"
python "$CliProject\app.py" --name Ada
Push-Location $CliProject
python -m unittest -q
Pop-Location
```

Approve the proposed `python -m unittest -q` validation command during
scaffolding.

**Expected result**

The plan names `python_cli`; `app.py`, `test_app.py`, and `README.md` are
created; the approved validation passes; and the direct invocation prints a
greeting for Ada.

**Cleanup**

```powershell
Remove-Item Env:\LUNAR_FORGE_PERMISSION_MODE -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force -LiteralPath $CliProject
```

### 9. Flask starter

**Purpose**

Confirm the Flask starter, approval-gated dependency installation, and starter
unit test.

**Setup**

Run this inside the disposable Python environment created in test 1.

```powershell
$FlaskProject = Join-Path $ManualRoot "new-flask"
New-Item -ItemType Directory -Force -Path $FlaskProject | Out-Null
$env:LUNAR_FORGE_PERMISSION_MODE = "yes"
```

**Command**

```powershell
lunar-forge new --project $FlaskProject "Build a small Flask API"
Push-Location $FlaskProject
python -m unittest -q
Pop-Location
```

Approve `python -m pip install -r requirements.txt`, then separately approve
`python -m unittest -q`.

**Expected result**

The plan names `flask`; `app.py`, `test_app.py`, `requirements.txt`, and
`README.md` are created. Dependency installation and validation are separate
approval requests, the test passes, and the run instructions use
`flask --app app run --debug`.

**Cleanup**

```powershell
Remove-Item Env:\LUNAR_FORGE_PERMISSION_MODE -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force -LiteralPath $FlaskProject
```

Flask remains installed in `.venv-manual`; removing that disposable environment
after the checklist removes the installed Python dependency.

### 10. FastAPI starter

**Purpose**

Confirm the FastAPI starter, approval-gated dependency installation, and
starter unit test.

**Setup**

Run this inside the disposable Python environment created in test 1.

```powershell
$FastApiProject = Join-Path $ManualRoot "new-fastapi"
New-Item -ItemType Directory -Force -Path $FastApiProject | Out-Null
$env:LUNAR_FORGE_PERMISSION_MODE = "yes"
```

**Command**

```powershell
lunar-forge new --project $FastApiProject "Build a FastAPI service"
Push-Location $FastApiProject
python -m unittest -q
Pop-Location
```

Approve `python -m pip install -r requirements.txt`, then separately approve
`python -m unittest -q`.

**Expected result**

The plan names `fastapi`; `app.py`, `test_app.py`, `requirements.txt`, and
`README.md` are created. Dependency installation and validation are separate
approval requests, the test passes, and the run instructions use
`uvicorn app:app --reload` and point to `/docs`.

**Cleanup**

```powershell
Remove-Item Env:\LUNAR_FORGE_PERMISSION_MODE -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force -LiteralPath $FastApiProject
```

FastAPI and Uvicorn remain installed in `.venv-manual`; removing that
disposable environment after the checklist removes them.

### 11. Vite React starter

**Purpose**

Confirm the `vite_react` starter, approval-gated npm installation, and separate
build validation.

**Setup**

Node.js and npm must be available, and npm installation requires network
access.

```powershell
$ViteProject = Join-Path $ManualRoot "new-vite-react"
New-Item -ItemType Directory -Force -Path $ViteProject | Out-Null
$env:LUNAR_FORGE_PERMISSION_MODE = "yes"
```

**Command**

```powershell
lunar-forge new --project $ViteProject "Build a Vite React website"
Push-Location $ViteProject
npm run build
Pop-Location
```

Approve `npm install`, then separately approve `npm run build`.

**Expected result**

The plan names `vite_react`; the target contains `package.json`,
`vite.config.js`, `index.html`, `src/main.jsx`, `src/App.jsx`, `src/App.css`, and
`README.md`. Installation and validation are separate approvals, both builds
pass, and the final instructions include `npm run dev` and `npm run preview`.

**Cleanup**

```powershell
Remove-Item Env:\LUNAR_FORGE_PERMISSION_MODE -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force -LiteralPath $ViteProject
```

## 12. Validation workflow

**Purpose**

Confirm that `run_validation` detects appropriate Python commands, requests
approval once, executes them, and reports structured success.

**Setup**

This test requires a configured model and pytest from the development install.

```powershell
$ValidationProject = Join-Path $ManualRoot "validation-project"
New-Item -ItemType Directory -Force -Path (Join-Path $ValidationProject ".agent") | Out-Null
@'
permissions:
  mode: yes
'@ | Set-Content -LiteralPath (Join-Path $ValidationProject ".agent\config.yaml") -Encoding utf8
@'
def add(left, right):
    return left + right
'@ | Set-Content -LiteralPath (Join-Path $ValidationProject "app.py") -Encoding utf8
@'
from app import add

def test_add():
    assert add(2, 3) == 5
'@ | Set-Content -LiteralPath (Join-Path $ValidationProject "test_app.py") -Encoding utf8
```

**Command**

```powershell
lunar-forge --project $ValidationProject "Use run_validation now and report every detected command and result. Do not edit files."
```

Approve the `Run detected validation commands` request.

**Expected result**

The result lists `python -m compileall .` and `pytest`; both exit with code 0;
the overall message says all validation commands passed; and no file edit is
made.

**Cleanup**

```powershell
Remove-Item -Recurse -Force -LiteralPath $ValidationProject
```

## 13. `browser-setup`

**Purpose**

Confirm that the deterministic browser dependency helper displays and
separately approves the optional Python package and Chromium installation.

**Setup**

Run from the LunarForge checkout using the disposable Python environment from
test 1. Network access is needed if the optional dependencies are not cached.

**Command**

```powershell
lunar-forge browser-setup --project $RepoRoot
```

Approve each displayed command after confirming it is exactly:

```text
python -m pip install -e ".[browser]"
python -m playwright install chromium
```

**Expected result**

Both commands are printed before execution, each receives a separate approval,
and the final JSON has `"ok": true` with two successful command results. If the
first command is denied or fails, setup stops without running the second.

**Cleanup**

Keep Playwright installed for the next tests. At the end of the checklist,
remove `.venv-manual`; removing Playwright's shared browser cache is optional.

## 14. Managed browser validation

**Purpose**

Confirm that LunarForge can start an approved local server, wait for its URL,
validate rendering and a CSS selector, capture a project-confined full-page
screenshot, and stop the server.

**Setup**

Complete `browser-setup` first, then install and build the checked-in browser
demo. Installation is explicit and may require network access.

```powershell
$BrowserProject = Join-Path $RepoRoot "examples\projects\browser-demo"
Push-Location $BrowserProject
npm install
npm run build
Pop-Location
```

**Command**

```powershell
lunar-forge browser-validate --serve "npm run dev" --url http://localhost:5173 --project $BrowserProject --check "#main-heading" --check "#below-fold-heading" --full-page --width 1440 --height 900
Get-ChildItem -Recurse -File -LiteralPath (Join-Path $BrowserProject ".agent\artifacts\browser")
lunar-forge browser-validate --serve "npm run dev" --url "http://localhost:5173/?consoleError=1" --project $BrowserProject --no-screenshot
```

Approve the exact `npm run dev` command for each managed validation.

**Expected result**

The first JSON result reports `"ok": true`, title
`LunarForge Browser Demo`, the final loopback URL, zero console errors, zero
failed requests, both selector checks passing, `"full_page": true`, and a
screenshot beneath `.agent\artifacts\browser`. Its `managed_server` object
reports `"ready": true` and intentional termination, and port 5173 is no longer
listening after the command exits. The second result collects
`Browser demo requested an optional console error.` without capturing another
screenshot; the default URL remains error-free.

**Cleanup**

```powershell
$BrowserGenerated = @("node_modules", "dist", ".agent", "package-lock.json")
foreach ($name in $BrowserGenerated) {
    $path = Join-Path $BrowserProject $name
    if (Test-Path -LiteralPath $path) {
        Remove-Item -Recurse -Force -LiteralPath $path
    }
}
```

## 15. Playwright MCP

**Purpose**

Confirm both opt-ins for a Playwright stdio server, bounded tool discovery, and
an approved model-routed Playwright MCP call against a local page.

**Setup**

This test requires Node.js, `npx.cmd`, network access if npm packages are not
cached, and a configured model. It reuses the browser demo and checked-in MCP
configuration examples.

```powershell
$McpProject = Join-Path $RepoRoot "examples\projects\browser-demo"
Push-Location $McpProject
npm install
npm run build
Pop-Location
New-Item -ItemType Directory -Force -Path (Join-Path $McpProject ".agent") | Out-Null
Copy-Item (Join-Path $RepoRoot "examples\mcp\playwright\config.yaml") (Join-Path $McpProject ".agent\config.yaml")
Copy-Item (Join-Path $RepoRoot "examples\mcp\playwright\mcp.yaml") (Join-Path $McpProject ".agent\mcp.yaml")
Get-Command npx.cmd
```

**Command**

```powershell
lunar-forge mcp list --project $McpProject
$McpServer = Start-Process -FilePath "python" -ArgumentList "-m","http.server","8766","--directory",(Join-Path $McpProject "dist") -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2
lunar-forge --project $McpProject "Use only Playwright MCP browser tools to open http://127.0.0.1:8766 and report the page title, main heading, counter button, and form input. Do not use curl or the built-in browser validation tool."
```

Approve each external MCP action after checking its dotted Playwright tool
name.

**Expected result**

`mcp list` reports both global and server enablement and discovers one or more
namespaced `mcp.playwright.*` tools with provider-safe aliases. The agent asks
before external MCP calls, then reports title `LunarForge Browser Demo`, the
main heading, the `Increase count` button, and the named form input. MCP startup
or discovery errors are bounded and do not print server stderr.

**Cleanup**

```powershell
if ($McpServer -and -not $McpServer.HasExited) { Stop-Process -Id $McpServer.Id }
$McpGenerated = @("node_modules", "dist", ".agent", "package-lock.json")
foreach ($name in $McpGenerated) {
    $path = Join-Path $McpProject $name
    if (Test-Path -LiteralPath $path) {
        Remove-Item -Recurse -Force -LiteralPath $path
    }
}
```

The `npx -y` package may remain in the npm cache.

## 16. Plugin diagnostics and echo usage

This test is included because the README already documents the local echo
plugin example.

**Purpose**

Confirm explicit plugin configuration, model-safe tool aliasing, diagnostic
discovery without importing plugin code, and approval-gated echo execution.

**Setup**

The echo invocation requires a configured model.

```powershell
$PluginProject = Join-Path $ManualRoot "plugin-project"
New-Item -ItemType Directory -Force -Path (Join-Path $PluginProject ".agent") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $PluginProject "plugin_packs\example") | Out-Null
@'
permissions:
  mode: yes
plugins:
  enabled: true
'@ | Set-Content -LiteralPath (Join-Path $PluginProject ".agent\config.yaml") -Encoding utf8
@'
plugins:
  example:
    manifest: plugin_packs/example/plugin.yaml
    enabled: true
'@ | Set-Content -LiteralPath (Join-Path $PluginProject ".agent\plugins.yaml") -Encoding utf8
@'
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
'@ | Set-Content -LiteralPath (Join-Path $PluginProject "plugin_packs\example\plugin.yaml") -Encoding utf8
@'
def echo(message):
    return {"ok": True, "echo": message}
'@ | Set-Content -LiteralPath (Join-Path $PluginProject "plugin_packs\example\example_plugin.py") -Encoding utf8
```

**Command**

```powershell
lunar-forge plugins list --project $PluginProject
lunar-forge --project $PluginProject "Call example.echo with the message hello, then report its exact result."
```

Approve the `example.echo` execution request.

**Expected result**

The diagnostic succeeds without importing the handler and reports internal name
`example.echo` plus provider-facing alias `example_echo`. The agent requests
approval using the dotted name, invokes the reviewed local module only after
approval, and reports an echo result containing `hello`.

**Cleanup**

```powershell
Remove-Item -Recurse -Force -LiteralPath $PluginProject
```

## 17. Session resume

**Purpose**

Confirm session listing, model-free redacted summaries, safe history loading,
and continuation into a new session without replaying historical tool calls.

**Setup**

This test requires a configured model.

```powershell
$SessionProject = Join-Path $ManualRoot "session-project"
New-Item -ItemType Directory -Force -Path $SessionProject | Out-Null
"The launch color is silver." | Set-Content -LiteralPath (Join-Path $SessionProject "note.txt") -Encoding utf8
lunar-forge --project $SessionProject "Read note.txt and summarize it. Do not edit files or run commands."
$SessionFile = Get-ChildItem -File -LiteralPath (Join-Path $SessionProject ".agent\sessions") | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$SessionId = $SessionFile.BaseName
```

**Command**

```powershell
lunar-forge sessions --project $SessionProject
lunar-forge resume $SessionId --project $SessionProject --summary-only
$BeforeResume = (Get-ChildItem -File -LiteralPath (Join-Path $SessionProject ".agent\sessions")).Count
lunar-forge resume $SessionId --project $SessionProject --prompt "What launch color did the previous session find? Do not call tools."
$AfterResume = (Get-ChildItem -File -LiteralPath (Join-Path $SessionProject ".agent\sessions")).Count
Write-Host "Session files before resume: $BeforeResume; after resume: $AfterResume"
```

**Expected result**

`sessions` lists the original JSONL filename and size. `--summary-only` prints a
bounded, redacted history without requiring the model. The continued run answers
`silver`, does not replay the old read tool call, creates one new session file,
and records a reference to the resumed session.

**Cleanup**

```powershell
Remove-Item -Recurse -Force -LiteralPath $SessionProject
```

## 18. Checkpoints and rollback

**Purpose**

Confirm that editing an existing file creates a checkpoint, the utility command
lists it, and rollback restores the newest saved state while preserving the
state it replaces.

**Setup**

This test requires a configured model.

```powershell
$RollbackProject = Join-Path $ManualRoot "rollback-project"
New-Item -ItemType Directory -Force -Path (Join-Path $RollbackProject ".agent") | Out-Null
@'
permissions:
  mode: yes
'@ | Set-Content -LiteralPath (Join-Path $RollbackProject ".agent\config.yaml") -Encoding utf8
"original" | Set-Content -LiteralPath (Join-Path $RollbackProject "message.txt") -Encoding utf8
```

**Command**

```powershell
lunar-forge --project $RollbackProject "Use read_file_with_line_numbers, then replace_lines to replace line 1 of message.txt with changed. Do not run commands."
lunar-forge checkpoints --project $RollbackProject
Get-Content -LiteralPath (Join-Path $RollbackProject "message.txt")
lunar-forge rollback message.txt --project $RollbackProject
Get-Content -LiteralPath (Join-Path $RollbackProject "message.txt")
lunar-forge checkpoints --project $RollbackProject
```

**Expected result**

The edit changes the file to `changed` and reports a checkpoint path. The first
listing shows at least one checkpoint. Rollback restores `original`, reports
the source checkpoint, and saves the replaced `changed` state to another
checkpoint before restoration.

**Cleanup**

```powershell
Remove-Item -Recurse -Force -LiteralPath $RollbackProject
```

## 19. Parallel subagents

**Purpose**

Confirm that writer work remains serialized, Tester and Reviewer run in the
fixed post-edit parallel group, result order is deterministic, and session
events include role and phase metadata.

**Setup**

This test requires a configured model and makes several model calls.

```powershell
$ParallelProject = Join-Path $ManualRoot "parallel-project"
New-Item -ItemType Directory -Force -Path (Join-Path $ParallelProject ".agent") | Out-Null
@'
permissions:
  mode: yes
'@ | Set-Content -LiteralPath (Join-Path $ParallelProject ".agent\config.yaml") -Encoding utf8
@'
# Parallel demo

Initial text.
'@ | Set-Content -LiteralPath (Join-Path $ParallelProject "README.md") -Encoding utf8
```

**Command**

```powershell
lunar-forge --project $ParallelProject --parallel-subagents "Add one concise sentence to README.md explaining that this is a manual parallel-subagent test, then validate if practical."
$ParallelSession = Get-ChildItem -File -LiteralPath (Join-Path $ParallelProject ".agent\sessions") | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Select-String -Path $ParallelSession.FullName -Pattern "subagent_started","subagent_completed","parallel_group_id"
```

**Expected result**

The final report lists Planner, Coder, Tester, and Reviewer in declared phase
order and lists `post-edit: tester, reviewer` under parallel groups. Coder is not
in a parallel group. The session log remains valid one-record-per-line JSON and
its subagent lifecycle events include `role`, `phase`, and
`parallel_group_id`.

**Cleanup**

```powershell
Remove-Item -Recurse -Force -LiteralPath $ParallelProject
```

## 20. No-command mode

**Purpose**

Confirm that `permissions.mode: no-command` and `runtime.mode: no-command`
remove command-backed tools while leaving read-only project inspection usable.

**Setup**

The inspection half requires a configured model.

```powershell
$NoCommandProject = Join-Path $ManualRoot "no-command-project"
New-Item -ItemType Directory -Force -Path (Join-Path $NoCommandProject ".agent") | Out-Null
@'
runtime:
  mode: no-command
permissions:
  mode: no-command
'@ | Set-Content -LiteralPath (Join-Path $NoCommandProject ".agent\config.yaml") -Encoding utf8
"readable without commands" | Set-Content -LiteralPath (Join-Path $NoCommandProject "note.txt") -Encoding utf8
```

**Command**

```powershell
lunar-forge browser-setup --project $NoCommandProject
$BrowserSetupExitCode = $LASTEXITCODE
Write-Host "browser-setup exit code: $BrowserSetupExitCode"
lunar-forge git status --project $NoCommandProject
lunar-forge git commit --project $NoCommandProject --message "Blocked commit"
lunar-forge --project $NoCommandProject "Read note.txt and explain whether command execution or validation tools are available. Do not edit files."
Get-Content -LiteralPath (Join-Path $NoCommandProject "note.txt")
$env:NO_COMMAND_PROJECT = $NoCommandProject
@'
import json
import os
from lunar_forge.tools.registry import create_tool_registry

registry = create_tool_registry(
    os.environ["NO_COMMAND_PROJECT"],
    mode="no-command",
    session_changed_files=["note.txt"],
)
for name, arguments in (
    ("git_status", {}),
    ("git_diff", {}),
    ("list_changed_files", {"source": "both"}),
    ("list_changed_files", {"source": "session"}),
    ("project_health", {}),
):
    print(name, json.dumps(registry.execute(name, arguments), sort_keys=True))
'@ | python -
```

**Expected result**

`browser-setup` exits with code 1 and clearly reports that no-command mode
blocks execution; neither install command runs and no approval prompt appears.
Both Git commands also fail before invoking Git or asking approval and report
that no-command mode blocks Git execution.
The agent can still read and quote `note.txt`, reports command-backed validation
as unavailable, and leaves the file unchanged. The direct registry calls show
the same block for `git_status`, `git_diff`, and `source="both"`;
`source="session"` succeeds without Git and returns `note.txt`.
`project_health` succeeds with `tracked_path_check: skipped_no_command`.

**Cleanup**

```powershell
Remove-Item Env:\NO_COMMAND_PROJECT -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force -LiteralPath $NoCommandProject
```

## 21. Guarded Git status and commit

**Purpose**

Confirm deterministic status, mandatory commit approval, current-session file
preference, unrelated dirty-file separation, generated/runtime exclusion,
stable final Git summaries, session logging, and commit-hash reporting.

**Setup**

This test requires Git and a configured model. It creates an isolated repository
with local-only identity settings.

```powershell
$GitProject = Join-Path $ManualRoot "git-project"
New-Item -ItemType Directory -Force -Path (Join-Path $GitProject ".agent") | Out-Null
Push-Location $GitProject
git init
git config user.name "LunarForge Manual Test"
git config user.email "lunar-forge@example.invalid"
"original" | Set-Content -LiteralPath note.txt -Encoding utf8
git add note.txt
git commit -m "Create baseline"
"working change" | Set-Content -LiteralPath note.txt -Encoding utf8
"unrelated" | Set-Content -LiteralPath unrelated.txt -Encoding utf8
$GitExcludedDirectories = @(
    ".agent\artifacts\browser",
    "node_modules\fixture",
    "dist",
    ".next\cache"
)
$GitExcludedDirectories | ForEach-Object {
    New-Item -ItemType Directory -Force -Path $_ | Out-Null
}
"screenshot" | Set-Content -LiteralPath .agent\artifacts\browser\full-page.png -Encoding utf8
"generated dependency" | Set-Content -LiteralPath node_modules\fixture\index.js -Encoding utf8
"build output" | Set-Content -LiteralPath dist\bundle.js -Encoding utf8
"framework cache" | Set-Content -LiteralPath .next\cache\entry.bin -Encoding utf8
"placeholder, not a real secret" | Set-Content -LiteralPath .env -Encoding utf8
@'
permissions:
  mode: yes
'@ | Set-Content -LiteralPath .agent\config.yaml -Encoding utf8
Pop-Location
```

**Command**

```powershell
lunar-forge git status --project $GitProject
$env:GIT_TOOL_PROJECT = $GitProject
@'
import json
import os
from lunar_forge.tools.registry import create_tool_registry

registry = create_tool_registry(
    os.environ["GIT_TOOL_PROJECT"],
    mode="plan",
    session_changed_files=["note.txt"],
)
print(json.dumps(registry.execute("git_status", {}), indent=2))
print(json.dumps(
    registry.execute(
        "git_diff",
        {"path": "note.txt", "staged": False, "max_lines": 40},
    ),
    indent=2,
))
print(json.dumps(
    registry.execute("list_changed_files", {"source": "both"}),
    indent=2,
))
print(json.dumps(
    registry.execute("git_diff", {"path": ".env", "max_lines": 40}),
    indent=2,
))
'@ | python -
lunar-forge --project $GitProject --commit --commit-message "Update note" "Use read_file_with_line_numbers, then replace_lines to replace line 1 of note.txt with updated. Do not run commands."
Push-Location $GitProject
git log -1 --format="%H %s"
git status --short
Pop-Location
lunar-forge git commit --project $GitProject --message "Add unrelated fixture"
```

Approve the file edit, then approve the agent-offered Git commit. For the final
deterministic command, review its proposal and approve only after confirming it
contains `unrelated.txt` and still excludes `.agent/`.

Repeat the agent command with a fresh edit and deny only the Git approval to
verify the files remain uncommitted. Also run `--commit` on a read-only task that
makes no file changes; it should not display a Git approval prompt.

**Expected result**

Initial status shows modified `note.txt`, `unrelated.txt`, and `.agent` runtime
files. The plan-mode registry calls report compact project-scoped status, a
bounded diff containing only `note.txt`, and combined session/Git metadata.
`note.txt` is both session-changed and Git-modified; `.agent`, `node_modules`,
`dist`, `.next`, and `.env` are excluded and never appear as diff contents or
commit candidates. The explicit `.env` diff fails with an excluded-path error
and does not return its placeholder body. Every result serializes as JSON. No
Git mutation or approval occurs for these read-only calls.

The agent commit preview shows bounded status and diff output, labels only
`note.txt` as changed by LunarForge and proposed for commit, lists
`unrelated.txt` as not included, and lists `.agent` browser artifacts, sessions,
and checkpoints under excluded files. It shows `Proposed commit message: Update
note`. Approval is required
despite `permissions.mode: yes`. An approved run ends with:

```text
Git:
- Commit created: <hash>
```

The result and `git log` show the `Update note` commit hash. `unrelated.txt`
remains dirty afterward. A denied run ends with `Commit not created: approval
denied` and leaves the proposed files uncommitted. A run with no LunarForge file
changes ends with `Commit not created: no changes` without asking approval. The
explicit deterministic commit then proposes and commits `unrelated.txt`, still
excludes `.agent`, requires another approval, and returns its hash.

The session JSONL contains `git_status_summary`, `git_commit_proposal`,
`git_commit_approval`, `git_commit_result`, and, for an approved commit,
`git_commit_created` events. A separate run with a failed `run_validation`
result and only the `--commit` flag ends with `Commit not created: validation
failed`. Merely mentioning a commit in the prompt does not override this guard;
the prompt must explicitly say something like `commit even if validation fails`.

**Cleanup**

```powershell
Remove-Item Env:\GIT_TOOL_PROJECT -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force -LiteralPath $GitProject
```

## 22. Checked-in example project smoke tests

**Purpose**

Confirm that every checked-in example has runnable, source-only commands and
that the browser demo and Vite project build without global npm packages,
application secrets, or generated content committed to the repository.

**Setup**

Run from the LunarForge repository root. Python 3.11+, Node.js, and npm are
required. Dependency installation is explicit and may need network access. A
single disposable virtual environment is kept outside the example projects.

```powershell
$ExamplesVenv = Join-Path $ManualRoot "examples-venv"
python -m venv $ExamplesVenv
$ExamplesPython = Join-Path $ExamplesVenv "Scripts\python.exe"
& $ExamplesPython -m pip install -r examples\projects\flask-api\requirements.txt -r examples\projects\fastapi-api\requirements.txt
```

**Command**

```powershell
Test-Path -LiteralPath examples\projects\static-site\index.html
Test-Path -LiteralPath examples\projects\static-site\styles.css
Select-String -SimpleMatch "Small pages are excellent test fixtures." examples\projects\static-site\index.html

Push-Location examples\projects\python-cli
& $ExamplesPython -B app.py --name Ada --excited
& $ExamplesPython -B -m unittest -q
Pop-Location

Push-Location examples\projects\flask-api
& $ExamplesPython -B -m unittest -q
Pop-Location

Push-Location examples\projects\fastapi-api
& $ExamplesPython -B -m unittest -q
Pop-Location

Push-Location examples\projects\vite-react
npm install
npm run build
Pop-Location

Push-Location examples\projects\browser-demo
npm install
npm run build
Pop-Location
```

**Expected result**

Both static-site paths exist and the expected heading is found. The Python CLI
prints `Hello, Ada!`; its two tests pass; and the Flask and FastAPI suites pass.
Both npm installations remain local to their example directories, and both
Vite builds exit with code 0 and write only local `dist` output. No command asks
for an API key, global npm installation, cloud service, or external runtime API.

**Cleanup**

```powershell
Remove-Item -Recurse -Force -LiteralPath $ExamplesVenv -ErrorAction SilentlyContinue
$ExampleGenerated = @(
    "examples\projects\browser-demo\node_modules",
    "examples\projects\browser-demo\dist",
    "examples\projects\browser-demo\package-lock.json",
    "examples\projects\vite-react\node_modules",
    "examples\projects\vite-react\dist",
    "examples\projects\vite-react\package-lock.json",
    "examples\projects\python-cli\__pycache__",
    "examples\projects\flask-api\__pycache__",
    "examples\projects\fastapi-api\__pycache__"
)
$ExampleGenerated | ForEach-Object {
    Remove-Item -Recurse -Force -LiteralPath $_ -ErrorAction SilentlyContinue
}
```

## Repository validation after documentation changes

**Purpose**

Confirm that the repository's automated tests, byte-compilation check, and
whitespace validation still pass after documentation work.

**Setup**

Return to the LunarForge repository root with the development environment
active.

**Command**

```powershell
python -m pytest -q
python -B -m compileall lunar_forge
git diff --check
```

**Expected result**

Pytest reports no failures, compileall reports no syntax errors, and
`git diff --check` produces no whitespace errors.

**Cleanup**

No test artifacts need to be retained. Remove `$ManualRoot` only after checking
that it still points to the disposable directory created for this guide:

```powershell
Write-Host $ManualRoot
Remove-Item -Recurse -Force -LiteralPath $ManualRoot
```

## Docker testing deferred

Docker manual testing is intentionally out of scope for this phase. Add a
dedicated Docker checklist in a later hardening pass; do not treat the absence
of Docker coverage here as a failure of the checks above.
