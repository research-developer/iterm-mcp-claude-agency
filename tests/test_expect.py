"""Tests for expect-style pattern matching functionality."""

import asyncio
import re
import tempfile
import shutil
import time
import unittest

import iterm2

from core.terminal import ItermTerminal
from core.session import (
    ExpectResult,
    ExpectTimeout,
    ExpectError,
    ExpectTimeoutError,
)
from tests.live_iterm_base import LiveItermTestCase


class TestExpectPatternMatching(LiveItermTestCase):
    """Test expect-style pattern matching functionality (live iTerm2 required)."""

    async def async_setup(self):
        """Set up the test environment."""
        await super().async_setup()

        # Create a tagged test window (auto-closed by run_async_test teardown).
        self.test_session = await self.create_tagged_window(name="ExpectTestSession")
        # Wait for window to be ready
        await asyncio.sleep(1)

    async def async_teardown(self):
        """Stop monitoring before base class teardown."""
        if hasattr(self, "test_session"):
            if self.test_session.is_monitoring:
                await self.test_session.stop_monitoring()
        await super().async_teardown()

    def test_expect_basic_pattern_match(self):
        """Test basic pattern matching with expect()."""
        async def test_impl():
            # Use a unique marker to avoid matching old output
            unique_marker = f"EXPECT_TEST_{time.time()}"

            # Send a command that outputs our marker
            await self.test_session.send_text(f"echo '{unique_marker}'")

            # Wait for the marker to appear
            result = await self.test_session.expect(
                [unique_marker, ExpectTimeout(10)],
                timeout=10
            )

            # Verify we matched the marker, not timeout
            self.assertEqual(result.match_index, 0)
            self.assertIn(unique_marker, result.output)
            self.assertIn(unique_marker, result.matched_text)

        self.run_async_test(test_impl)

    def test_expect_multiple_patterns(self):
        """Test expect() with multiple patterns."""
        async def test_impl():
            unique_id = f"MULTI_{time.time()}"

            # Send a command that will match the second pattern
            await self.test_session.send_text(f"echo 'ERROR_{unique_id}'")

            # Expect with multiple patterns
            result = await self.test_session.expect(
                [
                    f'SUCCESS_{unique_id}',  # Pattern 0 - won't match
                    f'ERROR_{unique_id}',    # Pattern 1 - will match
                    f'WARNING_{unique_id}',  # Pattern 2 - won't match
                    ExpectTimeout(10)
                ],
                timeout=10
            )

            # Verify we matched pattern 1 (ERROR)
            self.assertEqual(result.match_index, 1)
            self.assertIn(f'ERROR_{unique_id}', result.matched_text)

        self.run_async_test(test_impl)

    def test_expect_compiled_regex(self):
        """Test expect() with compiled regex patterns."""
        async def test_impl():
            unique_id = f"REGEX_{time.time()}"

            # Send a command
            await self.test_session.send_text(f"echo 'Value: {unique_id}'")

            # Use a compiled regex pattern
            pattern = re.compile(r'Value:\s+REGEX_\d+\.\d+')

            result = await self.test_session.expect(
                [pattern, ExpectTimeout(10)],
                timeout=10
            )

            # Verify match
            self.assertEqual(result.match_index, 0)
            self.assertIsNotNone(result.match)  # Should have regex match object

        self.run_async_test(test_impl)

    def test_expect_timeout_marker(self):
        """Test that ExpectTimeout marker returns result instead of raising."""
        async def test_impl():
            # Use a pattern that won't match
            impossible_pattern = f"IMPOSSIBLE_PATTERN_{time.time()}_NEVER_MATCH"

            # Short timeout with ExpectTimeout marker
            result = await self.test_session.expect(
                [impossible_pattern, ExpectTimeout(2)],
                timeout=2
            )

            # Should return timeout marker (index 1), not raise
            self.assertEqual(result.match_index, 1)
            self.assertIsInstance(result.matched_pattern, ExpectTimeout)
            self.assertEqual(result.matched_text, "")

        self.run_async_test(test_impl)

    def test_expect_timeout_raises(self):
        """Test that expect() raises ExpectTimeoutError without ExpectTimeout marker."""
        async def test_impl():
            # Use a pattern that won't match, no timeout marker
            impossible_pattern = f"IMPOSSIBLE_{time.time()}"

            with self.assertRaises(ExpectTimeoutError) as context:
                await self.test_session.expect(
                    [impossible_pattern],
                    timeout=2
                )

            # Check error details
            self.assertEqual(context.exception.timeout, 2)
            self.assertIn(impossible_pattern, str(context.exception))

        self.run_async_test(test_impl)

    def test_expect_result_before_text(self):
        """Test that ExpectResult.before contains text before the match."""
        async def test_impl():
            unique_id = f"BEFORE_{time.time()}"

            # Send commands that create predictable output
            await self.test_session.send_text(f"echo 'PREFIX_{unique_id}'")
            await asyncio.sleep(0.5)
            await self.test_session.send_text(f"echo 'TARGET_{unique_id}'")

            # Wait for TARGET
            result = await self.test_session.expect(
                [f'TARGET_{unique_id}', ExpectTimeout(10)],
                timeout=10
            )

            # Verify match and before text
            self.assertEqual(result.match_index, 0)
            # The 'before' should contain PREFIX
            self.assertIn(f'PREFIX_{unique_id}', result.before)

        self.run_async_test(test_impl)

    def test_wait_for_prompt(self):
        """Test wait_for_prompt() helper method."""
        async def test_impl():
            # Send a simple command that will complete quickly
            await self.test_session.send_text("echo 'done'")

            # Wait for prompt
            result = await self.test_session.wait_for_prompt(timeout=10)

            # Should detect prompt (returns True)
            self.assertTrue(result)

        self.run_async_test(test_impl)

    def test_wait_for_prompt_timeout(self):
        """Test wait_for_prompt() returns False on timeout."""
        async def test_impl():
            # Start a long-running command
            await self.test_session.send_text("sleep 10")

            # Wait for prompt with short timeout
            result = await self.test_session.wait_for_prompt(timeout=2)

            # Should timeout and return False
            self.assertFalse(result)

            # Clean up - send Ctrl+C to stop the sleep
            await self.test_session.send_control_character('c')
            await asyncio.sleep(0.5)

        self.run_async_test(test_impl)

    def test_wait_for_patterns_success(self):
        """Test wait_for_patterns() with success pattern."""
        async def test_impl():
            unique_id = f"SUCCESS_{time.time()}"

            # Send command that produces success output
            await self.test_session.send_text(f"echo 'Operation completed successfully {unique_id}'")

            is_success, result = await self.test_session.wait_for_patterns(
                success_patterns=[f'successfully {unique_id}'],
                error_patterns=['failed', 'error'],
                timeout=10
            )

            self.assertTrue(is_success)
            self.assertEqual(result.match_index, 0)

        self.run_async_test(test_impl)

    def test_wait_for_patterns_error(self):
        """Test wait_for_patterns() with error pattern."""
        async def test_impl():
            unique_id = f"ERROR_{time.time()}"

            # Send command that produces error output
            await self.test_session.send_text(f"echo 'Operation failed with error {unique_id}'")

            is_success, result = await self.test_session.wait_for_patterns(
                success_patterns=['success'],
                error_patterns=[f'error {unique_id}'],
                timeout=10
            )

            self.assertFalse(is_success)
            self.assertEqual(result.match_index, 1)  # Error pattern is after success patterns

        self.run_async_test(test_impl)

    def test_send_and_expect(self):
        """Test send_and_expect() convenience method."""
        async def test_impl():
            unique_id = f"SENDEXPECT_{time.time()}"

            result = await self.test_session.send_and_expect(
                f"echo 'Hello {unique_id}'",
                [f'{unique_id}', ExpectTimeout(10)],
                timeout=10
            )

            self.assertEqual(result.match_index, 0)
            self.assertIn(unique_id, result.output)

        self.run_async_test(test_impl)

    def test_expect_empty_patterns_raises(self):
        """Test that expect() raises ValueError for empty patterns list."""
        async def test_impl():
            with self.assertRaises(ValueError) as context:
                await self.test_session.expect([])

            self.assertIn("empty", str(context.exception).lower())

        self.run_async_test(test_impl)

    def test_expect_only_timeout_raises(self):
        """Test that expect() raises ValueError for only timeout marker."""
        async def test_impl():
            with self.assertRaises(ValueError) as context:
                await self.test_session.expect([ExpectTimeout(10)])

            self.assertIn("regex pattern", str(context.exception).lower())

        self.run_async_test(test_impl)

    def test_expect_invalid_regex_raises(self):
        """Test that expect() raises ValueError for invalid regex."""
        async def test_impl():
            with self.assertRaises(ValueError) as context:
                await self.test_session.expect(['[invalid regex'])

            self.assertIn("invalid regex", str(context.exception).lower())

        self.run_async_test(test_impl)

    def test_expect_result_repr(self):
        """Test ExpectResult string representation."""
        result = ExpectResult(
            matched_pattern=re.compile(r'test'),
            match_index=0,
            output="test output",
            matched_text="test",
        )

        repr_str = repr(result)
        self.assertIn("ExpectResult", repr_str)
        self.assertIn("test", repr_str)
        self.assertIn("index=0", repr_str)

    def test_expect_timeout_repr(self):
        """Test ExpectTimeout string representation."""
        timeout = ExpectTimeout(30)
        self.assertEqual(repr(timeout), "ExpectTimeout(30)")

    def test_expect_poll_interval(self):
        """Test expect() with custom poll interval."""
        async def test_impl():
            unique_id = f"POLL_{time.time()}"

            # Send command
            await self.test_session.send_text(f"echo '{unique_id}'")

            # Use faster polling
            start_time = time.time()
            result = await self.test_session.expect(
                [unique_id, ExpectTimeout(10)],
                poll_interval=0.05,  # 50ms polling
                timeout=10
            )
            elapsed = time.time() - start_time

            self.assertEqual(result.match_index, 0)
            # Should be faster than default polling
            self.assertLess(elapsed, 5)

        self.run_async_test(test_impl)

    def test_expect_search_window_lines(self):
        """Test expect() with custom search window."""
        async def test_impl():
            unique_id = f"LINES_{time.time()}"

            # Send command
            await self.test_session.send_text(f"echo '{unique_id}'")

            # Search with limited lines
            result = await self.test_session.expect(
                [unique_id, ExpectTimeout(10)],
                search_window_lines=100,
                timeout=10
            )

            self.assertEqual(result.match_index, 0)

        self.run_async_test(test_impl)


