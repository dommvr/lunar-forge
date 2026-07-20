"""Tests for the starter command-line application."""

import unittest

from app import greeting


class GreetingTests(unittest.TestCase):
    def test_greeting_uses_the_supplied_name(self) -> None:
        self.assertEqual(greeting("Ada"), "Hello, Ada!")


if __name__ == "__main__":
    unittest.main()
