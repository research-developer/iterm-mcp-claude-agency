"""Tests for scripts/download_samples.py argument parsing.

These tests verify that the argparse interface works correctly without
making any network calls or requiring iTerm2 to be running.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Add the scripts directory to the path so we can import the module
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import download_samples  # noqa: E402 (import after sys.path manipulation)


class TestArgParsing(unittest.TestCase):
    """Test that argparse accepts the expected flags and applies defaults."""

    def _parse(self, *args):
        """Helper to parse args without sys.argv side-effects."""
        with patch("sys.argv", ["download_samples.py", *args]):
            return download_samples.parse_args()

    def test_defaults(self):
        """All defaults are applied when no flags are passed."""
        args = self._parse()
        self.assertEqual(args.out_dir, download_samples.DEFAULT_OUTPUT_DIR)
        self.assertEqual(args.timeout, download_samples.DEFAULT_TIMEOUT)
        self.assertEqual(args.samples_json, download_samples.DEFAULT_SAMPLES_JSON)
        self.assertEqual(args.user_agent, download_samples.DEFAULT_USER_AGENT)
        self.assertFalse(args.extract)

    def test_out_dir(self):
        """--out-dir accepts a custom path."""
        args = self._parse("--out-dir", "/tmp/test_samples")
        self.assertEqual(args.out_dir, Path("/tmp/test_samples"))

    def test_timeout(self):
        """--timeout accepts an integer value."""
        args = self._parse("--timeout", "10")
        self.assertEqual(args.timeout, 10)

    def test_samples_json(self):
        """--samples-json accepts a path to the index file."""
        samples_path = str(SCRIPTS_DIR / "it2api_samples.json")
        args = self._parse("--samples-json", samples_path)
        self.assertEqual(args.samples_json, Path(samples_path))

    def test_user_agent(self):
        """--user-agent accepts a custom string."""
        args = self._parse("--user-agent", "test-agent/2.0")
        self.assertEqual(args.user_agent, "test-agent/2.0")

    def test_extract_flag(self):
        """--extract enables archive extraction."""
        args = self._parse("--extract")
        self.assertTrue(args.extract)

    def test_all_flags_combined(self):
        """All flags can be passed together without conflict."""
        samples_path = str(SCRIPTS_DIR / "it2api_samples.json")
        args = self._parse(
            "--out-dir", "/tmp/test",
            "--timeout", "10",
            "--samples-json", samples_path,
            "--user-agent", "custom/1.0",
            "--extract",
        )
        self.assertEqual(args.out_dir, Path("/tmp/test"))
        self.assertEqual(args.timeout, 10)
        self.assertEqual(args.samples_json, Path(samples_path))
        self.assertEqual(args.user_agent, "custom/1.0")
        self.assertTrue(args.extract)

    def test_default_samples_json_exists(self):
        """The default samples JSON file exists on disk."""
        self.assertTrue(
            download_samples.DEFAULT_SAMPLES_JSON.exists(),
            f"Expected {download_samples.DEFAULT_SAMPLES_JSON} to exist",
        )

    def test_default_samples_json_is_valid_json(self):
        """The default samples JSON file is valid JSON with string values."""
        import json
        with open(download_samples.DEFAULT_SAMPLES_JSON) as f:
            data = json.load(f)
        self.assertIsInstance(data, dict)
        self.assertGreater(len(data), 0)
        for key, value in data.items():
            self.assertIsInstance(key, str)
            self.assertIsInstance(value, str)
            self.assertTrue(value.startswith("http"), f"URL should start with http: {value}")


class TestExtractScriptLink(unittest.TestCase):
    """Test the HTML link extraction helper."""

    def test_its_link_absolute(self):
        """Finds an absolute .its link in HTML."""
        html = '<a href="https://example.com/foo/bar.its">Download</a>'
        result = download_samples.extract_script_link(html, "https://example.com/page.html")
        self.assertEqual(result, "https://example.com/foo/bar.its")

    def test_py_link_absolute(self):
        """Finds an absolute .py link in HTML."""
        html = '<a href="https://example.com/script.py">Script</a>'
        result = download_samples.extract_script_link(html, "https://example.com/page.html")
        self.assertEqual(result, "https://example.com/script.py")

    def test_relative_link_resolved(self):
        """Relative .its links are resolved against the base URL."""
        html = '<a href="relative/sample.its">Download</a>'
        result = download_samples.extract_script_link(html, "https://iterm2.com/docs/page.html")
        self.assertEqual(result, "https://iterm2.com/docs/relative/sample.its")

    def test_no_link_returns_none(self):
        """Returns None when no .its or .py link is found."""
        html = "<p>No links here</p>"
        result = download_samples.extract_script_link(html, "https://example.com/")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
