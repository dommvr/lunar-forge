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

The local/Docker safety section checks both enforcement and approval wording.
Local and Docker routing, path boundaries, `shell=False`, dangerous-command
denial, Docker `/workspace` behavior, first-session warnings, repeated ordinary
approvals, dependency and risky-command warnings, and Docker-specific prompts
are all current pass/fail checks.

## Progress

- [ ] Install and CLI availability
- [ ] Config loading and precedence
- [ ] Configurable model reasoning effort
- [ ] Plan mode
- [ ] Local and Docker execution safety
- [ ] Basic project inspection
- [ ] Built-in project intelligence
- [ ] Safe structured file readers
- [ ] Lightweight symbol listing
- [ ] Read-only CI configuration summary
- [ ] Efficient context-tool selection and role boundaries
- [ ] Task-profile tool-schema filtering
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
- [ ] Plugin diagnostics, echo usage, and web design review
- [ ] Session resume
- [ ] Checkpoints and rollback
- [ ] Parallel subagents
- [ ] No-command mode
- [ ] Guarded Git status and commit
- [x] Event-driven one-shot CLI parity
- [x] Approval provider behavior
- [x] Textual missing-dependency message
- [x] Textual multi-turn chat smoke test
- [x] Textual session resume
- [x] Working-memory compaction
- [x] Event/web-cloud transport hardening
- [x] Textual simplified layout, temporary progress, and multiline input
- [x] Textual slash commands, popup forms, and config persistence scopes
- [x] Repository validation after documentation changes

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

### 2a. Configurable model reasoning effort

**Purpose**

Confirm that `model.reasoning.effort` changes the thinking budget without
changing the configured model identity, that project configuration overrides
the environment, that the CLI overrides project configuration, and that
invalid values fail before a model call.

**Setup**

This test requires a configured OpenAI API key for the successful agent call.

```powershell
$ReasoningProject = Join-Path $ManualRoot "reasoning-project"
New-Item -ItemType Directory -Force -Path (Join-Path $ReasoningProject ".agent") | Out-Null
@'
model:
  provider: litellm
  api: responses
  model: openai/gpt-5.6-sol
  reasoning:
    effort: xhigh
'@ | Set-Content -LiteralPath (Join-Path $ReasoningProject ".agent\config.yaml") -Encoding utf8
$env:LUNAR_FORGE_REASONING_EFFORT = "low"
```

**Command**

```powershell
python -c "from pathlib import Path; from lunar_forge.config import load_config; c=load_config(Path(r'$ReasoningProject')); print(c.model.model, c.model.reasoning.effort)"
lunar-forge --reasoning-effort high --show-usage --project $ReasoningProject "Explain this project"
lunar-forge --reasoning-effort invalid --project $ReasoningProject "This must fail before contacting the model"
```

**Expected result**

The Python command prints `openai/gpt-5.6-sol xhigh`, proving the project value
overrides the environment without rewriting the model. The successful CLI call
keeps that same model and includes:

```text
Model usage:
- Reasoning effort: high
```

The final CLI command exits nonzero and reports that
`model.reasoning.effort` must be one of `low`, `medium`, `high`, `xhigh`, or
`max`. It does not contact a model.

For an OpenAI Responses call, debug with a fake client or session log if needed:
the outgoing payload contains `reasoning: {"effort": "high"}` and retains any
function tools. No reasoning summary is requested.

**Cleanup**

```powershell
Remove-Item Env:\LUNAR_FORGE_REASONING_EFFORT -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force -LiteralPath $ReasoningProject
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
lunar-forge --show-usage --project $PlanProject --plan --commit "Inspect note.txt and plan how to replace alpha with beta. List the file and validation steps, but do not edit anything."
Get-Content -LiteralPath (Join-Path $PlanProject "note.txt")
Test-Path -LiteralPath (Join-Path $PlanProject ".agent")
```

**Expected result**

LunarForge describes the goal, likely changed file, and validation approach.
`note.txt` still contains `alpha`, the final `Test-Path` prints `False`, and no
write or command approval prompt appears. The Git section says plan mode blocks
the commit, and no Git approval prompt appears. The final output includes calls,
input tokens, output tokens, total tokens, and exact/estimated counts; it still
reports `Session log: disabled in plan mode`.

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

## Safe structured file readers

**Purpose**

Confirm that plan mode exposes `read_json`, `read_yaml`, and
`read_many_files`; valid data is structured and bounded, malformed files report
locations, partial batch failures remain isolated, and secret/runtime/generated
or out-of-project files are never read.

**Setup**

```powershell
$StructuredProject = Join-Path $ManualRoot "structured-project"
New-Item -ItemType Directory -Force -Path $StructuredProject | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $StructuredProject "dist") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $StructuredProject "secrets") | Out-Null
@'
{"name": "manual-demo", "scripts": {"test": "pytest"}}
'@ | Set-Content -LiteralPath (Join-Path $StructuredProject "package.json") -Encoding utf8
@'
name: manual-demo
checks:
  - test
  - lint
'@ | Set-Content -LiteralPath (Join-Path $StructuredProject "config.yaml") -Encoding utf8
'{"name": ]' | Set-Content -LiteralPath (Join-Path $StructuredProject "broken.json") -Encoding utf8
"checks: [test," | Set-Content -LiteralPath (Join-Path $StructuredProject "broken.yaml") -Encoding utf8
"first`nsecond" | Set-Content -LiteralPath (Join-Path $StructuredProject "notes.txt") -Encoding utf8
"generated-canary" | Set-Content -LiteralPath (Join-Path $StructuredProject "dist\generated.txt") -Encoding utf8
"TOKEN=secret-canary" | Set-Content -LiteralPath (Join-Path $StructuredProject ".env") -Encoding utf8
"directory-secret-canary" | Set-Content -LiteralPath (Join-Path $StructuredProject "secrets\private.txt") -Encoding utf8
[IO.File]::WriteAllBytes(
    (Join-Path $StructuredProject "binary.bin"),
    [byte[]](0, 1, 2, 3)
)
'{"outside": "outside-canary"}' | Set-Content -LiteralPath (Join-Path $ManualRoot "structured-outside.json") -Encoding utf8
```

**Command**

```powershell
$env:STRUCTURED_PROJECT = $StructuredProject
@'
import json
import os
from lunar_forge.tools.registry import create_tool_registry

registry = create_tool_registry(os.environ["STRUCTURED_PROJECT"], mode="plan")
tools = {"read_json", "read_yaml", "read_many_files"}
print("available:", sorted(tools & set(registry.names())))
for name, arguments in (
    ("read_json", {"path": "package.json"}),
    ("read_yaml", {"path": "config.yaml"}),
    ("read_json", {"path": "broken.json"}),
    ("read_yaml", {"path": "broken.yaml"}),
    ("read_json", {"path": "package.json", "max_bytes": 16}),
    ("read_json", {"path": "../structured-outside.json"}),
    (
        "read_many_files",
        {
            "paths": [
                "notes.txt",
                "binary.bin",
                "missing.txt",
                ".env",
                "dist/generated.txt",
                "secrets/private.txt",
                ".",
                "**/*",
            ],
            "max_bytes_per_file": 32,
            "max_total_bytes": 64,
        },
    ),
):
    print(name, json.dumps(registry.execute(name, arguments), indent=2))
'@ | python -
```

**Expected result**

`available` lists all three exact provider-safe names. Valid JSON and YAML
return `ok: true`, structured `data`, top-level keys, and `truncated: false`.
Malformed results return `ok: false` with line and column and no content
preview. The low-byte JSON request returns a bounded preview rather than partial
parsed data. Traversal is rejected. The batch returns `notes.txt` successfully
and independent errors for the binary, missing, `.env`, `dist`, `secrets`,
directory, and literal glob paths while the top-level result remains `ok: true`;
globs are never expanded. None of `secret-canary`, `directory-secret-canary`,
`generated-canary`, or `outside-canary` appears. No approval prompt, file
mutation, project import, or subprocess occurs.

**Cleanup**

```powershell
Remove-Item Env:\STRUCTURED_PROJECT -ErrorAction SilentlyContinue
Remove-Item -Force -LiteralPath (Join-Path $ManualRoot "structured-outside.json")
Remove-Item -Recurse -Force -LiteralPath $StructuredProject
```

## Lightweight symbol listing

**Purpose**

Confirm that `list_symbols` is available in plan mode, uses Python AST parsing
without executing code, conservatively locates JS/TS exports and React
components, reports syntax and unsupported-file errors, caps results, and
inherits traversal and secret/runtime/generated path blocking.

**Setup**

```powershell
$SymbolProject = Join-Path $ManualRoot "symbol-project"
New-Item -ItemType Directory -Force -Path (Join-Path $SymbolProject "src") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $SymbolProject "dist") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $SymbolProject "secrets") | Out-Null
@'
from pathlib import Path

