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


class TestGamepadJavaScript(unittest.TestCase):
    """The browser-side mapper in static/gamepad.js passes its node tests."""

    @unittest.skipIf(shutil.which("node") is None, "node is not installed")
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
