"""Tests for feedback dispatcher (SP2 Task 8)."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from iterm_mcpy.tools.feedback import FeedbackDispatcher, feedback


def _make_ctx(
    feedback_registry=None,
    feedback_hook_manager=None,
    feedback_forker=None,
    github_integration=None,
    notification_manager=None,
    agent_registry=None,
    logger=None,
    **extra,
):
    """Build a fake MCP Context with the lifespan context filled in.

    `**extra` keys are merged into `lifespan_context` so tests can inject
    whichever collaborators they need. The notification_manager defaults to
    an AsyncMock because its `add_simple` is awaited.
    """
    ctx = MagicMock()

    nm = notification_manager
    if nm is None:
        nm = MagicMock()
        nm.add_simple = AsyncMock(return_value=None)

    ctx.request_context.lifespan_context = {
        "feedback_registry": feedback_registry or MagicMock(),
        "feedback_hook_manager": feedback_hook_manager or MagicMock(),
        "feedback_forker": feedback_forker or MagicMock(),
        "github_integration": github_integration or MagicMock(),
        "notification_manager": nm,
        "agent_registry": agent_registry or MagicMock(),
        "logger": logger or MagicMock(),
        **extra,
    }
    return ctx


def _fake_config(
    enabled=True,
    error_threshold_enabled=True,
    error_threshold_count=3,
    periodic_enabled=True,
    periodic_count=100,
    pattern_enabled=True,
    patterns=None,
    github_repo=None,
    github_labels=None,
):
    """Build a stand-in for FeedbackConfig with the attributes the dispatcher reads."""
    config = MagicMock()
    config.enabled = enabled

    et = MagicMock()
    et.enabled = error_threshold_enabled
    et.count = error_threshold_count
    config.error_threshold = et

    p = MagicMock()
    p.enabled = periodic_enabled
    p.tool_call_count = periodic_count
    config.periodic = p

    pat = MagicMock()
    pat.enabled = pattern_enabled
    pat.patterns = patterns if patterns is not None else []
    config.pattern = pat

    gh = MagicMock()
    gh.repo = github_repo
    gh.default_labels = github_labels if github_labels is not None else []
    config.github = gh

    return config


def _fake_entry(
    id_="fb-20240101-deadbeef",
    title="A title",
    category_value="bug",
    status_value="pending",
    agent_name="agent1",
    created_iso="2024-01-01T00:00:00+00:00",
    github_issue_url=None,
):
    """Build a stand-in for FeedbackEntry with the fields the dispatcher reads."""
    from datetime import datetime

    entry = MagicMock()
    entry.id = id_
    entry.title = title

    cat = MagicMock()
    cat.value = category_value
    entry.category = cat

    stat = MagicMock()
    stat.value = status_value
    entry.status = stat

    entry.agent_name = agent_name
    entry.created_at = datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
    entry.github_issue_url = github_issue_url
    return entry


# ========================================================================= #
# OPTIONS / HEAD / unknown verb                                             #
# ========================================================================= #


class TestOptions(unittest.TestCase):
    def test_options_returns_schema(self):
        parsed = json.loads(asyncio.run(feedback(ctx=_make_ctx(), op="OPTIONS")))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["collection"], "feedback")
        self.assertIn("GET", parsed["data"]["methods"])
        self.assertIn("POST", parsed["data"]["methods"])
        self.assertIn("PATCH", parsed["data"]["methods"])
        # Sub-resources advertised.
        for sub in ("triggers", "config", "worktrees", "issues", "notifications"):
            self.assertIn(sub, parsed["data"]["sub_resources"])

    def test_options_lists_post_definers(self):
        parsed = json.loads(asyncio.run(feedback(ctx=_make_ctx(), op="OPTIONS")))
        post = parsed["data"]["methods"]["POST"]
        self.assertIn("CREATE", post["definers"])
        self.assertIn("INVOKE", post["definers"])
        self.assertIn("TRIGGER", post["definers"])
        self.assertIn("SEND", post["definers"])

    def test_options_lists_patch_modify(self):
        parsed = json.loads(asyncio.run(feedback(ctx=_make_ctx(), op="OPTIONS")))
        patch_schema = parsed["data"]["methods"]["PATCH"]
        self.assertIn("MODIFY", patch_schema["definers"])

    def test_schema_verb_works(self):
        parsed = json.loads(asyncio.run(feedback(ctx=_make_ctx(), op="schema")))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])


class TestUnknownOp(unittest.TestCase):
    def test_bad_verb_returns_err_envelope(self):
        parsed = json.loads(asyncio.run(feedback(ctx=_make_ctx(), op="frobnicate")))
        self.assertFalse(parsed["ok"])
        self.assertIn("Unknown op", parsed["error"]["message"])


class TestWrongDefiner(unittest.TestCase):
    def test_post_replace_rejected(self):
        # REPLACE belongs to the PUT family, not POST.
        parsed = json.loads(asyncio.run(
            feedback(ctx=_make_ctx(), op="POST", definer="REPLACE")
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("not in POST family", parsed["error"]["message"])


# ========================================================================= #
# GET /feedback — query entries                                             #
# ========================================================================= #


class TestQuery(unittest.TestCase):
    def test_query_returns_entries(self):
        registry = MagicMock()
        registry.query.return_value = [
            _fake_entry(id_="fb-1", title="Bug 1", category_value="bug"),
            _fake_entry(
                id_="fb-2", title="UX issue", category_value="ux",
                status_value="triaged",
                github_issue_url="https://github.com/owner/repo/issues/1",
            ),
        ]

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_registry=registry),
            op="query",
        )))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 2)
        entries = parsed["data"]["entries"]
        self.assertEqual(entries[0]["id"], "fb-1")
        self.assertEqual(entries[0]["category"], "bug")
        self.assertEqual(entries[1]["status"], "triaged")
        self.assertEqual(
            entries[1]["github_issue_url"],
            "https://github.com/owner/repo/issues/1",
        )
        # query() is sync — never awaited.
        registry.query.assert_called_once()

    def test_query_filters_parsed(self):
        from core.feedback import FeedbackCategory, FeedbackStatus
        registry = MagicMock()
        registry.query.return_value = []

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_registry=registry),
            op="GET",
            status="triaged",
            category="bug",
            agent_name="agent1",
            limit=5,
        )))
        self.assertTrue(parsed["ok"])
        kwargs = registry.query.call_args.kwargs
        self.assertEqual(kwargs["status"], FeedbackStatus.TRIAGED)
        self.assertEqual(kwargs["category"], FeedbackCategory.BUG)
        self.assertEqual(kwargs["agent_name"], "agent1")
        self.assertEqual(kwargs["limit"], 5)

    def test_query_invalid_status_is_ignored(self):
        registry = MagicMock()
        registry.query.return_value = []

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_registry=registry),
            op="query",
            status="bogus_status",
        )))
        self.assertTrue(parsed["ok"])
        self.assertIsNone(registry.query.call_args.kwargs["status"])

    def test_query_empty(self):
        registry = MagicMock()
        registry.query.return_value = []
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_registry=registry),
            op="query",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 0)
        self.assertEqual(parsed["data"]["entries"], [])


class TestHead(unittest.TestCase):
    def test_head_returns_compact_envelope(self):
        # HEAD uses GET's handler internally; our GET returns a dict
        # {"count": N, "entries": [...]}. project_head passes dicts through
        # unchanged — the HEAD envelope still gets ok=true.
        registry = MagicMock()
        registry.query.return_value = []
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_registry=registry),
            op="HEAD",
        )))
        self.assertEqual(parsed["method"], "HEAD")
        self.assertTrue(parsed["ok"])


# ========================================================================= #
# POST /feedback (CREATE) — submit_feedback                                 #
# ========================================================================= #


def _fake_context():
    """Build a real FeedbackContext (needed by FeedbackEntry's validator)."""
    from core.feedback import FeedbackContext
    return FeedbackContext(
        git_commit="abc123",
        git_branch="main",
        project_path="/tmp/project",
    )


class TestSubmitFeedback(unittest.TestCase):
    def test_submit_via_friendly_verb(self):
        registry = MagicMock()
        agent_registry = MagicMock()
        agent_registry.active_session = "session-123"
        agent_obj = MagicMock()
        agent_obj.name = "TestAgent"
        agent_registry.get_agent_by_session.return_value = agent_obj

        # Patch capture_context so we don't spawn git subprocesses in tests.
        with patch(
            "core.feedback.FeedbackCollector.capture_context",
            new=AsyncMock(return_value=_fake_context()),
        ):
            parsed = json.loads(asyncio.run(feedback(
                ctx=_make_ctx(
                    feedback_registry=registry,
                    agent_registry=agent_registry,
                ),
                op="submit",
                title="Something is broken",
                description="Deep description",
                category="bug",
            )))

        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "CREATE")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["status"], "submitted")
        self.assertEqual(parsed["data"]["category"], "bug")
        self.assertEqual(parsed["data"]["title"], "Something is broken")
        registry.add.assert_called_once()

    def test_submit_via_post_plus_definer(self):
        registry = MagicMock()
        agent_registry = MagicMock()
        agent_registry.active_session = None
        agent_registry.get_agent_by_session.return_value = None

        with patch(
            "core.feedback.FeedbackCollector.capture_context",
            new=AsyncMock(return_value=_fake_context()),
        ):
            parsed = json.loads(asyncio.run(feedback(
                ctx=_make_ctx(
                    feedback_registry=registry,
                    agent_registry=agent_registry,
                ),
                op="POST", definer="CREATE",
                title="T", description="D",
                agent_name="alice",
                session_id="s-1",
            )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["definer"], "CREATE")
        registry.add.assert_called_once()

    def test_submit_missing_title_returns_err(self):
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(),
            op="submit",
            description="No title",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("title", parsed["error"]["message"].lower())

    def test_submit_missing_description_returns_err(self):
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(),
            op="submit",
            title="Only title",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("description", parsed["error"]["message"].lower())


# ========================================================================= #
# POST /feedback/triggers (INVOKE) — check_feedback_triggers                #
# ========================================================================= #


class TestCheckTriggers(unittest.TestCase):
    def test_error_message_fires_threshold_via_invoke_verb(self):
        from core.feedback import FeedbackTriggerType

        hook_manager = MagicMock()
        hook_manager.get_stats.return_value = {
            "error_count": 0, "error_threshold": 3,
            "tool_call_count": 0, "tool_call_threshold": 100,
            "has_pending_trigger": False, "pending_trigger_type": None,
        }
        hook_manager.record_error.return_value = FeedbackTriggerType.ERROR_THRESHOLD

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_hook_manager=hook_manager),
            op="invoke", target="triggers",
            agent_name="a1", session_id="s1",
            error_message="Something failed",
        )))

        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "INVOKE")
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["should_collect_feedback"])
        triggers = parsed["data"]["triggers_fired"]
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["trigger"], "error_threshold")
        hook_manager.record_error.assert_called_once_with("a1", "Something failed")

    def test_tool_call_fires_periodic(self):
        from core.feedback import FeedbackTriggerType
        hook_manager = MagicMock()
        hook_manager.get_stats.return_value = {
            "error_count": 0, "error_threshold": 3,
            "tool_call_count": 0, "tool_call_threshold": 100,
            "has_pending_trigger": False, "pending_trigger_type": None,
        }
        hook_manager.record_tool_call.return_value = FeedbackTriggerType.PERIODIC
        hook_manager.record_error.return_value = None
        hook_manager.check_pattern.return_value = None

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_hook_manager=hook_manager),
            op="POST", definer="INVOKE", target="triggers",
            agent_name="a1", session_id="s1",
            tool_call_name="some_tool",
        )))
        self.assertTrue(parsed["ok"])
        triggers = parsed["data"]["triggers_fired"]
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["trigger"], "periodic")

    def test_pattern_detected(self):
        from core.feedback import FeedbackTriggerType
        hook_manager = MagicMock()
        hook_manager.get_stats.return_value = {
            "error_count": 0, "error_threshold": 3,
            "tool_call_count": 0, "tool_call_threshold": 100,
            "has_pending_trigger": False, "pending_trigger_type": None,
        }
        hook_manager.check_pattern.return_value = FeedbackTriggerType.PATTERN_DETECTED

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_hook_manager=hook_manager),
            op="invoke", target="triggers",
            agent_name="a1", session_id="s1",
            output_text="it would be better if...",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["triggers_fired"][0]["trigger"], "pattern")

    def test_no_triggers_fired(self):
        hook_manager = MagicMock()
        hook_manager.get_stats.return_value = {
            "error_count": 0, "error_threshold": 3,
            "tool_call_count": 0, "tool_call_threshold": 100,
            "has_pending_trigger": False, "pending_trigger_type": None,
        }

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_hook_manager=hook_manager),
            op="invoke", target="triggers",
            agent_name="a1", session_id="s1",
        )))
        self.assertTrue(parsed["ok"])
        self.assertFalse(parsed["data"]["should_collect_feedback"])
        self.assertEqual(parsed["data"]["triggers_fired"], [])

    def test_missing_agent_returns_err(self):
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(),
            op="invoke", target="triggers",
            session_id="s1",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("agent_name", parsed["error"]["message"].lower())

    def test_missing_session_returns_err(self):
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(),
            op="invoke", target="triggers",
            agent_name="a1",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("session_id", parsed["error"]["message"].lower())


