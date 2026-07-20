"""A minimal Flask application."""

from __future__ import annotations

from flask import Flask, jsonify


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return jsonify(message="Hello from Flask!")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
