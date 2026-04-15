"""Feedback system for agent-driven development.

Provides multi-trigger hooks for collecting agent feedback, context-preserving
forks via git worktrees, and integration with GitHub for triage.

Security note: This module uses asyncio.create_subprocess_exec (not shell=True)
which is safe from command injection - arguments are passed as arrays, not
interpolated into shell commands.
"""

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from .agents import AgentRegistry


# ============================================================================
# ENUMS
# ============================================================================

class FeedbackCategory(str, Enum):
    """Categories for classifying feedback."""
    BUG = "bug"
    ENHANCEMENT = "enhancement"
    UX = "ux"
    PERFORMANCE = "performance"
    DOCUMENTATION = "documentation"


class FeedbackStatus(str, Enum):
    """Lifecycle status of feedback."""
    PENDING = "pending"
    TRIAGED = "triaged"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    TESTING = "testing"
    CLOSED = "closed"


class FeedbackTriggerType(str, Enum):
    """Types of feedback triggers."""
    MANUAL = "manual"
    ERROR_THRESHOLD = "error_threshold"
    PERIODIC = "periodic"
    PATTERN_DETECTED = "pattern_detected"


# ============================================================================
# MODELS
# ============================================================================

class FeedbackContext(BaseModel):
    """Captured context at feedback time for reproducibility."""

    # Git state
    git_commit: str = Field(..., description="Current git commit SHA")
    git_branch: str = Field(..., description="Current git branch name")
    git_diff: Optional[str] = Field(default=None, description="Uncommitted changes (git diff)")
    git_remote: Optional[str] = Field(default=None, description="Git remote URL")

    # Project info
    project_path: str = Field(..., description="Absolute path to project root")

    # Agent state
    recent_tool_calls: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Recent tool calls before feedback (max 10)"
    )
    recent_errors: List[str] = Field(
        default_factory=list,
        description="Recent error messages (max 10)"
    )
    active_file_paths: List[str] = Field(
        default_factory=list,
        description="Files the agent was working with"
    )

    # Terminal state
    terminal_output_snapshot: Optional[str] = Field(
        default=None,
        description="Terminal output at feedback time"
    )

    # Trigger-specific context
    trigger_context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context from the trigger"
    )


class FeedbackEntry(BaseModel):
    """Core feedback data structure with full context."""

    # Identification
    id: str = Field(
        default_factory=lambda: f"fb-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}",
        description="Unique feedback ID"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When feedback was created"
    )
    updated_at: Optional[datetime] = Field(default=None, description="Last update time")

    # Source
    agent_id: str = Field(..., description="Agent's session ID")
    agent_name: str = Field(..., description="Agent's registered name")
    session_id: str = Field(..., description="iTerm session ID")
    trigger_type: FeedbackTriggerType = Field(..., description="How feedback was triggered")

    # Context snapshot
    context: FeedbackContext = Field(..., description="Captured context at feedback time")

    # Feedback content
    category: FeedbackCategory = Field(..., description="Feedback category")
    title: str = Field(..., max_length=200, description="Brief title")
    description: str = Field(..., description="Detailed description")
    reproduction_steps: Optional[List[str]] = Field(
        default=None,
        description="Steps to reproduce (for bugs)"
    )
    suggested_improvement: Optional[str] = Field(
        default=None,
        description="Agent's suggested solution"
    )
    error_messages: Optional[List[str]] = Field(
        default=None,
        description="Related error messages"
    )

    # Lifecycle
    status: FeedbackStatus = Field(default=FeedbackStatus.PENDING, description="Current status")
    github_issue_url: Optional[str] = Field(default=None, description="Linked GitHub issue")
    github_pr_url: Optional[str] = Field(default=None, description="Linked GitHub PR")
    resolution_notes: Optional[str] = Field(default=None, description="Notes about resolution")

    # Linking
    related_feedback_ids: List[str] = Field(
        default_factory=list,
        description="IDs of related feedback entries"
    )
    blocking_feedback_ids: List[str] = Field(
        default_factory=list,
        description="IDs of feedback this is blocked by"
    )

    # Worktree tracking
    worktree_path: Optional[str] = Field(
        default=None,
        description="Path to isolated worktree for this feedback"
    )
    forked_session_id: Optional[str] = Field(
        default=None,
        description="Claude session ID of the forked conversation"
    )

    # SP2: fields returned by HEAD (compact projection).
    # id + title + category + status gives callers enough to triage feedback
    # without pulling context/diffs/etc.
    HEAD_FIELDS: ClassVar[set[str]] = {"id", "title", "category", "status"}