Path("executed.txt").write_text("must-not-run")

async def load_data():
    return None

class Service:
    def run(self):
        return True
'@ | Set-Content -LiteralPath (Join-Path $SymbolProject "src\service.py") -Encoding utf8
@'
export async function loadData() {}
export default function App() {}
export class Service {}
export const API_URL = "/api";
const Card = () => null;
class Legacy extends React.Component {}
'@ | Set-Content -LiteralPath (Join-Path $SymbolProject "src\App.tsx") -Encoding utf8
"def broken(:" | Set-Content -LiteralPath (Join-Path $SymbolProject "src\broken.py") -Encoding utf8
"plain text" | Set-Content -LiteralPath (Join-Path $SymbolProject "notes.txt") -Encoding utf8
"SECRET=must-not-leak" | Set-Content -LiteralPath (Join-Path $SymbolProject ".env.py") -Encoding utf8
"function Generated() {}" | Set-Content -LiteralPath (Join-Path $SymbolProject "dist\generated.js") -Encoding utf8
"def hidden(): pass" | Set-Content -LiteralPath (Join-Path $SymbolProject "secrets\hidden.py") -Encoding utf8
"CANARY = 'outside-canary'" | Set-Content -LiteralPath (Join-Path $ManualRoot "symbol-outside.py") -Encoding utf8
1..205 | ForEach-Object { "def symbol_$($_)():`n    pass" } |
    Set-Content -LiteralPath (Join-Path $SymbolProject "src\many.py") -Encoding utf8
```

**Command**

```powershell
$env:SYMBOL_PROJECT = $SymbolProject
@'
import json
import os
from lunar_forge.tools.registry import create_tool_registry

registry = create_tool_registry(os.environ["SYMBOL_PROJECT"], mode="plan")
print("available:", "list_symbols" in registry.names())
for path in (
    "src/service.py",
    "src/App.tsx",
    "src/broken.py",
    "src/many.py",
    "notes.txt",
    ".env.py",
    "dist/generated.js",
    "secrets/hidden.py",
    "../symbol-outside.py",
):
    result = registry.execute("list_symbols", {"path": path})
    print(path, json.dumps(result, indent=2))
'@ | python -
Test-Path -LiteralPath (Join-Path $SymbolProject "executed.txt")
```

**Expected result**

`available` is `True`. Python output includes `load_data`, `Service`, and its
`run` method with line/container metadata. TSX output includes the functions,
class, exported constant, and React-component hints. The broken Python and text
files return clear syntax and unsupported-type errors. `src/many.py` returns at
most 200 symbols with `truncated: true`. The `.env.py`, `dist`, `secrets`, and
traversal requests are blocked without returning either canary. `Test-Path` is
`False`, proving the Python module was parsed but never executed. No approval or
command tool is requested.

**Cleanup**

```powershell
Remove-Item Env:\SYMBOL_PROJECT -ErrorAction SilentlyContinue
Remove-Item -Force -LiteralPath (Join-Path $ManualRoot "symbol-outside.py")
Remove-Item -Recurse -Force -LiteralPath $SymbolProject
```

## Read-only CI configuration summary

**Purpose**

Confirm that `ci_summary` detects supported providers without running CI,
extracts bounded jobs/runtime/package-manager/command hints, suggests clear
validation commands, redacts environment and secret values, reports malformed
YAML safely, and remains available without command tools in plan mode.

**Setup**

```powershell
$CiProject = Join-Path $ManualRoot "ci-project"
$NoCiProject = Join-Path $ManualRoot "no-ci-project"
New-Item -ItemType Directory -Force -Path (Join-Path $CiProject ".github\workflows") | Out-Null
New-Item -ItemType Directory -Force -Path $NoCiProject | Out-Null
@'
name: Quality
env:
  PRIVATE_TOKEN: &private_token env-canary-must-not-leak
jobs:
  test:
    name: *private_token
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
      - run: |
          python -m pip install -e .
          python -m pytest -q
          npm test
          API_TOKEN=command-canary-must-not-leak npm run build
      - run: echo "$(RELEASE_CREDENTIAL)"
      - run: python -c "from pathlib import Path; Path('executed.txt').write_text('ran')"
'@ | Set-Content -LiteralPath (Join-Path $CiProject ".github\workflows\quality.yml") -Encoding utf8
@'
variables:
  ACCESS_TOKEN: azure-canary-must-not-leak
jobs:
  - job: test
    pool:
      vmImage: ubuntu-latest
    steps:
      - task: UsePythonVersion@0
        inputs:
          versionSpec: "3.12"
      - script: python -m pytest
'@ | Set-Content -LiteralPath (Join-Path $CiProject "azure-pipelines.yml") -Encoding utf8
```

**Command**

```powershell
$env:CI_PROJECT = $CiProject
$env:NO_CI_PROJECT = $NoCiProject
@'
import json
import os
from pathlib import Path
from lunar_forge.tools.registry import create_tool_registry

empty_registry = create_tool_registry(os.environ["NO_CI_PROJECT"], mode="plan")
print("empty:", json.dumps(empty_registry.execute("ci_summary", {}), indent=2))

registry = create_tool_registry(os.environ["CI_PROJECT"], mode="plan")
print("available:", "ci_summary" in registry.names())
print("command-tools:", sorted(
    name for name in registry.names()
    if name in {"run_command", "run_validation"}
))
print("valid:", json.dumps(registry.execute("ci_summary", {}), indent=2))
print("executed:", (Path(os.environ["CI_PROJECT"]) / "executed.txt").exists())

circle = Path(os.environ["CI_PROJECT"]) / ".circleci"
circle.mkdir()
(circle / "config.yml").write_text("jobs:\n  build: [\n", encoding="utf-8")
print("malformed:", json.dumps(registry.execute("ci_summary", {}), indent=2))
'@ | python -
```

**Expected result**

The empty result has `ok: true`, `ci_files: []`, and a no-CI message.
`available` is `True`, while `command-tools` is empty. The valid summary detects
GitHub Actions and Azure Pipelines, reports their jobs, Python/Node/runner
hints, pip/npm hints, discovered commands, and safe validation suggestions.
None of the three canary values appears; the command assignment, YAML-aliased
environment value, and credential variable reference are redacted. `executed`
is `False`, proving discovered commands were reported but never run. After
adding malformed CircleCI YAML, the result has `ok: false`, retains valid
provider metadata, and includes a bounded CircleCI parse error with line and
column. No CI command, project command, approval prompt, or session file is
created.

**Cleanup**

```powershell
Remove-Item Env:\CI_PROJECT -ErrorAction SilentlyContinue
Remove-Item Env:\NO_CI_PROJECT -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force -LiteralPath $CiProject
Remove-Item -Recurse -Force -LiteralPath $NoCiProject
```

## Explicit read-only fast path

**Purpose**

Confirm that an unambiguous named read-only tool request executes one registry
handler, uses one compact summary call with no tool schemas, skips configured
subagents, and does not infer browser intent from a `browser-demo` pathname.

**Setup and command**

```powershell
$FastPathProject = Join-Path $ManualRoot "readonly-fast-path"
New-Item -ItemType Directory -Force -Path (Join-Path $FastPathProject "examples/projects/browser-demo") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $FastPathProject ".github/workflows") | Out-Null
@'
{"name": "fast-path-demo", "scripts": {"test": "pytest -q"}}
'@ | Set-Content -LiteralPath (Join-Path $FastPathProject "examples/projects/browser-demo/package.json") -Encoding utf8
"export default function App() { return null; }" | Set-Content -LiteralPath (Join-Path $FastPathProject "examples/projects/browser-demo/App.jsx") -Encoding utf8
@'
{"name": "fast-path-root", "scripts": {"test": "python -m pytest -q"}}
'@ | Set-Content -LiteralPath (Join-Path $FastPathProject "package.json") -Encoding utf8
@'
export default function App() {
  return <main>Fast path fixture</main>;
}
'@ | Set-Content -LiteralPath (Join-Path $FastPathProject "App.jsx") -Encoding utf8
@'
name: checks
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: python -m pytest -q
'@ | Set-Content -LiteralPath (Join-Path $FastPathProject ".github/workflows/checks.yml") -Encoding utf8
Push-Location $FastPathProject
git init
Pop-Location

