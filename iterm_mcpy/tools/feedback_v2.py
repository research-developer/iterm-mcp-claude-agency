"""SP2 method-semantic `feedback_v2` tool — Task 8.

Fifth SP2 collection tool (after sessions_v2 + agents_v2 + teams_v2 +
managers_v2). Replaces 7 legacy feedback tools under a unified collection:

    - submit_feedback             -> POST + CREATE   /feedback
    - query_feedback              -> GET              /feedback
    - check_feedback_triggers     -> POST + INVOKE    /feedback/triggers
    - get_feedback_config         -> GET              /feedback/config
    - (config update)             -> PATCH + MODIFY   /feedback/config
    - fork_for_feedback           -> POST + TRIGGER   /feedback/{id}/worktrees
    - triage_feedback_to_github   -> POST + SEND      /feedback/{id}/issues
    - notify_feedback_update      -> POST + SEND      /feedback/{id}/notifications

Registered under the provisional name ``feedback_v2`` to coexist with the
legacy feedback tools; the cutover (rename to ``feedback`` and unregister
the legacy tools) happens at the end of SP2.
"""
import os
from typing import List, Optional

from mcp.server.fastmcp import Context

from iterm_mcpy.dispatcher import MethodDispatcher


class FeedbackDispatcher(MethodDispatcher):
    """Dispatcher for the `feedback` collection (SP2 method-semantic)."""

    collection = "feedback"

    METHODS = {
        "GET": {
            "aliases": ["list", "get", "read", "query", "find"],
            "params": [
                "target=None | target='config'",
                # query params (target=None):
                "status?", "category?", "agent_name?", "limit?",
            ],
            "description": (
                "List feedback entries with filters (no target), or fetch the "
                "feedback trigger config (target='config')."
            ),
        },
        "POST": {
            "definers": {
                "CREATE": {
                    "aliases": ["submit", "create", "add"],
                    "params": [
                        "title", "description",
                        "category?='enhancement'|'bug'|'ux'|'performance'|'documentation'",
                        "agent_name?", "session_id?",
                        "reproduction_steps?", "suggested_improvement?",
                        "error_messages?",
                    ],
                    "description": "Submit a new feedback entry.",
                },
                "INVOKE": {
                    "aliases": ["invoke", "execute", "run"],
                    "params": [
                        "target='triggers'",
                        "agent_name", "session_id",
                        "error_message?", "tool_call_name?", "output_text?",
                    ],
                    "description": (
                        "Record events and check if feedback triggers "
                        "should fire (target='triggers'). "
                        "Note: 'check' routes to GET, not INVOKE — use "
                        "'invoke' or op='POST'+definer='INVOKE' here."
                    ),
                },
                "TRIGGER": {
                    "aliases": ["fork", "trigger"],
                    "params": [
                        "target='worktrees'",
                        "feedback_id", "session_id",
                    ],
                    "description": (
                        "Create an isolated git worktree for a feedback "
                        "entry (target='worktrees')."
                    ),
                },
                "SEND": {
                    "aliases": ["triage", "send", "notify", "dispatch"],
                    "params": [
                        "target='issues' | target='notifications'",
                        "feedback_id",
                        # issues:
                        "labels?", "assignee?",
                        # notifications:
                        "update_type?", "message?", "pr_url?",
                    ],
                    "description": (
                        "Send a feedback entry to GitHub as an issue "
                        "(target='issues') or notify the submitting agent "
                        "about a feedback update (target='notifications')."
                    ),
                },
            },
        },
        "PATCH": {
            "definers": {
                "MODIFY": {
                    "aliases": ["update", "patch", "modify"],
                    "params": [
                        "target='config'",
                        "error_threshold_count?",
                        "periodic_tool_call_count?",
                        "add_pattern?", "remove_pattern?",
                    ],
                    "description": (
                        "Update feedback trigger config (target='config')."
                    ),
                },
            },
        },
        "HEAD": {"compact_fields": ["id", "title", "category", "status"]},
        "OPTIONS": {"description": "Return this schema."},
    }

    sub_resources = ["triggers", "config", "worktrees", "issues", "notifications"]

    # -------------------------------- GET -------------------------------- #

    async def on_get(self, ctx, **params):
        """Route GET by `target` — list feedback or get config."""
        target = params.get("target")
        if target == "config":
            return await self._get_config(ctx, **params)
        return await self._query_feedback(ctx, **params)

    async def _query_feedback(self, ctx, **params):
        """GET /feedback — list feedback entries with filters."""
        from core.feedback import FeedbackCategory, FeedbackStatus

        lifespan = ctx.request_context.lifespan_context
        feedback_registry = lifespan["feedback_registry"]
        logger = lifespan["logger"]

        # Parse status filter
        status_filter = None
        status = params.get("status")
        if status:
            try:
                status_filter = FeedbackStatus(status.lower())
            except ValueError:
                pass

        # Parse category filter
        category_filter = None
        category = params.get("category")
        if category:
            try:
                category_filter = FeedbackCategory(category.lower())
            except ValueError:
                pass

        agent_name = params.get("agent_name")
        limit = params.get("limit", 20)

        # Query is sync (returns List[FeedbackEntry])
        entries = feedback_registry.query(
            status=status_filter,
            category=category_filter,
            agent_name=agent_name,
            limit=limit,
        )

        # Format results like legacy query_feedback did — keep compact
        # rows for the list view; full entry is available via HEAD/GET on
        # a single id in later iterations.
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

        logger.info(f"feedback_v2 GET: listed {len(results)} feedback entries")
        return {"count": len(results), "entries": results}

    async def _get_config(self, ctx, **params):
        """GET /feedback/config — current feedback trigger configuration."""
        lifespan = ctx.request_context.lifespan_context
        hook_manager = lifespan["feedback_hook_manager"]
        logger = lifespan["logger"]

        config = hook_manager.config
        logger.info("feedback_v2 GET config: returning current feedback config")
        return {
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
        }

    # ------------------------------- POST -------------------------------- #

    async def on_post(self, ctx, definer, **params):
        """Route POST by (definer, target)."""
        target = params.get("target")

        if definer == "CREATE":
            return await self._submit_feedback(ctx, **params)
        if definer == "INVOKE" and target == "triggers":
            return await self._check_triggers(ctx, **params)
        if definer == "TRIGGER" and target == "worktrees":
            return await self._fork(ctx, **params)
        if definer == "SEND" and target == "issues":
            return await self._triage_to_github(ctx, **params)
        if definer == "SEND" and target == "notifications":
            return await self._notify_update(ctx, **params)

        raise NotImplementedError(
            f"POST+{definer} on target={target!r} not yet implemented"
        )

    async def _submit_feedback(self, ctx, **params):
        """POST /feedback (CREATE) — submit a new feedback entry."""
        from core.feedback import (
            FeedbackCategory,
            FeedbackCollector,
            FeedbackEntry,
            FeedbackTriggerType,
        )

        title = params.get("title")
        description = params.get("description")
        if not title:
            raise ValueError("submit feedback requires title")
        if not description:
            raise ValueError("submit feedback requires description")

        lifespan = ctx.request_context.lifespan_context
        feedback_registry = lifespan["feedback_registry"]
        agent_registry = lifespan["agent_registry"]
        notification_manager = lifespan["notification_manager"]
        logger = lifespan["logger"]

        agent_name = params.get("agent_name")
        session_id = params.get("session_id")

        # Resolve agent / session (mirrors legacy submit_feedback)
        if not agent_name and session_id:
            agent = agent_registry.get_agent_by_session(session_id)
            if agent:
                agent_name = agent.name

        if not session_id:
            session_id = (
                getattr(agent_registry, "active_session", None) or "unknown"
            )

        if not agent_name:
            agent = agent_registry.get_agent_by_session(session_id)
            agent_name = agent.name if agent else "unknown-agent"

        # Collect context via the feedback collector
        collector = FeedbackCollector()
        context = await collector.capture_context(
            project_path=os.getcwd(),
            recent_tool_calls=[],
            recent_errors=params.get("error_messages") or [],
        )

        # Parse category (default: enhancement)
        category_raw = params.get("category", "enhancement")
        try:
            category = FeedbackCategory(category_raw.lower())
        except ValueError:
            category = FeedbackCategory.ENHANCEMENT

        entry = FeedbackEntry(
            agent_id=agent_name,
            agent_name=agent_name,
            session_id=session_id,
            trigger_type=FeedbackTriggerType.MANUAL,
            context=context,
            category=category,
            title=title,
            description=description,
            reproduction_steps=params.get("reproduction_steps"),
            suggested_improvement=params.get("suggested_improvement"),
            error_messages=params.get("error_messages"),
        )

        # add() is sync
        feedback_registry.add(entry)

        # Notify the agent (matches legacy behavior)
        await notification_manager.add_simple(
            agent=agent_name,
            level="success",
            summary=f"Feedback submitted: {title[:50]}",
            context=f"Feedback ID: {entry.id}",
        )

        logger.info(f"feedback_v2 CREATE: submitted {entry.id} by {agent_name}")
        return {
            "status": "submitted",
            "feedback_id": entry.id,
            "title": entry.title,
            "category": entry.category.value,
            "message": (
                "Thank you for your feedback! It has been recorded for review."
            ),
        }

    async def _check_triggers(self, ctx, **params):
        """POST /feedback/triggers (INVOKE) — record events + check triggers."""
        from core.feedback import FeedbackTriggerType

        lifespan = ctx.request_context.lifespan_context
        hook_manager = lifespan["feedback_hook_manager"]
        logger = lifespan["logger"]

        agent_name = params.get("agent_name")
        session_id = params.get("session_id")
        if not agent_name:
            raise ValueError("check triggers requires agent_name")
        if not session_id:
            raise ValueError("check triggers requires session_id")

        triggered = []
        stats = hook_manager.get_stats(agent_name)

        error_message = params.get("error_message")
        if error_message:
            trigger_type = hook_manager.record_error(agent_name, error_message)
            if trigger_type == FeedbackTriggerType.ERROR_THRESHOLD:
                triggered.append({
                    "trigger": "error_threshold",
                    "reason": (
                        f"Error threshold reached "
                        f"({stats['error_threshold']} errors)"
                    ),
                    "error": error_message,
                })

        tool_call_name = params.get("tool_call_name")
        if tool_call_name:
            trigger_type = hook_manager.record_tool_call(agent_name)
            if trigger_type == FeedbackTriggerType.PERIODIC:
                triggered.append({
                    "trigger": "periodic",
                    "reason": (
                        f"Periodic check "
                        f"({stats['tool_call_threshold']} tool calls)"
                    ),
                })

        output_text = params.get("output_text")
        if output_text:
            trigger_type = hook_manager.check_pattern(agent_name, output_text)
            if trigger_type == FeedbackTriggerType.PATTERN_DETECTED:
                triggered.append({
                    "trigger": "pattern",
                    "reason": "Feedback pattern detected in output",
                })

        logger.info(
            f"feedback_v2 INVOKE triggers: {agent_name} -> "
            f"{len(triggered)} triggers fired"
        )
        return {
            "agent": agent_name,
            "triggers_fired": triggered,
            "should_collect_feedback": len(triggered) > 0,
        }

    async def _fork(self, ctx, **params):
        """POST /feedback/{id}/worktrees (TRIGGER) — create worktree fork."""
        lifespan = ctx.request_context.lifespan_context
        forker = lifespan["feedback_forker"]
        notification_manager = lifespan["notification_manager"]
        agent_registry = lifespan["agent_registry"]
        logger = lifespan["logger"]

        feedback_id = params.get("feedback_id")
        session_id = params.get("session_id")
        if not feedback_id:
            raise ValueError("fork requires feedback_id")
        if not session_id:
            raise ValueError("fork requires session_id")

        agent = agent_registry.get_agent_by_session(session_id)
        agent_name = agent.name if agent else "unknown"

        worktree_path = await forker.create_worktree(feedback_id)
        fork_command = forker.get_fork_command(session_id, worktree_path)

        await notification_manager.add_simple(
            agent=agent_name,
            level="info",
            summary=f"Forked for feedback: {feedback_id}",
            context=f"Worktree: {worktree_path}",
            action_hint="Continue in the forked session to provide feedback",
        )

        logger.info(
            f"feedback_v2 TRIGGER worktrees: created worktree for "
            f"session={session_id} at {worktree_path}"
        )
        return {
            "status": "worktree_created",
            "feedback_id": feedback_id,
            "worktree_path": str(worktree_path),
            "fork_command": fork_command,
            "message": (
                "Worktree created. Execute the fork_command to continue in "
                "an isolated environment."
            ),
        }

    async def _triage_to_github(self, ctx, **params):
        """POST /feedback/{id}/issues (SEND) — triage to GitHub issue."""
        from core.feedback import FeedbackStatus

        lifespan = ctx.request_context.lifespan_context
        feedback_registry = lifespan["feedback_registry"]
        github_integration = lifespan["github_integration"]
        notification_manager = lifespan["notification_manager"]
        logger = lifespan["logger"]

        feedback_id = params.get("feedback_id")
        if not feedback_id:
            raise ValueError("triage requires feedback_id")

        entry = feedback_registry.get(feedback_id)
        if not entry:
            raise RuntimeError(f"Feedback {feedback_id} not found")

        labels = params.get("labels")
        assignee = params.get("assignee")

        issue_url = await github_integration.create_issue(
            feedback=entry,
            labels=labels,
            assignee=assignee,
        )

        if not issue_url:
            raise RuntimeError(
                "Failed to create GitHub issue. "
                "Check gh CLI is authenticated."
            )

        # Update the registry with the issue URL + TRIAGED status (sync).
        feedback_registry.update(
            entry.id,
            github_issue_url=issue_url,
            status=FeedbackStatus.TRIAGED,
        )

        await notification_manager.add_simple(
            agent=entry.agent_name,
            level="success",
            summary="Feedback triaged to GitHub",
            context=issue_url,
            action_hint="Check the GitHub issue for updates",
        )

        logger.info(
            f"feedback_v2 SEND issues: triaged {feedback_id} to {issue_url}"
        )
        return {
            "status": "triaged",
            "feedback_id": feedback_id,
            "github_issue_url": issue_url,
        }

    async def _notify_update(self, ctx, **params):
        """POST /feedback/{id}/notifications (SEND) — notify agent of update."""
        from core.feedback import FeedbackStatus

        lifespan = ctx.request_context.lifespan_context
        feedback_registry = lifespan["feedback_registry"]
        notification_manager = lifespan["notification_manager"]
        logger = lifespan["logger"]

        feedback_id = params.get("feedback_id")
        update_type = params.get("update_type")
        message = params.get("message")
        if not feedback_id:
            raise ValueError("notify update requires feedback_id")
        if not update_type:
            raise ValueError("notify update requires update_type")
        if message is None:
            raise ValueError("notify update requires message")

        entry = feedback_registry.get(feedback_id)
        if not entry:
            raise RuntimeError(f"Feedback {feedback_id} not found")

        status_map = {
            "acknowledged": FeedbackStatus.TRIAGED,
            "in_progress": FeedbackStatus.IN_PROGRESS,
            "pr_opened": FeedbackStatus.IN_PROGRESS,
            "ready_for_testing": FeedbackStatus.TESTING,
            "resolved": FeedbackStatus.RESOLVED,
        }

        updates: dict = {}
        if update_type in status_map:
            updates["status"] = status_map[update_type]

        pr_url = params.get("pr_url")
        if pr_url:
            updates["github_pr_url"] = pr_url

        updated_entry = feedback_registry.update(entry.id, **updates)
        if updated_entry:
            entry = updated_entry

        level = "success" if update_type == "ready_for_testing" else "info"
        action_hint = None
        if update_type == "ready_for_testing":
            action_hint = (
                f"Please test the fix: {pr_url}" if pr_url else "Please test the fix"
            )

        await notification_manager.add_simple(
            agent=entry.agent_name,
            level=level,
            summary=f"Feedback update: {update_type}",
            context=message,
            action_hint=action_hint,
        )

        logger.info(
            f"feedback_v2 SEND notifications: notified {entry.agent_name} "
            f"about {feedback_id} update={update_type}"
        )
        return {
            "status": "notified",
            "feedback_id": feedback_id,
            "agent": entry.agent_name,
            "update_type": update_type,
            "new_status": entry.status.value,
        }

    # ------------------------------- PATCH ------------------------------- #

    async def on_patch(self, ctx, definer, **params):
        """Route PATCH by `target` — update config."""
        target = params.get("target")
        if target == "config":
            return await self._update_config(ctx, **params)
        raise NotImplementedError(
            f"PATCH target={target!r} not yet implemented"
        )

    async def _update_config(self, ctx, **params):
        """PATCH /feedback/config (MODIFY) — update feedback trigger config."""
        lifespan = ctx.request_context.lifespan_context
        hook_manager = lifespan["feedback_hook_manager"]
        logger = lifespan["logger"]

        error_threshold_count = params.get("error_threshold_count")
        periodic_tool_call_count = params.get("periodic_tool_call_count")
        add_pattern = params.get("add_pattern")
        remove_pattern = params.get("remove_pattern")

        if error_threshold_count is not None:
            hook_manager.config.error_threshold.count = error_threshold_count
        if periodic_tool_call_count is not None:
            hook_manager.config.periodic.tool_call_count = periodic_tool_call_count
        if add_pattern:
            hook_manager.config.pattern.patterns.append(add_pattern)
        if (
            remove_pattern
            and remove_pattern in hook_manager.config.pattern.patterns
        ):
            hook_manager.config.pattern.patterns.remove(remove_pattern)

        # save_config is sync.
        hook_manager.save_config()
        logger.info("feedback_v2 MODIFY config: persisted updated feedback config")

        config = hook_manager.config
        return {
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
        }


