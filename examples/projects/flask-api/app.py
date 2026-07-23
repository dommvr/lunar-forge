"""A minimal Flask JSON API."""

from __future__ import annotations

from flask import Flask, jsonify


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return jsonify(message="Hello from the Flask example!")

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
