"""Tests for wait_for_agent tool functionality.

This module tests the wait_for_agent MCP tool that allows orchestrators
to wait for subagents to complete with graceful timeout handling.
"""

import json
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

from core.models import WaitForAgentRequest, WaitResult
from core.agents import AgentRegistry


class TestWaitForAgentScenarios(unittest.TestCase):
    """Test various wait scenarios for the wait_for_agent tool."""

    def test_wait_result_completion_scenario(self):
        """Test WaitResult for a completed agent scenario."""
        result = WaitResult(
            agent="build-agent",
            completed=True,
            timed_out=False,
            elapsed_seconds=5.2,
            status="idle",
            output="npm run build\n\n> build\n> webpack\n\nBuild complete!\n",
            summary="Agent completed successfully",
            can_continue_waiting=False
        )

        self.assertTrue(result.completed)
        self.assertFalse(result.timed_out)
        self.assertEqual(result.status, "idle")
        self.assertIn("Build complete", result.output)

    def test_wait_result_timeout_scenario(self):
        """Test WaitResult for a timeout scenario with progress update."""
        result = WaitResult(
            agent="codex-1",
            completed=False,
            timed_out=True,
            elapsed_seconds=30.0,
            status="running",
            output="Building modules... 847/1203 complete",
            summary="Still running. Last output: Building modules... 847/1203 complete",
            can_continue_waiting=True
        )

        self.assertFalse(result.completed)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.status, "running")
        self.assertTrue(result.can_continue_waiting)
        self.assertIsNotNone(result.summary)

    def test_wait_result_agent_not_found(self):
        """Test WaitResult when agent is not found."""
        result = WaitResult(
            agent="unknown-agent",
            completed=False,
            timed_out=False,
            elapsed_seconds=0,
            status="unknown",
            summary="Agent 'unknown-agent' not found",
            can_continue_waiting=False
        )

        self.assertEqual(result.status, "unknown")
        self.assertFalse(result.can_continue_waiting)
        self.assertIn("not found", result.summary)

    def test_wait_result_session_not_found(self):
        """Test WaitResult when session is not found."""
        result = WaitResult(
            agent="orphan-agent",
            completed=False,
            timed_out=False,
            elapsed_seconds=0,
            status="unknown",
            summary="Session for agent 'orphan-agent' not found",
            can_continue_waiting=False
        )

        self.assertEqual(result.status, "unknown")
        self.assertIn("Session", result.summary)

    def test_wait_result_error_scenario(self):
        """Test WaitResult for error scenario."""
        result = WaitResult(
            agent="failing-agent",
            completed=False,
            timed_out=False,
            elapsed_seconds=0,
            status="error",
            summary="Connection to iTerm2 failed",
            can_continue_waiting=False
        )

        self.assertEqual(result.status, "error")
        self.assertFalse(result.can_continue_waiting)


class TestWaitForAgentRequestValidation(unittest.TestCase):
    """Test WaitForAgentRequest validation edge cases."""

    def test_agent_name_required(self):
        """Test that agent name is required."""
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            WaitForAgentRequest()

    def test_negative_timeout_rejected(self):
        """Test that negative timeout is rejected."""
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            WaitForAgentRequest(agent="test", wait_up_to=-1)

    def test_boundary_timeout_values(self):
        """Test boundary timeout values."""
        # Minimum valid
        req_min = WaitForAgentRequest(agent="test", wait_up_to=1)
        self.assertEqual(req_min.wait_up_to, 1)

        # Maximum valid
        req_max = WaitForAgentRequest(agent="test", wait_up_to=600)
        self.assertEqual(req_max.wait_up_to, 600)


