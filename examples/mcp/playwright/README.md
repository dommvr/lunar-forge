# Playwright MCP configuration

These files show the two explicit opt-ins needed to expose Playwright MCP tools
to LunarForge on Windows. The configuration contains no credentials.

`config.yaml` uses the normal application schema and turns on MCP globally for
the selected project. `mcp.yaml` uses the supported MCP-server schema: a
top-level `servers` mapping whose server entries accept only `command`, `args`,
`env`, and `enabled`. `command` is a string, `args` is a list of strings,
`enabled` is a boolean, and optional environment values must be references such
as `${HOST_TOKEN}` rather than raw credentials. A nested `mcp: {servers: ...}`
shape is also accepted, but the checked-in file intentionally uses the simpler
top-level form.

From the repository root, copy the examples into the browser demo:

```powershell
New-Item -ItemType Directory -Force -Path examples\projects\browser-demo\.agent | Out-Null
Copy-Item examples\mcp\playwright\config.yaml examples\projects\browser-demo\.agent\config.yaml
Copy-Item examples\mcp\playwright\mcp.yaml examples\projects\browser-demo\.agent\mcp.yaml
lunar-forge mcp list --project examples\projects\browser-demo
```

The configured command uses `npx -y`, so the diagnostic may download
`@playwright/mcp` into npm's user cache when it is not already available. This
is an explicit consequence of enabling and running this example; it does not
install a global npm package or require a secret. The diagnostic should report
namespaced tools such as
`mcp.playwright.browser_navigate` and their provider-safe aliases.

Start the browser demo in one terminal:

```powershell
cd examples/projects/browser-demo
npm install
npm run dev
```

From the same project in a second terminal, ask LunarForge to use the MCP tools:

```powershell
lunar-forge --project . "Use Playwright MCP to inspect http://localhost:5173. Report the title, main heading, button, and input."
```

Review and approve each external MCP action.

## POSIX command

On Linux and macOS, copy `mcp.yaml` and change only the executable:

```yaml
servers:
  playwright:
    command: npx
    args:
      - -y
      - "@playwright/mcp@latest"
      - "--isolated"
    enabled: true
```
