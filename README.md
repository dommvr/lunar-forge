# LunarForge

LunarForge is a local Python scaffold for a small code-agent style project.

## Layout

- `lunar_forge/` contains the package source.
- `lunar_forge/model_clients/` contains model provider adapters.
- `lunar_forge/tools/` contains file, search, shell, and project tools.
- `lunar_forge/runtime/` contains local execution, sessions, checkpoints, and diffs.
- `lunar_forge/workflows/` contains higher-level workflow entry points.
- `lunar_forge/templates/` contains starter project templates.
- `tests/` contains focused unit tests.

## Development

```powershell
python -m pytest
```
