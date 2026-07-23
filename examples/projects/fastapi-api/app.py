"""A minimal FastAPI JSON API."""

from __future__ import annotations

from fastapi import FastAPI


app = FastAPI(title="LunarForge FastAPI Example")


@app.get("/")
def read_root() -> dict[str, str]:
    return {"message": "Hello from the FastAPI example!"}


@app.get("/health")
def read_health() -> dict[str, str]:
    return {"status": "ok"}