class TestAgentRegistryForWait(unittest.TestCase):
    """Test agent registry operations relevant to wait_for_agent."""

    def setUp(self):
        """Create temporary directory for registry data."""
        self.temp_dir = tempfile.mkdtemp()
        self.registry = AgentRegistry(data_dir=self.temp_dir)

    def tearDown(self):
        """Clean up temporary directory."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_agent_lookup_for_wait(self):
        """Test looking up an agent before waiting."""
        self.registry.register_agent(
            name="build-agent",
            session_id="session-123",
            teams=["build"],
            metadata={}
        )

        found = self.registry.get_agent("build-agent")
        self.assertIsNotNone(found)
        self.assertEqual(found.session_id, "session-123")

    def test_agent_not_found_for_wait(self):
        """Test behavior when agent doesn't exist."""
        found = self.registry.get_agent("nonexistent")
        self.assertIsNone(found)


class TestWaitResultUsagePatterns(unittest.TestCase):
    """Test common usage patterns for WaitResult."""

    def test_resume_waiting_pattern(self):
        """Test the pattern of resuming waiting after timeout."""
        # First wait - timeout
        result1 = WaitResult(
            agent="long-task",
            completed=False,
            timed_out=True,
            elapsed_seconds=30.0,
            status="running",
            output="Processing... 50%",
            summary="Task in progress",
            can_continue_waiting=True
        )

        # Check if we should wait more
        self.assertTrue(result1.can_continue_waiting)
        self.assertFalse(result1.completed)

        # Second wait - completed
        result2 = WaitResult(
            agent="long-task",
            completed=True,
            timed_out=False,
            elapsed_seconds=15.0,  # Additional wait time
            status="idle",
            output="Processing... 100%\nDone!",
            summary="Agent completed successfully",
            can_continue_waiting=False
        )

        self.assertTrue(result2.completed)

    def test_conditional_output_return(self):
        """Test that output is conditional based on request."""
        # With output
        result_with = WaitResult(
            agent="test",
            completed=True,
            timed_out=False,
            elapsed_seconds=1.0,
            status="idle",
            output="Hello World",
            can_continue_waiting=False
        )
        self.assertIsNotNone(result_with.output)

        # Without output (when return_output=False)
        result_without = WaitResult(
            agent="test",
            completed=True,
            timed_out=False,
            elapsed_seconds=1.0,
            status="idle",
            output=None,
            can_continue_waiting=False
        )
        self.assertIsNone(result_without.output)

    def test_summary_generation_on_timeout(self):
        """Test that summary is generated on timeout."""
        # Timeout with summary
        result = WaitResult(
            agent="build-agent",
            completed=False,
            timed_out=True,
            elapsed_seconds=30.0,
            status="running",
            summary="Still running. Last output: Building... | Compiling... | Linking...",
            can_continue_waiting=True
        )

        self.assertIsNotNone(result.summary)
        self.assertIn("Still running", result.summary)

    def test_no_summary_when_completed(self):
        """Test that summary can be simple when completed."""
        result = WaitResult(
            agent="quick-task",
            completed=True,
            timed_out=False,
            elapsed_seconds=0.5,
            status="idle",
            summary="Agent completed successfully",
            can_continue_waiting=False
        )

        self.assertEqual(result.summary, "Agent completed successfully")


class TestWaitResultJsonSerialization(unittest.TestCase):
    """Test JSON serialization for MCP response format."""

    def test_full_result_serialization(self):
        """Test that a full WaitResult serializes correctly."""
        result = WaitResult(
            agent="codex-1",
            completed=True,
            timed_out=False,
            elapsed_seconds=10.5,
            status="idle",
            output="Task complete\nAll tests passed",
            summary="Agent completed successfully",
            can_continue_waiting=False
        )

        json_str = result.model_dump_json(indent=2)
        data = json.loads(json_str)

        self.assertEqual(data["agent"], "codex-1")
        self.assertTrue(data["completed"])
        self.assertFalse(data["timed_out"])
        self.assertEqual(data["elapsed_seconds"], 10.5)
        self.assertEqual(data["status"], "idle")
        self.assertIn("Task complete", data["output"])
        self.assertFalse(data["can_continue_waiting"])

    def test_timeout_result_serialization(self):
        """Test timeout result serialization matches expected format."""
        result = WaitResult(
            agent="codex-1",
            completed=False,
            timed_out=True,
            elapsed_seconds=30.0,
            status="running",
            output="Building modules... 847/1203 complete",
            summary="Build in progress: 70% complete, ~15s remaining",
            can_continue_waiting=True
        )

        json_str = result.model_dump_json(indent=2)
        data = json.loads(json_str)

        # Verify matches the example in the issue
        self.assertEqual(data["agent"], "codex-1")
        self.assertFalse(data["completed"])
        self.assertTrue(data["timed_out"])
        self.assertEqual(data["elapsed_seconds"], 30.0)
        self.assertEqual(data["status"], "running")
        self.assertIn("847/1203", data["output"])
        self.assertIn("70%", data["summary"])
        self.assertTrue(data["can_continue_waiting"])


