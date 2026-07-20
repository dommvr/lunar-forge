"""A minimal FastAPI application."""

from __future__ import annotations

from fastapi import FastAPI


app = FastAPI(title="FastAPI Starter")


@app.get("/")
def read_root() -> dict[str, str]:
    return {"message": "Hello from FastAPI!"}
