# FastAPI API example

This example exposes two typed JSON endpoints and keeps its test import-level
so no extra HTTP client dependency is needed. Nothing installs automatically.

Run these commands from the LunarForge repository root. Python 3.11 or newer is
required; the app needs no secrets or external service.

## Create an environment and install

```powershell
cd examples/projects/fastapi-api
python -m venv .venv
$ExamplePython = ".\.venv\Scripts\python.exe"
& $ExamplePython -m pip install -r requirements.txt
```

## Test

```powershell
& $ExamplePython -m unittest -q
```

## Run

```powershell
& $ExamplePython -m uvicorn app:app --reload
```

Open <http://127.0.0.1:8000/docs>. Stop the server with `Ctrl+C`.

## Cleanup

```powershell
Remove-Item -Recurse -Force .venv
```
