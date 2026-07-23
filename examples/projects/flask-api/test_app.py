"""Tests for the Flask API example."""

import unittest

from app import create_app


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = create_app().test_client()

    def test_health_endpoint(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
