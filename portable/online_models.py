"""Explicit, bounded OpenAI-compatible online model routing.

Credentials are accepted at runtime and kept only in this object. This module
never reads environment credentials, installs provider software, or uploads
images. Callers choose routes and decide whether hybrid routing is enabled.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

PROVIDERS: dict[str, str] = {
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
}
GATEWAY = "gateway"
ALLOWED_ROLES = frozenset({"chat", "analysis", "code", "files"})
_MAX_ROUTES = 8
_MAX_BODY_BYTES = 4 * 1024 * 1024
_MAX_INPUT_CHARS = 512 * 1024
_MAX_TOKENS = 16_384
_TIMEOUT_SECONDS = 45
_MAX_COOLDOWN_SECONDS = 7 * 24 * 60 * 60


class OnlineModelError(RuntimeError):
    """Sanitized provider configuration, transport, or response error."""


class OnlineUnavailable(ValueError):
    """No configured online route is currently usable; callers may use local inference."""


class OnlineModelCancelled(InterruptedError):
    """Raised when a caller cancels before or between requests."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OnlineModelError("Provider redirects are not followed.")


class OnlineRouter:
    """A user-configured, memory-keyed router for text-only online completion."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._config: dict[str, Any] = {"mode": "local", "routes": [], "allow_paid": False}
        self._keys: dict[str, str] = {}
        self._cooldowns: dict[str, float] = {}
        self._auth_blocked: set[str] = set()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def configure(
        self,
        config: Mapping[str, Any],
        keys: Mapping[str, str] | None = None,
        clear_keys: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Validate and save route choices; supplied API keys remain memory-only.

        `clear_keys` is a sequence of provider names. An updated key clears that
        provider's auth block; merely re-saving an unchanged bad key does not.
        """
        normalized = self._validate_config(config)
        if keys is not None and not isinstance(keys, Mapping):
            raise ValueError("Keys must be a provider-to-key object.")
        clear = set(clear_keys or ())
        if any(name not in (*PROVIDERS, GATEWAY) for name in clear):
            raise ValueError("Unknown provider in clear_keys.")
        key_updates: dict[str, str] = {}
        for provider, key in (keys or {}).items():
            if provider not in (*PROVIDERS, GATEWAY):
                raise ValueError("Unknown provider key name.")
            if not isinstance(key, str) or len(key) > 8192 or any(ord(char) < 32 or ord(char) == 127 for char in key):
                raise ValueError("Provider keys must be bounded plain text.")
            key = key.strip()
            if key and (not key.isascii() or any(not (33 <= ord(char) <= 126) for char in key)):
                raise ValueError("Provider keys must use visible ASCII characters only.")
            if key:
                key_updates[provider] = key
        with self._lock:
            for provider in clear:
                self._keys.pop(provider, None)
                self._auth_blocked.discard(provider)
                self._cooldowns.pop(provider, None)
            for provider, key in key_updates.items():
                prior = self._keys.get(provider)
                self._keys[provider] = key
                if prior != key:
                    self._auth_blocked.discard(provider)
                    self._cooldowns.pop(provider, None)
            self._config = normalized
        return self._safe_config(normalized)

    def snapshot(self) -> dict[str, Any]:
        """Return safe status; never return credentials or provider error bodies."""
        now = time.monotonic()
        with self._lock:
            routes = []
            for route in self._config["routes"]:
                provider = route["provider"]
                cooldown = max(0, int(self._cooldowns.get(provider, 0) - now))
                routes.append({
                    **route,
                    "key_configured": bool(self._keys.get(provider)) or provider == GATEWAY,
                    "cooldown_seconds": cooldown,
                    "auth_blocked": provider in self._auth_blocked,
                    "ready": provider not in self._auth_blocked and cooldown == 0 and
                             (provider == GATEWAY or bool(self._keys.get(provider))),
                })
            provider_status = []
            provider_names = {
                "nvidia": "NVIDIA NIM", "openrouter": "OpenRouter", "groq": "Groq",
                "google": "Google AI", "gateway": "Local OpenAI-compatible gateway",
            }
            for provider in (*PROVIDERS, GATEWAY):
                key_configured = bool(self._keys.get(provider))
                configured = bool(self._config.get("gateway_url")) if provider == GATEWAY else key_configured
                cooldown = max(0, int(self._cooldowns.get(provider, 0) - now))
                blocked = provider in self._auth_blocked
                provider_status.append({
                    "id": provider,
                    "name": provider_names[provider],
                    "configured": configured,
                    "key_configured": key_configured,
                    "auth_blocked": blocked,
                    "cooldown_seconds": cooldown,
                    "status": "auth_blocked" if blocked else "cooldown" if cooldown else "ready" if configured else "needs_key" if provider != GATEWAY else "not_configured",
                })
            return {
                "config": self._safe_config(self._config),
                "mode": self._config["mode"],
                "routes": routes,
                "providers": provider_status,
                "allow_paid": self._config["allow_paid"],
                "gateway_configured": bool(self._config.get("gateway_url")),
                "billing_note": "Provider pricing and account limits are controlled by the provider; Mavi cannot guarantee free use.",
            }

    def catalog(self, provider: str) -> list[dict[str, str]]:
        """Fetch a provider's text model catalog using its fixed endpoint."""
        if provider not in (*PROVIDERS, GATEWAY):
            raise ValueError("Unknown online provider.")
        with self._lock:
            if self._config["mode"] != "hybrid":
                raise OnlineUnavailable("Online routing is disabled in local mode.")
            key = self._keys.get(provider)
            base = self._base_url(provider)
            if provider != GATEWAY and not key:
                raise OnlineUnavailable("Enter this provider's key in its local settings first.")
        payload = self._request_json(provider, base + "/models", None, key, None)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise OnlineModelError("Provider returned an unsupported model catalog.")
        catalog = []
        for item in data[:500]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            model_id = item["id"][:240]
            label = item.get("name") if isinstance(item.get("name"), str) else model_id
            catalog.append({"id": model_id, "name": label[:240]})
        return catalog

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        role: str = "chat",
        max_tokens: int = 1024,
        cancelled: Any = None,
        progress: Callable[[str], Any] | None = None,
        route_hint: str | None = None,
    ) -> dict[str, Any]:
        """Run one text completion, falling back over at most eight user routes."""
        self._check_cancelled(cancelled)
        if role not in ALLOWED_ROLES:
            raise ValueError("Online routing is limited to chat, analysis, code, and files roles.")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= _MAX_TOKENS:
            raise ValueError("max_tokens must be between 1 and 16384.")
        with self._lock:
            if self._config["mode"] != "hybrid":
                raise OnlineUnavailable("Online routing is disabled in local mode.")
            routes = [dict(route) for route in self._config["routes"] if role in route["roles"]]
            allow_paid = self._config["allow_paid"]
        if not routes:
            raise OnlineUnavailable("No online route is configured for this role.")
        safe_messages = self._validate_messages(messages)
        if route_hint is not None:
            if not isinstance(route_hint, str) or len(route_hint) > 500:
                raise OnlineUnavailable("Requested online route is not configured for this role.")
            match = next((route for route in routes if self._route_id(route) == route_hint), None)
            if match is None:
                raise OnlineUnavailable("Requested online route is not configured for this role.")
            routes = [match] + [route for route in routes if route is not match]

        failures = []
        for index, route in enumerate(routes, 1):
            self._check_cancelled(cancelled)
            provider = route["provider"]
            model = route["model"]
            with self._lock:
                key = self._keys.get(provider)
                blocked = provider in self._auth_blocked
                cooldown = self._cooldowns.get(provider, 0) - time.monotonic()
                base = self._base_url(provider)
            if blocked:
                failures.append("A provider key needs to be changed.")
                continue
            if cooldown > 0:
                failures.append("A provider is cooling down after rate limiting.")
                continue
            if provider != GATEWAY and not key:
                failures.append("A configured provider key is missing.")
                continue
            if provider == "openrouter" and not allow_paid and not self._is_openrouter_free_model(model):
                failures.append("OpenRouter route is not marked as free.")
                continue
            self._notify(progress, f"Trying configured online route {index} of {len(routes)}.")
            request_payload = {
                "model": model,
                "messages": safe_messages,
                "max_tokens": max_tokens,
                "stream": False,
            }
            try:
                payload = self._request_json(provider, base + "/chat/completions", request_payload, key, cancelled)
                self._check_cancelled(cancelled)
                text = self._response_text(payload)
                usage = self._safe_usage(payload.get("usage") if isinstance(payload, dict) else None)
                self._notify(progress, "Online response received.")
                return {"text": text, "provider": provider, "model": model, "usage": usage}
            except OnlineModelCancelled:
                raise
            except _ProviderFailure as exc:
                self._record_failure(provider, key, exc)
                if exc.status in (400, 413, 422):
                    raise OnlineModelError("Provider rejected the request format.") from None
                failures.append(exc.safe_reason)
            except OnlineModelError as exc:
                failures.append(str(exc))
        self._check_cancelled(cancelled)
        # Fixed strings only: never include upstream text, headers, or keys.
        if any("free" in failure for failure in failures):
            raise OnlineUnavailable("No eligible free OpenRouter route is available; review your selected routes.")
        raise OnlineUnavailable("All configured online routes failed or are unavailable.")

    @staticmethod
    def _validate_config(config: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(config, Mapping):
            raise ValueError("Online model configuration must be an object.")
        if set(config) - {"mode", "routes", "allow_paid", "gateway_url"}:
            raise ValueError("Online model configuration contains an unsupported field.")
        mode = config.get("mode", "local")
        if not isinstance(mode, str) or mode not in {"local", "hybrid"}:
            raise ValueError("mode must be local or hybrid.")
        raw_routes = config.get("routes", [])
        if not isinstance(raw_routes, list) or len(raw_routes) > _MAX_ROUTES:
            raise ValueError("routes must contain no more than eight configured routes.")
        allow_paid = config.get("allow_paid", False)
        if not isinstance(allow_paid, bool):
            raise ValueError("allow_paid must be a boolean.")
        gateway_url = config.get("gateway_url")
        providers_used = set()
        routes = []
        for route in raw_routes:
            if not isinstance(route, Mapping) or set(route) != {"provider", "model", "roles"}:
                raise ValueError("Each route must contain provider, model, and roles only.")
            provider, model, roles = route["provider"], route["model"], route["roles"]
            if provider not in (*PROVIDERS, GATEWAY):
                raise ValueError("Unknown online provider.")
            if not isinstance(model, str) or not model.strip() or len(model) > 240 or any(ord(c) < 32 for c in model):
                raise ValueError("Model ID must be bounded plain text.")
            if not isinstance(roles, list) or not roles or any(not isinstance(role, str) or role not in ALLOWED_ROLES for role in roles):
                raise ValueError("Routes must use only chat, analysis, code, and files roles.")
            if len(set(roles)) != len(roles):
                raise ValueError("A route cannot repeat a role.")
            if provider == "openrouter" and not allow_paid and not OnlineRouter._is_openrouter_free_model(model):
                raise ValueError("OpenRouter routes must use a model marked :free unless allow_paid is explicitly enabled.")
            routes.append({"provider": provider, "model": model.strip(), "roles": list(roles)})
            providers_used.add(provider)
        if "gateway" in providers_used:
            gateway_url = OnlineRouter._validate_gateway_url(gateway_url)
        elif gateway_url is not None:
            gateway_url = OnlineRouter._validate_gateway_url(gateway_url)
        if mode == "local" and routes:
            # Retain explicit selections but do no network work; switching to
            # hybrid later is an affirmative user action.
            pass
        return {"mode": mode, "routes": routes, "allow_paid": allow_paid, **({"gateway_url": gateway_url} if gateway_url else {})}

    @staticmethod
    def _route_id(route: Mapping[str, Any]) -> str:
        return f"online:{route['provider']}:{route['model']}"

    @staticmethod
    def _safe_config(config: dict[str, Any]) -> dict[str, Any]:
        return {"mode": config["mode"], "routes": [dict(r, roles=list(r["roles"])) for r in config["routes"]],
                "allow_paid": config["allow_paid"], **({"gateway_url": config["gateway_url"]} if config.get("gateway_url") else {})}

    @staticmethod
    def _validate_gateway_url(value: Any) -> str:
        if not isinstance(value, str) or len(value) > 300:
            raise ValueError("Gateway URL must be a loopback OpenAI-compatible /v1 endpoint.")
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.username or parsed.password or parsed.path != "/v1" or parsed.query or parsed.fragment:
            raise ValueError("Gateway URL must be http://127.0.0.1:<port>/v1 or http://localhost:<port>/v1.")
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port is None or not 1 <= port <= 65535:
            raise ValueError("Gateway URL must include a valid loopback port.")
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)}
            if not addresses or not all(ipaddress.ip_address(address).is_loopback for address in addresses):
                raise ValueError
        except (OSError, ValueError, IndexError):
            raise ValueError("Gateway host did not resolve exclusively to loopback addresses.") from None
        return value.rstrip("/")

    def _base_url(self, provider: str) -> str:
        if provider == GATEWAY:
            return self._config["gateway_url"]
        return PROVIDERS[provider]

    @staticmethod
    def _validate_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)) or not messages:
            raise ValueError("messages must be a nonempty sequence of text messages.")
        copied: list[dict[str, str]] = []
        total = 0
        for message in messages:
            if not isinstance(message, Mapping) or set(message) - {"role", "content"}:
                raise ValueError("Online messages must contain only role and text content.")
            role = message.get("role")
            content = message.get("content")
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise ValueError("Online routing accepts text-only system, user, and assistant messages.")
            total += len(content)
            if total > _MAX_INPUT_CHARS:
                raise ValueError("Online message input exceeds its size limit.")
            copied.append({"role": role, "content": content})
        return copied

    def _request_json(self, provider: str, url: str, payload: dict[str, Any] | None, key: str | None, cancelled: Any) -> dict[str, Any]:
        self._check_cancelled(cancelled)
        data = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Accept": "application/json", "User-Agent": "Mavi/2"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(url, data=data, headers=headers, method="GET" if data is None else "POST")
        try:
            with self._opener.open(request, timeout=_TIMEOUT_SECONDS) as response:
                self._check_cancelled(cancelled)
                body = response.read(_MAX_BODY_BYTES + 1)
                if len(body) > _MAX_BODY_BYTES:
                    raise OnlineModelError("Provider response exceeded the size limit.")
                status = getattr(response, "status", 200)
                if status < 200 or status >= 300:
                    raise _ProviderFailure(status, None, self._retry_after(getattr(response, "headers", {})))
        except urllib.error.HTTPError as exc:
            retry_after = self._retry_after(exc.headers or {})
            raise _ProviderFailure(exc.code, None, retry_after) from None
        except OnlineModelError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            raise _ProviderFailure(None, None, None) from None
        self._check_cancelled(cancelled)
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OnlineModelError("Provider returned an invalid response.") from None
        if not isinstance(decoded, dict):
            raise OnlineModelError("Provider returned an unsupported response.")
        return decoded

    def _record_failure(self, provider: str, key: str | None, failure: "_ProviderFailure") -> None:
        with self._lock:
            if failure.status in (401, 403):
                self._auth_blocked.add(provider)
            elif failure.status == 429:
                self._cooldowns[provider] = time.monotonic() + (failure.retry_after or 60)

    @staticmethod
    def _retry_after(headers: Any) -> int | None:
        value = headers.get("Retry-After") if headers is not None else None
        if not isinstance(value, str):
            return None
        try:
            return max(1, min(_MAX_COOLDOWN_SECONDS, int(value.strip())))
        except ValueError:
            try:
                moment = parsedate_to_datetime(value)
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
                seconds = int((moment - datetime.now(timezone.utc)).total_seconds())
                return max(1, min(_MAX_COOLDOWN_SECONDS, seconds))
            except (TypeError, ValueError, OverflowError):
                return None

    @staticmethod
    def _response_text(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise OnlineModelError("Provider returned no text response.")
        message = choices[0].get("message")
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text:
            raise OnlineModelError("Provider returned no text response.")
        return text[:_MAX_BODY_BYTES]

    @staticmethod
    def _safe_usage(raw: Any) -> dict[str, int]:
        if not isinstance(raw, dict):
            return {}
        result = {}
        for field in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
            value = raw.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**9:
                result[field] = value
        return result

    @staticmethod
    def _is_openrouter_free_model(model: str) -> bool:
        normalized = model.strip().lower()
        return normalized == "openrouter/free" or normalized.endswith(":free")

    @staticmethod
    def _check_cancelled(cancelled: Any) -> None:
        if cancelled is None:
            return
        is_set = getattr(cancelled, "is_set", None)
        if callable(is_set) and is_set():
            raise OnlineModelCancelled("Online request cancelled.")
        if isinstance(cancelled, bool) and cancelled:
            raise OnlineModelCancelled("Online request cancelled.")

    @staticmethod
    def _notify(progress: Callable[[str], Any] | None, message: str) -> None:
        if callable(progress):
            try:
                progress(message)
            except Exception:
                pass


class _ProviderFailure(OnlineModelError):
    def __init__(self, status: int | None, _private: Any, retry_after: int | None):
        self.status = status
        self.retry_after = retry_after
        self.safe_reason = "Provider request failed." if status is None else f"Provider returned HTTP {status}."
        super().__init__(self.safe_reason)
