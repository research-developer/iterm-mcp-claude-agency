"""
Coordination Primitives for Multi-Agent Workflows.

This module implements advanced coordination patterns for multi-agent orchestration:
- Barrier: Wait for all agents before proceeding
- Voting/Consensus: Multi-agent decision making
- Leader Election: Dynamic orchestrator selection

See: research/RESEARCH_SYNTHESIS_ADDENDUM.md and EPIC_PROPOSAL Sub-Issue 3.

SUGGESTED IMPLEMENTATION - Code review comments inline.

Example usage:
    ```python
    # Barrier example
    barrier = Barrier(agents=["builder", "tester", "reviewer"])
    await barrier.mark_ready("builder")
    await barrier.mark_ready("tester")
    await barrier.mark_ready("reviewer")  # All ready, barrier opens

    # Voting example
    vote = VotingRound(
        question="Deploy to production?",
        options=["yes", "no", "defer"],
        voters=["lead", "devops", "qa"]
    )
    await vote.cast("lead", "yes")
    await vote.cast("devops", "yes")
    await vote.cast("qa", "defer")
    result = vote.get_result()  # "yes" wins
    ```
"""
import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set, Any
from enum import Enum
import uuid


class BarrierState(Enum):
    """State of a barrier."""
    WAITING = "waiting"
    RELEASED = "released"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass
class Barrier:
    """
    Synchronization primitive to wait for all agents.

    # REVIEW: Should we support partial barriers (wait for N of M)?
    # REVIEW: Consider adding callback support for when barrier releases
    # SUGGESTION: Add ability to reset barrier for reuse
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    agents: Set[str] = field(default_factory=set)
    ready: Set[str] = field(default_factory=set)
    timeout: int = 60  # seconds
    state: BarrierState = BarrierState.WAITING
    created_at: datetime = field(default_factory=datetime.utcnow)
    released_at: Optional[datetime] = None
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def __post_init__(self):
        if isinstance(self.agents, list):
            self.agents = set(self.agents)

    async def mark_ready(self, agent: str) -> bool:
        """
        Mark an agent as ready at the barrier.

        # REVIEW: Should we reject unknown agents or accept them?

        Args:
            agent: Agent name

        Returns:
            True if agent was marked ready, False if not in barrier
        """
        if agent not in self.agents:
            # REVIEW: Decide on behavior - currently rejecting unknown agents
            return False

        if self.state != BarrierState.WAITING:
            return False

        self.ready.add(agent)

        # Check if all agents are ready
        if self.ready == self.agents:
            self.state = BarrierState.RELEASED
            self.released_at = datetime.utcnow()
            self._event.set()

        return True

    async def wait(self) -> BarrierState:
        """
        Wait for all agents to be ready.

        # REVIEW: Consider adding progress callback for long waits

        Returns:
            Final barrier state (RELEASED or TIMED_OUT)
        """
        if self.state == BarrierState.RELEASED:
            return self.state

        try:
            await asyncio.wait_for(self._event.wait(), timeout=self.timeout)
            return BarrierState.RELEASED
        except asyncio.TimeoutError:
            self.state = BarrierState.TIMED_OUT
            return BarrierState.TIMED_OUT

    def cancel(self):
        """Cancel the barrier."""
        self.state = BarrierState.CANCELLED
        self._event.set()

    @property
    def waiting_for(self) -> Set[str]:
        """Get agents that haven't marked ready yet."""
        return self.agents - self.ready

    @property
    def progress(self) -> float:
        """Get progress as percentage (0.0 to 1.0)."""
        if not self.agents:
            return 1.0
        return len(self.ready) / len(self.agents)


class VoteState(Enum):
    """State of a voting round."""
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


