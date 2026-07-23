# Flask API example

This example exposes small JSON endpoints and tests one with Flask's built-in
test client. Nothing installs automatically.

## Create an environment and install

```powershell
cd examples/projects/flask-api
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
flask --app app run --debug
```

Open <http://127.0.0.1:5000/health>. Stop the server with `Ctrl+C`.

## Cleanup

```powershell
deactivate
Remove-Item -Recurse -Force .venv
```
