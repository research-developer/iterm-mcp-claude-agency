"""Unit tests for auto-derived daemon version (major.minor + git commit count).

Pure-logic tests: importing iterm_mcpy.daemon pulls in only the standard
library, so running this module never touches iTerm2. The staleness handshake
that relies on package_version() is exercised via freezing behavior below.
"""

import re
import unittest
from unittest import mock

from iterm_mcpy import daemon


class PackageVersionTests(unittest.TestCase):
    def setUp(self):
        daemon.package_version.cache_clear()

    def tearDown(self):
        daemon.package_version.cache_clear()

    def test_version_is_base_plus_commit_count(self):
        with mock.patch.object(daemon, "_base_version", return_value="0.1"), \
             mock.patch.object(daemon, "_commit_count", return_value="263"):
            self.assertEqual(daemon.package_version(), "0.1.263")

    def test_real_version_looks_like_semver(self):
        # In this repo git is present, so the live value must be x.y.z.
        v = daemon.package_version()
        self.assertRegex(v, r"^\d+\.\d+\.\d+")

    def test_frozen_per_process_after_first_call(self):
        # lru_cache freezes the value: the daemon must keep reporting the code
        # it started with even after the checkout advances underneath it.
        with mock.patch.object(daemon, "_base_version", return_value="0.1"), \
             mock.patch.object(daemon, "_commit_count",
                               side_effect=["100", "200"]) as counter:
            first = daemon.package_version()
            second = daemon.package_version()
        self.assertEqual(first, "0.1.100")
        self.assertEqual(second, "0.1.100")  # cached, not recomputed to 200
        self.assertEqual(counter.call_count, 1)

    def test_falls_back_to_metadata_without_git(self):
        # No git (e.g. installed wheel): degrade to a static version rather
        # than crash. Both daemon and shim degrade identically, so the
        # equality handshake still holds.
        with mock.patch.object(daemon, "_commit_count", return_value=None):
            v = daemon.package_version()
        self.assertTrue(re.match(r"^\d+\.\d+", v) or v.endswith("+dev"))


if __name__ == "__main__":
    unittest.main()