$env:FAST_PATH_PROJECT = $FastPathProject
@'
import json
import os
from pathlib import Path

from lunar_forge.agent import CodeAgent
from lunar_forge.config import AppConfig, SubagentConfig
from lunar_forge.model_clients import ModelResponse

class FakeModel:
    def __init__(self):
        self.calls = []

    def complete(self, messages, tools=None):
        self.calls.append((messages, list(tools or [])))
        return ModelResponse(text="The test script is `pytest -q`.")

root = Path(os.environ["FAST_PATH_PROJECT"])
model = FakeModel()
output = CodeAgent(
    AppConfig(subagents=SubagentConfig(enabled=True)),
    model_client=model,
).run(
    "Run read_json on examples/projects/browser-demo/package.json and summarize scripts.",
    root,
)
session = max((root / ".agent/sessions").glob("*.jsonl"))
events = [
    json.loads(line)
    for line in session.read_text(encoding="utf-8").splitlines()
]
print(output)
print("model calls:", len(model.calls))
print("summary schemas:", len(model.calls[0][1]))
print("direct tools:", [
    event["data"]["name"]
    for event in events
    if event["event"] == "tool_call"
])
print("subagent events:", [
    event["event"]
    for event in events
    if event["event"].startswith("subagent_")
])
print("browser events:", [
    event["event"]
    for event in events
    if event["event"] == "browser_intent_detected"
])
print("usage:", next(
    event["data"]
    for event in events
    if event["event"] == "model_usage"
))
'@ | python -
```

**Expected result**

The result summarizes `pytest -q`. `model calls` is `1`, `summary schemas` is
`0`, and `direct tools` is `['read_json']`. Both the subagent and browser event
lists are empty even though subagents were enabled and the path contains
`browser-demo`. The usage event has `phase: readonly_fast_path`,
`task_profile: explicit_readonly`, `messages_count: 2`, and
`tool_schema_count: 0`. No validation command, checkpoint, file mutation,
browser server, or commit runs.

Requests that add `then run validation`, `open it in the browser`, a second
tool name, or an edit instruction should instead use the normal workflow.

To inspect deterministic role selection without contacting a model:

```powershell
@'
from lunar_forge.subagents import WorkflowKind, build_task_phase_plan
from lunar_forge.tools.registry import TaskProfile

cases = (
    ("explain", "Explain the package layout.", TaskProfile.REVIEW_ONLY),
    (
        "review",
        "Review uncommitted changes for diff quality.",
        TaskProfile.REVIEW_ONLY,
    ),
    (
        "validation",
        "Run validation without changing files.",
        TaskProfile.EDIT_TASK,
    ),
    ("edit", "Update the parser.", TaskProfile.EDIT_TASK),
)
for name, request, profile in cases:
    plan = build_task_phase_plan(
        WorkflowKind.EXISTING_PROJECT,
        request=request,
        task_profile=profile,
    )
    print(name, plan.role_names)
'@ | python -
```

Expected roles are `('planner',)`, `('reviewer',)`, `('tester',)`, and
`('planner', 'coder', 'tester', 'reviewer')`, respectively.

With a configured model and a Git working tree, these commands exercise the
user-facing summaries:

```powershell
lunar-forge --subagents --project $FastPathProject "Run list_symbols on examples/projects/browser-demo/App.jsx."
lunar-forge --subagents --project $FastPathProject "Review uncommitted changes for diff quality."
```

The first command omits `Subagents run` because the deterministic fast path ran
no role. The second lists only `reviewer`; it must not list Planner, Coder,
Tester, or Security unless the request is expanded to require them.

### Read-only token and call-count benchmark

This deterministic benchmark covers `read_json`, `list_symbols`, `ci_summary`,
and `git_status`. It uses a fake summary model, so it needs no API key and does
not report provider billing. The `before` value reconstructs the input shape of
a model-mediated tool loop: one call to request the tool and another to
summarize its result, both carrying the profile schemas. The `after` value is
read from the real fast-path `model_usage` event. Both token values use
LunarForge's clearly labeled character-count estimate.

```powershell
@'
import json
import math
import os
from pathlib import Path

from lunar_forge.agent import CodeAgent
from lunar_forge.config import AppConfig
from lunar_forge.instructions import load_project_instructions
from lunar_forge.model_clients import ModelResponse
from lunar_forge.project_detection import detect_project
from lunar_forge.prompts import build_system_prompt, build_user_prompt
from lunar_forge.tools.registry import (
    TaskProfile,
    create_tool_registry,
    parse_explicit_readonly_tool_request,
)

CASES = (
    ("read_json", "Run read_json on package.json and summarize scripts."),
    ("list_symbols", "Run list_symbols on App.jsx and summarize components."),
    ("ci_summary", "Run ci_summary and report CI commands."),
    ("git_status", "Run git_status and tell me whether the repo is clean."),
)

class FakeModel:
    def __init__(self):
        self.calls = []

    def complete(self, messages, tools=None):
        self.calls.append((list(messages), list(tools or [])))
        return ModelResponse(text="Concise read-only summary.")

def serialized_characters(value):
    return len(json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        default=str,
    ))

def estimated_input_tokens(messages, schemas):
    return (
        math.ceil(serialized_characters(messages) / 4)
        + math.ceil(serialized_characters(schemas) / 4)
    )

root = Path(os.environ["FAST_PATH_PROJECT"])
for label, prompt in CASES:
    sessions_dir = root / ".agent/sessions"
    before_sessions = (
        set(sessions_dir.glob("*.jsonl"))
        if sessions_dir.exists()
        else set()
    )
    model = FakeModel()
    registry = create_tool_registry(root, mode="default")
    CodeAgent(AppConfig(), model_client=model).run(
        prompt,
        root,
        registry=registry,
    )
    session = (set(sessions_dir.glob("*.jsonl")) - before_sessions).pop()
    events = [
        json.loads(line)
        for line in session.read_text(encoding="utf-8").splitlines()
    ]
    usage = next(
        event["data"]
        for event in events
        if event["event"] == "model_usage"
    )
    tool_result = next(
        event["data"]["result"]
        for event in events
        if event["event"] == "tool_result"
    )
    parsed = parse_explicit_readonly_tool_request(prompt)
    assert parsed is not None
    schemas = registry.schemas(
        profile=TaskProfile.EXPLICIT_READONLY,
        requested_tools=(parsed.tool_name,),
    )
    model_tool_name = registry.model_name_for(parsed.tool_name)
    base_messages = [
        {
            "role": "system",
            "content": build_system_prompt(
                detect_project(root),
                load_project_instructions(root),
                "default",
                task_profile=TaskProfile.EXPLICIT_READONLY.value,
            ),
        },
        {"role": "user", "content": build_user_prompt(prompt)},
    ]
    assistant_tool_call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "legacy-call",
            "type": "function",
            "function": {
                "name": model_tool_name,
                "arguments": json.dumps(
                    dict(parsed.arguments),
                    sort_keys=True,
                ),
            },
        }],
    }
    tool_message = {
        "role": "tool",
        "tool_call_id": "legacy-call",
        "name": model_tool_name,
        "content": json.dumps(tool_result, sort_keys=True),
    }
    before_tokens = (
        estimated_input_tokens(base_messages, schemas)
        + estimated_input_tokens(
            [*base_messages, assistant_tool_call, tool_message],
            schemas,
        )
    )
    after_tokens = usage["input_tokens"]
    assert len(model.calls) == 1
    assert model.calls[0][1] == []
    assert before_tokens > after_tokens
    print(
        f"{label}: calls 2 -> {len(model.calls)}; "
        f"schema exposures {len(schemas) * 2} -> "
        f"{usage['tool_schema_count']}; "
        f"estimated input tokens {before_tokens} -> {after_tokens}; "
        f"tool-result estimate "
        f"{usage['context_components']['tool_results_token_estimate']}"
    )