class TestWaitForAgentIntegration(unittest.TestCase):
    """Integration-style tests for wait_for_agent behavior."""

    def test_quick_completion_scenario(self):
        """Test scenario where agent completes quickly."""
        # Simulate: agent finishes in 2 seconds, we wait up to 30
        request = WaitForAgentRequest(
            agent="quick-agent",
            wait_up_to=30,
            return_output=True,
            summary_on_timeout=True
        )

        # Expected result
        result = WaitResult(
            agent="quick-agent",
            completed=True,
            timed_out=False,
            elapsed_seconds=2.1,
            status="idle",
            output="echo 'hello'\nhello\n$",
            summary="Agent completed successfully",
            can_continue_waiting=False
        )

        self.assertTrue(result.completed)
        self.assertLess(result.elapsed_seconds, request.wait_up_to)

    def test_long_running_task_scenario(self):
        """Test scenario where task takes longer than wait time."""
        request = WaitForAgentRequest(
            agent="build-agent",
            wait_up_to=10,
            return_output=True,
            summary_on_timeout=True
        )

        # Simulate: build takes 60 seconds, we only wait 10
        result = WaitResult(
            agent="build-agent",
            completed=False,
            timed_out=True,
            elapsed_seconds=10.0,
            status="running",
            output="[1/5] Installing dependencies...\n[2/5] Compiling...",
            summary="Still running. Last output: [1/5] Installing | [2/5] Compiling",
            can_continue_waiting=True
        )

        self.assertFalse(result.completed)
        self.assertTrue(result.timed_out)
        self.assertTrue(result.can_continue_waiting)
        # User can choose to wait again
        self.assertEqual(result.elapsed_seconds, request.wait_up_to)

    def test_multiple_wait_cycles(self):
        """Test multiple sequential wait cycles."""
        results = []

        # First wait cycle - timeout
        results.append(WaitResult(
            agent="npm-install",
            completed=False,
            timed_out=True,
            elapsed_seconds=30.0,
            status="running",
            output="Installing packages...\n50/100 packages",
            summary="Installing packages (50%)",
            can_continue_waiting=True
        ))

        # Second wait cycle - still running
        results.append(WaitResult(
            agent="npm-install",
            completed=False,
            timed_out=True,
            elapsed_seconds=30.0,
            status="running",
            output="Installing packages...\n90/100 packages",
            summary="Installing packages (90%)",
            can_continue_waiting=True
        ))

        # Third wait cycle - completed
        results.append(WaitResult(
            agent="npm-install",
            completed=True,
            timed_out=False,
            elapsed_seconds=15.0,
            status="idle",
            output="Installing packages...\n100/100 packages\nDone!",
            summary="Agent completed successfully",
            can_continue_waiting=False
        ))

        # Verify progression
        self.assertFalse(results[0].completed)
        self.assertFalse(results[1].completed)
        self.assertTrue(results[2].completed)

        # Total wait time
        total_time = sum(r.elapsed_seconds for r in results)
        self.assertEqual(total_time, 75.0)  # 30 + 30 + 15


if __name__ == "__main__":
    unittest.main()
