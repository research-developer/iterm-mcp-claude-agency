"""Tests for the utterance->action classifier."""
import unittest

from core.voice.match import classify
from core.voice.models import Option

OPTS = [
    Option(id="clean", label="Clean it up"),
    Option(id="ship", label="Ship as is"),
    Option(id="revert", label="Revert the change"),
]


class TestClassify(unittest.TestCase):
    def test_empty_is_nomatch(self):
        self.assertEqual(classify("", OPTS).action, "nomatch")
        self.assertEqual(classify("   ", OPTS).action, "nomatch")

    def test_digit_selects(self):
        a = classify("2", OPTS)
        self.assertEqual((a.action, a.value), ("select", "ship"))

    def test_ordinal_word_selects(self):
        self.assertEqual(classify("the first one", OPTS).value, "clean")
        self.assertEqual(classify("option three", OPTS).value, "revert")

    def test_out_of_range_number_is_not_select(self):
        self.assertNotEqual(classify("9", OPTS).action, "select")

    def test_keyword_selects(self):
        a = classify("let's revert the change", OPTS)
        self.assertEqual((a.action, a.value), ("select", "revert"))

    def test_repeat(self):
        self.assertEqual(classify("can you repeat that", OPTS).action, "repeat")
        self.assertEqual(classify("say again", OPTS).action, "repeat")

    def test_regenerate_captures_direction(self):
        a = classify("none of these, something about tests", OPTS)
        self.assertEqual(a.action, "regenerate")
        self.assertIn("tests", a.value)

    def test_drilldown(self):
        a = classify("go deeper on clean it up", OPTS)
        self.assertEqual(a.action, "drilldown")
        self.assertEqual(a.value, "clean")

    def test_freeform_fallback(self):
        a = classify("my name is preston", OPTS)
        self.assertEqual(a.action, "freeform")


if __name__ == "__main__":
    unittest.main()
