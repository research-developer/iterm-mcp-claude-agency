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
        self.assertEqual(classify("the second one", OPTS).value, "ship")

    def test_out_of_range_number_is_not_select(self):
        self.assertNotEqual(classify("9", OPTS).action, "select")

    def test_keyword_selects(self):
        a = classify("let's revert the change", OPTS)
        self.assertEqual((a.action, a.value), ("select", "revert"))

    def test_trailing_noun_does_not_select_option_one(self):
        # "the <thing> one" must match by keyword, never default to option 1.
        opts = [Option("a", "Apple"), Option("b", "Banana"), Option("c", "Cherry")]
        a = classify("the banana one", opts)
        self.assertEqual((a.action, a.value), ("select", "b"))

    def test_non_leading_ordinal_does_not_mis_select(self):
        # "third" appears mid-phrase with no keyword match -> not a confident select-1.
        a = classify("give me the green one", OPTS)
        self.assertNotEqual(a.value, "clean")

    def test_spoken_text_matches(self):
        opts = [Option("log", "Tidy logs", say="clean up the log files"),
                Option("ship", "Ship as is")]
        a = classify("clean up the log files", opts)
        self.assertEqual((a.action, a.value), ("select", "log"))

    def test_repeat(self):
        self.assertEqual(classify("can you repeat that", OPTS).action, "repeat")
        self.assertEqual(classify("say again", OPTS).action, "repeat")

    def test_regenerate_captures_direction(self):
        a = classify("none of these, something about tests", OPTS)
        self.assertEqual(a.action, "regenerate")
        self.assertIn("tests", a.value)

    def test_bare_regenerate_has_no_direction(self):
        a = classify("none of these", OPTS)
        self.assertEqual(a.action, "regenerate")
        self.assertIsNone(a.value)

    def test_regenerate_word_boundary(self):
        # bare "none" inside ordinary speech must NOT trigger regenerate.
        self.assertNotEqual(classify("I want none of it gone", OPTS).action, "regenerate")

    def test_drilldown(self):
        a = classify("go deeper on clean it up", OPTS)
        self.assertEqual(a.action, "drilldown")
        self.assertEqual(a.value, "clean")

    def test_drilldown_no_target(self):
        a = classify("go deeper on the quantum widget", OPTS)
        self.assertEqual(a.action, "drilldown")
        self.assertIsNone(a.value)

    def test_drilldown_word_boundary(self):
        # "deeper" as an ordinary adjective must NOT trigger drilldown.
        self.assertNotEqual(classify("revert for deeper meaning", OPTS).action, "drilldown")

    def test_freeform_fallback(self):
        a = classify("my name is preston", OPTS)
        self.assertEqual(a.action, "freeform")

    def test_empty_options_does_not_crash(self):
        self.assertEqual(classify("go deeper now", []).action, "drilldown")
        self.assertEqual(classify("anything at all", []).action, "freeform")


if __name__ == "__main__":
    unittest.main()