class TriggerConfig(BaseModel):
    """Configuration for a single trigger type."""

    enabled: bool = Field(default=True, description="Whether this trigger is active")


class ErrorThresholdConfig(TriggerConfig):
    """Configuration for error threshold trigger."""

    count: int = Field(default=3, ge=1, description="Number of errors before triggering")
    window_seconds: int = Field(
        default=300,
        ge=60,
        description="Time window for counting errors"
    )


class PeriodicConfig(TriggerConfig):
    """Configuration for periodic trigger."""

    tool_call_count: int = Field(
        default=100,
        ge=10,
        description="Tool calls between triggers"
    )


class PatternConfig(TriggerConfig):
    """Configuration for pattern detection trigger."""

    patterns: List[str] = Field(
        default_factory=lambda: [
            r"this\s+should\s+",
            r"it\s+would\s+be\s+better\s+if",
            r"I\s+wish\s+(this|the|it)",
            r"(?:bug|issue|problem)\s+(?:with|in)",
            r"unexpected\s+(?:behavior|result|output)",
        ],
        description="Regex patterns to detect"
    )

    @field_validator('patterns', mode='after')
    @classmethod
    def validate_patterns(cls, v):
        """Validate all patterns are valid regex."""
        for pattern in v:
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern '{pattern}': {e}")
        return v


class GitHubConfig(BaseModel):
    """Configuration for GitHub integration."""

    repo: Optional[str] = Field(default=None, description="owner/repo format")
    default_labels: List[str] = Field(
        default_factory=lambda: ["agent-feedback"],
        description="Labels to apply to issues"
    )
    auto_triage: bool = Field(
        default=False,
        description="Automatically create issues for new feedback"
    )


class FeedbackConfig(BaseModel):
    """Full configuration for the feedback system."""

    enabled: bool = Field(default=True, description="Master switch for feedback system")
    error_threshold: ErrorThresholdConfig = Field(default_factory=ErrorThresholdConfig)
    periodic: PeriodicConfig = Field(default_factory=PeriodicConfig)
    pattern: PatternConfig = Field(default_factory=PatternConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)


# ============================================================================
# FEEDBACK HOOK MANAGER
# ============================================================================