@dataclass
class VotingRound:
    """
    Collect votes from agents on a decision.

    # REVIEW: Should we support weighted voting?
    # REVIEW: Consider adding quorum requirements
    # SUGGESTION: Add support for ranked-choice voting
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    question: str = ""
    options: List[str] = field(default_factory=list)
    voters: Set[str] = field(default_factory=set)
    votes: Dict[str, str] = field(default_factory=dict)
    timeout: int = 60  # seconds
    state: VoteState = VoteState.OPEN
    created_at: datetime = field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None
    require_all_votes: bool = True  # Require all voters to participate

    def __post_init__(self):
        if isinstance(self.voters, list):
            self.voters = set(self.voters)

    async def cast(self, agent: str, option: str) -> bool:
        """
        Cast a vote.

        # REVIEW: Should we allow vote changes before closing?

        Args:
            agent: Voting agent name
            option: Selected option

        Returns:
            True if vote was accepted
        """
        if self.state != VoteState.OPEN:
            return False

        if agent not in self.voters:
            return False

        if option not in self.options:
            return False

        # Currently allows changing vote
        # REVIEW: Make this configurable?
        self.votes[agent] = option

        # Auto-close if all votes received
        if self.require_all_votes and len(self.votes) == len(self.voters):
            self.close()

        return True

    def close(self):
        """Close voting and finalize results."""
        self.state = VoteState.CLOSED
        self.closed_at = datetime.utcnow()

    def cancel(self):
        """Cancel the voting round."""
        self.state = VoteState.CANCELLED

    def get_result(self) -> Optional[str]:
        """
        Get the winning option.

        # REVIEW: How to handle ties? Currently returns first in tie.
        # SUGGESTION: Add tie-breaker configuration

        Returns:
            Winning option, or None if voting not complete
        """
        if self.require_all_votes and len(self.votes) < len(self.voters):
            return None

        if not self.votes:
            return None

        counts = Counter(self.votes.values())
        winner, count = counts.most_common(1)[0]
        return winner

    def get_detailed_result(self) -> Dict[str, Any]:
        """
        Get detailed voting results.

        Returns:
            Dictionary with vote counts, percentages, and winner
        """
        if not self.votes:
            return {"status": "no_votes", "winner": None}

        counts = Counter(self.votes.values())
        total = len(self.votes)

        results = {
            "status": "complete" if self.state == VoteState.CLOSED else "in_progress",
            "total_votes": total,
            "total_voters": len(self.voters),
            "participation": total / len(self.voters) if self.voters else 0,
            "counts": dict(counts),
            "percentages": {k: v / total for k, v in counts.items()},
            "winner": self.get_result(),
        }

        # Check for tie
        top_two = counts.most_common(2)
        if len(top_two) > 1 and top_two[0][1] == top_two[1][1]:
            results["tie"] = True
            results["tied_options"] = [opt for opt, cnt in top_two if cnt == top_two[0][1]]

        return results

    @property
    def missing_votes(self) -> Set[str]:
        """Get voters who haven't voted yet."""
        return self.voters - set(self.votes.keys())


class LeaderState(Enum):
    """State of leader election."""
    NO_LEADER = "no_leader"
    LEADER_ELECTED = "leader_elected"
    ELECTION_IN_PROGRESS = "election_in_progress"


