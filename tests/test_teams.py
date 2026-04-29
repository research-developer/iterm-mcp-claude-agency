"""Tests for teams dispatcher (SP2 Task 6)."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from iterm_mcpy.tools.teams import TeamsDispatcher, teams


def _make_ctx(
    agent_registry=None,
    profile_manager=None,
    service_hook_manager=None,
    logger=None,
    **extra,
):
    """Build a fake MCP Context with the lifespan context filled in.

    `**extra` keys go straight into `lifespan_context` so tests can inject
    whichever managers they need.
    """
    ctx = MagicMock()
    ctx.request_context.lifespan_context = {
        "agent_registry": agent_registry or MagicMock(),
        "profile_manager": profile_manager or MagicMock(),
        "service_hook_manager": service_hook_manager or MagicMock(),
        "logger": logger or MagicMock(),
        **extra,
    }
    return ctx


def _fake_hook_result(
    proceed=True,
    prompt_required=False,
    inactive_services=None,
    auto_started=None,
    message=None,
):
    """Build a stand-in for HookResult with the fields the dispatcher reads."""
    result = MagicMock()
    result.proceed = proceed
    result.prompt_required = prompt_required
    result.inactive_services = inactive_services or []
    result.auto_started = auto_started or []
    result.message = message
    return result


# ========================================================================= #
# OPTIONS / HEAD / unknown verb                                             #
# ========================================================================= #


class TestOptions(unittest.TestCase):
    def test_options_returns_schema(self):
        parsed = asyncio.run(teams(ctx=_make_ctx(), op="OPTIONS"))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["collection"], "teams")
        self.assertIn("GET", parsed["data"]["methods"])
        self.assertIn("POST", parsed["data"]["methods"])
        self.assertIn("DELETE", parsed["data"]["methods"])
        # Sub-resource 'agents' should be advertised for team membership.
        self.assertIn("agents", parsed["data"]["sub_resources"])

    def test_options_lists_post_definers(self):
        parsed = asyncio.run(teams(ctx=_make_ctx(), op="OPTIONS"))
        post = parsed["data"]["methods"]["POST"]
        self.assertIn("CREATE", post["definers"])

    def test_schema_verb_works(self):
        parsed = asyncio.run(teams(ctx=_make_ctx(), op="schema"))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])


class TestUnknownOp(unittest.TestCase):
    def test_bad_verb_returns_err_envelope(self):
        parsed = asyncio.run(teams(ctx=_make_ctx(), op="frobnicate"))
        self.assertFalse(parsed["ok"])
        self.assertIn("Unknown op", parsed["error"]["message"])


class TestWrongDefiner(unittest.TestCase):
    def test_post_replace_rejected(self):
        # REPLACE is in the PUT family, not POST.
        parsed = asyncio.run(
            teams(ctx=_make_ctx(), op="POST", definer="REPLACE")
        )
        self.assertFalse(parsed["ok"])
        self.assertIn("not in POST family", parsed["error"]["message"])


# ========================================================================= #
# GET /teams — list                                                         #
# ========================================================================= #


class TestList(unittest.TestCase):
    def test_list_returns_teams_with_member_counts(self):
        from core.agents import Team, Agent

        registry = MagicMock()
        registry.list_teams.return_value = [
            Team(name="backend", description="Backend squad"),
            Team(name="infra", description="Infrastructure", parent_team="root"),
        ]
        # list_agents is called once per team with team=<name> for member_count.
        registry.list_agents.side_effect = lambda team=None: {
            "backend": [Agent(name="alice", session_id="s1", teams=["backend"])],
            "infra": [],
        }.get(team, [])

        profile_manager = MagicMock()
        # No profile → skip profile fields.
        profile_manager.get_team_profile.return_value = None

        parsed = asyncio.run(teams(
            ctx=_make_ctx(
                agent_registry=registry,
                profile_manager=profile_manager,
            ),
            op="list",
        ))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 2)
        team_list = parsed["data"]["teams"]
        self.assertEqual(team_list[0]["name"], "backend")
        self.assertEqual(team_list[0]["description"], "Backend squad")
        self.assertEqual(team_list[0]["member_count"], 1)
        self.assertEqual(team_list[1]["name"], "infra")
        self.assertEqual(team_list[1]["parent_team"], "root")
        self.assertEqual(team_list[1]["member_count"], 0)

    def test_list_includes_profile_info_when_present(self):
        from core.agents import Team

        registry = MagicMock()
        registry.list_teams.return_value = [Team(name="backend")]
        registry.list_agents.return_value = []

        fake_color = MagicMock()
        fake_color.hue = 123.456
        fake_profile = MagicMock()
        fake_profile.guid = "guid-1"
        fake_profile.color = fake_color
        profile_manager = MagicMock()
        profile_manager.get_team_profile.return_value = fake_profile

        parsed = asyncio.run(teams(
            ctx=_make_ctx(
                agent_registry=registry,
                profile_manager=profile_manager,
            ),
            op="GET",
        ))
        self.assertTrue(parsed["ok"])
        team = parsed["data"]["teams"][0]
        self.assertEqual(team["profile_guid"], "guid-1")
        self.assertEqual(team["color_hue"], 123.5)

    def test_list_empty(self):
        registry = MagicMock()
        registry.list_teams.return_value = []
        parsed = asyncio.run(teams(
            ctx=_make_ctx(agent_registry=registry),
            op="list",
        ))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 0)
        self.assertEqual(parsed["data"]["teams"], [])


class TestHead(unittest.TestCase):
    def test_head_returns_compact_envelope(self):
        # HEAD uses GET's handler internally; our GET returns a dict, which
        # passes through project_head unchanged. That's acceptable — the
        # HEAD envelope still gets ok=true and a compact summary.
        registry = MagicMock()
        registry.list_teams.return_value = []
        parsed = asyncio.run(teams(
            ctx=_make_ctx(agent_registry=registry),
            op="HEAD",
        ))
        self.assertEqual(parsed["method"], "HEAD")
        self.assertTrue(parsed["ok"])


# ========================================================================= #
# POST /teams (CREATE) — create team                                        #
# ========================================================================= #


class TestCreateTeam(unittest.TestCase):
    def _setup_ctx(self):
        from core.agents import Team

        registry = MagicMock()
        registry.create_team.return_value = Team(
            name="infra",
            description="Infrastructure team",
            parent_team=None,
        )

        fake_color = MagicMock()
        fake_color.hue = 45.0
        fake_profile = MagicMock()
        fake_profile.guid = "guid-infra"
        fake_profile.color = fake_color
        profile_manager = MagicMock()
        profile_manager.get_or_create_team_profile.return_value = fake_profile

        service_hook_manager = MagicMock()
        service_hook_manager.pre_create_team_hook = AsyncMock(
            return_value=_fake_hook_result()
        )
        return registry, profile_manager, service_hook_manager

    def test_create_team_via_friendly_verb(self):
        registry, profile_manager, service_hook_manager = self._setup_ctx()

        parsed = asyncio.run(teams(
            ctx=_make_ctx(
                agent_registry=registry,
                profile_manager=profile_manager,
                service_hook_manager=service_hook_manager,
            ),
            op="create",
            team_name="infra",
            description="Infrastructure team",
        ))
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "CREATE")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["name"], "infra")
        self.assertEqual(parsed["data"]["description"], "Infrastructure team")
        self.assertEqual(parsed["data"]["profile_guid"], "guid-infra")
        self.assertEqual(parsed["data"]["color_hue"], 45.0)
        registry.create_team.assert_called_once_with(
            name="infra",
            description="Infrastructure team",
            parent_team=None,
        )
        profile_manager.get_or_create_team_profile.assert_called_once_with("infra")
        profile_manager.save_profiles.assert_called_once()
        service_hook_manager.pre_create_team_hook.assert_awaited_once_with(
            team_name="infra",
            repo_path=None,
        )

    def test_create_team_via_post_plus_definer(self):
        registry, profile_manager, service_hook_manager = self._setup_ctx()
        parsed = asyncio.run(teams(
            ctx=_make_ctx(
                agent_registry=registry,
                profile_manager=profile_manager,
                service_hook_manager=service_hook_manager,
            ),
            op="POST",
            definer="CREATE",
            team_name="infra",
        ))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["definer"], "CREATE")

    def test_create_team_missing_name_returns_err(self):
        parsed = asyncio.run(teams(
            ctx=_make_ctx(),
            op="create",
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("team_name", parsed["error"]["message"].lower())

    def test_create_team_with_parent_and_repo_path(self):
        registry, profile_manager, service_hook_manager = self._setup_ctx()
        parsed = asyncio.run(teams(
            ctx=_make_ctx(
                agent_registry=registry,
                profile_manager=profile_manager,
                service_hook_manager=service_hook_manager,
            ),
            op="create",
            team_name="infra",
            parent_team="engineering",
            repo_path="/repo",
        ))
        self.assertTrue(parsed["ok"])
        registry.create_team.assert_called_once_with(
            name="infra",
            description="",
            parent_team="engineering",
        )
        service_hook_manager.pre_create_team_hook.assert_awaited_once_with(
            team_name="infra",
            repo_path="/repo",
        )

    def test_create_team_hook_blocks(self):
        registry = MagicMock()
        profile_manager = MagicMock()
        service_hook_manager = MagicMock()
        service_hook_manager.pre_create_team_hook = AsyncMock(
            return_value=_fake_hook_result(
                proceed=False,
                message="Required service failed to start",
            )
        )

        parsed = asyncio.run(teams(
            ctx=_make_ctx(
                agent_registry=registry,
                profile_manager=profile_manager,
                service_hook_manager=service_hook_manager,
            ),
            op="create",
            team_name="infra",
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("Required service failed", parsed["error"]["message"])
        # Must NOT have created the team or profile when hook blocks.
        registry.create_team.assert_not_called()
        profile_manager.get_or_create_team_profile.assert_not_called()

    def test_create_team_hook_prompt_included_in_response(self):
        from core.agents import Team

        # Build a fake inactive service so the prompt info makes it into the response.
        fake_service = MagicMock()
        fake_service.name = "postgres"
        fake_service.effective_display_name = "Postgres"
        fake_service.priority.value = "preferred"

        registry = MagicMock()
        registry.create_team.return_value = Team(name="infra", description="")
        profile_manager = MagicMock()
        fake_color = MagicMock()
        fake_color.hue = 10.0
        fake_profile = MagicMock()
        fake_profile.guid = "guid-infra"
        fake_profile.color = fake_color
        profile_manager.get_or_create_team_profile.return_value = fake_profile
        service_hook_manager = MagicMock()
        service_hook_manager.pre_create_team_hook = AsyncMock(
            return_value=_fake_hook_result(
                proceed=True,
                prompt_required=True,
                inactive_services=[fake_service],
                message="Start postgres?",
            )
        )

        parsed = asyncio.run(teams(
            ctx=_make_ctx(
                agent_registry=registry,
                profile_manager=profile_manager,
                service_hook_manager=service_hook_manager,
            ),
            op="create",
            team_name="infra",
            repo_path="/repo",
        ))
        self.assertTrue(parsed["ok"])
        sp = parsed["data"]["service_prompt"]
        self.assertEqual(sp["message"], "Start postgres?")
        self.assertEqual(sp["inactive_services"][0]["name"], "postgres")
        self.assertTrue(sp["action_required"])


# ========================================================================= #
# POST /teams/{name}/agents (CREATE) — assign agent to team                 #
# ========================================================================= #


class TestAssignAgent(unittest.TestCase):
    def test_assign_agent_delegates_to_registry(self):
        registry = MagicMock()
        registry.assign_to_team.return_value = True

        parsed = asyncio.run(teams(
            ctx=_make_ctx(agent_registry=registry),
            op="POST", definer="CREATE", target="agents",
            team_name="infra", agent_name="alice",
        ))
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "CREATE")
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["assigned"])
        self.assertEqual(parsed["data"]["team_name"], "infra")
        self.assertEqual(parsed["data"]["agent_name"], "alice")
        registry.assign_to_team.assert_called_once_with("alice", "infra")

    def test_assign_agent_fails_when_not_found_or_already_member(self):
        registry = MagicMock()
        registry.assign_to_team.return_value = False

        parsed = asyncio.run(teams(
            ctx=_make_ctx(agent_registry=registry),
            op="create", target="agents",
            team_name="infra", agent_name="missing",
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("agent not found", parsed["error"]["message"].lower())

    def test_assign_agent_missing_team_name_returns_err(self):
        parsed = asyncio.run(teams(
            ctx=_make_ctx(),
            op="create", target="agents", agent_name="alice",
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("team_name", parsed["error"]["message"].lower())

    def test_assign_agent_missing_agent_name_returns_err(self):
        parsed = asyncio.run(teams(
            ctx=_make_ctx(),
            op="create", target="agents", team_name="infra",
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("agent_name", parsed["error"]["message"].lower())


# ========================================================================= #
# DELETE /teams/{name} — remove team                                        #
# ========================================================================= #


class TestRemoveTeam(unittest.TestCase):
    def test_remove_team_removes_profile_too(self):
        registry = MagicMock()
        registry.remove_team.return_value = True
        profile_manager = MagicMock()
        profile_manager.remove_team_profile.return_value = True

        parsed = asyncio.run(teams(
            ctx=_make_ctx(
                agent_registry=registry,
                profile_manager=profile_manager,
            ),
            op="delete",
            team_name="infra",
        ))
        self.assertEqual(parsed["method"], "DELETE")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["team_name"], "infra")
        self.assertTrue(parsed["data"]["profile_removed"])
        registry.remove_team.assert_called_once_with("infra")
        profile_manager.remove_team_profile.assert_called_once_with("infra")
        profile_manager.save_profiles.assert_called_once()

    def test_remove_team_without_profile(self):
        registry = MagicMock()
        registry.remove_team.return_value = True
        profile_manager = MagicMock()
        profile_manager.remove_team_profile.return_value = False

        parsed = asyncio.run(teams(
            ctx=_make_ctx(
                agent_registry=registry,
                profile_manager=profile_manager,
            ),
            op="DELETE",
            team_name="infra",
        ))
        self.assertTrue(parsed["ok"])
        self.assertFalse(parsed["data"]["profile_removed"])
        profile_manager.save_profiles.assert_not_called()

    def test_remove_team_not_found_returns_err(self):
        registry = MagicMock()
        registry.remove_team.return_value = False

        parsed = asyncio.run(teams(
            ctx=_make_ctx(agent_registry=registry),
            op="delete",
            team_name="missing",
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("missing", parsed["error"]["message"])

    def test_remove_team_missing_name_returns_err(self):
        parsed = asyncio.run(teams(
            ctx=_make_ctx(),
            op="delete",
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("team_name", parsed["error"]["message"].lower())


# ========================================================================= #
# DELETE /teams/{name}/agents — remove agent from team                      #
# ========================================================================= #


class TestRemoveAgentFromTeam(unittest.TestCase):
    def test_remove_agent_delegates_to_registry(self):
        registry = MagicMock()
        registry.remove_from_team.return_value = True

        parsed = asyncio.run(teams(
            ctx=_make_ctx(agent_registry=registry),
            op="delete", target="agents",
            team_name="infra", agent_name="alice",
        ))
        self.assertEqual(parsed["method"], "DELETE")
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["removed"])
        self.assertEqual(parsed["data"]["team_name"], "infra")
        self.assertEqual(parsed["data"]["agent_name"], "alice")
        registry.remove_from_team.assert_called_once_with("alice", "infra")

    def test_remove_agent_not_found_returns_err(self):
        registry = MagicMock()
        registry.remove_from_team.return_value = False

        parsed = asyncio.run(teams(
            ctx=_make_ctx(agent_registry=registry),
            op="delete", target="agents",
            team_name="infra", agent_name="missing",
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("not found", parsed["error"]["message"].lower())

    def test_remove_agent_missing_team_returns_err(self):
        parsed = asyncio.run(teams(
            ctx=_make_ctx(),
            op="DELETE", target="agents", agent_name="alice",
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("team_name", parsed["error"]["message"].lower())

    def test_remove_agent_missing_agent_returns_err(self):
        parsed = asyncio.run(teams(
            ctx=_make_ctx(),
            op="DELETE", target="agents", team_name="infra",
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("agent_name", parsed["error"]["message"].lower())


# ========================================================================= #
# Unsupported combinations                                                  #
# ========================================================================= #


class TestUnsupportedCombinations(unittest.TestCase):
    def test_post_send_not_implemented(self):
        parsed = asyncio.run(
            teams(ctx=_make_ctx(), op="POST", definer="SEND")
        )
        self.assertFalse(parsed["ok"])
        self.assertIn("not", parsed["error"]["message"].lower())

    def test_delete_unknown_target_not_implemented(self):
        parsed = asyncio.run(teams(
            ctx=_make_ctx(),
            op="DELETE", target="bogus",
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("not", parsed["error"]["message"].lower())


if __name__ == "__main__":
    unittest.main()