class FeedbackHookManager:
    """Manages multi-trigger feedback hooks.

    Supports four trigger types:
    - Manual: Agent explicitly calls /feedback
    - Error threshold: After N errors within a time window
    - Periodic: Every N tool calls
    - Pattern detection: When agent output matches suggestion patterns
    """

    def __init__(
        self,
        config: Optional[FeedbackConfig] = None,
        config_path: Optional[Path] = None,
    ):
        """Initialize the hook manager.

        Args:
            config: Optional config object. If not provided, loads from file or env.
            config_path: Path to config file. Defaults to ~/.iterm-mcp/feedback_hooks.json
        """
        self._config_path = config_path or Path(
            os.path.expanduser("~/.iterm-mcp/feedback_hooks.json")
        )

        if config:
            self._config = config
        else:
            self._config = self._load_config()

        # Override from environment variables
        self._apply_env_overrides()

        # Compile patterns for efficiency
        self._compiled_patterns: List[re.Pattern] = []
        self._compile_patterns()

        # State tracking per agent
        self._error_counts: Dict[str, List[datetime]] = {}  # agent -> list of error timestamps
        self._tool_call_counts: Dict[str, int] = {}  # agent -> count since last periodic

        # Pending triggers (agent_id -> trigger_type)
        self._pending_triggers: Dict[str, FeedbackTriggerType] = {}

    def _load_config(self) -> FeedbackConfig:
        """Load configuration from file or return defaults."""
        if self._config_path.exists():
            try:
                with open(self._config_path, 'r') as f:
                    data = json.load(f)
                return FeedbackConfig(**data)
            except (json.JSONDecodeError, ValueError) as e:
                # Log warning and use defaults
                print(f"Warning: Failed to load feedback config: {e}")
        return FeedbackConfig()

    def _apply_env_overrides(self) -> None:
        """Apply environment variable overrides to config."""
        # Error threshold
        env_threshold = os.environ.get("ITERM_MCP_FEEDBACK_ERROR_THRESHOLD")
        if env_threshold:
            try:
                self._config.error_threshold.count = int(env_threshold)
            except ValueError:
                pass

        # Periodic tool calls
        env_periodic = os.environ.get("ITERM_MCP_FEEDBACK_PERIODIC_CALLS")
        if env_periodic:
            try:
                self._config.periodic.tool_call_count = int(env_periodic)
            except ValueError:
                pass

        # Master enable/disable
        env_enabled = os.environ.get("ITERM_MCP_FEEDBACK_ENABLED")
        if env_enabled:
            self._config.enabled = env_enabled.lower() in ("true", "1", "yes")

        # GitHub repo
        env_repo = os.environ.get("ITERM_MCP_FEEDBACK_GITHUB_REPO")
        if env_repo:
            self._config.github.repo = env_repo

    def _compile_patterns(self) -> None:
        """Compile regex patterns for efficient matching."""
        self._compiled_patterns = []
        if self._config.pattern.enabled:
            for pattern in self._config.pattern.patterns:
                try:
                    self._compiled_patterns.append(
                        re.compile(pattern, re.IGNORECASE)
                    )
                except re.error:
                    pass  # Skip invalid patterns (already validated in config)

    def save_config(self) -> None:
        """Save current configuration to file."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_path, 'w') as f:
            json.dump(self._config.model_dump(), f, indent=2)

    @property
    def config(self) -> FeedbackConfig:
        """Get current configuration."""
        return self._config

    def update_config(self, **kwargs) -> None:
        """Update configuration and save."""
        data = self._config.model_dump()
        for key, value in kwargs.items():
            if '.' in key:
                # Handle nested keys like "error_threshold.count"
                parts = key.split('.')
                current = data
                for part in parts[:-1]:
                    current = current[part]
                current[parts[-1]] = value
            else:
                data[key] = value
        self._config = FeedbackConfig(**data)
        self._compile_patterns()
        self.save_config()

    def record_error(self, agent_id: str, error: str) -> Optional[FeedbackTriggerType]:
        """Record an error and check if threshold is reached.

        Args:
            agent_id: The agent's identifier
            error: The error message

        Returns:
            FeedbackTriggerType.ERROR_THRESHOLD if threshold reached, else None
        """
        if not self._config.enabled or not self._config.error_threshold.enabled:
            return None

        now = datetime.now(timezone.utc)

        # Initialize if needed
        if agent_id not in self._error_counts:
            self._error_counts[agent_id] = []

        # Add this error
        self._error_counts[agent_id].append(now)

        # Clean old errors outside the window
        window_start = now.timestamp() - self._config.error_threshold.window_seconds
        self._error_counts[agent_id] = [
            ts for ts in self._error_counts[agent_id]
            if ts.timestamp() > window_start
        ]

        # Check threshold
        if len(self._error_counts[agent_id]) >= self._config.error_threshold.count:
            self._pending_triggers[agent_id] = FeedbackTriggerType.ERROR_THRESHOLD
            self._error_counts[agent_id] = []  # Reset after triggering
            return FeedbackTriggerType.ERROR_THRESHOLD

        return None

    def record_tool_call(self, agent_id: str) -> Optional[FeedbackTriggerType]:
        """Record a tool call and check if periodic trigger should fire.

        Args:
            agent_id: The agent's identifier

        Returns:
            FeedbackTriggerType.PERIODIC if threshold reached, else None
        """
        if not self._config.enabled or not self._config.periodic.enabled:
            return None

        # Initialize if needed
        if agent_id not in self._tool_call_counts:
            self._tool_call_counts[agent_id] = 0

        self._tool_call_counts[agent_id] += 1

        # Check threshold
        if self._tool_call_counts[agent_id] >= self._config.periodic.tool_call_count:
            self._pending_triggers[agent_id] = FeedbackTriggerType.PERIODIC
            self._tool_call_counts[agent_id] = 0  # Reset after triggering
            return FeedbackTriggerType.PERIODIC

        return None

    def check_pattern(self, agent_id: str, text: str) -> Optional[FeedbackTriggerType]:
        """Check if text matches any feedback suggestion patterns.

        Args:
            agent_id: The agent's identifier
            text: Text to check (agent output, user message, etc.)

        Returns:
            FeedbackTriggerType.PATTERN_DETECTED if matched, else None
        """
        if not self._config.enabled or not self._config.pattern.enabled:
            return None

        for pattern in self._compiled_patterns:
            if pattern.search(text):
                self._pending_triggers[agent_id] = FeedbackTriggerType.PATTERN_DETECTED
                return FeedbackTriggerType.PATTERN_DETECTED

        return None

    def has_pending_trigger(self, agent_id: str) -> bool:
        """Check if agent has a pending feedback trigger."""
        return agent_id in self._pending_triggers

    def get_pending_trigger(self, agent_id: str) -> Optional[FeedbackTriggerType]:
        """Get and clear pending trigger for agent."""
        return self._pending_triggers.pop(agent_id, None)

    def clear_state(self, agent_id: str) -> None:
        """Clear all state for an agent."""
        self._error_counts.pop(agent_id, None)
        self._tool_call_counts.pop(agent_id, None)
        self._pending_triggers.pop(agent_id, None)

    def get_stats(self, agent_id: str) -> Dict[str, Any]:
        """Get current stats for an agent.

        Returns:
            Dict with error_count, tool_call_count, has_pending_trigger
        """
        return {
            "error_count": len(self._error_counts.get(agent_id, [])),
            "error_threshold": self._config.error_threshold.count,
            "tool_call_count": self._tool_call_counts.get(agent_id, 0),
            "tool_call_threshold": self._config.periodic.tool_call_count,
            "has_pending_trigger": agent_id in self._pending_triggers,
            "pending_trigger_type": self._pending_triggers.get(agent_id),
        }


# ============================================================================
# FEEDBACK COLLECTOR
# ============================================================================

async def run_git_command(cwd: str, args: List[str]) -> str:
    """Run a git command safely using create_subprocess_exec.

    Uses create_subprocess_exec (not shell) to prevent command injection.
    Arguments are passed as a list, not interpolated into a string.

    Args:
        cwd: Working directory for the command
        args: Arguments to pass to git (e.g., ["status", "--short"])

    Returns:
        stdout from the command, or empty string on error
    """
    try:
        # create_subprocess_exec is safe - no shell, args are array
        process = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        return stdout.decode('utf-8', errors='replace')
    except Exception:
        return ""


class FeedbackCollector:
    """Collects feedback with full context capture."""

    def __init__(
        self,
        feedback_dir: Optional[Path] = None,
        agent_registry: Optional["AgentRegistry"] = None,
    ):
        """Initialize the collector.

        Args:
            feedback_dir: Directory for feedback files. Defaults to ~/.iterm-mcp/feedback/
            agent_registry: Optional agent registry for agent lookups
        """
        if feedback_dir is None:
            feedback_dir = Path(os.path.expanduser("~/.iterm-mcp/feedback"))
        self.feedback_dir = feedback_dir
        self.feedback_dir.mkdir(parents=True, exist_ok=True)
        self.agent_registry = agent_registry

    async def capture_context(
        self,
        project_path: str,
        recent_tool_calls: Optional[List[Dict[str, Any]]] = None,
        recent_errors: Optional[List[str]] = None,
        active_files: Optional[List[str]] = None,
        terminal_output: Optional[str] = None,
        trigger_context: Optional[Dict[str, Any]] = None,
    ) -> FeedbackContext:
        """Capture current git and agent state for context.

        Args:
            project_path: Path to the project root
            recent_tool_calls: List of recent tool calls (max 10 kept)
            recent_errors: List of recent errors (max 10 kept)
            active_files: Files the agent was working with
            terminal_output: Terminal output snapshot
            trigger_context: Additional trigger-specific context

        Returns:
            FeedbackContext with captured state
        """
        # Capture git state using safe subprocess calls
        git_commit = await run_git_command(project_path, ["rev-parse", "HEAD"])
        git_branch = await run_git_command(project_path, ["branch", "--show-current"])
        git_diff = await run_git_command(project_path, ["diff"])
        git_remote = await run_git_command(project_path, ["remote", "get-url", "origin"])

        # Limit lists to prevent bloat
        recent_tool_calls = (recent_tool_calls or [])[-10:]
        recent_errors = (recent_errors or [])[-10:]

        return FeedbackContext(
            git_commit=git_commit.strip() or "unknown",
            git_branch=git_branch.strip() or "unknown",
            git_diff=git_diff if git_diff else None,
            git_remote=git_remote.strip() if git_remote else None,
            project_path=os.path.abspath(project_path),
            recent_tool_calls=recent_tool_calls,
            recent_errors=recent_errors,
            active_file_paths=active_files or [],
            terminal_output_snapshot=terminal_output,
            trigger_context=trigger_context or {},
        )

    def create_feedback(
        self,
        agent_name: str,
        agent_id: str,
        session_id: str,
        trigger_type: FeedbackTriggerType,
        category: FeedbackCategory,
        title: str,
        description: str,
        context: FeedbackContext,
        reproduction_steps: Optional[List[str]] = None,
        suggested_improvement: Optional[str] = None,
        error_messages: Optional[List[str]] = None,
    ) -> FeedbackEntry:
        """Create a new feedback entry.

        Args:
            agent_name: Registered agent name
            agent_id: Agent's identifier
            session_id: iTerm session ID
            trigger_type: How feedback was triggered
            category: Feedback category
            title: Brief title
            description: Detailed description
            context: Captured context
            reproduction_steps: Steps to reproduce
            suggested_improvement: Agent's suggestion
            error_messages: Related errors

        Returns:
            New FeedbackEntry
        """
        return FeedbackEntry(
            agent_name=agent_name,
            agent_id=agent_id,
            session_id=session_id,
            trigger_type=trigger_type,
            category=category,
            title=title,
            description=description,
            context=context,
            reproduction_steps=reproduction_steps,
            suggested_improvement=suggested_improvement,
            error_messages=error_messages,
        )

    def write_feedback_file(self, entry: FeedbackEntry) -> Path:
        """Write feedback to YAML file.

        Args:
            entry: The feedback entry to write

        Returns:
            Path to the created file
        """
        file_path = self.feedback_dir / f"{entry.id}.yaml"

        # Use JSON-compatible YAML for simplicity
        try:
            import yaml
            with open(file_path, 'w') as f:
                yaml.dump(
                    entry.model_dump(mode='json'),
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                )
        except ImportError:
            # Fallback to JSON if PyYAML not available
            with open(file_path.with_suffix('.json'), 'w') as f:
                json.dump(entry.model_dump(mode='json'), f, indent=2, default=str)
            file_path = file_path.with_suffix('.json')

        return file_path


# ============================================================================
# FEEDBACK REGISTRY
# ============================================================================

class FeedbackRegistry:
    """Manages feedback entries with JSONL persistence.

    Follows the same pattern as AgentRegistry for consistency.
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        max_entries: int = 1000,
    ):
        """Initialize the feedback registry.

        Args:
            data_dir: Directory for data files. Defaults to ~/.iterm-mcp/
            max_entries: Max feedback entries to keep in memory
        """
        if data_dir is None:
            data_dir = os.path.expanduser("~/.iterm-mcp")

        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.feedback_file = self.data_dir / "feedback.jsonl"
        self.feedback_dir = self.data_dir / "feedback"
        self.feedback_dir.mkdir(parents=True, exist_ok=True)

        # In-memory cache
        self._entries: Dict[str, FeedbackEntry] = {}
        self._max_entries = max_entries

        # Load existing data
        self._load_data()

    def _load_data(self) -> None:
        """Load feedback from JSONL file."""
        if self.feedback_file.exists():
            with open(self.feedback_file, 'r') as f:
                for line in f:
                    if line.strip():
                        try:
                            data = json.loads(line)
                            entry = FeedbackEntry(**data)
                            self._entries[entry.id] = entry
                        except (json.JSONDecodeError, ValueError):
                            continue  # Skip corrupted entries

    def _save_all(self) -> None:
        """Persist all feedback to JSONL file."""
        with open(self.feedback_file, 'w') as f:
            for entry in self._entries.values():
                f.write(entry.model_dump_json() + '\n')

    def _append_entry(self, entry: FeedbackEntry) -> None:
        """Append a single entry to file."""
        with open(self.feedback_file, 'a') as f:
            f.write(entry.model_dump_json() + '\n')

    def add(self, entry: FeedbackEntry) -> FeedbackEntry:
        """Add a new feedback entry.

        Args:
            entry: The feedback entry to add

        Returns:
            The added entry
        """
        self._entries[entry.id] = entry
        self._append_entry(entry)

        # Trim if over limit (remove oldest)
        if len(self._entries) > self._max_entries:
            oldest_id = min(
                self._entries.keys(),
                key=lambda k: self._entries[k].created_at
            )
            del self._entries[oldest_id]
            self._save_all()

        return entry

    def get(self, id: str) -> Optional[FeedbackEntry]:
        """Get feedback by ID."""
        return self._entries.get(id)

    def update(self, id: str, **updates) -> Optional[FeedbackEntry]:
        """Update an existing feedback entry.

        Args:
            id: Feedback ID
            **updates: Fields to update

        Returns:
            Updated entry, or None if not found
        """
        entry = self._entries.get(id)
        if not entry:
            return None

        # Update fields
        data = entry.model_dump()
        data.update(updates)
        data['updated_at'] = datetime.now(timezone.utc).isoformat()
        updated_entry = FeedbackEntry(**data)
        self._entries[id] = updated_entry
        self._save_all()

        return updated_entry

    def remove(self, id: str) -> bool:
        """Remove feedback by ID.

        Returns:
            True if removed, False if not found
        """
        if id in self._entries:
            del self._entries[id]
            self._save_all()
            return True
        return False

    def query(
        self,
        status: Optional[FeedbackStatus] = None,
        category: Optional[FeedbackCategory] = None,
        agent_name: Optional[str] = None,
        trigger_type: Optional[FeedbackTriggerType] = None,
        since: Optional[datetime] = None,
        limit: int = 20,
    ) -> List[FeedbackEntry]:
        """Query feedback entries with filters.

        Args:
            status: Filter by status
            category: Filter by category
            agent_name: Filter by agent name
            trigger_type: Filter by trigger type
            since: Only entries after this time
            limit: Max entries to return

        Returns:
            List of matching FeedbackEntry objects
        """
        results = []

        for entry in self._entries.values():
            # Apply filters
            if status and entry.status != status:
                continue
            if category and entry.category != category:
                continue
            if agent_name and entry.agent_name != agent_name:
                continue
            if trigger_type and entry.trigger_type != trigger_type:
                continue
            if since and entry.created_at < since:
                continue

            results.append(entry)

        # Sort by created_at descending (newest first)
        results.sort(key=lambda e: e.created_at, reverse=True)

        return results[:limit]

    def list_all(self) -> List[FeedbackEntry]:
        """List all feedback entries."""
        return list(self._entries.values())

    def link_github_issue(self, id: str, issue_url: str) -> Optional[FeedbackEntry]:
        """Link feedback to a GitHub issue.

        Args:
            id: Feedback ID
            issue_url: GitHub issue URL

        Returns:
            Updated entry, or None if not found
        """
        return self.update(id, github_issue_url=issue_url, status=FeedbackStatus.TRIAGED)

    def link_github_pr(self, id: str, pr_url: str) -> Optional[FeedbackEntry]:
        """Link feedback to a GitHub PR.

        Args:
            id: Feedback ID
            pr_url: GitHub PR URL

        Returns:
            Updated entry, or None if not found
        """
        return self.update(id, github_pr_url=pr_url, status=FeedbackStatus.IN_PROGRESS)

    def get_by_agent(self, agent_name: str) -> List[FeedbackEntry]:
        """Get all feedback from a specific agent."""
        return [e for e in self._entries.values() if e.agent_name == agent_name]

    def get_pending(self) -> List[FeedbackEntry]:
        """Get all pending feedback."""
        return self.query(status=FeedbackStatus.PENDING, limit=100)

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about feedback.

        Returns:
            Dict with counts by status, category, etc.
        """
        stats = {
            "total": len(self._entries),
            "by_status": {},
            "by_category": {},
            "by_trigger": {},
        }

        for entry in self._entries.values():
            # By status
            status = entry.status.value
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

            # By category
            category = entry.category.value
            stats["by_category"][category] = stats["by_category"].get(category, 0) + 1

            # By trigger
            trigger = entry.trigger_type.value
            stats["by_trigger"][trigger] = stats["by_trigger"].get(trigger, 0) + 1

        return stats


# ============================================================================
# FORK MECHANISM
# ============================================================================

class FeedbackForker:
    """Creates isolated feedback environments using git worktrees and Claude forks."""

    def __init__(
        self,
        project_path: Optional[str] = None,
        feedback_registry: Optional[FeedbackRegistry] = None,
    ):
        """Initialize the forker.

        Args:
            project_path: Path to the main project repository. Defaults to current directory.
            feedback_registry: Optional registry to update with worktree info
        """
        if project_path is None:
            project_path = os.getcwd()
        self.project_path = Path(project_path).resolve()
        self.feedback_registry = feedback_registry

    async def create_worktree(self, feedback_id: str) -> Path:
        """Create a git worktree for isolated feedback.

        Args:
            feedback_id: ID of the feedback entry

        Returns:
            Path to the created worktree
        """
        worktree_path = self.project_path.parent / f"iterm-mcp-feedback-{feedback_id}"

        # Create worktree from HEAD using safe subprocess
        process = await asyncio.create_subprocess_exec(
            "git", "worktree", "add", str(worktree_path), "HEAD",
            cwd=str(self.project_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(f"Failed to create worktree: {stderr.decode()}")

        return worktree_path

    def get_fork_command(
        self,
        session_id: str,
        worktree_path: Path,
    ) -> str:
        """Get the command to fork the Claude session into the worktree.

        Args:
            session_id: Current Claude session ID
            worktree_path: Path to the worktree

        Returns:
            Command string to be executed in iTerm
        """
        # Return the command to be executed in iTerm
        # The actual execution will be done by the MCP tool
        return f"cd {worktree_path} && claude --fork-session -r {session_id}"

    async def cleanup_worktree(self, feedback_id: str) -> bool:
        """Remove a worktree after feedback is submitted.

        Args:
            feedback_id: ID of the feedback entry

        Returns:
            True if cleaned up, False if not found
        """
        worktree_path = self.project_path.parent / f"iterm-mcp-feedback-{feedback_id}"

        if not worktree_path.exists():
            return False

        # Remove the worktree using safe subprocess
        process = await asyncio.create_subprocess_exec(
            "git", "worktree", "remove", str(worktree_path), "--force",
            cwd=str(self.project_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()

        return process.returncode == 0

    async def list_worktrees(self) -> List[Dict[str, str]]:
        """List all feedback-related worktrees.

        Returns:
            List of dicts with 'path' and 'feedback_id' keys
        """
        process = await asyncio.create_subprocess_exec(
            "git", "worktree", "list", "--porcelain",
            cwd=str(self.project_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()

        worktrees = []
        current_worktree: Dict[str, str] = {}

        for line in stdout.decode().split('\n'):
            if line.startswith('worktree '):
                if current_worktree:
                    worktrees.append(current_worktree)
                path = line[9:]  # Remove 'worktree ' prefix
                current_worktree = {'path': path}

                # Check if it's a feedback worktree
                name = Path(path).name
                if name.startswith('iterm-mcp-feedback-'):
                    current_worktree['feedback_id'] = name[19:]  # Extract ID

        if current_worktree:
            worktrees.append(current_worktree)

        # Filter to only feedback worktrees
        return [w for w in worktrees if 'feedback_id' in w]


# ============================================================================
# GITHUB INTEGRATION
# ============================================================================

GITHUB_ISSUE_TEMPLATE = """## Feedback Report: {title}

