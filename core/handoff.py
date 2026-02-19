"""
Agent Handoff Protocol for Control Transfer.

This module implements explicit session-to-session control transfer inspired by
OpenAI Swarm and Agency Swarm patterns.
See: research/RESEARCH_SYNTHESIS_ADDENDUM.md for full design rationale.

SUGGESTED IMPLEMENTATION - Code review comments inline.

Example usage:
    ```python
    # Initiate handoff from builder to tester
    handoff = await handoff_manager.initiate_handoff(
        source="builder",
        target="tester",
        context={"build_output": "/dist", "version": "1.2.3"},
        reason="Build complete, ready for testing"
    )
    ```
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
import json
from pathlib import Path

# REVIEW: Consider importing from core.agents instead of TYPE_CHECKING
# to avoid circular imports
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.agents import AgentRegistry


class Handoff(BaseModel):
    """
    Represents a control transfer between agents.

    # REVIEW: Should handoffs be reversible? Consider adding rollback capability.
    # SUGGESTION: Add optional acknowledgment requirement from target agent.
    """

    id: str = Field(default_factory=lambda: str(__import__('uuid').uuid4()))
    source_agent: str
    target_agent: str
    context: Dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    acknowledged: bool = False
    acknowledged_at: Optional[datetime] = None

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class HandoffManager:
    """
    Manages agent-to-agent control transfers.

    # REVIEW: Consider adding hooks for pre/post handoff events
    # REVIEW: Should integrate with the existing NotificationManager
    # SUGGESTION: Add support for conditional handoffs based on output patterns
    """

    def __init__(
        self,
        registry: "AgentRegistry",
        storage_path: Optional[Path] = None
    ):
        """
        Initialize the HandoffManager.

        Args:
            registry: Agent registry for looking up agents
            storage_path: Optional path for handoff history persistence
        """
        self.registry = registry
        self.storage_path = storage_path or Path.home() / ".iterm-mcp" / "handoffs.jsonl"
        self.handoff_history: List[Handoff] = []
        self._load_history()

    def _load_history(self):
        """Load handoff history from storage."""
        if self.storage_path.exists():
            with open(self.storage_path) as f:
                for line in f:
                    self.handoff_history.append(Handoff.parse_raw(line))

    def _save_handoff(self, handoff: Handoff):
        """Persist a handoff to storage."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.storage_path, 'a') as f:
            f.write(handoff.json() + '\n')

    async def initiate_handoff(
        self,
        source: str,
        target: str,
        context: Dict[str, Any],
        reason: str = "",
        focus_target: bool = True,
        require_acknowledgment: bool = False
    ) -> Handoff:
        """
        Transfer control from source agent to target agent.

        # REVIEW: Should we capture the source session's output history as context?
        # SUGGESTION: Add optional timeout for acknowledgment

        Args:
            source: Name of the source agent
            target: Name of the target agent
            context: Data to pass to target agent
            reason: Human-readable reason for handoff
            focus_target: Whether to focus the target session
            require_acknowledgment: Whether target must acknowledge

        Returns:
            Handoff record

        Raises:
            ValueError: If source or target agent not found
        """
        # Validate agents exist
        source_agent = self.registry.get_agent(source)
        target_agent = self.registry.get_agent(target)

        if not source_agent:
            raise ValueError(f"Source agent not found: {source}")
        if not target_agent:
            raise ValueError(f"Target agent not found: {target}")

        # Create handoff record
        handoff = Handoff(
            source_agent=source,
            target_agent=target,
            context=context,
            reason=reason
        )

        # Store in history
        self.handoff_history.append(handoff)
        self._save_handoff(handoff)

        # Notify target agent
        # REVIEW: Should use typed messaging from core/messaging.py
        await self._notify_handoff(handoff)

        # Optionally focus target session
        if focus_target:
            await self._focus_target(target_agent)

        return handoff

    async def _notify_handoff(self, handoff: Handoff):
        """
        Send handoff notification to target agent.

        # REVIEW: Consider using the EventBus for this notification
        # SUGGESTION: Format the context as a structured message
        """
        # Format context for display
        context_str = json.dumps(handoff.context, indent=2)
        message = f"""
=== HANDOFF FROM {handoff.source_agent} ===
Reason: {handoff.reason}
Context:
{context_str}
=== END HANDOFF ===
"""
        # TODO: Send to target session via write_to_sessions
        # await write_to_sessions(...)
        pass

    async def _focus_target(self, target_agent):
        """Focus the target agent's session."""
        # TODO: Use modify_sessions to focus
        # await modify_sessions(...)
        pass

    async def acknowledge_handoff(self, handoff_id: str, agent: str) -> bool:
        """
        Acknowledge receipt of a handoff.

        # REVIEW: Should we allow acknowledgment with response data?

        Args:
            handoff_id: ID of the handoff to acknowledge
            agent: Agent acknowledging (must be target)

        Returns:
            True if acknowledged successfully
        """
        for handoff in self.handoff_history:
            if handoff.id == handoff_id:
                if handoff.target_agent != agent:
                    raise ValueError("Only target agent can acknowledge handoff")
                handoff.acknowledged = True
                handoff.acknowledged_at = datetime.utcnow()
                return True
        return False

    def get_pending_handoffs(self, agent: str) -> List[Handoff]:
        """Get unacknowledged handoffs for an agent."""
        return [
            h for h in self.handoff_history
            if h.target_agent == agent and not h.acknowledged
        ]

    def get_handoff_history(
        self,
        agent: Optional[str] = None,
        limit: int = 10
    ) -> List[Handoff]:
        """
        Get handoff history.

        Args:
            agent: Filter by agent (source or target)
            limit: Maximum number of records to return

        Returns:
            List of handoffs, most recent first
        """
        history = self.handoff_history
        if agent:
            history = [
                h for h in history
                if h.source_agent == agent or h.target_agent == agent
            ]
        return sorted(history, key=lambda h: h.timestamp, reverse=True)[:limit]


