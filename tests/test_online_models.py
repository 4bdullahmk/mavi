from __future__ import annotations

import sys
import threading
import unittest
from unittest.mock import Mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
from online_models import (  # noqa: E402
    OnlineModelCancelled,
    OnlineModelError,
    OnlineRouter,
    OnlineUnavailable,
    _NoRedirect,
    _ProviderFailure,
)


def success(text="ok", model="selected/model"):
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15, "secret": "discard"},
    }


class OnlineRouterTests(unittest.TestCase):
    def make_router(self, routes=None, *, allow_paid=False, mode="hybrid", keys=None):
        router = OnlineRouter()
        router.configure({"mode": mode, "routes": routes or [
            {"provider": "nvidia", "model": "test/model", "roles": ["chat"]}
        ], "allow_paid": allow_paid}, keys=keys or {"nvidia": "secret-nvidia-key"})
        return router

    def test_local_mode_never_attempts_network(self):
        router = self.make_router(mode="local")
        router._request_json = Mock(side_effect=AssertionError("network must not be called"))
        with self.assertRaises(OnlineUnavailable):
            router.complete([{"role": "user", "content": "hello"}])
        router._request_json.assert_not_called()

    def test_provider_status_supports_saving_key_before_route_selection(self):
        router = OnlineRouter()
        router.configure({"mode": "local", "routes": []}, keys={"groq": "memory-only"})
        snapshot = router.snapshot()
        self.assertEqual(snapshot["config"]["routes"], [])
        statuses = {item["id"]: item for item in snapshot["providers"]}
        self.assertTrue(statuses["groq"]["configured"])
        self.assertEqual(statuses["groq"]["status"], "ready")
        self.assertFalse(statuses["nvidia"]["configured"])
        self.assertNotIn("memory-only", repr(snapshot))

    def test_config_snapshot_is_safe_and_can_be_reloaded_without_keys(self):
        router = self.make_router()
        snapshot = router.snapshot()
        self.assertNotIn("secret-nvidia-key", repr(snapshot))
        self.assertEqual(snapshot["config"]["mode"], "hybrid")
        self.assertEqual(snapshot["config"]["routes"][0]["model"], "test/model")
        self.assertTrue(snapshot["routes"][0]["key_configured"])
        reloaded = OnlineRouter()
        self.assertEqual(reloaded.configure(snapshot["config"]), snapshot["config"])
        self.assertFalse(reloaded.snapshot()["routes"][0]["key_configured"])

    def test_route_roles_are_allowlisted_and_limited(self):
        router = OnlineRouter()
        for role in ("router", "vision", "computer", "image"):
            with self.subTest(role=role), self.assertRaises(ValueError):
                router.configure({"mode": "hybrid", "routes": [
                    {"provider": "groq", "model": "model", "roles": [role]}
                ]})
        with self.assertRaises(ValueError):
            router.configure({"mode": "hybrid", "routes": [
                {"provider": "groq", "model": str(i), "roles": ["chat"]} for i in range(9)
            ]})

    def test_provider_keys_reject_header_unsafe_non_ascii_and_controls(self):
        router = OnlineRouter()
        for key in ("secret\nInjected: yes", "non-ascii-🔐"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                router.configure({"mode": "hybrid", "routes": []}, keys={"groq": key})

    def test_fixed_provider_endpoints_cannot_be_overridden(self):
        router = OnlineRouter()
        with self.assertRaises(ValueError):
            router.configure({"mode": "hybrid", "base_url": "https://attacker.invalid/v1", "routes": []})
        with self.assertRaises(ValueError):
            router.configure({"mode": "hybrid", "routes": [
                {"provider": "nvidia", "model": "model", "roles": ["chat"], "endpoint": "https://attacker.invalid"}
            ]})

    def test_gateway_must_be_explicit_loopback_v1_with_port(self):
        router = OnlineRouter()
        for url in (
            "https://127.0.0.1:8080/v1", "http://example.com:8080/v1",
            "http://127.0.0.1/v1", "http://user:pass@127.0.0.1:8080/v1",
            "http://127.0.0.1:8080/v1?token=x", "http://127.0.0.1:8080/v1/other",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                router.configure({"mode": "hybrid", "gateway_url": url, "routes": [
                    {"provider": "gateway", "model": "local/model", "roles": ["chat"]}
                ]})
        safe = router.configure({"mode": "hybrid", "gateway_url": "http://127.0.0.1:8000/v1", "routes": [
            {"provider": "gateway", "model": "local/model", "roles": ["chat"]}
        ]})
        self.assertEqual(safe["gateway_url"], "http://127.0.0.1:8000/v1")

    def test_openrouter_requires_free_model_unless_explicitly_opted_in(self):
        router = OnlineRouter()
        with self.assertRaisesRegex(ValueError, "marked :free"):
            router.configure({"mode": "hybrid", "routes": [
                {"provider": "openrouter", "model": "vendor/private", "roles": ["chat"]}
            ]})
        config = router.configure({"mode": "hybrid", "routes": [
            {"provider": "openrouter", "model": "vendor/free-model:free", "roles": ["chat"]}
        ]})
        self.assertFalse(config["allow_paid"])
        config = router.configure({"mode": "hybrid", "allow_paid": True, "routes": [
            {"provider": "openrouter", "model": "vendor/model", "roles": ["chat"]}
        ]})
        self.assertTrue(config["allow_paid"])

    def test_falls_back_once_per_route_and_returns_bounded_usage(self):
        routes = [
            {"provider": "nvidia", "model": "one", "roles": ["chat"]},
            {"provider": "groq", "model": "two", "roles": ["chat"]},
        ]
        router = self.make_router(routes, keys={"nvidia": "k1", "groq": "k2"})
        calls = []

        def request(provider, url, payload, key, cancelled):
            calls.append(provider)
            if provider == "nvidia":
                raise _ProviderFailure(503, None, None)
            return success("answer")

        router._request_json = request
        result = router.complete([{"role": "user", "content": "hello"}], role="chat")
        self.assertEqual(calls, ["nvidia", "groq"])
        self.assertEqual(result, {"text": "answer", "provider": "groq", "model": "two",
                                  "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}})

    def test_rate_limit_cooldown_and_long_retry_after_are_respected(self):
        routes = [
            {"provider": "nvidia", "model": "one", "roles": ["chat"]},
            {"provider": "groq", "model": "two", "roles": ["chat"]},
        ]
        router = self.make_router(routes, keys={"nvidia": "k1", "groq": "k2"})
        calls = []

        def request(provider, url, payload, key, cancelled):
            calls.append(provider)
            if provider == "nvidia":
                raise _ProviderFailure(429, None, 86400)
            return success("fallback")

        router._request_json = request
        router.complete([{"role": "user", "content": "hello"}])
        self.assertEqual(calls, ["nvidia", "groq"])
        self.assertGreaterEqual(router.snapshot()["routes"][0]["cooldown_seconds"], 86399)
        self.assertEqual(OnlineRouter._retry_after({"Retry-After": "86400"}), 86400)
        calls.clear()
        router._request_json = lambda provider, *args: (calls.append(provider) or success("next"))
        router.complete([{"role": "user", "content": "again"}])
        self.assertEqual(calls, ["groq"])

    def test_auth_failure_blocks_until_key_changes_and_never_leaks(self):
        secret = "do-not-leak-this-token"
        router = self.make_router(keys={"nvidia": secret})
        router._request_json = Mock(side_effect=_ProviderFailure(401, None, None))
        with self.assertRaises(OnlineUnavailable) as caught:
            router.complete([{"role": "user", "content": "hello"}])
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, repr(router.snapshot()))
        self.assertTrue(router.snapshot()["routes"][0]["auth_blocked"])
        router.configure(router.snapshot()["config"], keys={"nvidia": secret})
        self.assertTrue(router.snapshot()["routes"][0]["auth_blocked"])
        router.configure(router.snapshot()["config"], keys={"nvidia": "new-key"})
        self.assertFalse(router.snapshot()["routes"][0]["auth_blocked"])

    def test_route_hints_select_distinct_configured_routes(self):
        routes = [
            {"provider": "nvidia", "model": "first", "roles": ["analysis"]},
            {"provider": "groq", "model": "second", "roles": ["analysis"]},
        ]
        router = self.make_router(routes, keys={"nvidia": "k1", "groq": "k2"})
        calls = []
        router._request_json = lambda provider, url, payload, key, cancelled: (calls.append(provider) or success(provider))
        first = router.complete([{"role": "user", "content": "compare"}], role="analysis", route_hint="online:nvidia:first")
        second = router.complete([{"role": "user", "content": "compare"}], role="analysis", route_hint="online:groq:second")
        self.assertEqual(calls, ["nvidia", "groq"])
        self.assertEqual((first["provider"], first["model"]), ("nvidia", "first"))
        self.assertEqual((second["provider"], second["model"]), ("groq", "second"))
        calls.clear()
        with self.assertRaises(OnlineUnavailable):
            router.complete([{"role": "user", "content": "compare"}], role="analysis", route_hint="online:nvidia:not-configured")
        self.assertEqual(calls, [])

    def test_local_mode_precedes_multimodal_validation_for_local_fallback(self):
        router = self.make_router(mode="local")
        router._request_json = Mock(side_effect=AssertionError("network must not be called"))
        multimodal = [{"role": "user", "content": [{"type": "image_url", "image_url": "https://example.invalid/x"}]}]
        with self.assertRaises(OnlineUnavailable):
            router.complete(multimodal)
        router._request_json.assert_not_called()

    def test_cancelled_before_request_and_between_fallbacks(self):
        router = self.make_router(routes=[
            {"provider": "nvidia", "model": "one", "roles": ["chat"]},
            {"provider": "groq", "model": "two", "roles": ["chat"]},
        ], keys={"nvidia": "k1", "groq": "k2"})
        cancelled = threading.Event()
        cancelled.set()
        router._request_json = Mock()
        with self.assertRaises(OnlineModelCancelled):
            router.complete([{"role": "user", "content": "hi"}], cancelled=cancelled)
        router._request_json.assert_not_called()

        cancelled.clear()
        calls = []
        def cancel_after_first(provider, url, payload, key, event):
            calls.append(provider)
            cancelled.set()
            raise _ProviderFailure(503, None, None)
        router._request_json = cancel_after_first
        with self.assertRaises(OnlineModelCancelled):
            router.complete([{"role": "user", "content": "hi"}], cancelled=cancelled)
        self.assertEqual(calls, ["nvidia"])

    def test_multimodal_messages_are_rejected_and_never_fallback(self):
        router = self.make_router(routes=[
            {"provider": "nvidia", "model": "one", "roles": ["chat"]},
            {"provider": "groq", "model": "two", "roles": ["chat"]},
        ], keys={"nvidia": "k1", "groq": "k2"})
        router._request_json = Mock()
        with self.assertRaises(ValueError):
            router.complete([{"role": "user", "content": [{"type": "image_url", "image_url": "https://example.invalid/x"}]}])
        with self.assertRaises(ValueError):
            router.complete([{"role": "user", "content": "text", "image": "data:..."}])
        router._request_json.assert_not_called()

    def test_redirects_are_never_followed(self):
        handler = _NoRedirect()
        request = object()
        with self.assertRaises(OnlineModelError):
            handler.redirect_request(request, None, 302, "Found", {}, "https://attacker.invalid/")

    def test_progress_is_sanitized_and_unavailable_errors_are_generic(self):
        router = self.make_router()
        progress = []
        router._request_json = Mock(side_effect=_ProviderFailure(500, "private provider body", None))
        with self.assertRaises(OnlineUnavailable) as caught:
            router.complete([{"role": "user", "content": "private prompt text"}], progress=progress.append)
        self.assertNotIn("private", str(caught.exception))
        self.assertTrue(progress)
        self.assertTrue(all("private" not in value and "key" not in value.lower() for value in progress))

    def test_catalog_requires_hybrid_and_sanitizes_entries(self):
        router = self.make_router(mode="local")
        router._request_json = Mock()
        with self.assertRaises(OnlineUnavailable):
            router.catalog("nvidia")
        router._request_json.assert_not_called()
        router.configure({"mode": "hybrid", "routes": [
            {"provider": "nvidia", "model": "test/model", "roles": ["chat"]}
        ]}, keys={"nvidia": "key"})
        router._request_json = lambda provider, url, payload, key, cancelled: {
            "data": [{"id": "m", "name": "Model"}, {"id": "bad", "name": 12}, "invalid"]
        }
        self.assertEqual(router.catalog("nvidia"), [{"id": "m", "name": "Model"}, {"id": "bad", "name": "bad"}])


if __name__ == "__main__":
    unittest.main()
