"""Tests for core.voice data models."""
import unittest

from core.voice.models import Option, Action


class TestOption(unittest.TestCase):
    def test_spoken_defaults_to_label(self):
        self.assertEqual(Option(id="a", label="Clean it up").spoken, "Clean it up")

    def test_spoken_uses_say_when_present(self):
        self.assertEqual(
            Option(id="a", label="Clean it up", say="tidy the logs").spoken,
            "tidy the logs",
        )


class TestAction(unittest.TestCase):
    def test_to_dict_shape(self):
        d = Action("select", transcript="two", value="b", confidence=1.0).to_dict()
        self.assertEqual(
            d, {"action": "select", "value": "b", "transcript": "two", "confidence": 1.0}
        )

    def test_confidence_rounded(self):
        d = Action("freeform", transcript="x", confidence=0.33333).to_dict()
        self.assertEqual(d["confidence"], 0.333)


if __name__ == "__main__":
    unittest.main()
