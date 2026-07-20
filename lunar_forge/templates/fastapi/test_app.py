"""Tests for the starter FastAPI application."""

import unittest

from app import read_root


class AppTests(unittest.TestCase):
    def test_root_returns_a_greeting(self) -> None:
        self.assertEqual(read_root(), {"message": "Hello from FastAPI!"})


if __name__ == "__main__":
    unittest.main()
