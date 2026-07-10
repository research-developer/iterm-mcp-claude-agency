"""Run the node-level gamepad mapper tests as part of the Python suite.

Headless: shells out to `node --test`; skips cleanly when node is not
installed so CI environments without a JS runtime are unaffected.
"""

import os
import shutil
import subprocess
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
JS_TEST_FILE = os.path.join(PROJECT_ROOT, "tests", "js", "test_gamepad.mjs")


def _node_supports_test_runner() -> bool:
    """Return True if a node with the built-in test runner (18+) is on PATH."""
    if shutil.which("node") is None:
        return False
    try:
        version = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        major = int(version.lstrip("v").split(".", 1)[0])
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return major >= 18


class TestGamepadJavaScript(unittest.TestCase):
    """The browser-side mapper in static/gamepad.js passes its node tests."""

    @unittest.skipUnless(
        _node_supports_test_runner(), "node 18+ (built-in test runner) not available"
    )
    def test_node_gamepad_suite_passes(self):
        result = subprocess.run(
            ["node", "--test", JS_TEST_FILE],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )
        self.assertEqual(
            result.returncode,
            0,
            "node --test failed:\n" + result.stdout + result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
