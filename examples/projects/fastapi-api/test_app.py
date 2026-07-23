"""Tests for the FastAPI example without an HTTP test dependency."""

import unittest

from app import read_health, read_root


class AppTests(unittest.TestCase):
    def test_root_message(self) -> None:
        self.assertEqual(
            read_root(),
            {"message": "Hello from the FastAPI example!"},
        )

    def test_health_status(self) -> None:
        self.assertEqual(read_health(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
