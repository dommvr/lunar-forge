# LunarForge examples

These source-only examples are intentionally small enough to inspect in one
session. They do not contain generated dependency or build directories. Run
installation commands yourself only for the example you want to try.

Run the commands below from the LunarForge repository root. The examples do not
need application secrets, global npm packages, cloud services, or a global
Python package installation. The Node examples install into their own
`node_modules/` directories, and the API READMEs create project-local virtual
environments.

| Example | What it demonstrates | Dependencies |
| --- | --- | --- |
| [Browser demo](projects/browser-demo/) | Managed browser validation, full-page screenshots, console capture, and Playwright MCP | Node.js and npm |
| [Static site](projects/static-site/) | Dependency-free HTML and CSS | None |
| [Vite React](projects/vite-react/) | A minimal React frontend and production build | Node.js and npm |
| [Python CLI](projects/python-cli/) | `argparse` plus a standard-library test | None |
| [Flask API](projects/flask-api/) | A JSON endpoint and Flask test client | See `requirements.txt` |
| [FastAPI API](projects/fastapi-api/) | A typed JSON endpoint and import-level test | See `requirements.txt` |
| [Playwright MCP config](mcp/playwright/) | Windows stdio configuration using `npx.cmd` | Node.js and npm |
| [Web design review plugin](plugins/web-design-review/) | Read-only HTML/CSS/JSX design heuristics and a browser-demo config | None |

## Browser validation quick start

From the repository root in PowerShell:

```powershell
Push-Location examples/projects/browser-demo
npm install
npm run build
Pop-Location
lunar-forge browser-validate --serve "npm run dev" --url http://localhost:5173 --project examples/projects/browser-demo --check "#main-heading" --check "#below-fold-heading" --full-page
```

Approve the exact `npm run dev` command when LunarForge prompts. The screenshot
is written beneath `examples/projects/browser-demo/.agent/artifacts/browser/`.
See the browser demo README for console-error and Playwright MCP checks.

## Web design review plugin quick start

The checked-in `web_design.review_files` example is advisory and read-only. It
uses local heuristics, has no dependencies, and declares no command or network
permission. Its explicit manifest remains inside the same Git checkout as the
nested browser-demo project; reviewed website files remain confined to
browser-demo itself.

Open Command Prompt. The config command enables plugins globally for this
project; alternatively place the same `plugins.enabled: true` YAML in
`%USERPROFILE%\.lunar-forge\config.yaml`. The commands assume browser-demo's
ignored `.agent` directory is disposable; merge an existing config instead of
overwriting settings you want to keep.

```cmd
cd /d C:\Users\tiron\Desktop\lunar-forge
mkdir examples\projects\browser-demo\.agent 2>nul
copy examples\plugins\web-design-review\plugins.yaml.example examples\projects\browser-demo\.agent\plugins.yaml
(echo plugins: & echo   enabled: true)>examples\projects\browser-demo\.agent\config.yaml
lunar-forge plugins list --project examples\projects\browser-demo
lunar-forge --project examples\projects\browser-demo "Use web_design.review_files to review index.html, src\App.jsx, and src\App.css for accessibility, responsive layout, and visual hierarchy. Do not edit files."
```

The diagnostic is model-free. The final agent command needs the normally
configured LunarForge model and asks for approval before loading the local
plugin. It performs static source review and does not trigger browser validation
or require browser, npm, Playwright, dev-server, or network dependencies.
Browser-demo has `src/styles.css`, not `src/App.css`, so the exact command
honestly reports that requested file as skipped; scores and finding counts may
change with the heuristics. Review the plugin
[README](plugins/web-design-review/) for permissions, path safety, the actual
stylesheet variant, and cleanup. Its advisory source findings are not
browser-rendered evidence; use browser validation or Playwright MCP separately
when rendered-page evidence is required.

## Keeping the checkout clean

The npm examples ignore `node_modules/`, `dist/`, and `.agent/`. Python cache
and virtual-environment directories are ignored by the repository. Remove local
generated directories after testing; do not commit them.

From the repository root, remove all example-generated state with:

```powershell
$Generated = @(
    "examples\projects\browser-demo\node_modules",
    "examples\projects\browser-demo\dist",
    "examples\projects\browser-demo\.agent",
    "examples\projects\browser-demo\package-lock.json",
    "examples\projects\vite-react\node_modules",
    "examples\projects\vite-react\dist",
    "examples\projects\vite-react\package-lock.json",
    "examples\projects\python-cli\__pycache__",
    "examples\projects\flask-api\.venv",
    "examples\projects\flask-api\__pycache__",
    "examples\projects\fastapi-api\.venv",
    "examples\projects\fastapi-api\__pycache__"
)
$Generated | ForEach-Object {
    Remove-Item -Recurse -Force -LiteralPath $_ -ErrorAction SilentlyContinue
}
```
