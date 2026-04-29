"""Tests for the structured error contract introduced for fb-20260424-157473f7 #1b."""
import json
import unittest

from iterm_mcpy.errors import ErrorCode, ToolError


class TestErrorCode(unittest.TestCase):
    def test_codes_are_lower_snake_strings(self):
        # Codes are part of the public API; they must be stable strings.
        self.assertEqual(ErrorCode.INVALID_OP.value, "invalid_op")
        self.assertEqual(ErrorCode.SESSION_NOT_FOUND.value, "session_not_found")
        self.assertEqual(ErrorCode.INTERNAL.value, "internal")

    def test_str_enum_compares_to_strings(self):
        self.assertEqual(ErrorCode.INVALID_OP, "invalid_op")


class TestToolError(unittest.TestCase):
    def test_construct_with_code_and_message(self):
        err = ToolError(ErrorCode.INVALID_OP, "Unknown op 'foo'")
        self.assertEqual(err.code, ErrorCode.INVALID_OP)
        self.assertEqual(err.message, "Unknown op 'foo'")
        self.assertIsNone(err.hint)

    def test_construct_with_hint(self):
        err = ToolError(
            ErrorCode.INVALID_OP,
            "Unknown op 'foo'",
            hint="Try op='GET' or op='POST'",
        )
        self.assertEqual(err.hint, "Try op='GET' or op='POST'")

    def test_to_dict_shape(self):
        err = ToolError(ErrorCode.SESSION_NOT_FOUND, "no such session", hint="check id")
        self.assertEqual(
            err.to_dict(),
            {
                "code": "session_not_found",
                "message": "no such session",
                "hint": "check id",
            },
        )

    def test_to_dict_omits_hint_when_none(self):
        err = ToolError(ErrorCode.INTERNAL, "oops")
        self.assertEqual(err.to_dict(), {"code": "internal", "message": "oops"})

    def test_is_an_exception(self):
        # Tools should be able to `raise ToolError(...)` and let the dispatcher
        # catch it.
        with self.assertRaises(ToolError):
            raise ToolError(ErrorCode.INVALID_PARAM, "missing 'session_id'")


class TestFromException(unittest.TestCase):
    def test_keyerror_maps_to_missing_param(self):
        try:
            raise KeyError("layout")
        except KeyError as exc:
            err = ToolError.from_exception(exc)
        self.assertEqual(err.code, ErrorCode.MISSING_PARAM)
        self.assertIn("layout", err.message)

    def test_value_error_maps_to_invalid_param(self):
        try:
            raise ValueError("bad layout 'spiral'")
        except ValueError as exc:
            err = ToolError.from_exception(exc)
        self.assertEqual(err.code, ErrorCode.INVALID_PARAM)
        self.assertIn("spiral", err.message)

    def test_unknown_exception_maps_to_internal(self):
        err = ToolError.from_exception(RuntimeError("kernel panic"))
        self.assertEqual(err.code, ErrorCode.INTERNAL)
        self.assertIn("kernel panic", err.message)

    def test_passes_through_existing_toolerror(self):
        # If the caller already raised a ToolError, from_exception should
        # return it unchanged (no double-wrapping).
        original = ToolError(ErrorCode.LOCKED, "session is locked", hint="unlock first")
        passed = ToolError.from_exception(original)
        self.assertIs(passed, original)


class TestErrEnvelopeAcceptsToolError(unittest.TestCase):
    def test_err_envelope_with_toolerror_yields_structured_shape(self):
        from iterm_mcpy.responses import err_envelope
        env = err_envelope(
            method="POST",
            error=ToolError(ErrorCode.INVALID_OP, "Unknown op 'foo'", hint="see OPTIONS"),
            definer="CREATE",
        )
        parsed = json.loads(env)
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "CREATE")
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["error"]["code"], "invalid_op")
        self.assertEqual(parsed["error"]["message"], "Unknown op 'foo'")
        self.assertEqual(parsed["error"]["hint"], "see OPTIONS")

    def test_err_envelope_with_legacy_string_still_works(self):
        # Backward compatibility for sites we haven't migrated yet: passing
        # a bare string still produces an envelope, but with the new shape
        # — the string becomes the `message`, with code=INTERNAL.
        from iterm_mcpy.responses import err_envelope
        env = err_envelope(method="GET", error="something broke")
        parsed = json.loads(env)
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["error"]["code"], "internal")
        self.assertEqual(parsed["error"]["message"], "something broke")
        self.assertNotIn("hint", parsed["error"])


if __name__ == "__main__":
    unittest.main()
