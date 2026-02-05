"""
Termination Conditions for Session Lifecycle Control.

This module implements composable termination conditions inspired by AutoGen/AG2 patterns.
See: research/RESEARCH_SYNTHESIS_ADDENDUM.md for full design rationale.

SUGGESTED IMPLEMENTATION - Code review comments inline.

Example usage:
    ```python
    # Composable conditions with | (OR) and & (AND)
    condition = MaxMessages(100) | TextMention("DONE") | Timeout(300)

    # Apply to a workflow
    await run_workflow(termination=condition)
    ```
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Union
import re
import asyncio


class TerminationCondition(ABC):
    """
    Base class for termination conditions.

    # REVIEW: Consider adding priority levels for condition evaluation order.
    # REVIEW: Should we support async check() for conditions that need I/O?
    """

    @abstractmethod
    async def check(self, context: Dict[str, Any]) -> bool:
        """
        Check if termination condition is met.

        Args:
            context: Dictionary containing:
                - message_count: int - Number of messages sent
                - last_output: str - Most recent output
                - elapsed_seconds: float - Time since start
                - session_id: str - Session identifier
                - agent: Optional[str] - Agent name

        Returns:
            True if termination condition is met.
        """
        pass

    def __or__(self, other: "TerminationCondition") -> "OrCondition":
        """Combine with OR logic: either condition triggers termination."""
        return OrCondition(self, other)

    def __and__(self, other: "TerminationCondition") -> "AndCondition":
        """Combine with AND logic: both conditions must be met."""
        return AndCondition(self, other)


class OrCondition(TerminationCondition):
    """Composite condition: terminates if ANY child condition is met."""

    def __init__(self, *conditions: TerminationCondition):
        self.conditions = conditions

    async def check(self, context: Dict[str, Any]) -> bool:
        # REVIEW: Consider parallel evaluation with asyncio.gather for efficiency
        for condition in self.conditions:
            if await condition.check(context):
                return True
        return False


class AndCondition(TerminationCondition):
    """Composite condition: terminates only if ALL child conditions are met."""

    def __init__(self, *conditions: TerminationCondition):
        self.conditions = conditions

    async def check(self, context: Dict[str, Any]) -> bool:
        for condition in self.conditions:
            if not await condition.check(context):
                return False
        return True


class MaxMessages(TerminationCondition):
    """
    Terminate after a maximum number of messages.

    # REVIEW: Should this count input messages, output messages, or both?
    # SUGGESTION: Add message_type filter parameter
    """

    def __init__(self, max_count: int):
        if max_count <= 0:
            raise ValueError("max_count must be positive")
        self.max_count = max_count

    async def check(self, context: Dict[str, Any]) -> bool:
        return context.get("message_count", 0) >= self.max_count


class TextMention(TerminationCondition):
    """
    Terminate when specific text appears in output.

    # REVIEW: Should this be case-sensitive by default?
    # SUGGESTION: Add case_sensitive parameter
    """

    def __init__(self, text: str, case_sensitive: bool = True):
        self.text = text
        self.case_sensitive = case_sensitive

    async def check(self, context: Dict[str, Any]) -> bool:
        output = context.get("last_output", "")
        if not self.case_sensitive:
            return self.text.lower() in output.lower()
        return self.text in output


class Timeout(TerminationCondition):
    """
    Terminate after a specified duration.

    # REVIEW: Consider adding a warning callback before timeout
    # SUGGESTION: Add warning_at parameter for pre-timeout notification
    """

    def __init__(self, seconds: int, warning_at: Optional[int] = None):
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        self.seconds = seconds
        self.warning_at = warning_at

    async def check(self, context: Dict[str, Any]) -> bool:
        elapsed = context.get("elapsed_seconds", 0)
        return elapsed >= self.seconds


class OutputPattern(TerminationCondition):
    """
    Terminate when output matches a regex pattern.

    # REVIEW: Consider caching compiled regex for performance
    # SUGGESTION: Support multiple patterns with OR logic
    """

    def __init__(self, pattern: str, flags: int = 0):
        self.pattern = re.compile(pattern, flags)

    async def check(self, context: Dict[str, Any]) -> bool:
        output = context.get("last_output", "")
        return bool(self.pattern.search(output))


class NoOutputFor(TerminationCondition):
    """
    Terminate if no output received for specified duration.

    # REVIEW: This needs integration with the monitoring system
    # SUGGESTION: Store last_output_time in context
    """

    def __init__(self, seconds: int):
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        self.seconds = seconds

    async def check(self, context: Dict[str, Any]) -> bool:
        last_output_time = context.get("last_output_time")
        if last_output_time is None:
            return False
        elapsed = (datetime.utcnow() - last_output_time).total_seconds()
        return elapsed >= self.seconds


class ErrorDetected(TerminationCondition):
    """
    Terminate when error patterns are detected in output.

    # REVIEW: Should integrate with the existing expect-style pattern matching
    # SUGGESTION: Allow customizable error patterns
    """

    DEFAULT_PATTERNS = [
        r"(?i)error:",
        r"(?i)exception:",
        r"(?i)fatal:",
        r"(?i)failed:",
        r"(?i)traceback",
    ]

    def __init__(self, patterns: Optional[list] = None):
        self.patterns = [
            re.compile(p) for p in (patterns or self.DEFAULT_PATTERNS)
        ]

    async def check(self, context: Dict[str, Any]) -> bool:
        output = context.get("last_output", "")
        return any(p.search(output) for p in self.patterns)


# =============================================================================
# MCP Tool Integration (to be added to fastmcp_server.py)
# =============================================================================
#
# @mcp.tool()
# async def set_session_termination(
#     target: SessionTarget,
#     conditions: List[TerminationConditionSpec],
#     operator: Literal["or", "and"] = "or"
# ) -> dict:
#     """
#     Set termination conditions for a session.
#
#     Args:
#         target: Session to apply conditions to
#         conditions: List of condition specifications:
#             - {"type": "max_messages", "count": 100}
#             - {"type": "text_mention", "text": "DONE"}
#             - {"type": "timeout", "seconds": 300}
#             - {"type": "output_pattern", "pattern": "SUCCESS|COMPLETE"}
#             - {"type": "no_output_for", "seconds": 60}
#             - {"type": "error_detected"}
#         operator: How to combine conditions ("or" = any, "and" = all)
#
#     Returns:
#         {"success": True, "conditions_applied": [...]}
#     """
#     pass
#
# @mcp.tool()
# async def clear_session_termination(target: SessionTarget) -> dict:
#     """Remove termination conditions from a session."""
#     pass
#
# @mcp.tool()
# async def check_termination(target: SessionTarget) -> dict:
#     """Check if termination conditions are met for a session."""
#     pass