@dataclass
class LeaderElection:
    """
    Simple leader election for dynamic orchestrator selection.

    # REVIEW: This is a simplified implementation - consider Raft for production
    # REVIEW: Should we support automatic re-election on leader failure?
    # SUGGESTION: Add heartbeat mechanism for leader liveness

    This implements a simple priority-based election where the highest-priority
    healthy candidate becomes leader.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    candidates: List[str] = field(default_factory=list)
    priorities: Dict[str, int] = field(default_factory=dict)  # Lower = higher priority
    leader: Optional[str] = None
    term: int = 0
    state: LeaderState = LeaderState.NO_LEADER
    elected_at: Optional[datetime] = None

    async def _is_healthy(self, candidate: str) -> bool:
        """
        Check if a candidate is healthy/available.

        # REVIEW: This should integrate with the session health checking
        # TODO: Implement actual health check via session status

        Args:
            candidate: Candidate agent name

        Returns:
            True if candidate is healthy
        """
        # Placeholder - should check actual agent/session health
        # from core.agents import AgentRegistry
        # return registry.get_agent(candidate) is not None
        return True

    async def elect(self) -> Optional[str]:
        """
        Elect a leader from candidates.

        Election algorithm:
        1. Sort candidates by priority (lower number = higher priority)
        2. Check health of each candidate in order
        3. First healthy candidate becomes leader

        Returns:
            Name of elected leader, or None if no healthy candidates
        """
        self.state = LeaderState.ELECTION_IN_PROGRESS

        # Sort by priority (candidates without explicit priority get default)
        sorted_candidates = sorted(
            self.candidates,
            key=lambda c: self.priorities.get(c, 100)
        )

        for candidate in sorted_candidates:
            if await self._is_healthy(candidate):
                self.leader = candidate
                self.term += 1
                self.state = LeaderState.LEADER_ELECTED
                self.elected_at = datetime.utcnow()
                return candidate

        self.state = LeaderState.NO_LEADER
        return None

    def step_down(self):
        """Current leader voluntarily steps down."""
        self.leader = None
        self.state = LeaderState.NO_LEADER

    def is_leader(self, agent: str) -> bool:
        """Check if agent is the current leader."""
        return self.leader == agent


# =============================================================================
# Barrier Manager for tracking multiple barriers
# =============================================================================

class BarrierManager:
    """
    Manage multiple barriers.

    # REVIEW: Consider adding automatic cleanup of old barriers
    """

    def __init__(self):
        self.barriers: Dict[str, Barrier] = {}

    def create(
        self,
        name: str,
        agents: List[str],
        timeout: int = 60
    ) -> Barrier:
        """Create a new barrier."""
        barrier = Barrier(name=name, agents=set(agents), timeout=timeout)
        self.barriers[barrier.id] = barrier
        return barrier

    def get(self, barrier_id: str) -> Optional[Barrier]:
        """Get a barrier by ID."""
        return self.barriers.get(barrier_id)

    def get_by_name(self, name: str) -> Optional[Barrier]:
        """Get a barrier by name."""
        for barrier in self.barriers.values():
            if barrier.name == name:
                return barrier
        return None


# =============================================================================
# MCP Tool Integration (to be added to fastmcp_server.py)
# =============================================================================
#
# @mcp.tool()
# async def create_barrier(
#     name: str,
#     agents: List[str],
#     timeout: int = 60
# ) -> dict:
#     """
#     Create a barrier for agent synchronization.
#
#     A barrier blocks until all specified agents have marked themselves
#     as ready. Useful for coordinating multi-agent workflows.
#
#     Args:
#         name: Human-readable barrier name
#         agents: List of agent names that must reach the barrier
#         timeout: Seconds to wait before timeout
#
#     Returns:
#         {"barrier_id": "uuid", "name": "...", "agents": [...]}
#     """
#     pass
#
# @mcp.tool()
# async def barrier_ready(barrier_id: str, agent: str) -> dict:
#     """
#     Mark an agent as ready at a barrier.
#
#     Args:
#         barrier_id: Barrier ID
#         agent: Agent marking ready
#
#     Returns:
#         {
#             "success": True,
#             "barrier_state": "waiting|released",
#             "waiting_for": ["agent1", "agent2"],
#             "progress": 0.66
#         }
#     """
#     pass
#
# @mcp.tool()
# async def wait_at_barrier(barrier_id: str) -> dict:
#     """
#     Wait for all agents at a barrier.
#
#     Blocks until all agents are ready or timeout occurs.
#
#     Args:
#         barrier_id: Barrier ID
#
#     Returns:
#         {"released": True/False, "state": "released|timed_out"}
#     """
#     pass
#
# @mcp.tool()
# async def start_vote(
#     question: str,
#     options: List[str],
#     voters: List[str],
#     timeout: int = 60
# ) -> dict:
#     """
#     Start a voting round among agents.
#
#     Args:
#         question: What agents are voting on
#         options: Available choices
#         voters: Agents allowed to vote
#         timeout: Voting window in seconds
#
#     Returns:
#         {"vote_id": "uuid", "question": "...", "options": [...]}
#     """
#     pass
#
# @mcp.tool()
# async def cast_vote(vote_id: str, agent: str, option: str) -> dict:
#     """
#     Cast a vote in an active voting round.
#
#     Args:
#         vote_id: Voting round ID
#         agent: Voting agent
#         option: Selected option
#
#     Returns:
#         {"success": True, "votes_received": N, "votes_needed": M}
#     """
#     pass
#
# @mcp.tool()
# async def get_vote_result(vote_id: str) -> dict:
#     """
#     Get voting results.
#
#     Args:
#         vote_id: Voting round ID
#
#     Returns:
#         {
#             "status": "complete|in_progress",
#             "winner": "option_name",
#             "counts": {"yes": 2, "no": 1},
#             "tie": False
#         }
#     """
#     pass
#
# @mcp.tool()
# async def elect_leader(
#     candidates: List[str],
#     priorities: Optional[Dict[str, int]] = None
# ) -> dict:
#     """
#     Elect a leader from candidates.
#
#     Args:
#         candidates: Agent names eligible for leadership
#         priorities: Optional priority mapping (lower = higher priority)
#
#     Returns:
#         {"leader": "agent_name", "term": N}
#     """
#     pass