# ========================================================================= #
# POST /feedback/{id}/worktrees (TRIGGER) — fork_for_feedback               #
# ========================================================================= #


class TestFork(unittest.TestCase):
    def test_fork_creates_worktree(self):
        forker = MagicMock()
        forker.create_worktree = AsyncMock(return_value="/tmp/ws/fb-1")
        forker.get_fork_command.return_value = (
            "cd /tmp/ws/fb-1 && claude --fork-session -r s-1"
        )

        agent_registry = MagicMock()
        agent_obj = MagicMock()
        agent_obj.name = "alice"
        agent_registry.get_agent_by_session.return_value = agent_obj

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(
                feedback_forker=forker,
                agent_registry=agent_registry,
            ),
            op="fork", target="worktrees",
            feedback_id="fb-1", session_id="s-1",
        )))

        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "TRIGGER")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["status"], "worktree_created")
        self.assertEqual(parsed["data"]["feedback_id"], "fb-1")
        self.assertIn("fork_command", parsed["data"])
        forker.create_worktree.assert_awaited_once_with("fb-1")
        forker.get_fork_command.assert_called_once_with("s-1", "/tmp/ws/fb-1")

    def test_fork_via_post_plus_definer(self):
        forker = MagicMock()
        forker.create_worktree = AsyncMock(return_value="/tmp/ws/fb-1")
        forker.get_fork_command.return_value = "claude --fork"

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_forker=forker),
            op="POST", definer="TRIGGER", target="worktrees",
            feedback_id="fb-1", session_id="s-1",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["definer"], "TRIGGER")

    def test_fork_missing_feedback_id_returns_err(self):
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(),
            op="fork", target="worktrees",
            session_id="s-1",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("feedback_id", parsed["error"]["message"].lower())

    def test_fork_missing_session_id_returns_err(self):
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(),
            op="fork", target="worktrees",
            feedback_id="fb-1",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("session_id", parsed["error"]["message"].lower())


