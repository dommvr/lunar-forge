# Python CLI example

This example uses only `argparse` and `unittest` from the Python standard
library. It has no third-party dependencies.

Run these commands from the LunarForge repository root. No virtual environment,
network access, or secret is required.

## Run

```powershell
cd examples/projects/python-cli
python app.py --name Ada --excited
```

Expected output:

```text
Hello, Ada!
```

## Test

```powershell
python -m unittest -q
```

## Cleanup

Python may create `__pycache__`; remove it with:

```powershell
Remove-Item -Recurse -Force __pycache__ -ErrorAction SilentlyContinue
```
