# Static site example

This example is plain HTML and CSS. It has no package manager, dependency
installation, or build step.

## Open directly

From the repository root on Windows:

```powershell
Start-Process examples\projects\static-site\index.html
```

## Serve locally

```powershell
cd examples/projects/static-site
python -m http.server 8000
```

Open <http://localhost:8000>. Stop the server with `Ctrl+C`.

## Cleanup

No generated files are created by the example.
