import argparse
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PORTABLE = Path(__file__).resolve().parents[1] / "portable"
if str(PORTABLE) not in sys.path:
    sys.path.insert(0, str(PORTABLE))

import setup_check


class SetupDoctorTests(unittest.TestCase):
    def test_report_exposes_readiness_without_claiming_images_ready(self):
        with patch.object(setup_check, "_installed_models", return_value=(True, [{"name": "qwen3:8b", "size_gb": 5.0}], None)), \
             patch.object(setup_check, "_dependency_facts", return_value={
                 "pillow": True, "pdf_read": True, "pdf_create": True, "docx": True, "xlsx": True, "pptx": True,
                 "image_torch": True, "image_diffusers": True, "image_transformers": True,
                 "image_accelerate": True, "windows_gui": False, "windows_window_list": False,
             }), \
             patch.object(setup_check, "_ram_gb", return_value=16), \
             patch.object(setup_check, "_data_volume_free_gb", return_value=20.0), \
             patch.object(setup_check, "_gpu_facts", return_value={"nvidia_smi": True, "devices": [{"name": "Test GPU", "vram_gb": 8.0}], "note": "mock"}):
            report = setup_check.collect_report()
        self.assertEqual(report["overall"], "ready")
        self.assertIn("Core chat and document tools only", report["overall_scope"])
        self.assertTrue(report["capabilities"]["chat"]["available"])
        self.assertEqual(report["capabilities"]["images"]["status"], "needs_runtime_validation")
        self.assertEqual(report["capabilities"]["images"]["checkpoint"], "not inspected")
        encoded = json.dumps(report)
        self.assertNotIn("token", encoded.lower())
        self.assertNotIn("profile", encoded.lower())

    def test_model_identifiers_that_look_credential_shaped_are_redacted(self):
        private_name = "sk-example-private-value"
        with patch.object(setup_check, "_local_json", return_value={"models": [{"name": private_name, "size": 10}]}):
            available, models, error = setup_check._installed_models(2)
        self.assertTrue(available)
        self.assertIsNone(error)
        self.assertEqual(models[0]["name"], "[redacted model name]")
        self.assertNotIn(private_name, json.dumps(models))

    def test_local_json_pins_requests_to_loopback_and_bounds_body(self):
        class Response:
            body = b'{"models": []}'
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, size): self.requested_size = size; return self.body
        class Opener:
            def __init__(self): self.request = None; self.timeout = None; self.response = Response()
            def open(self, request, timeout): self.request, self.timeout = request, timeout; return self.response
        fake = Opener()
        with patch.object(setup_check, "_opener", return_value=fake):
            self.assertEqual(setup_check._local_json("/api/tags", timeout=2), {"models": []})
        self.assertEqual(fake.request.full_url, "http://127.0.0.1:11434/api/tags")
        self.assertEqual(fake.timeout, 2)
        self.assertEqual(fake.response.requested_size, setup_check.MAX_API_BYTES + 1)
        with self.assertRaises(ValueError):
            setup_check._local_json("/api/generate")
        fake.response.body = b" " * (setup_check.MAX_API_BYTES + 1)
        with patch.object(setup_check, "_opener", return_value=fake), self.assertRaises(ValueError):
            setup_check._local_json("/api/tags")

    def test_redirect_policy_rejects_non_loopback(self):
        handler = setup_check._LoopbackOnlyRedirect()
        with self.assertRaises(ValueError):
            handler.redirect_request(None, None, 302, "Found", {}, "https://example.com/api/tags")

    def test_chat_smoke_requires_visible_reply_after_thinking_filter(self):
        model = [{"name": "qwen3:8b"}]
        with patch.object(setup_check, "_local_json", return_value={"message": {"content": "<think>draft only"}}):
            result = setup_check._chat_smoke(model, 3)
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "empty_or_thinking_only_reply")
        with patch.object(setup_check, "_local_json", return_value={"message": {"content": "<think>draft</think>Hello there."}}):
            result = setup_check._chat_smoke(model, 3)
        self.assertTrue(result["passed"])
        self.assertEqual(result["status"], "reply_received")

    def test_chat_failure_report_does_not_echo_exception_or_secret(self):
        secret = "sk-example-private-value"
        with patch.object(setup_check, "_local_json", side_effect=ValueError(secret)):
            result = setup_check._chat_smoke([{"name": "qwen3:8b"}], 3)
        self.assertFalse(result["passed"])
        self.assertNotIn(secret, json.dumps(result))

    def test_spreadsheet_smoke_roundtrips_formula_in_temporary_output(self):
        with patch.object(setup_check, "_dependency_facts", return_value={"xlsx": True}):
            try:
                import openpyxl  # noqa: F401
            except ImportError:
                self.skipTest("openpyxl is not installed in this Python environment")
            result = setup_check._spreadsheet_smoke({"xlsx": True})
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["status"], "workbook_created_and_formula_validated")

    def test_timeout_rejects_nonfinite_and_unbounded_values(self):
        for value in ("nan", "inf", "0", "61", "nope"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                setup_check._timeout_arg(value)
        self.assertEqual(setup_check._timeout_arg("15"), 15)


if __name__ == "__main__":
    unittest.main()