# ========================================================================= #
# POST /feedback/{id}/issues (SEND) — triage_feedback_to_github             #
# ========================================================================= #


class TestTriageToGithub(unittest.TestCase):
    def test_triage_creates_issue_and_updates_entry(self):
        entry = _fake_entry(id_="fb-1", title="A bug", agent_name="alice")
        registry = MagicMock()
        registry.get.return_value = entry

        gh = MagicMock()
        gh.create_issue = AsyncMock(
            return_value="https://github.com/owner/repo/issues/42"
        )

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(
                feedback_registry=registry,
                github_integration=gh,
            ),
            op="triage", target="issues",
            feedback_id="fb-1",
            labels=["p1", "urgent"],
            assignee="maintainer",
        )))

        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "SEND")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["status"], "triaged")
        self.assertEqual(
            parsed["data"]["github_issue_url"],
            "https://github.com/owner/repo/issues/42",
        )
        gh.create_issue.assert_awaited_once()
        call_kwargs = gh.create_issue.await_args.kwargs
        self.assertEqual(call_kwargs["feedback"], entry)
        self.assertEqual(call_kwargs["labels"], ["p1", "urgent"])
        self.assertEqual(call_kwargs["assignee"], "maintainer")
        # Registry must be updated with the URL + TRIAGED status.
        from core.feedback import FeedbackStatus
        registry.update.assert_called_once()
        update_kwargs = registry.update.call_args.kwargs
        self.assertEqual(
            update_kwargs["github_issue_url"],
            "https://github.com/owner/repo/issues/42",
        )
        self.assertEqual(update_kwargs["status"], FeedbackStatus.TRIAGED)

    def test_triage_entry_not_found_returns_err(self):
        registry = MagicMock()
        registry.get.return_value = None

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_registry=registry),
            op="triage", target="issues",
            feedback_id="missing",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("missing", parsed["error"]["message"])

    def test_triage_gh_fails_returns_err(self):
        registry = MagicMock()
        registry.get.return_value = _fake_entry(id_="fb-1")

        gh = MagicMock()
        gh.create_issue = AsyncMock(return_value=None)

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(
                feedback_registry=registry,
                github_integration=gh,
            ),
            op="triage", target="issues",
            feedback_id="fb-1",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("Failed to create GitHub issue", parsed["error"]["message"])

    def test_triage_missing_feedback_id_returns_err(self):
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(),
            op="triage", target="issues",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("feedback_id", parsed["error"]["message"].lower())