'@ | python -
```

Every line should show calls `2 -> 1`, schema exposures ending in `0`, and a
smaller estimated input-token count after routing. `list_symbols` can have a
larger reconstructed schema count because its normal profile also exposes the
minimal numbered-reader support tool. The fast-path usage event must label the
call `estimated: true`, report `messages_count: 2`, and include a non-zero
`tool_results_token_estimate`. No subagent, validation, browser, checkpoint,
write, or commit event should appear.

**Cleanup**

```powershell
Remove-Item Env:\FAST_PATH_PROJECT -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force -LiteralPath $FastPathProject
```

## Efficient context-tool selection and role boundaries

**Purpose**

Confirm that prompts select the narrowest useful context tool, each subagent
receives its intended restricted view, and plan mode exposes the read-only
context suite without mutation or command tools.

**Command**

```powershell
@'
from lunar_forge.prompts import build_system_prompt
from lunar_forge.subagents import (
    CODER_ROLE,
    PLANNER_ROLE,
    REVIEWER_ROLE,
    SECURITY_ROLE,
    TESTER_ROLE,
)
from lunar_forge.tools.registry import create_tool_registry

project_info = {
    "languages": ["python"],
    "frameworks": [],
    "package_manager": None,
    "routing": None,
    "test_command": "pytest",
    "build_command": None,
    "is_empty": False,
}
prompt = build_system_prompt(project_info, "No extra instructions.", "plan")
guidance = (
    "Use read_json for a known JSON",
    "Use read_yaml for a known YAML",
    "Use read_many_files only for a small",
    "prefer list_symbols before reading broad file ranges",
    "ci_summary before",
    "Do not call every introspection tool",
)
print("guidance:", {text: text in prompt for text in guidance})

context = {
    "project_health", "dependency_summary", "git_status", "git_diff",
    "list_changed_files", "read_json", "read_yaml", "read_many_files",
    "list_symbols", "ci_summary",
}
for role in (PLANNER_ROLE, REVIEWER_ROLE, SECURITY_ROLE):
    print(role.name, "context:", sorted(context & role.allowed_tools))
    print(role.name, "forbidden:", sorted(
        {"write_file", "edit_file", "run_command", "run_validation", "git_commit"}
        & role.allowed_tools
    ))
print("tester helpers:", sorted(
    {
        "dependency_summary", "ci_summary", "git_status",
        "list_changed_files", "read_json", "read_yaml", "read_many_files",
    } & TESTER_ROLE.allowed_tools
))
print("coder read helpers:", sorted(
    {"read_json", "read_yaml", "read_many_files", "list_symbols"}
    & CODER_ROLE.allowed_tools
))
print("coder forbidden:", sorted(
    {"run_command", "run_validation", "git_commit"} & CODER_ROLE.allowed_tools
))

registry = create_tool_registry(".", mode="plan")
print("plan context:", sorted(context & set(registry.names())))
second_batch = {
    "read_json", "read_yaml", "read_many_files", "list_symbols", "ci_summary",
}
print("second-batch schemas:", [
    (
        name,
        registry.model_name_for(name),
        registry.get(name).permission.value,
        registry.get(name).parameters.get("additionalProperties"),
    )
    for name in sorted(second_batch)
])
print("plan forbidden:", sorted(
    {
        "write_file", "edit_file", "replace_lines", "insert_lines",
        "run_command", "run_validation", "git_commit",
    } & set(registry.names())
))
'@ | python -
```

**Expected result**

Every guidance check is `True`. Planner, Reviewer, and Security list all ten
context tools and no forbidden tools. Tester lists the seven validation-context
helpers; Coder lists the four read helpers and no command, validation, or commit
tool. `plan context` lists all ten read-only tools. Every second-batch schema
tuple repeats the exact tool name, reports `read`, and ends in `False`;
`plan forbidden` is empty. The check does not contact a model, execute Git or
project commands, write project files, or request approval.

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

The result lists `python -B -m compileall .` and `pytest`; both exit with code 0;
the overall message says all validation commands passed; and no file edit is
made.

Delete the test file and repeat the same command:

```powershell
Remove-Item -LiteralPath (Join-Path $ValidationProject "test_app.py")
lunar-forge --project $ValidationProject "Use run_validation now and report every detected command and result. Do not edit files."
```

The tiny Python project now selects only `python -B -m compileall .`; it must
not attempt `pytest` when no tests or pytest configuration exist.

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

### Web design review example

This test uses the real checked-in plugin against
`examples/projects/browser-demo`. The plugin is advisory and read-only. It
declares `filesystem: read`, `commands: false`, and `network: false`; it uses no
LLM internally and does not require npm, Playwright, browser dependencies,
network access, or an API key. The final LunarForge agent command still needs
the normally configured model because the agent selects and calls the tool.

Open Command Prompt (`cmd.exe`). These commands are intentionally CMD-friendly.
The generated project `.agent/config.yaml` contains the required global plugin
switch, `plugins.enabled: true`. The same setting may instead be placed in
`%USERPROFILE%\.lunar-forge\config.yaml`. This manual test assumes
browser-demo's ignored `.agent` directory is disposable; merge the YAML instead
of overwriting it when it contains runtime state you want to keep.

```cmd
cd /d C:\Users\tiron\Desktop\lunar-forge
mkdir examples\projects\browser-demo\.agent 2>nul
copy examples\plugins\web-design-review\plugins.yaml.example examples\projects\browser-demo\.agent\plugins.yaml
(echo plugins: & echo   enabled: true)>examples\projects\browser-demo\.agent\config.yaml
lunar-forge plugins list --project examples\projects\browser-demo
lunar-forge --project examples\projects\browser-demo "Use web_design.review_files to review index.html, src\App.jsx, and src\App.css for accessibility, responsive layout, and visual hierarchy. Do not edit files."
```

Approve only the `web_design.review_files` execution request. The diagnostic
should report internal tool name `web_design.review_files`, provider alias
`web_design_review_files`, and permissions `filesystem=read`,
`commands=false`, and `network=false`. The tool result should:

- return `ok: true`;
- list `index.html` and `src/App.jsx` as reviewed;
- list `src/App.css` in `files_skipped` because browser-demo's stylesheet is
  named `src/styles.css`;
- return integer 0-10 scores for accessibility, visual hierarchy, responsive
  behavior, and content clarity;
- report at most 50 concise findings; and
- avoid command output, network activity, browser startup, and file changes.

Do not require exact scores or a fixed finding count: the deterministic
heuristics may evolve. A follow-up invocation may substitute
`src\styles.css` to review the real stylesheet. A path such as
`..\..\..\README.md` must still be rejected as outside the browser-demo
project root.

The request names a static source-review plugin. It must not start a dev server,
expose browser validation tools, request Playwright, or create browser
artifacts. Plugin execution still requires the normal
`web_design.review_files` approval. This plugin call does not use the built-in
read-only fast path; separate structured requests such as
`Run read_json on package.json` continue to use that fast path normally.
Findings are advisory source heuristics, not browser-rendered evidence; use the
separate browser-validation or Playwright MCP checks when runtime, screenshot,
computed-style, or pixel evidence is required. The result must stay bounded and
must not copy reviewed source contents into findings.

Run one explicit traversal check from the repository root:

```cmd
lunar-forge --project examples\projects\browser-demo "Use web_design.review_files to review ..\..\outside.txt. Do not edit files."
```

Approve only `web_design.review_files`. The result should return `ok: false`,
review no files, report that the path is outside the project root, and include
no outside-file content.

The plugin never applies a finding. If a suggestion is worth adopting, request
the edit separately so built-in LunarForge editing tools keep their normal
approval and checkpoint protections.

Clean up from Command Prompt:

```cmd
cd /d C:\Users\tiron\Desktop\lunar-forge
rmdir /S /Q examples\projects\browser-demo\.agent
```

## 17. Session resume

**Purpose**

Confirm session listing, model-free redacted summaries, token telemetry, safe
history loading, and continuation into a new session without replaying
historical tool calls.

**Setup**

This test requires a configured model.

```powershell
$SessionProject = Join-Path $ManualRoot "session-project"
New-Item -ItemType Directory -Force -Path $SessionProject | Out-Null
"The launch color is silver." | Set-Content -LiteralPath (Join-Path $SessionProject "note.txt") -Encoding utf8
lunar-forge --show-usage --project $SessionProject "Run read_file on note.txt and summarize it. Do not edit files or run commands."
$SessionFile = Get-ChildItem -File -LiteralPath (Join-Path $SessionProject ".agent\sessions") | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$SessionId = $SessionFile.BaseName
```

**Command**

```powershell
lunar-forge sessions --project $SessionProject
lunar-forge resume $SessionId --project $SessionProject --summary-only --show-usage
$UsageEvents = Get-Content -LiteralPath $SessionFile.FullName | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object event -eq "model_usage"
$UsageEvents.data | Select-Object phase, role, model, provider, input_tokens, output_tokens, total_tokens, exact, estimated, messages_count, tool_schema_count
$ProfileEvents = Get-Content -LiteralPath $SessionFile.FullName | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object event -eq "tool_schema_selection"
$ProfileEvents.data | Select-Object task_profile, exposed_tool_count, exposed_tool_names, exposed_tool_names_truncated
$BeforeResume = (Get-ChildItem -File -LiteralPath (Join-Path $SessionProject ".agent\sessions")).Count
lunar-forge resume $SessionId --project $SessionProject --prompt "What launch color did the previous session find? Do not call tools."
$AfterResume = (Get-ChildItem -File -LiteralPath (Join-Path $SessionProject ".agent\sessions")).Count
Write-Host "Session files before resume: $BeforeResume; after resume: $AfterResume"
```

**Expected result**

`sessions` lists the original JSONL filename and size. `--summary-only` prints a
bounded, redacted history without requiring the model. Usage output reports at
least one call and labels each event as exact/provider-reported or estimated.
Each event includes message and tool-schema counts plus numeric context component
estimates, without raw prompts, instructions, tool schemas, or environment
values. The tool-schema event reports `explicit_readonly`, an exposed count of
`1`, and only the provider-safe `read_file` name. The continued run answers
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
result and only the `--commit` flag must show the failed validation in the final
answer, must not display the normal Git approval prompt, and must end with
`Commit not created: validation failed`. A passing validation status must appear
above `Git status --short` in the approval preview, before `Allow? [y/N]`.
Merely mentioning a commit in the prompt does not override a failure; the prompt
must explicitly say something like `commit even if validation fails`. That
override approval repeats the failed status before the Git proposal.

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

## Local and Docker execution safety

**Purpose**

Confirm the current local/Docker execution boundaries and record the targeted
approval-warning behavior without treating local execution as a sandbox.

Current guarantees:

- path-based tools are confined to the selected project root;
- local commands use the project root as `cwd`, run with `shell=False`, and
  execute as the current host user;
- dangerous command patterns are blocked before execution;
- Docker commands run inside `lunar-forge-sandbox` at `/workspace`;
- Docker wrappers never request privileged mode or a Docker socket mount; and
- plan and no-command modes block explicit command requests.

Use Docker mode for untrusted projects, generated code, dependency installs,
and scripts that have not been reviewed. The local-warning and Docker-approval
text below is the expected current wording.

**Setup**

These agent-driven tests require a configured model. Git is used only as a
small ordinary executable for warning-order tests. The Docker test requires the
checked-in `lunar-forge-sandbox` image:

```powershell
$ExecutionProject = Join-Path $ManualRoot "execution-safety-project"
New-Item -ItemType Directory -Force -Path (Join-Path $ExecutionProject ".agent") | Out-Null
@'
runtime:
  mode: local
  allow_network: false
  project_trust: trusted