# =============================================================================
# MCP Tool Integration (to be added to fastmcp_server.py)
# =============================================================================
#
# @mcp.tool()
# async def handoff_to_agent(
#     source_agent: str,
#     target_agent: str,
#     context: Dict[str, Any],
#     reason: str = "",
#     focus_target: bool = True,
#     require_acknowledgment: bool = False
# ) -> dict:
#     """
#     Transfer control from one agent to another with context.
#
#     This implements the "handoff" pattern from OpenAI Swarm, allowing
#     agents to explicitly transfer control to each other with full
#     context preservation.
#
#     Args:
#         source_agent: Name of the agent initiating handoff
#         target_agent: Name of the agent receiving control
#         context: Data to pass to target (e.g., {"artifacts": [...], "state": {...}})
#         reason: Human-readable reason for the handoff
#         focus_target: Whether to focus the target session in iTerm
#         require_acknowledgment: Whether target must call acknowledge_handoff
#
#     Returns:
#         {
#             "success": True,
#             "handoff_id": "uuid",
#             "source": "builder",
#             "target": "tester",
#             "timestamp": "2026-01-06T..."
#         }
#
#     Example:
#         # Builder completes work, hands off to tester
#         handoff_to_agent(
#             source_agent="builder",
#             target_agent="tester",
#             context={
#                 "build_output": "/dist",
#                 "version": "1.2.3",
#                 "test_focus": ["unit", "integration"]
#             },
#             reason="Build v1.2.3 complete, ready for testing"
#         )
#     """
#     pass
#
# @mcp.tool()
# async def acknowledge_handoff(handoff_id: str, agent: str) -> dict:
#     """
#     Acknowledge receipt of a handoff.
#
#     Call this when the target agent has received and processed
#     the handoff context.
#
#     Args:
#         handoff_id: ID returned from handoff_to_agent
#         agent: Name of the acknowledging agent (must be target)
#
#     Returns:
#         {"success": True, "acknowledged_at": "2026-01-06T..."}
#     """
#     pass
#
# @mcp.tool()
# async def get_pending_handoffs(agent: str) -> List[dict]:
#     """
#     Get unacknowledged handoffs for an agent.
#
#     Use this to check if there are pending handoffs that need
#     to be processed.
#
#     Args:
#         agent: Agent name to check
#
#     Returns:
#         List of pending handoff records
#     """
#     pass
#
# @mcp.tool()
# async def get_handoff_history(
#     agent: Optional[str] = None,
#     limit: int = 10
# ) -> List[dict]:
#     """
#     Get handoff history.
#
#     Args:
#         agent: Filter by agent (matches source or target)
#         limit: Maximum records to return
#
#     Returns:
#         List of handoff records, most recent first
#     """
#     pass
