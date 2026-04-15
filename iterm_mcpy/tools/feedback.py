"""Feedback system tools.

Provides tools for submitting, querying, forking, triaging, and updating
user/agent feedback about the iTerm MCP system. Integrates with the
feedback registry, forker, GitHub integration, and notification manager
instantiated during lifespan.
"""

import json
import os
from typing import List, Optional

from mcp.server.fastmcp import Context

from core.feedback import (
    FeedbackCategory,
    FeedbackCollector,
    FeedbackEntry,
    FeedbackStatus,
    FeedbackTriggerType,
)


async def submit_feedback(
    ctx: Context,
    title: str,
    description: str,
    category: str = "enhancement",
    agent_name: Optional[str] = None,
    session_id: Optional[str] = None,
    reproduction_steps: Optional[List[str]] = None,
    suggested_improvement: Optional[str] = None,
    error_messages: Optional[List[str]] = None,
) -> str:
    """Submit feedback about the iTerm MCP system.

    This is the manual /feedback command. Use when you have suggestions,
    found bugs, or want to request improvements to the iterm-mcp.

    Args:
        title: Short summary of the feedback
        description: Detailed description of the issue or suggestion
        category: One of: bug, enhancement, ux, performance, docs
        agent_name: Name of the agent submitting (auto-detected if not provided)
        session_id: Session ID (auto-detected from active session if not provided)
        reproduction_steps: Steps to reproduce (for bugs)
        suggested_improvement: What you think should be improved
        error_messages: Any error messages encountered
    """
    feedback_registry = ctx.request_context.lifespan_context["feedback_registry"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    notification_manager = ctx.request_context.lifespan_context["notification_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Get agent info
        if not agent_name and session_id:
            agent = agent_registry.get_agent_by_session(session_id)
            if agent:
                agent_name = agent.name

        if not session_id:
            session_id = agent_registry.active_session or "unknown"

        if not agent_name:
            agent = agent_registry.get_agent_by_session(session_id)
            agent_name = agent.name if agent else "unknown-agent"

        # Collect context
        collector = FeedbackCollector()
        context = await collector.capture_context(
            project_path=os.getcwd(),
            recent_tool_calls=[],  # Would need hook integration for real data
            recent_errors=error_messages or [],
        )

        # Parse category
        try:
            cat = FeedbackCategory(category.lower())
        except ValueError:
            cat = FeedbackCategory.ENHANCEMENT

        # Create feedback entry
        entry = FeedbackEntry(
            agent_id=agent_name,
            agent_name=agent_name,
            session_id=session_id,
            trigger_type=FeedbackTriggerType.MANUAL,
            context=context,
            category=cat,
            title=title,
            description=description,
            reproduction_steps=reproduction_steps,
            suggested_improvement=suggested_improvement,
            error_messages=error_messages,
        )

        # Save to registry (sync method, no await needed)
        feedback_registry.add(entry)

        # Notify
        await notification_manager.add_simple(
            agent=agent_name,
            level="success",
            summary=f"Feedback submitted: {title[:50]}",
            context=f"Feedback ID: {entry.id}",
        )

        logger.info(f"Feedback submitted: {entry.id} by {agent_name}")
        return json.dumps({
            "status": "submitted",
            "feedback_id": entry.id,
            "title": title,
            "category": cat.value,
            "message": "Thank you for your feedback! It has been recorded for review."
        }, indent=2)

    except Exception as e:
        logger.error(f"Error submitting feedback: {e}")
        return json.dumps({"error": str(e)}, indent=2)


async def check_feedback_triggers(
    ctx: Context,
    agent_name: str,
    session_id: str,
    error_message: Optional[str] = None,
    tool_call_name: Optional[str] = None,
    output_text: Optional[str] = None,
) -> str:
    """Record events and check if feedback triggers should fire.

    Call this to record errors, tool calls, or check for pattern matches
    that might trigger feedback collection.

    Args:
        agent_name: Name of the agent
        session_id: Session ID
        error_message: Error message to record (triggers error threshold)
        tool_call_name: Name of tool called (triggers periodic counter)
        output_text: Text to scan for feedback patterns
    """
    hook_manager = ctx.request_context.lifespan_context["feedback_hook_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        triggered = []
        stats = hook_manager.get_stats(agent_name)

        # Record error and check threshold - record_error returns trigger type if threshold reached
        if error_message:
            trigger_type = hook_manager.record_error(agent_name, error_message)
            if trigger_type == FeedbackTriggerType.ERROR_THRESHOLD:
                triggered.append({
                    "trigger": "error_threshold",
                    "reason": f"Error threshold reached ({stats['error_threshold']} errors)",
                    "error": error_message,
                })

        # Record tool call and check periodic - record_tool_call returns trigger type if threshold reached
        if tool_call_name:
            trigger_type = hook_manager.record_tool_call(agent_name)
            if trigger_type == FeedbackTriggerType.PERIODIC:
                triggered.append({
                    "trigger": "periodic",
                    "reason": f"Periodic check ({stats['tool_call_threshold']} tool calls)",
                })

        # Check for pattern matches - check_pattern returns trigger type if pattern found
        if output_text:
            trigger_type = hook_manager.check_pattern(agent_name, output_text)
            if trigger_type == FeedbackTriggerType.PATTERN_DETECTED:
                triggered.append({
                    "trigger": "pattern",
                    "reason": "Feedback pattern detected in output",
                })

        logger.info(f"Trigger check for {agent_name}: {len(triggered)} triggers fired")
        return json.dumps({
            "agent": agent_name,
            "triggers_fired": triggered,
            "should_collect_feedback": len(triggered) > 0,
        }, indent=2)

    except Exception as e:
        logger.error(f"Error checking triggers: {e}")
        return json.dumps({"error": str(e)}, indent=2)


async def query_feedback(
    ctx: Context,
    status: Optional[str] = None,
    category: Optional[str] = None,
    agent_name: Optional[str] = None,
    limit: int = 20,
) -> str:
    """Query the feedback registry.

    Args:
        status: Filter by status (pending, triaged, in_progress, resolved, testing, closed)
        category: Filter by category (bug, enhancement, ux, performance, docs)
        agent_name: Filter by agent who submitted
        limit: Max number of results
    """
    feedback_registry = ctx.request_context.lifespan_context["feedback_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Parse filters
        status_filter = None
        if status:
            try:
                status_filter = FeedbackStatus(status.lower())
            except ValueError:
                pass

        category_filter = None
        if category:
            try:
                category_filter = FeedbackCategory(category.lower())
            except ValueError:
                pass

        # Query (FeedbackRegistry.query is sync — do not await)
        entries = feedback_registry.query(
            status=status_filter,
            category=category_filter,
            agent_name=agent_name,
            limit=limit,
        )

        # Format results
        results = []
        for entry in entries:
            results.append({
                "id": entry.id,
                "title": entry.title,
                "category": entry.category.value,
                "status": entry.status.value,
                "agent": entry.agent_name,
                "created_at": entry.created_at.isoformat(),
                "github_issue_url": entry.github_issue_url,
            })

        logger.info(f"Query returned {len(results)} feedback entries")
        return json.dumps({
            "count": len(results),
            "entries": results,
        }, indent=2)

    except Exception as e:
        logger.error(f"Error querying feedback: {e}")
        return json.dumps({"error": str(e)}, indent=2)


async def fork_for_feedback(
    ctx: Context,
    feedback_id: str,
    session_id: str,
) -> str:
    """Fork the current session to a git worktree for safe feedback submission.

    Creates an isolated worktree and forks the Claude conversation there,
    allowing the agent to provide detailed feedback without affecting
    the main codebase.

    Args:
        feedback_id: The feedback ID to associate with the fork
        session_id: The session ID to fork from
    """
    forker = ctx.request_context.lifespan_context["feedback_forker"]
    notification_manager = ctx.request_context.lifespan_context["notification_manager"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        agent = agent_registry.get_agent_by_session(session_id)
        agent_name = agent.name if agent else "unknown"

        # Create worktree
        worktree_path = await forker.create_worktree(feedback_id)

        # Get fork command (the actual forking is done by executing this command)
        fork_command = forker.get_fork_command(session_id, worktree_path)

        await notification_manager.add_simple(
            agent=agent_name,
            level="info",
            summary=f"Forked for feedback: {feedback_id}",
            context=f"Worktree: {worktree_path}",
            action_hint="Continue in the forked session to provide feedback",
        )

        logger.info(f"Created worktree for session {session_id} at {worktree_path}")
        return json.dumps({
            "status": "worktree_created",
            "feedback_id": feedback_id,
            "worktree_path": str(worktree_path),
            "fork_command": fork_command,
            "message": "Worktree created. Execute the fork_command to continue in an isolated environment.",
        }, indent=2)

    except Exception as e:
        logger.error(f"Error forking for feedback: {e}")
        return json.dumps({"error": str(e)}, indent=2)


async def triage_feedback_to_github(
    ctx: Context,
    feedback_id: str,
    labels: Optional[List[str]] = None,
    assignee: Optional[str] = None,
) -> str:
    """Create a GitHub issue from feedback.

    Triages the feedback into a GitHub issue with proper labels and context.

    Args:
        feedback_id: The feedback ID to triage
        labels: Additional labels for the issue
        assignee: GitHub username to assign
    """
    feedback_registry = ctx.request_context.lifespan_context["feedback_registry"]
    github_integration = ctx.request_context.lifespan_context["github_integration"]
    notification_manager = ctx.request_context.lifespan_context["notification_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Get the feedback entry (sync method, no await needed)
        entry = feedback_registry.get(feedback_id)
        if not entry:
            return json.dumps({"error": f"Feedback {feedback_id} not found"}, indent=2)

        # Create GitHub issue
        issue_url = await github_integration.create_issue(
            feedback=entry,
            labels=labels,
            assignee=assignee,
        )

        if issue_url:
            # Update entry with issue URL (sync method, no await needed)
            feedback_registry.update(
                entry.id,
                github_issue_url=issue_url,
                status=FeedbackStatus.TRIAGED,
            )

            # Notify the agent
            await notification_manager.add_simple(
                agent=entry.agent_name,
                level="success",
                summary=f"Feedback triaged to GitHub",
                context=issue_url,
                action_hint="Check the GitHub issue for updates",
            )

            logger.info(f"Triaged feedback {feedback_id} to {issue_url}")
            return json.dumps({
                "status": "triaged",
                "feedback_id": feedback_id,
                "github_issue_url": issue_url,
            }, indent=2)
        else:
            return json.dumps({
                "status": "failed",
                "error": "Failed to create GitHub issue. Check gh CLI is authenticated.",
            }, indent=2)

    except Exception as e:
        logger.error(f"Error triaging feedback: {e}")
        return json.dumps({"error": str(e)}, indent=2)


async def notify_feedback_update(
    ctx: Context,
    feedback_id: str,
    update_type: str,
    message: str,
    pr_url: Optional[str] = None,
) -> str:
    """Notify agents about feedback status updates.

    Use this to notify the original agent when their feedback has been
    addressed, a PR is ready for testing, etc.

    Args:
        feedback_id: The feedback ID
        update_type: One of: acknowledged, in_progress, pr_opened, ready_for_testing, resolved
        message: Human-readable update message
        pr_url: URL to the PR if applicable
    """
    feedback_registry = ctx.request_context.lifespan_context["feedback_registry"]
    notification_manager = ctx.request_context.lifespan_context["notification_manager"]
    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Get the feedback entry (sync method, no await needed)
        entry = feedback_registry.get(feedback_id)
        if not entry:
            return json.dumps({"error": f"Feedback {feedback_id} not found"}, indent=2)

        # Update entry status based on update_type
        status_map = {
            "acknowledged": FeedbackStatus.TRIAGED,
            "in_progress": FeedbackStatus.IN_PROGRESS,
            "pr_opened": FeedbackStatus.IN_PROGRESS,
            "ready_for_testing": FeedbackStatus.TESTING,
            "resolved": FeedbackStatus.RESOLVED,
        }

        # Build updates dict
        updates = {}
        if update_type in status_map:
            updates["status"] = status_map[update_type]

        if pr_url:
            updates["github_pr_url"] = pr_url

        # Update entry (sync method, no await needed)
        updated_entry = feedback_registry.update(entry.id, **updates)
        if updated_entry:
            entry = updated_entry

        # Notify the agent
        level = "success" if update_type == "ready_for_testing" else "info"
        action_hint = None
        if update_type == "ready_for_testing":
            action_hint = f"Please test the fix: {pr_url}" if pr_url else "Please test the fix"

        await notification_manager.add_simple(
            agent=entry.agent_name,
            level=level,
            summary=f"Feedback update: {update_type}",
            context=message,
            action_hint=action_hint,
        )

        # Try to send a direct message to the agent's session if available
        agent = agent_registry.get_agent(entry.agent_name)
        if agent:
            session = await terminal.get_session_by_id(agent.session_id)
            if session:
                # Don't execute, just display the notification
                notification_text = f"\n[Feedback {feedback_id}] {update_type}: {message}"
                if pr_url:
                    notification_text += f"\nPR: {pr_url}"
                # Log but don't send to terminal (could be disruptive)
                logger.info(f"Would notify agent {entry.agent_name}: {notification_text}")

        logger.info(f"Notified about feedback {feedback_id}: {update_type}")
        return json.dumps({
            "status": "notified",
            "feedback_id": feedback_id,
            "agent": entry.agent_name,
            "update_type": update_type,
            "new_status": entry.status.value,
        }, indent=2)

    except Exception as e:
        logger.error(f"Error notifying about feedback: {e}")
        return json.dumps({"error": str(e)}, indent=2)


async def get_feedback_config(
    ctx: Context,
    update: bool = False,
    error_threshold_count: Optional[int] = None,
    periodic_tool_call_count: Optional[int] = None,
    add_pattern: Optional[str] = None,
    remove_pattern: Optional[str] = None,
) -> str:
    """Get or update feedback trigger configuration.

    Args:
        update: If True, apply the provided configuration changes
        error_threshold_count: New error threshold (e.g., 3 = trigger after 3 errors)
        periodic_tool_call_count: New periodic interval (e.g., 100 = trigger every 100 tool calls)
        add_pattern: Regex pattern to add to pattern detection
        remove_pattern: Regex pattern to remove from pattern detection
    """
    hook_manager = ctx.request_context.lifespan_context["feedback_hook_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        if update:
            # Apply updates
            if error_threshold_count is not None:
                hook_manager.config.error_threshold.count = error_threshold_count
            if periodic_tool_call_count is not None:
                hook_manager.config.periodic.tool_call_count = periodic_tool_call_count
            if add_pattern:
                hook_manager.config.pattern.patterns.append(add_pattern)
            if remove_pattern and remove_pattern in hook_manager.config.pattern.patterns:
                hook_manager.config.pattern.patterns.remove(remove_pattern)

            # Save config
            await hook_manager.save_config()
            logger.info("Feedback config updated")

        # Return current config
        config = hook_manager.config
        return json.dumps({
            "enabled": config.enabled,
            "error_threshold": {
                "enabled": config.error_threshold.enabled,
                "count": config.error_threshold.count,
            },
            "periodic": {
                "enabled": config.periodic.enabled,
                "tool_call_count": config.periodic.tool_call_count,
            },
            "pattern": {
                "enabled": config.pattern.enabled,
                "patterns": config.pattern.patterns,
            },
            "github": {
                "repo": config.github.repo,
                "default_labels": config.github.default_labels,
            },
        }, indent=2)

    except Exception as e:
        logger.error(f"Error with feedback config: {e}")
        return json.dumps({"error": str(e)}, indent=2)


def register(mcp):
    """Register feedback system tools with the FastMCP instance."""
    mcp.tool()(submit_feedback)
    mcp.tool()(check_feedback_triggers)
    mcp.tool()(query_feedback)
    mcp.tool()(fork_for_feedback)
    mcp.tool()(triage_feedback_to_github)
    mcp.tool()(notify_feedback_update)
    mcp.tool()(get_feedback_config)