permissions:
  mode: default
'@ | Set-Content -LiteralPath (Join-Path $ExecutionProject ".agent\config.yaml") -Encoding utf8
@'
print("safe-script-ran")
'@ | Set-Content -LiteralPath (Join-Path $ExecutionProject "safe_script.py") -Encoding utf8
docker build -t lunar-forge-sandbox -f lunar_forge/sandbox/Dockerfile .
```

### First and second ordinary local command approvals

Run two ordinary commands in one LunarForge session:

```powershell
lunar-forge --project $ExecutionProject "Use run_command to run python --version, then use run_command to run git --version. Do not edit files."
```

Targeted warning result:

1. The first approval shows the full local warning:

   ```text
   Run local command: python --version

   Local commands run as your user account on this machine. The project root is
   used as the working directory, but this is not OS-level isolation. Use Docker
   mode for untrusted projects or commands you have not reviewed.

   Allow? [y/N]
   ```

2. After approving it, the second ordinary command in the same trusted session
   uses short wording:

   ```text
   Run local command: git --version. Allow? [y/N]
   ```

Approve both commands. Both version commands should succeed, no mutation tool
should be exposed, and `safe_script.py` should remain unchanged.

### Dependency-install warning

Use an ordinary command first so the dependency warning is not merely the
first-command warning:

```powershell
lunar-forge --project $ExecutionProject "Use run_command to run git --version, then use run_command to run python -m pip install --dry-run lunar-forge-warning-probe. Do not edit files."
```

Approve `git --version`, then inspect and deny the install request. The targeted
result is a full local warning for the second command because dependency
installation always receives the long warning, even after the first trusted
session warning. Denial must prevent pip from running.

### Risky-but-not-blocked command warning

Run an ordinary command followed by a benign project script:

```powershell
lunar-forge --project $ExecutionProject "Use run_command to run git --version, then use run_command to run python safe_script.py. Do not edit files."
```

Approve both commands. The targeted result is a full warning for
`python safe_script.py` because direct execution of a project script is
risky-but-not-blocked, even though it is the second command in the session.
The command runs locally as the current user with `cwd=$ExecutionProject`,
prints `safe-script-ran`, and does not imply OS-level isolation.

### Blocked dangerous command

```powershell
lunar-forge --project $ExecutionProject "Use run_command to run sudo python --version. Do not edit files."
```

The command is rejected by the dangerous-command policy before any approval.
No local subprocess runs. Blocked commands must never be converted into a full
warning followed by an approval choice.

### Docker approval wording and `/workspace` proof

```powershell
lunar-forge --docker --project $ExecutionProject "Use run_command to run pwd. Then include stdout. Do not edit files."
```

The targeted Docker-specific approval is:

```text
Run Docker command: pwd
This runs inside lunar-forge-sandbox with the project mounted at /workspace.
Allow? [y/N]
```

Approve the command. Docker stdout must report `/workspace`, proving the
command ran in the container working directory. The application-generated
wrapper must use one project mount, omit `--privileged`, omit
`/var/run/docker.sock`, and retain network isolation unless `--allow-network`
was explicitly supplied.

### Safe Python inspection and bare-interpreter blocking

```powershell
lunar-forge --docker --project $ExecutionProject "Use run_command to run python --version. Do not edit files."
lunar-forge --docker --project $ExecutionProject "Use run_command to run python -V. Do not edit files."
lunar-forge --docker --project $ExecutionProject "Use run_command to run python. Do not edit files."
```

Approve each version inspection; both must run through the Docker runner. The
bare `python` request must be rejected before approval because it would start an
interactive interpreter. Repeat the last command with `python.exe`, `py`, and
`py.exe`; each no-argument form must remain blocked.

### No-edit and no-command wording

The successful local and Docker tests above prove that `Do not edit files`
blocks mutation tools without blocking an explicitly requested `run_command`.
The same distinction applies to explicit `run_validation`, browser validation,
plugin review tools, MCP read/review tools, Git inspection tools,
project-intelligence tools, and structured read-only tools when their normal
configuration, permission, and approval checks allow them.

Now add an explicit command prohibition:

```powershell
lunar-forge --project $ExecutionProject "Use run_command to run python --version. Do not edit files and do not run commands."
```

No command schema should be exposed, no approval should appear, and no command
should run.

### Plan mode blocks explicit `run_command`

```powershell
lunar-forge --plan --project $ExecutionProject "Use run_command to run python --version. Do not edit files."
```

Plan mode remains authoritative: `run_command` is not exposed, no approval
appears, and Python is not executed.

### No-command mode blocks explicit `run_command`

```powershell
@'
runtime:
  mode: no-command
