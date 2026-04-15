"""Tests for Tier 1 definer verb machinery."""
import pytest
from core.definer_verbs import (
    resolve_op, validate_definer,
    UnknownVerbError, WrongFamilyError,
)


class TestResolveOp:
    def test_http_method_passes_through(self):
        r = resolve_op("GET")
        assert r.method == "GET"
        assert r.definer is None

    def test_post_defaults_to_canonical(self):
        r = resolve_op("POST")
        assert r.method == "POST"
        assert r.definer == "CREATE"

    def test_patch_defaults_to_canonical(self):
        r = resolve_op("PATCH")
        assert r.method == "PATCH"
        assert r.definer == "MODIFY"

    def test_put_defaults_to_canonical(self):
        r = resolve_op("PUT")
        assert r.method == "PUT"
        assert r.definer == "REPLACE"

    def test_friendly_verb_list_maps_to_get(self):
        r = resolve_op("list")
        assert r.method == "GET"

    def test_friendly_verb_submit_maps_to_post_create(self):
        r = resolve_op("submit")
        assert r.method == "POST"
        assert r.definer == "CREATE"

    def test_friendly_verb_fork_maps_to_post_trigger(self):
        r = resolve_op("fork")
        assert r.method == "POST"
        assert r.definer == "TRIGGER"

    def test_friendly_verb_triage_maps_to_post_send(self):
        r = resolve_op("triage")
        assert r.method == "POST"
        assert r.definer == "SEND"

    def test_friendly_verb_remove_maps_to_delete(self):
        r = resolve_op("remove")
        assert r.method == "DELETE"

    def test_friendly_verb_peek_maps_to_head(self):
        r = resolve_op("peek")
        assert r.method == "HEAD"

    def test_friendly_verb_schema_maps_to_options(self):
        r = resolve_op("schema")
        assert r.method == "OPTIONS"

    def test_unknown_verb_raises(self):
        with pytest.raises(UnknownVerbError):
            resolve_op("frobnicate")

    def test_explicit_method_plus_definer(self):
        r = resolve_op("POST", definer="TRIGGER")
        assert r.method == "POST"
        assert r.definer == "TRIGGER"

    def test_wrong_family_definer_raises(self):
        with pytest.raises(WrongFamilyError):
            resolve_op("POST", definer="REPLACE")  # REPLACE is PUT family

    def test_head_no_definer(self):
        r = resolve_op("HEAD")
        assert r.method == "HEAD"
        assert r.definer is None

    def test_options_no_definer(self):
        r = resolve_op("OPTIONS")
        assert r.method == "OPTIONS"
        assert r.definer is None

    def test_delete_no_definer(self):
        r = resolve_op("DELETE")
        assert r.method == "DELETE"
        assert r.definer is None

    def test_case_insensitive_method(self):
        r = resolve_op("get")
        assert r.method == "GET"

    def test_case_insensitive_definer(self):
        r = resolve_op("POST", definer="create")
        assert r.definer == "CREATE"


class TestValidateDefiner:
    def test_post_create_valid(self):
        assert validate_definer("POST", "CREATE") is True

    def test_post_modify_invalid(self):
        assert validate_definer("POST", "MODIFY") is False

    def test_put_replace_valid(self):
        assert validate_definer("PUT", "REPLACE") is True

    def test_put_create_invalid(self):
        assert validate_definer("PUT", "CREATE") is False

    def test_patch_append_valid(self):
        assert validate_definer("PATCH", "APPEND") is True

    def test_get_ignores_definer(self):
        assert validate_definer("GET", "CREATE") is True  # Safe methods ignore definers

    def test_delete_ignores_definer(self):
        assert validate_definer("DELETE", "REMOVE") is True

    def test_post_missing_definer_invalid(self):
        assert validate_definer("POST", None) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
