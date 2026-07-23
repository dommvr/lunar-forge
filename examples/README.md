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