# ========================================================================= #
# POST /feedback/{id}/notifications (SEND) — notify_feedback_update         #
# ========================================================================= #


class TestNotifyUpdate(unittest.TestCase):
    def test_notify_ready_for_testing(self):
        entry = _fake_entry(id_="fb-1", agent_name="alice")
        registry = MagicMock()
        registry.get.return_value = entry

        # Return an updated entry to exercise the update-swap path.
        updated = _fake_entry(
            id_="fb-1", agent_name="alice", status_value="testing"
        )
        registry.update.return_value = updated

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_registry=registry),
            op="notify", target="notifications",
            feedback_id="fb-1",
            update_type="ready_for_testing",
            message="PR is ready",
            pr_url="https://github.com/owner/repo/pull/7",
        )))

        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "SEND")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["status"], "notified")
        self.assertEqual(parsed["data"]["agent"], "alice")
        self.assertEqual(parsed["data"]["update_type"], "ready_for_testing")
        self.assertEqual(parsed["data"]["new_status"], "testing")

        # Status + pr_url must be persisted.
        from core.feedback import FeedbackStatus
        registry.update.assert_called_once()
        update_kwargs = registry.update.call_args.kwargs
        self.assertEqual(update_kwargs["status"], FeedbackStatus.TESTING)
        self.assertEqual(
            update_kwargs["github_pr_url"],
            "https://github.com/owner/repo/pull/7",
        )

    def test_notify_resolved(self):
        entry = _fake_entry(id_="fb-1", agent_name="alice")
        registry = MagicMock()
        registry.get.return_value = entry
        registry.update.return_value = _fake_entry(
            id_="fb-1", agent_name="alice", status_value="resolved"
        )

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_registry=registry),
            op="POST", definer="SEND", target="notifications",
            feedback_id="fb-1",
            update_type="resolved",
            message="All done",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["new_status"], "resolved")

    def test_notify_entry_not_found_returns_err(self):
        registry = MagicMock()
        registry.get.return_value = None

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_registry=registry),
            op="notify", target="notifications",
            feedback_id="missing",
            update_type="acknowledged",
            message="hi",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("missing", parsed["error"]["message"])

    def test_notify_missing_feedback_id_returns_err(self):
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(),
            op="notify", target="notifications",
            update_type="resolved",
            message="done",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("feedback_id", parsed["error"]["message"].lower())

    def test_notify_missing_update_type_returns_err(self):
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(),
            op="notify", target="notifications",
            feedback_id="fb-1",
            message="done",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("update_type", parsed["error"]["message"].lower())

    def test_notify_missing_message_returns_err(self):
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(),
            op="notify", target="notifications",
            feedback_id="fb-1",
            update_type="resolved",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("message", parsed["error"]["message"].lower())


