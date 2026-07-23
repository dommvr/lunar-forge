# Python CLI example

This example uses only `argparse` and `unittest` from the Python standard
library. It has no third-party dependencies.

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

Python may create `__pycache__`; it is ignored by the repository and can be
removed at any time.