**Category:** {category}
**Reported by:** Agent `{agent_name}` (session: {session_id})
**Date:** {created_at}
**Feedback ID:** `{id}`
**Trigger:** {trigger_type}

### Description

{description}

### Reproduction Steps

{reproduction_steps_formatted}

### Suggested Improvement

{suggested_improvement}

### Context

- **Git Commit:** `{git_commit}`
- **Git Branch:** `{git_branch}`
- **Project:** `{project_path}`

### Error Messages

```
{error_messages_formatted}
```

### Related Feedback

{related_links}

---
*This issue was auto-generated from agent feedback.*
*Feedback file: `~/.iterm-mcp/feedback/{id}.yaml`*
"""


class GitHubIntegration:
    """Integrates feedback with GitHub issues and PRs using gh CLI."""

    def __init__(
        self,
        repo: Optional[str] = None,
        default_labels: Optional[List[str]] = None,
    ):
        """Initialize GitHub integration.

        Args:
            repo: Repository in owner/repo format
            default_labels: Default labels to apply to issues
        """
        self.repo = repo
        self.default_labels = default_labels or ["agent-feedback"]

    async def _run_gh_command(self, args: List[str]) -> tuple[int, str, str]:
        """Run a gh CLI command safely.

        Uses create_subprocess_exec (not shell) to prevent command injection.

        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        process = await asyncio.create_subprocess_exec(
            "gh", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return (
            process.returncode,
            stdout.decode('utf-8', errors='replace'),
            stderr.decode('utf-8', errors='replace'),
        )

    def _format_steps(self, steps: Optional[List[str]]) -> str:
        """Format reproduction steps as markdown list."""
        if not steps:
            return "N/A"
        return "\n".join(f"{i+1}. {step}" for i, step in enumerate(steps))

    def _format_errors(self, errors: Optional[List[str]]) -> str:
        """Format error messages."""
        if not errors:
            return "No errors recorded"
        return "\n".join(errors)

    def _format_related(self, ids: List[str]) -> str:
        """Format related feedback IDs as links."""
        if not ids:
            return "None"
        return "\n".join(f"- `{id}`" for id in ids)

    async def create_issue(
        self,
        feedback: FeedbackEntry,
        labels: Optional[List[str]] = None,
        assignee: Optional[str] = None,
        milestone: Optional[str] = None,
    ) -> Optional[str]:
        """Create a GitHub issue from feedback.

        Args:
            feedback: The feedback entry
            labels: Additional labels to apply
            assignee: GitHub user to assign
            milestone: Milestone to assign

        Returns:
            Issue URL if successful, None otherwise
        """
        if not self.repo:
            raise ValueError("GitHub repo not configured")

        # Format issue body
        body = GITHUB_ISSUE_TEMPLATE.format(
            title=feedback.title,
            category=feedback.category.value,
            agent_name=feedback.agent_name,
            session_id=feedback.session_id,
            created_at=feedback.created_at.isoformat(),
            id=feedback.id,
            trigger_type=feedback.trigger_type.value,
            description=feedback.description,
            reproduction_steps_formatted=self._format_steps(feedback.reproduction_steps),
            suggested_improvement=feedback.suggested_improvement or "N/A",
            git_commit=feedback.context.git_commit,
            git_branch=feedback.context.git_branch,
            project_path=feedback.context.project_path,
            error_messages_formatted=self._format_errors(feedback.error_messages),
            related_links=self._format_related(feedback.related_feedback_ids),
        )

        # Build command - all args are passed as separate strings (safe)
        all_labels = self.default_labels + (labels or [])
        args = [
            "issue", "create",
            "--repo", self.repo,
            "--title", f"[Agent Feedback] {feedback.title}",
            "--body", body,
        ]

        for label in all_labels:
            args.extend(["--label", label])

        if assignee:
            args.extend(["--assignee", assignee])

        if milestone:
            args.extend(["--milestone", milestone])

        # Execute
        returncode, stdout, stderr = await self._run_gh_command(args)

        if returncode != 0:
            raise RuntimeError(f"Failed to create issue: {stderr}")

        # Parse issue URL from output
        issue_url = stdout.strip()
        return issue_url

    async def check_gh_available(self) -> bool:
        """Check if gh CLI is available and authenticated."""
        returncode, _, _ = await self._run_gh_command(["auth", "status"])
        return returncode == 0
