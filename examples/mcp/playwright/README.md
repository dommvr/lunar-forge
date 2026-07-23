# Playwright MCP configuration

These files show the two explicit opt-ins needed to expose Playwright MCP tools
to LunarForge on Windows. The configuration contains no credentials.

From the repository root, copy the examples into the browser demo:

```powershell
New-Item -ItemType Directory -Force -Path examples\projects\browser-demo\.agent | Out-Null
Copy-Item examples\mcp\playwright\config.yaml examples\projects\browser-demo\.agent\config.yaml
Copy-Item examples\mcp\playwright\mcp.yaml examples\projects\browser-demo\.agent\mcp.yaml
lunar-forge mcp list --project examples\projects\browser-demo
```

The diagnostic may use `npx -y` to download `@playwright/mcp` when it is not
already cached. It should report namespaced tools such as
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
