"""Tests for response serialization helpers."""
import unittest
from pydantic import BaseModel
from typing import Optional, Dict, Any


class SampleResponse(BaseModel):
    operation: str
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class TestOkJson(unittest.TestCase):
    def test_excludes_none_fields(self):
        from iterm_mcpy.responses import ok_json
        resp = SampleResponse(operation="test", success=True, data={"count": 5})
        result = ok_json(resp)
        self.assertNotIn('"error"', result)
        self.assertNotIn("null", result)

    def test_includes_present_fields(self):
        from iterm_mcpy.responses import ok_json
        resp = SampleResponse(operation="test", success=True, data={"key": "val"})
        result = ok_json(resp)
        self.assertIn('"operation"', result)
        self.assertIn('"success"', result)
        self.assertIn('"data"', result)

    def test_includes_error_when_present(self):
        from iterm_mcpy.responses import ok_json
        resp = SampleResponse(operation="test", success=False, error="something broke")
        result = ok_json(resp)
        self.assertIn('"error"', result)
        self.assertIn("something broke", result)

    def test_returns_valid_json(self):
        import json
        from iterm_mcpy.responses import ok_json
        resp = SampleResponse(operation="test", success=True)
        parsed = json.loads(ok_json(resp))
        self.assertEqual(parsed["operation"], "test")


if __name__ == "__main__":
    unittest.main()
