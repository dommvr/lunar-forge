# Flask API example

This example exposes small JSON endpoints and tests one with Flask's built-in
test client. Nothing installs automatically.

Run these commands from the LunarForge repository root. Python 3.11 or newer is
required; the app needs no secrets or external service.

## Create an environment and install

```powershell
cd examples/projects/flask-api
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
& $ExamplePython -m flask --app app run --debug
```

Open <http://127.0.0.1:5000/health>. Stop the server with `Ctrl+C`.

## Cleanup

```powershell
Remove-Item -Recurse -Force .venv
```