# ========================================================================= #
# GET /feedback/config — fetch config                                       #
# ========================================================================= #


class TestGetConfig(unittest.TestCase):
    def test_get_config_returns_current(self):
        hook_manager = MagicMock()
        hook_manager.config = _fake_config(
            error_threshold_count=5,
            periodic_count=200,
            patterns=["p1", "p2"],
            github_repo="owner/repo",
            github_labels=["agent-feedback"],
        )

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_hook_manager=hook_manager),
            op="GET", target="config",
        )))

        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        data = parsed["data"]
        self.assertTrue(data["enabled"])
        self.assertEqual(data["error_threshold"]["count"], 5)
        self.assertEqual(data["periodic"]["tool_call_count"], 200)
        self.assertEqual(data["pattern"]["patterns"], ["p1", "p2"])
        self.assertEqual(data["github"]["repo"], "owner/repo")

    def test_get_config_via_get_verb(self):
        hook_manager = MagicMock()
        hook_manager.config = _fake_config()
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_hook_manager=hook_manager),
            op="get", target="config",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "GET")


# ========================================================================= #
# PATCH /feedback/config (MODIFY) — update config                           #
# ========================================================================= #


class TestUpdateConfig(unittest.TestCase):
    def test_update_error_threshold(self):
        hook_manager = MagicMock()
        hook_manager.config = _fake_config(error_threshold_count=3)

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_hook_manager=hook_manager),
            op="update", target="config",
            error_threshold_count=7,
        )))

        self.assertEqual(parsed["method"], "PATCH")
        self.assertEqual(parsed["definer"], "MODIFY")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["error_threshold"]["count"], 7)
        self.assertEqual(hook_manager.config.error_threshold.count, 7)
        hook_manager.save_config.assert_called_once()

    def test_update_periodic_count(self):
        hook_manager = MagicMock()
        hook_manager.config = _fake_config(periodic_count=100)

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_hook_manager=hook_manager),
            op="PATCH", definer="MODIFY", target="config",
            periodic_tool_call_count=250,
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["periodic"]["tool_call_count"], 250)

    def test_update_add_pattern(self):
        hook_manager = MagicMock()
        hook_manager.config = _fake_config(patterns=["existing"])

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_hook_manager=hook_manager),
            op="update", target="config",
            add_pattern="new_pattern",
        )))
        self.assertTrue(parsed["ok"])
        self.assertIn("new_pattern", parsed["data"]["pattern"]["patterns"])
        self.assertIn("existing", parsed["data"]["pattern"]["patterns"])

    def test_update_remove_pattern(self):
        hook_manager = MagicMock()
        hook_manager.config = _fake_config(patterns=["keep", "drop"])

        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(feedback_hook_manager=hook_manager),
            op="update", target="config",
            remove_pattern="drop",
        )))
        self.assertTrue(parsed["ok"])
        self.assertIn("keep", parsed["data"]["pattern"]["patterns"])
        self.assertNotIn("drop", parsed["data"]["pattern"]["patterns"])

    def test_update_unknown_target_returns_err(self):
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(),
            op="update", target="bogus",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not", parsed["error"]["message"].lower())


