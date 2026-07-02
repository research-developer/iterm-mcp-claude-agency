"""Unit tests for auto-derived daemon version and the staleness handshake.

Pure-logic tests: importing iterm_mcpy.daemon pulls in only the standard
library, so running this module never touches iTerm2.
"""

import re
import unittest
from unittest import mock

from iterm_mcpy import daemon


class PackageVersionTests(unittest.TestCase):
    def setUp(self):
        daemon._resolve_version.cache_clear()

    def tearDown(self):
        daemon._resolve_version.cache_clear()

    def test_version_is_base_plus_commit_count(self):
        with mock.patch.object(daemon, "_base_version", return_value="0.1"), \
             mock.patch.object(daemon, "_commit_count", return_value="263"):
            self.assertEqual(daemon.package_version(), "0.1.263")
            self.assertEqual(daemon.version_source(), "git")

    def test_real_version_looks_like_semver(self):
        # In this repo git is present, so the live value must be x.y.z.
        self.assertRegex(daemon.package_version(), r"^\d+\.\d+\.\d+")

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
        # than crash, and report the "metadata" source.
        with mock.patch.object(daemon, "_commit_count", return_value=None):
            v = daemon.package_version()
            self.assertEqual(daemon.version_source(), "metadata")
        self.assertTrue(re.match(r"^\d+\.\d+", v) or v.endswith("+dev"))


class IsStaleTests(unittest.TestCase):
    """A daemon is 'stale' only on a confident git-vs-git version mismatch."""

    def _patch_local(self, version, source):
        return (
            mock.patch.object(daemon, "package_version", return_value=version),
            mock.patch.object(daemon, "version_source", return_value=source),
        )

    def test_matching_version_is_never_stale(self):
        for src in ("git", "metadata", None):
            with self.subTest(src=src):
                pv, vs = self._patch_local("0.1.265", "git")
                with pv, vs:
                    self.assertFalse(daemon.is_stale("0.1.265", src))

    def test_git_vs_git_mismatch_is_stale(self):
        pv, vs = self._patch_local("0.1.265", "git")
        with pv, vs:
            self.assertTrue(daemon.is_stale("0.1.264", "git"))

    def test_local_metadata_never_triggers_restart(self):
        # Our side can't run git -> a mismatch is not trustworthy.
        pv, vs = self._patch_local("0.1.0", "metadata")
        with pv, vs:
            self.assertFalse(daemon.is_stale("0.1.265", "git"))

    def test_remote_metadata_never_triggers_restart(self):
        # The daemon's env lacks git (e.g. Desktop from Finder) -> don't
        # thrash the shared singleton just because we happen to have git.
        pv, vs = self._patch_local("0.1.265", "git")
        with pv, vs:
            self.assertFalse(daemon.is_stale("0.1.0", "metadata"))

    def test_old_daemon_without_source_field_is_not_confidently_stale(self):
        # Pre-upgrade daemons report no version_source; treat as not-stale so
        # a git-lacking client can't kill them. (Replaced on manual restart.)
        pv, vs = self._patch_local("0.1.265", "git")
        with pv, vs:
            self.assertFalse(daemon.is_stale("0.1.0", None))


if __name__ == "__main__":
    unittest.main()
