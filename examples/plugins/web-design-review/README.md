# Web design review example plugin

This real local plugin exposes `web_design.review_files`, a bounded advisory
review of project-local HTML, CSS, JSX, and TSX source. It uses deterministic
Python heuristics only: no LLM, commands, network, browser, Playwright, npm,
API key, dependency installation, or file writes.

The checks cover document metadata, image alternatives, interactive names,
form labels, heading structure, semantic landmarks, responsive CSS, tiny font
sizes, and call-to-action copy. Results contain at most 50 concise findings.
Unsupported and missing files are reported in `files_skipped`.

## Enable it for the browser demo

Plugins are globally disabled by default. Set `plugins.enabled: true` in the
project's `.agent/config.yaml`:

```yaml
plugins:
  enabled: true
```

The same setting may instead live in the user config at
`%USERPROFILE%\.lunar-forge\config.yaml`. The project config is explicit and
keeps this example self-contained.

Open Command Prompt (`cmd.exe`) and run these commands exactly. The config
creation command assumes browser-demo's ignored `.agent` directory is
disposable; merge the YAML manually if that config already contains settings
you want to keep.

```cmd
cd /d C:\Users\tiron\Desktop\lunar-forge
mkdir examples\projects\browser-demo\.agent 2>nul
copy examples\plugins\web-design-review\plugins.yaml.example examples\projects\browser-demo\.agent\plugins.yaml
(echo plugins: & echo   enabled: true)>examples\projects\browser-demo\.agent\config.yaml
lunar-forge plugins list --project examples\projects\browser-demo
lunar-forge --project examples\projects\browser-demo "Use web_design.review_files to review index.html, src\App.jsx, and src\App.css for accessibility, responsive layout, and visual hierarchy. Do not edit files."
```

The copied plugin config references
`../../plugins/web-design-review/plugin.yaml`. LunarForge allows an explicitly
configured manifest within the nested project's containing Git checkout, but
rejects paths outside that repository boundary. The plugin still confines every
reviewed website file to `examples/projects/browser-demo`.

`plugins list` is model-free and does not import the Python handler. The final
command requires the normally configured LunarForge model so it can select the
tool, then asks for approval before loading and invoking
`web_design.review_files`. Approve only that dotted tool name.

This is a static source review. Mentioning accessibility, layout, visual
hierarchy, or the `browser-demo` path does not start browser validation. No
browser tool is exposed unless the request separately asks for a browser,
Playwright, rendering, or screenshots. No browser installation, npm install,
dev server, API key for the plugin, or network access is needed.

The review is advisory and never applies its own suggestions. Make any desired
edits with LunarForge's built-in editing tools, which retain their normal
approval and checkpoint behavior. Findings come only from source heuristics;
they are not browser-rendered evidence and make no claim about computed styles,
runtime behavior, screenshots, or pixels. Use the separate browser-validation
or Playwright MCP workflows when rendered-page evidence is required.

## Expected browser-demo result

The result is JSON-serializable and returns four integer scores from 0 through
10, but exact scores and finding counts are intentionally not promised because
the local heuristics may evolve. `index.html` and `src/App.jsx` should be
reviewed. Browser-demo currently uses `src/styles.css`, not `src/App.css`, so
the exact required command demonstrates safe missing-file handling:
`src/App.css` should appear in `files_skipped`.

To include the demo's real stylesheet in a follow-up review, substitute
`src\styles.css` for `src\App.css`.

Named plugin calls intentionally do not use the deterministic built-in
read-only fast path because local plugin execution must pass through approval.
Explicit structured requests such as `Run read_json on package.json` continue
to use the normal built-in fast path.

For a manual path-safety check, request `../../outside.txt`; the result should
be `ok: false`, review no files, and report that the path is outside the project
root without returning file content.

Remove `examples\projects\browser-demo\.agent` to disable and clean up the
example configuration.