permissions:
  mode: no-command
'@ | Set-Content -LiteralPath (Join-Path $ExecutionProject ".agent\config.yaml") -Encoding utf8
lunar-forge --project $ExecutionProject "Use run_command to run python --version. Do not edit files."
```

No-command mode also remains authoritative: no command tool is exposed, no
approval appears, and Python is not executed.

**Cleanup**

```powershell
Remove-Item -Recurse -Force -LiteralPath $ExecutionProject
```

## Event, Textual, resume, and compaction hardening

This wave is implemented. Use the commands below to check the shared event
protocol, one-shot parity, UI-independent approvals, optional Textual UI,
resumable chat, and working-memory compaction without creating runtime files in
the LunarForge repository.

Create a unique disposable project outside the repository:

```powershell
$ChatProject = Join-Path $ManualRoot ("chat-wave-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path (Join-Path $ChatProject ".agent") | Out-Null
Set-Content -LiteralPath (Join-Path $ChatProject "marker.txt") -Value "moon-marker" -Encoding utf8
Set-Content -LiteralPath (Join-Path $ChatProject "probe.py") -Value 'print("probe-ok")' -Encoding utf8
git -C $ChatProject init
git -C $ChatProject config user.name "LunarForge Manual Test"
git -C $ChatProject config user.email "lunar-forge@example.invalid"
git -C $ChatProject add marker.txt probe.py
git -C $ChatProject commit -m "Initial fixture"
```

### Deterministic hardening smoke matrix

Run the model-free regression matrix from the LunarForge repository root. These
tests use pytest temporary projects and fake model clients; they do not use an
API key or replay historical actions.

```powershell
python -m pytest -q `
  tests/test_events.py `
  tests/test_approvals.py `
  tests/test_textual_ui.py `
  tests/test_slash_commands.py `
  tests/test_textual_project_commands.py `
  tests/test_textual_action_commands.py `
  tests/test_sessions.py `
  tests/test_compaction.py `
  tests/test_readonly_fast_path.py `
  tests/test_validation.py `
  tests/test_no_edit_routing.py `
  tests/test_git.py `
  tests/test_docker_runner.py
```

Expected: pytest exits `0`. All selected tests pass; Textual pilot tests may be
reported as skipped when the optional dependency is absent. The matrix covers:

- read-only, edit, validation, Docker-command, Git-gating, and usage parity;
- stable schema version `1`, JSON round trips, payload limits, secret and
  hidden-reasoning redaction, and plain artifact references;
- CLI full/short local warnings, Docker wording, dangerous-command pre-denial,
  plan/no-command denial, and transport-neutral approval records;
- missing Textual, basic mount, two turns, command approval, and slash commands;
- latest-session selection, project binding, inert historical tools, and fresh
  approvals; and
- low-threshold compaction, persistent summaries, and summary-aware resume.

### Event protocol and future transport check

Run this direct protocol probe:

```powershell
@'
import json
from pathlib import Path
from lunar_forge.events import EventFactory, EventType, MAX_EVENT_PAYLOAD_CHARACTERS

event = EventFactory(
    session_id="session_manual",
    turn_id="turn_manual",
    environment={"OPENAI_API_KEY": "sk-event-manual-secret-12345678"},
).create(
    EventType.BROWSER_FINISHED,
    {
        "artifact": {
            "kind": "browser.screenshot",
            "path": Path(".agent/artifacts/browser/page.png"),
        },
        "api_key": "sk-event-manual-secret-12345678",
        "hidden_reasoning": "private scratchpad",
        "output": "x" * (MAX_EVENT_PAYLOAD_CHARACTERS + 1000),
    },
)
record = json.loads(event.to_json())
serialized = json.dumps(record)
print(record["schema_version"], record["type"])
print(type(record["payload"].get("artifact", {}).get("path")).__name__)
print("secret-present", "sk-event-manual-secret" in serialized)
print("reasoning-present", "private scratchpad" in serialized)
print("bounded", len(json.dumps(record["payload"])) <= MAX_EVENT_PAYLOAD_CHARACTERS)
'@ | python -
```

Expected output:

```text
1 browser.finished
str
secret-present False
reasoning-present False
bounded True
```

The large output is truncated while the normally sized artifact path remains a
plain string. Core event modules must not import Rich or Textual, and approval
payloads must remain ordinary JSON dictionaries suitable for a future
WebSocket or SSE adapter.

### Event-driven one-shot CLI parity

These commands require a configured model. Run them in order:

```powershell
lunar-forge --project $ChatProject "Read marker.txt and report its value. Do not edit files or run commands."
lunar-forge --project $ChatProject "Use write_file to create edit-result.txt containing event-parity. Do not run commands."
lunar-forge --project $ChatProject "Use run_validation to validate this Python project. Do not edit files."
lunar-forge --reasoning-effort high --show-usage --project $ChatProject "Explain marker.txt in one sentence. Do not edit files or run commands."
lunar-forge --docker --project $ChatProject "Use run_command to run pwd. Include stdout. Do not edit files."
lunar-forge --project $ChatProject --commit --commit-message "Add commit parity result" "Use write_file to create commit-result.txt containing commit-parity. Do not run commands."
```

Expected:

- read-only output reports `moon-marker` without edit or command approval;
- the edit asks approval once, creates `edit-result.txt` only after `y`, and
  reports its changed-file state in the existing concise final shape;
- validation displays its approval and result before any later Git proposal;
- usage includes `Reasoning effort: high`;
- Docker wording says `Run Docker command`, mentions
  `lunar-forge-sandbox` and `/workspace`, and approved `pwd` reports
  `/workspace`; and
- the commit proposal excludes `.agent/`, requires separate approval, and an
  approved result commits `commit-result.txt` and ends with `Git:` and
  `Commit created: <hash>`.

Each non-plan run writes its project-local JSONL session. The public event
stream adds no `Working...` or tool chatter to the quiet one-shot final answer.

### Approval-provider behavior

Use one turn for the first/second local-warning state:

```powershell
lunar-forge --project $ChatProject "Use run_command to run python --version, then use run_command to run git --version. Do not edit files."
lunar-forge --project $ChatProject "Use run_command to run sudo python --version. Do not edit files."
lunar-forge --plan --project $ChatProject "Use run_command to run python --version and write blocked.txt. Do not edit any other files."
@'
runtime:
  mode: no-command
permissions:
  mode: no-command
'@ | Set-Content -LiteralPath (Join-Path $ChatProject ".agent\config.yaml") -Encoding utf8
lunar-forge --project $ChatProject "Use run_command to run python --version. Do not edit files."
```

Expected:

- the first ordinary local command includes `not OS-level isolation`;
- the second ordinary local command uses the single-line short prompt;
- `sudo` is blocked before any approval provider is called;
- plan mode emits no approval and performs neither command nor write; and
- no-command mode emits no command approval and runs no subprocess.

The session log for approved or denied interactive requests contains one
bounded `permission.requested` and one correlated `permission.resolved`.
Dependency installs, risky allowed commands, Docker commands, and Git commits
retain the dedicated wording and gating documented in the preceding safety and
Git sections.

Restore the ordinary local/default config before continuing:

```powershell
@'
runtime:
  mode: local
  project_trust: trusted
permissions:
  mode: default
'@ | Set-Content -LiteralPath (Join-Path $ChatProject ".agent\config.yaml") -Encoding utf8
```

### Textual missing-dependency message

Create a core-only virtual environment and run chat without installing `tui`:

```powershell
$CoreOnlyVenv = Join-Path $ManualRoot "lf-core-only"
python -m venv $CoreOnlyVenv
$CoreOnlyPython = Join-Path $CoreOnlyVenv "Scripts\python.exe"
& $CoreOnlyPython -m pip install -e .
& $CoreOnlyPython -m lunar_forge.cli chat --project $ChatProject
```

Expected exact message and a clean exit without a traceback:

```text
Textual UI is not installed. Install it with: python -m pip install -e ".[tui]"
```

Also run:

```powershell
& $CoreOnlyPython -m lunar_forge.cli chat --help
```

Expected: help lists `--project` and `--resume` without importing Textual at
CLI module-import time.

### Textual startup, layout, and clean exit

Install the optional extra in the development environment and start chat:

```powershell
python -m pip install -e ".[tui]"
lunar-forge chat --project $ChatProject
```

Enter:

```text
/help
/status
Read marker.txt and remember its value. Do not edit files or run commands.
What value did you read in the previous turn? Do not call tools.
/clear
Use run_command to run python --version. Do not edit files.
/exit
```

Expected:

- the top card border reads `LunarForge v0.x`, the body shows
  `What are we building today?`, and the version is not repeated in the body;
- the top card shows project, model, reasoning effort, and current mode;
- only the transcript, temporary approval/popup surfaces, and bottom input are
  present—there are no permanent activity, tool-log, or metadata panels;
- the second turn answers `moon-marker` from the same in-process conversation;
- the two turns have blank-line spacing, `/help` lists the complete supported
  slash surface, `/status` reports the active state, `/clear` clears only the
  visible transcript, and `/exit` closes cleanly;
- `python --version` remains paused until the Textual approval is approved or
  denied; and
- hidden reasoning and unbounded tool output never appear.

### Resumable Textual chat

After exiting the preceding chat, run:

```powershell
lunar-forge chat --resume latest --project $ChatProject
```

Enter:

```text
What harmless marker value did we discuss? Do not call tools.
/status
/exit
```

Expected: the UI identifies the source session ID, answers `moon-marker` from
bounded historical context, and `/status` includes the resumed-session
metadata. The new session header references the source session. Historical tool
calls are marked context-only and are never replayed; historical approval
decisions never authorize a new action. Repeating the command with another
project or an ambiguous partial ID fails clearly before chat starts.

### Working-memory compaction and resume

Set an intentionally low disposable-project threshold:

```powershell
@'
runtime:
  mode: local
  project_trust: trusted
permissions:
  mode: default
ui:
  chat:
    compact_at_tokens: 200
    compact_to_tokens: 50
'@ | Set-Content -LiteralPath (Join-Path $ChatProject ".agent\config.yaml") -Encoding utf8
lunar-forge chat --project $ChatProject
```

Send several harmless read-only messages until activity reports compaction,
then run `/status` and `/exit`. Inspect and resume:

```powershell
$SummaryFile = Get-ChildItem -File -LiteralPath (Join-Path $ChatProject ".agent\summaries") |
  Sort-Object LastWriteTimeUtc -Descending |
  Select-Object -First 1
$Summary = Get-Content -Raw -LiteralPath $SummaryFile.FullName | ConvertFrom-Json
$Summary | Select-Object schema_version, session_id, source_events_through, source_event_count, summary
$Summary.facts | Select-Object project_root, runtime_mode, permission_mode, model, reasoning_effort, changed_files, validation, open_items
Select-String -Path $SummaryFile.FullName -Pattern "sk-|hidden_reasoning|chain.of.thought"
lunar-forge chat --resume latest --project $ChatProject
```

Expected:

- activity receives one correlated `memory.compaction.started` /
  `memory.compaction.finished` pair;
- `.agent/summaries/<session-id>.summary.json` exists with schema version `1`,
  a source-event boundary, bounded safe summary, retained facts, and recent
  turns;
- the secret/reasoning search returns no matches;
- `/status` reports the compaction count and summary path; and
- resumed chat retains the compacted goal/constraints/state plus recent turns
  without replaying tools or approvals.

If summarization fails, the transcript shows a clear warning and retains the
uncompacted live context when safe. Plan-mode chat without a session log does
not compact.

## Textual UI wave acceptance tests

The layout, input behavior, slash router, popup selectors, project/session
commands, and action wrappers are implemented. Run these acceptance checks as a
final interactive hardening pass. The existing event stream, approval
providers, session JSONL, resume, and compaction tests remain mandatory
regressions.

### Final hardening smoke sequence

Use the detailed procedures in the following subsections for setup, approvals,
and cleanup. This is the final 20-check execution order and expected result:

1. Run `python -m lunar_forge.cli chat --help`.
   Expected: exit `0`, `--project` and `--resume` are listed, and the Textual
   app module is not imported just to render help.
2. Run chat from the core-only environment with
   `& $CoreOnlyPython -m lunar_forge.cli chat --project $ChatProject`.
   Expected: the exact `Textual UI is not installed...` setup guidance and a
   clean exit without a traceback.
3. Run `lunar-forge chat --project $ChatProject`.
   Expected: the border title is `LunarForge v0.x`; no duplicate version or
   permanent activity/tool/status panel appears.
4. Inspect the new-session card.
   Expected: `What are we building today?`.
5. Exit, then run
   `lunar-forge chat --resume latest --project $ChatProject`.
   Expected: `Let’s get back to building.` and the source session ID.
6. Send the two read-only turns from
   [Textual startup, layout, and clean exit](#textual-startup-layout-and-clean-exit).
   Expected: a blank line separates messages and turns.
7. Send
   `Use run_command to run python --version. Do not edit files.`
   Expected: one temporary progress block shows public work, the bounded
   command, and a changing elapsed timer while approval is pending.
8. Approve or deny that request.
   Expected: temporary progress is removed and the final result includes
   `Done in <duration>.`.
9. Paste the two-line prompt in
   [Input, normal paste, and multiline paste](#input-normal-paste-and-multiline-paste).
   Expected: its newline is preserved.
10. Press `Shift+Enter`, then `Enter`.
    Expected: the first key inserts a newline and the second submits once.
11. Run `/status`.
    Expected: current project, model, effort, runtime, permissions, network,
    subagents, commit, MCP, plugins, session, resume, and compaction values.
12. Run `/reasoning-effort`.
    Expected: a selector shows the current value and all allowed choices.
13. Apply `high` to this chat only, exit, and compare `$ConfigBefore` with
    `$ConfigAfterSession`.
    Expected: `/status` changes immediately and the comparison returns `True`.
14. Run `/reasoning-effort xhigh scope=project`, approve the write, and inspect
    `$ConfigPath`.
    Expected: the file is safely created or updated, unrelated keys are
    retained when practical, and the live session changes immediately.
15. Run the generated `/project "<second-project-path>"` command.
    Expected: project-scoped config/instructions/integrations reload and a
    fresh conversation/session context starts without old file facts.
16. Run `/resume`, then `/resume latest`.
    Expected: the picker and direct path load compatible safe history without
    replaying tools or reusing approvals.
17. Run `/browser-validate`, then
    `/browser-validate url=http://127.0.0.1:5173 no-screenshot=true check="#root"`.
    Expected: the first opens the validated form and the second delegates to
    the existing browser workflow.
18. Run `/git status` and `/git commit message="Manual Textual command test"`.
    Expected: status is model-free; validation remains visible before commit
    approval; failed validation blocks a normal commit.
19. Run `/mcp list` and `/plugins list`.
    Expected: bounded diagnostics appear without auto-enabling either
    integration.
20. Run the six commands under
    [Event-driven one-shot CLI parity](#event-driven-one-shot-cli-parity).
    Expected: read, edit, validation, usage, Docker, and Git behavior retains
    the existing one-shot output and safety gates.

### Simplified new-session layout

Start a new chat in the disposable project:

```powershell
lunar-forge chat --project $ChatProject
```

Expected:

- the top card border title is exactly `LunarForge v0.x`;
- the card body does not repeat the version;
- the new-session greeting is `What are we building today?`;
- project, model, reasoning effort, and mode are visible;
- the main body contains the chat transcript and bottom input;
- there is readable blank space between messages and between turns; and
- there are no permanent recent-activity, last-session, changed-file,
  tips/status, shortcut, Docker-status, tool-log, or activity panels.

The UI may show approvals in a popup or temporary inline block. It must not
restore a permanent approval/activity dashboard.

### Resumed-session layout

Exit the new chat after one harmless turn, then run:

```powershell
lunar-forge chat --resume latest --project $ChatProject
```

Expected:

- the top card says `Let’s get back to building.`;
- its border still contains `LunarForge v0.x` exactly once;
- the selected project and resumed session metadata are correct;
- safe historical turns appear with blank-line spacing; and
- no historical tool call or approval is replayed.

### Temporary progress and elapsed time

In chat, submit:

```text
Read marker.txt and explain its value. Do not edit files or run commands.
```

While the turn runs, expected temporary transcript content resembles:

```text
Working: reading the requested project file.
Tool: read_file marker.txt
Elapsed: 00:04
```

Expected after completion:

- the progress block was driven by structured public events;
- hidden reasoning was never displayed;
- the temporary block disappeared or was replaced by the final response;
- only one final assistant response remains for the turn; and
- the final response contains a human-readable duration such as
  `Done in 12 seconds.` or `Done in 1 minute 12 seconds.`

Repeat with an approved command to verify command and approval progress:

```text
Use run_command to run python --version. Do not edit files.
```

The command must stay paused until the Textual approval provider resolves it.
The temporary block may show the bounded command preview, but approval must not
fall back to raw terminal `input()`.

### Input, normal paste, and multiline paste

Test these input paths separately:

1. Paste a one-line prompt and press `Enter`.
2. Type the first line below, press `Shift+Enter`, type the second line, then
   press `Enter`.
3. Paste the entire multiline block and press `Enter`.

```text
Summarize marker.txt.
Do not edit files or run commands.
```

Expected:

- normal paste preserves the complete prompt;
- `Shift+Enter` inserts a newline without sending;
- multiline paste preserves its newline and does not submit only the first
  line;
- `Enter` sends exactly one message after the input is ready; and
- oversized paste is bounded or produces a clear warning instead of freezing
  the app.

### Slash-command discovery and argument routing

Run:

```text
/help
```

Expected `/help` entries:

```text
/help
/status
/clear
/exit
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

Expected routing rules:

- typed arguments run directly after validation;
- a command requiring omitted arguments opens its popup form;
- quoted typed strings are preserved;
- invalid arguments show a clear error without starting an agent turn; and
- popup cancellation performs no action and changes no settings.

Exercise representative typed and form paths:

```text
/project
/commit-message
/git commit
/rollback
/reasoning-effort
/reasoning-effort high
/git commit message="Manual Textual command test"
```

The commands without required arguments open forms. The typed reasoning value
uses session scope by default. The quoted commit message reaches the existing
Git workflow unchanged.

### Config-backed values and persistence scope

Before starting chat, record the project config:

```powershell
$ConfigPath = Join-Path $ChatProject ".agent\config.yaml"
$ConfigBefore = Get-Content -Raw -LiteralPath $ConfigPath
lunar-forge chat --project $ChatProject
```

In chat, run:

```text
/reasoning-effort
```

Expected popup:

- it shows the current effective reasoning effort;
- it lists `low`, `medium`, `high`, `xhigh`, and `max`; and
- it offers `this chat session only` and
  `save to project .agent/config.yaml`.

Choose `high` for this chat session only, then run:

```text
/status
/runtime docker scope=session
/allow-network true scope=session
/subagents true scope=session
/parallel-subagents true scope=session
/mcp false scope=session
/plugins false scope=session
/status
/exit
```

Verify:

```powershell
$ConfigAfterSession = Get-Content -Raw -LiteralPath $ConfigPath
$ConfigAfterSession -ceq $ConfigBefore
```

Expected: both `/status` calls reflect the active session values, future turns
use them, and the PowerShell comparison returns `True`. No session-only value
was written to project config.

Start chat again and explicitly save a safe project value:

```text
/reasoning-effort xhigh scope=project
/status
/exit
```

Verify:

```powershell
Get-Content -Raw -LiteralPath $ConfigPath
```

Expected: only the relevant `model.reasoning.effort` value is updated, unrelated
YAML remains intact when practical, and the active chat used `xhigh`
immediately. Repeat representative popup checks for `/plan`, `/docker`,
`/allow-network`, `/subagents`, `/parallel-subagents`, `/runtime`,
`/permissions`, `/mcp`, and `/plugins`.

Project-scope saves must use normal file safety, contain no raw API key or other
secret, and require an explicit save choice. `/commit`, `/commit-message`, and
`/show-usage` remain session controls in this wave.

### Project switching, resume picker, and new-project workflow

Create another disposable project:

```powershell
$SecondChatProject = Join-Path $ManualRoot ("chat-wave-second-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $SecondChatProject | Out-Null
Set-Content -LiteralPath (Join-Path $SecondChatProject "other.txt") -Value "second-project" -Encoding utf8
$ProjectSwitchCommand = '/project "{0}"' -f $SecondChatProject
Write-Output $ProjectSwitchCommand
```

In the first project’s chat, run:

```text
/project
```

Expected: a project picker/form opens. Cancel it and verify nothing changes.
Then paste the command printed by PowerShell, which has this shape:

```text
/project "C:\...\chat-wave-second-..."
```

Expected:

- the active project switches only after path validation;
- project config, instructions, MCP, and plugins reload;
- a fresh conversation/session context starts for the second project;
- first-project messages, changed files, approvals, and compaction facts are
  not carried into the new context; and
- the transcript confirms the new project.

Run:

```text
/sessions
/resume
/resume latest
/new
```

Expected: `/sessions` and `/resume` open project-scoped pickers;
`/resume latest` uses the latest compatible session without replaying tools;
and `/new` asks for a natural-language prompt before delegating to the existing
new-project workflow. It must not introduce another scaffolding or session
implementation.

### Existing workflow slash commands

Run or open each form in a disposable project:

```text
/browser-setup
/browser-validate
/browser-validate url=http://127.0.0.1:5173 no-screenshot=true check="#root"
/browser-validate url=http://127.0.0.1:5173 serve="npm run dev" screenshot=true full-page=true width=1440 height=900 startup-timeout-ms=30000 check="#root" check="title"
/checkpoints
/rollback
/git status
/git commit
/mcp list
/plugins list
```

The `/browser-validate` form and parser expose:

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

Expected:

- repeatable `check` values are retained;
- `screenshot` and `no-screenshot` are mutually clear;
- existing browser, checkpoint, rollback, Git, MCP, and plugin helpers perform
  the work;
- dangerous commands remain blocked before approval;
- all guarded actions use approval providers;
- plan/no-command restrictions still apply; and
- outputs enter the transcript as bounded plain event data, not Rich/Textual
  objects embedded in core events.

### Regression and architecture checks for the UI wave

After implementation, run:

```powershell
python -m pytest -q
python -B -m compileall lunar_forge
python -B -m compileall examples/plugins/web-design-review
git diff --check
```

Expected:

- one-shot CLI output and flags remain unchanged;
- `python -m pip install -e .` still works without Textual;
- `lunar-forge chat` still prints the documented install message when the
  optional dependency is missing;
- one-shot and Textual consume the same transport-neutral event protocol;
- approval providers are the only approval mechanism;
- JSONL sessions and compacted summaries remain the persistent source of truth;
- no session-only setting is silently persisted; and
- no web/cloud UI, cloud sandbox, prompt-toolkit REPL, or duplicate compaction
  system was introduced.

### Runtime-artifact audit and cleanup

Confirm no disposable project or generated runtime data is tracked:

```powershell
$TrackedRuntime = git -C $RepoRoot ls-files |
  Where-Object { $_ -match '(^|/)\.agent/(sessions|summaries|checkpoints|artifacts)(/|$)' }
if ($TrackedRuntime) {
  throw "Tracked runtime files found: $($TrackedRuntime -join ', ')"
}
git -C $RepoRoot status --short --untracked-files=all
```

Expected: `$TrackedRuntime` is empty. Repository status contains only intended
source, test, or documentation changes and no path beneath `$ManualRoot`.

Before cleanup, resolve and verify that the target remains under the system
temporary directory:

```powershell
$ResolvedManualRoot = (Resolve-Path -LiteralPath $ManualRoot).Path
$ResolvedTemp = (Resolve-Path -LiteralPath $env:TEMP).Path
if (-not $ResolvedManualRoot.StartsWith($ResolvedTemp, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing to remove non-temporary manual root: $ResolvedManualRoot"
}
Remove-Item -Recurse -Force -LiteralPath $ResolvedManualRoot
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
python -B -m compileall examples/plugins/web-design-review
git diff --check
```

**Expected result**

Pytest reports no failures, both compileall commands report no syntax errors,
and `git diff --check` produces no whitespace errors.

**Cleanup**

No test artifacts need to be retained. Remove `$ManualRoot` only after checking
that it still points to the disposable directory created for this guide:

```powershell
Write-Host $ManualRoot
Remove-Item -Recurse -Force -LiteralPath $ManualRoot
```