# ========================================================================= #
# DELETE — not supported (feedback entries are immutable-ish)               #
# ========================================================================= #


class TestDeleteNotImplemented(unittest.TestCase):
    def test_delete_returns_not_implemented_err(self):
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(),
            op="DELETE",
            feedback_id="fb-1",
        )))
        self.assertFalse(parsed["ok"])
        # The dispatcher's default on_delete raises NotImplementedError,
        # which the dispatcher converts into an err envelope.
        self.assertIn("not implemented", parsed["error"]["message"].lower())


# ========================================================================= #
# Unsupported POST combinations                                             #
# ========================================================================= #


class TestUnsupportedCombinations(unittest.TestCase):
    def test_post_invoke_wrong_target(self):
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(),
            op="POST", definer="INVOKE", target="bogus",
            agent_name="a1", session_id="s1",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not", parsed["error"]["message"].lower())

    def test_post_trigger_wrong_target(self):
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(),
            op="POST", definer="TRIGGER", target="bogus",
            feedback_id="fb-1", session_id="s1",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not", parsed["error"]["message"].lower())

    def test_post_send_wrong_target(self):
        parsed = json.loads(asyncio.run(feedback(
            ctx=_make_ctx(),
            op="POST", definer="SEND", target="bogus",
            feedback_id="fb-1",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not", parsed["error"]["message"].lower())


if __name__ == "__main__":
    unittest.main()
