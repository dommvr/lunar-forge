"""Tests for the starter Flask application."""

import unittest

from app import create_app


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = create_app().test_client()

    def test_index_returns_a_greeting(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"message": "Hello from Flask!"})


if __name__ == "__main__":
    unittest.main()
