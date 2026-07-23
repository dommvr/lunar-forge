# FastAPI API example

This example exposes two typed JSON endpoints and keeps its test import-level
so no extra HTTP client dependency is needed. Nothing installs automatically.

## Create an environment and install

```powershell
cd examples/projects/fastapi-api
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Test

```powershell
python -m unittest -q
```

## Run

```powershell
uvicorn app:app --reload
```

Open <http://127.0.0.1:8000/docs>. Stop the server with `Ctrl+C`.

## Cleanup

```powershell
deactivate
Remove-Item -Recurse -Force .venv
```