_dispatcher = FeedbackDispatcher()


async def feedback_v2(
    ctx: Context,
    op: str = "GET",
    definer: Optional[str] = None,
    target: Optional[str] = None,
    # query / get:
    feedback_id: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    agent_name: Optional[str] = None,
    limit: Optional[int] = None,
    # submit:
    title: Optional[str] = None,
    description: Optional[str] = None,
    session_id: Optional[str] = None,
    reproduction_steps: Optional[List[str]] = None,
    suggested_improvement: Optional[str] = None,
    error_messages: Optional[List[str]] = None,
    # check triggers:
    error_message: Optional[str] = None,
    tool_call_name: Optional[str] = None,
    output_text: Optional[str] = None,
    # triage to github:
    labels: Optional[List[str]] = None,
    assignee: Optional[str] = None,
    # notify update:
    update_type: Optional[str] = None,
    message: Optional[str] = None,
    pr_url: Optional[str] = None,
    # config update:
    error_threshold_count: Optional[int] = None,
    periodic_tool_call_count: Optional[int] = None,
    add_pattern: Optional[str] = None,
    remove_pattern: Optional[str] = None,
) -> str:
    """Feedback lifecycle: submit, query, fork, triage, notify, config.

    Use op="query" (or op="GET") (+ status?/category?/agent_name?/limit?) to
      list feedback entries with filters.
    Use op="GET" + target="config" to fetch the current feedback trigger
      configuration.
    Use op="submit" (or op="POST" + definer="CREATE") + title + description
      (+ category?/agent_name?/session_id?/reproduction_steps?/
      suggested_improvement?/error_messages?) to submit new feedback.
    Use op="check" (or op="POST" + definer="INVOKE") + target="triggers" +
      agent_name + session_id (+ error_message?/tool_call_name?/output_text?)
      to record events and check if feedback triggers should fire.
    Use op="fork" (or op="POST" + definer="TRIGGER") + target="worktrees" +
      feedback_id + session_id to create an isolated git worktree for a
      feedback entry.
    Use op="triage" (or op="POST" + definer="SEND") + target="issues" +
      feedback_id (+ labels?/assignee?) to create a GitHub issue from a
      feedback entry.
    Use op="notify" (or op="POST" + definer="SEND") + target="notifications"
      + feedback_id + update_type + message (+ pr_url?) to notify the
      submitting agent about a feedback status change.
    Use op="update" (or op="PATCH" + definer="MODIFY") + target="config"
      (+ error_threshold_count?/periodic_tool_call_count?/add_pattern?/
      remove_pattern?) to adjust feedback trigger configuration.
    Use op="HEAD" (or "peek"/"summary") for a compact entry list.
    Use op="OPTIONS" (or "schema") to discover the tool's surface.

    Args:
        op: HTTP method or friendly verb.
        definer: Explicit definer (CREATE/INVOKE/TRIGGER/SEND/MODIFY).
        target: Sub-resource: 'triggers', 'config', 'worktrees', 'issues',
            'notifications'. Omit to address the feedback collection itself.
        feedback_id: Feedback entry ID (for fork/triage/notify).
        status: Filter by status for query.
        category: Filter by category for query, or category for submit.
        agent_name: Filter by agent for query, or the submitting agent name
            for submit / check.
        limit: Max results for query.
        title: Feedback title (submit).
        description: Feedback description (submit).
        session_id: Session ID (submit/fork/check triggers).
        reproduction_steps: Steps to reproduce (submit).
        suggested_improvement: Suggested improvement text (submit).
        error_messages: Error messages list (submit).
        error_message: Error message (check triggers — single).
        tool_call_name: Tool call name (check triggers — single).
        output_text: Text to scan for patterns (check triggers).
        labels: Additional labels (triage).
        assignee: GitHub assignee (triage).
        update_type: Feedback update type (notify — e.g., 'acknowledged',
            'in_progress', 'ready_for_testing', 'resolved').
        message: Update message text (notify).
        pr_url: Linked PR URL (notify).
        error_threshold_count: New error threshold (config update).
        periodic_tool_call_count: New periodic interval (config update).
        add_pattern: Regex pattern to add to pattern detection (config update).
        remove_pattern: Regex pattern to remove (config update).

    This is SP2's fifth method-semantic collection tool. It coexists with
    the legacy feedback tools and will eventually replace them.
    """
    raw_params = {
        "target": target,
        "feedback_id": feedback_id,
        "status": status,
        "category": category,
        "agent_name": agent_name,
        "limit": limit,
        "title": title,
        "description": description,
        "session_id": session_id,
        "reproduction_steps": reproduction_steps,
        "suggested_improvement": suggested_improvement,
        "error_messages": error_messages,
        "error_message": error_message,
        "tool_call_name": tool_call_name,
        "output_text": output_text,
        "labels": labels,
        "assignee": assignee,
        "update_type": update_type,
        "message": message,
        "pr_url": pr_url,
        "error_threshold_count": error_threshold_count,
        "periodic_tool_call_count": periodic_tool_call_count,
        "add_pattern": add_pattern,
        "remove_pattern": remove_pattern,
    }
    params = {k: v for k, v in raw_params.items() if v is not None}

    return await _dispatcher.dispatch(ctx=ctx, op=op, definer=definer, **params)


def register(mcp):
    """Register the feedback_v2 dispatcher tool.

    Named ``feedback_v2`` to coexist with the legacy feedback tools during
    the SP2 coexistence period. Final cutover (renaming to ``feedback``
    and unregistering legacy tools) happens at the end of SP2.
    """
    mcp.tool(name="feedback_v2")(feedback_v2)