class TestExpectResultDataclass(unittest.TestCase):
    """Unit tests for ExpectResult dataclass (no iTerm2 required)."""

    def test_expect_result_creation(self):
        """Test creating ExpectResult instances."""
        result = ExpectResult(
            matched_pattern="test.*pattern",
            match_index=0,
            output="This is test pattern output",
            matched_text="test pattern"
        )

        self.assertEqual(result.matched_pattern, "test.*pattern")
        self.assertEqual(result.match_index, 0)
        self.assertEqual(result.output, "This is test pattern output")
        self.assertEqual(result.matched_text, "test pattern")
        self.assertEqual(result.before, "")  # Default value
        self.assertIsNone(result.match)  # Default value

    def test_expect_result_with_all_fields(self):
        """Test ExpectResult with all fields populated."""
        pattern = re.compile(r'test')
        match = pattern.search("test output")

        result = ExpectResult(
            matched_pattern=pattern,
            match_index=0,
            output="test output",
            matched_text="test",
            before="",
            match=match
        )

        self.assertEqual(result.matched_pattern, pattern)
        self.assertIsNotNone(result.match)
        self.assertEqual(result.match.group(0), "test")


class TestExpectTimeoutMarker(unittest.TestCase):
    """Unit tests for ExpectTimeout class (no iTerm2 required)."""

    def test_expect_timeout_default(self):
        """Test ExpectTimeout with default timeout."""
        timeout = ExpectTimeout()
        self.assertEqual(timeout.seconds, 30)

    def test_expect_timeout_custom(self):
        """Test ExpectTimeout with custom timeout."""
        timeout = ExpectTimeout(60)
        self.assertEqual(timeout.seconds, 60)

    def test_expect_timeout_repr(self):
        """Test ExpectTimeout repr."""
        timeout = ExpectTimeout(15)
        self.assertEqual(repr(timeout), "ExpectTimeout(15)")


class TestExpectExceptions(unittest.TestCase):
    """Unit tests for expect exceptions (no iTerm2 required)."""

    def test_expect_timeout_error(self):
        """Test ExpectTimeoutError creation and message."""
        patterns = [r'pattern1', r'pattern2']
        error = ExpectTimeoutError(
            timeout=30,
            patterns=patterns,
            output="some output"
        )

        self.assertEqual(error.timeout, 30)
        self.assertEqual(error.patterns, patterns)
        self.assertEqual(error.output, "some output")
        self.assertIn("30s", str(error))
        self.assertIn("pattern1", str(error))

    def test_expect_timeout_error_with_compiled_pattern(self):
        """Test ExpectTimeoutError with compiled regex patterns."""
        patterns = [re.compile(r'test')]
        error = ExpectTimeoutError(
            timeout=10,
            patterns=patterns,
            output=""
        )

        self.assertIn("test", str(error))

    def test_expect_error_base(self):
        """Test ExpectError base exception."""
        error = ExpectError("Test error message")
        self.assertEqual(str(error), "Test error message")


if __name__ == "__main__":
    unittest.main()
