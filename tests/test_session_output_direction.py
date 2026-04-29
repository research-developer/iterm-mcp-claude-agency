"""Regression coverage for fb-20260424-157473f7 item #3.

`session.get_screen_contents(max_lines=N)` used to slice from the TOP of
the buffer (`for i in range(max_lines)`). For monitoring long-running
sessions the caller almost always wants the tail. This module verifies
the new tail-by-default behavior and the opt-out via `from_end=False`.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from core.session import ItermSession


def _fake_contents(line_strings):
    """Build a fake iterm2 contents object that yields the given lines."""
    contents = MagicMock()
    contents.number_of_lines = len(line_strings)

    def line(i):
        line_obj = MagicMock()
        line_obj.string = line_strings[i]
        return line_obj

    contents.line.side_effect = line
    return contents


def _session_with_buffer(line_strings):
    """Build an ItermSession wrapper around a fake iterm2 session whose
    `async_get_screen_contents` returns the given lines."""
    fake_iterm2_session = MagicMock(spec=["name", "async_set_name", "session_id",
                                          "async_get_screen_contents"])
    fake_iterm2_session.session_id = "fake-id"
    fake_iterm2_session.name = "fake"
    fake_iterm2_session.async_set_name = AsyncMock()
    fake_iterm2_session.async_get_screen_contents = AsyncMock(
        return_value=_fake_contents(line_strings)
    )
    return ItermSession(session=fake_iterm2_session, name="fake", max_lines=50)


class TestGetScreenContentsDirection(unittest.TestCase):
    """Tail vs. top slicing for `get_screen_contents`."""

    def setUp(self):
        # 20 lines of known content; max_lines=5 lets us see which 5.
        self.lines = [f"line-{i:02d}" for i in range(20)]

    def test_default_returns_tail(self):
        sess = _session_with_buffer(self.lines)
        output = asyncio.run(sess.get_screen_contents(max_lines=5))
        # The TAIL: lines 15..19 inclusive.
        self.assertEqual(
            output.split("\n"),
            ["line-15", "line-16", "line-17", "line-18", "line-19"],
        )

    def test_from_end_false_returns_top_legacy_behavior(self):
        sess = _session_with_buffer(self.lines)
        output = asyncio.run(sess.get_screen_contents(max_lines=5, from_end=False))
        self.assertEqual(
            output.split("\n"),
            ["line-00", "line-01", "line-02", "line-03", "line-04"],
        )

    def test_max_lines_larger_than_buffer_returns_full_buffer(self):
        sess = _session_with_buffer(self.lines)
        output = asyncio.run(sess.get_screen_contents(max_lines=100))
        self.assertEqual(len(output.split("\n")), 20)
        # Tail of 20 from a 20-line buffer = full buffer.
        self.assertEqual(output.split("\n")[0], "line-00")
        self.assertEqual(output.split("\n")[-1], "line-19")

    def test_default_max_lines_uses_session_default(self):
        sess = _session_with_buffer(self.lines)
        sess._max_lines = 3
        output = asyncio.run(sess.get_screen_contents())
        # No max_lines arg → uses session default of 3 → tail of 3.
        self.assertEqual(
            output.split("\n"),
            ["line-17", "line-18", "line-19"],
        )

    def test_empty_lines_are_skipped(self):
        # Existing get_screen_contents skips empty-string lines (preserving
        # current behavior). Verify the tail slice respects that.
        lines = ["line-0", "", "line-2", "", "line-4", "line-5"]
        sess = _session_with_buffer(lines)
        output = asyncio.run(sess.get_screen_contents(max_lines=3))
        # Tail of 3 of the buffer = lines 3, 4, 5 = ['', 'line-4', 'line-5']
        # After empty skip = ['line-4', 'line-5'].
        self.assertEqual(output.split("\n"), ["line-4", "line-5"])


if __name__ == "__main__":
    unittest.main()
