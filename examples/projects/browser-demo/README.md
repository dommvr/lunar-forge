# Browser validation demo

This React/Vite page is the canonical LunarForge browser-validation target. It
has stable headings, content below the first viewport, a counter button, a form
field, and no console errors during normal use.

The demo uses no secrets, remote assets, external APIs, global npm packages, or
model calls for its install/build/server and deterministic `browser-validate`
workflow. Node.js, npm, and a LunarForge environment with optional browser
support are the only prerequisites. `npm install` writes dependencies locally;
network access is needed only when those packages are not already cached.

## Install

From the repository root:

```powershell
cd examples/projects/browser-demo
npm install
```

## Run the development server

```powershell
npm run dev
```

Open <http://localhost:5173>.

## Build

```powershell
npm run build
```

## Managed browser validation and full-page screenshot

Install LunarForge's optional browser support first from the repository root:

```powershell
lunar-forge browser-setup --project .
```

Then return to this example directory and let LunarForge manage the Vite server:

```powershell
cd examples/projects/browser-demo
lunar-forge browser-validate --serve "npm run dev" --url http://localhost:5173 --project . --check "#main-heading" --check "#below-fold-heading" --full-page --width 1440 --height 900
```

Approve the exact `npm run dev` command. The JSON result should report both
selector checks as passed, no console errors, and a screenshot under
`.agent/artifacts/browser/`. LunarForge stops the managed server afterward.

## Optional console-error collection

Normal page loads do not write console errors. Add `?consoleError=1` to request
one deterministic test error:

```powershell
lunar-forge browser-validate --serve "npm run dev" --url "http://localhost:5173/?consoleError=1" --project . --no-screenshot
```

The result's `console_errors` list should contain
`Browser demo requested an optional console error.` Remove the query parameter
to return to the error-free default.

## Playwright MCP inspection

This optional section uses the configured LunarForge model and therefore needs
that model's normal environment-variable credential. The browser demo itself
does not read or store the credential. `npx -y` may download the explicitly
enabled MCP package into npm's user cache when it is not already available.

Copy the checked-in Windows MCP examples into this project's local config:

```powershell
New-Item -ItemType Directory -Force -Path .agent | Out-Null
Copy-Item ..\..\mcp\playwright\config.yaml .agent\config.yaml
Copy-Item ..\..\mcp\playwright\mcp.yaml .agent\mcp.yaml
lunar-forge mcp list --project .
```

Keep `npm run dev` running in one terminal. In a second terminal, from this
directory, run:

```powershell
lunar-forge --project . "Use Playwright MCP to inspect http://localhost:5173. Report the page title, the main heading, the counter button, and the form input."
```

Review and approve each external MCP action. POSIX users must change the MCP
server command from `npx.cmd` to `npx`; see the MCP example README.

## Cleanup

```powershell
$Generated = @("node_modules", "dist", ".agent", "package-lock.json")
$Generated | ForEach-Object {
    Remove-Item -Recurse -Force -LiteralPath $_ -ErrorAction SilentlyContinue
}
```
