"""Small, bounded Discord transport helpers for the optional Mavi bot.

This module does not connect to Discord until a caller invokes DiscordClient.
It deliberately keeps command parsing and local attachment handling separate
from any server or model runtime.
"""
from __future__ import annotations

import json
import base64
import mimetypes
import math
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

API_BASE = "https://discord.com/api/v10"
COMMANDS = {"ask", "task", "image", "edit", "status", "steer", "stop", "answer", "help"}
MAX_COMMAND_BODY = 4000
MAX_ATTACHMENTS = 6
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_TEXT_ATTACHMENT_CHARS = 50_000
CDN_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}
_IMAGE_SIGNATURES = ((b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
                     (b"\xff\xd8\xff", ".jpg", "image/jpeg"))


class DiscordError(RuntimeError):
    """Sanitized transport or input error; never includes credentials."""


@dataclass(frozen=True)
class Command:
    name: str
    body: str


def parse_command(content: str) -> Command | None:
    """Parse !mavi commands; unrelated messages return None."""
    if not isinstance(content, str):
        return None
    match = re.match(r"^\s*!mavi(?:\s+([a-z]+))?(?:\s+([\s\S]*))?\s*$", content, re.I)
    if not match:
        return None
    name = (match.group(1) or "help").lower()
    if name not in COMMANDS:
        return None
    body = (match.group(2) or "").strip()
    if len(body) > MAX_COMMAND_BODY:
        raise DiscordError(f"Command text is limited to {MAX_COMMAND_BODY} characters.")
    return Command(name, body)


def authorized_message(message: Mapping[str, Any], *, allowed_channels: set[str],
                       allowed_users: set[str]) -> bool:
    """Accept only ordinary messages from configured users in configured channels."""
    if not isinstance(message, Mapping) or message.get("webhook_id"):
        return False
    author = message.get("author") or {}
    if not isinstance(author, Mapping) or author.get("bot") is True or author.get("webhook_id"):
        return False
    channel_id, user_id = str(message.get("channel_id", "")), str(author.get("id", ""))
    return bool(channel_id and user_id and channel_id in allowed_channels and user_id in allowed_users)


def _checked_cdn_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in CDN_HOSTS or parsed.username or parsed.password:
        raise DiscordError("Attachment URL is not on an approved Discord CDN host.")
    return url


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, validator: Callable[[str], str] | None = None):
        super().__init__()
        self.validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self.validator is None:
            raise DiscordError("Discord API redirects are not allowed.")
        self.validator(newurl)  # Validate every hop before following it.
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(redirect_validator=None):
    return urllib.request.build_opener(_SafeRedirect(redirect_validator),
                                       urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def download_attachments(attachments: list[Mapping[str, Any]], data_dir: str | Path,
                         *, cancelled=None) -> list[dict[str, str]]:
    """Download up to six bounded image/text files into data/uploads.

    Returns image entries with validated image paths and text entries with
    decoded UTF-8 text. Redirects are followed only while each hop remains on
    an approved HTTPS CDN host.
    """
    if len(attachments) > MAX_ATTACHMENTS:
        raise DiscordError(f"Use no more than {MAX_ATTACHMENTS} attachments.")
    data_path = Path(data_dir).absolute()
    if data_path.is_symlink():
        raise DiscordError("Data directory is invalid.")
    root = data_path.resolve()
    upload_dir = root / "uploads"
    if upload_dir.is_symlink():
        raise DiscordError("Upload directory is invalid.")
    upload_dir.mkdir(parents=True, exist_ok=True)
    if upload_dir.is_symlink():
        raise DiscordError("Upload directory is invalid.")
    opener = _opener(_checked_cdn_url)
    results, total = [], 0
    for item in attachments:
        if cancelled is not None and cancelled.is_set():
            raise InterruptedError("Attachment download stopped.")
        url = _checked_cdn_url(str(item.get("url", "")))
        request = urllib.request.Request(url, headers={"User-Agent": "Mavi/1.0"})
        try:
            with opener.open(request, timeout=20) as response:
                final_url = _checked_cdn_url(response.geturl())
                del final_url
                data = response.read(MAX_ATTACHMENT_BYTES + 1)
        except DiscordError:
            raise
        except Exception as exc:
            raise DiscordError("Could not download Discord attachment.") from None
        total += len(data)
        if len(data) > MAX_ATTACHMENT_BYTES or total > MAX_ATTACHMENT_BYTES:
            raise DiscordError("Discord attachments exceed the 10 MB combined limit.")
        name = Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).name
        # Strip both platform path separators before passing names to local tools.
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", name.replace("\\", "/").split("/")[-1])[:80] or "attachment"
        if any(data.startswith(sig) for sig, _, _ in _IMAGE_SIGNATURES):
            _, suffix, _ = next(x for x in _IMAGE_SIGNATURES if data.startswith(x[0]))
            suffix = ".jpg" if suffix == ".jpg" else suffix
            if Path(safe_name).suffix.lower() not in (".png", ".jpg", ".jpeg"):
                safe_name += suffix
            results.append({"name": safe_name, "data_base64": base64.b64encode(data).decode("ascii")})
        else:
            content_type = str(item.get("content_type", ""))
            if content_type.startswith("text/") or safe_name.lower().endswith((".txt", ".md", ".csv", ".json")):
                try:
                    decoded = data.decode("utf-8")
                except UnicodeDecodeError:
                    raise DiscordError("Text attachment must use UTF-8 encoding.") from None
                if len(decoded) > MAX_TEXT_ATTACHMENT_CHARS:
                    raise DiscordError("Text attachments are limited to 50,000 characters each.")
                results.append({"kind": "text", "name": safe_name, "text": decoded})
            else:
                raise DiscordError("Only PNG, JPEG, and UTF-8 text attachments are supported.")
    return results


class DiscordClient:
    """Bounded Discord REST client. Inject sleep/opener for deterministic tests."""
    def __init__(self, token: str, *, opener=None, sleep: Callable[[float], None] = time.sleep,
                 max_retries: int = 4):
        if not token or "\r" in token or "\n" in token:
            raise ValueError("A Discord bot token is required.")
        self._token = token
        self._opener = opener or _opener()
        self._sleep = sleep
        self._max_retries = max_retries

    def request(self, method: str, path: str, *, payload: Mapping[str, Any] | None = None,
                cancelled=None, file_path: str | Path | None = None, data_dir: str | Path | None = None):
        if not path.startswith("/") or ".." in path:
            raise ValueError("Discord API path must be an absolute API path.")
        url = API_BASE + path
        for attempt in range(self._max_retries + 1):
            if cancelled is not None and cancelled.is_set():
                raise InterruptedError("Discord request stopped.")
            headers = {"Authorization": "Bot " + self._token, "User-Agent": "Mavi/1.0"}
            body = None
            if file_path is not None:
                body, content_type = _multipart_file(file_path, data_dir, payload or {})
                headers["Content-Type"] = content_type
            elif payload is not None:
                headers["Content-Type"] = "application/json"
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
            try:
                with self._opener.open(req, timeout=30) as response:
                    raw = response.read(2 * 1024 * 1024)
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < self._max_retries:
                    try:
                        info = json.loads(exc.read(65536) or b"{}")
                        delay = float(info.get("retry_after", 1))
                        if not math.isfinite(delay):
                            delay = 1.0
                        delay = max(0.1, min(delay, 30.0))
                    except Exception:
                        delay = 1.0
                    finally:
                        exc.close()
                    if cancelled is not None and cancelled.wait(delay):
                        raise InterruptedError("Discord request stopped.") from None
                    if cancelled is None:
                        self._sleep(delay)
                    continue
                raise DiscordError(f"Discord request failed with HTTP {exc.code}.") from None
            except DiscordError:
                raise
            except Exception:
                raise DiscordError("Discord request failed.") from None
        raise DiscordError("Discord rate limit retry limit reached.")

    def send_message(self, channel_id: str, content: str):
        payload = {"content": content[:2000], "allowed_mentions": {"parse": []}}
        return self.request("POST", f"/channels/{channel_id}/messages", payload=payload)

    def upload_file(self, channel_id: str, file_path: str | Path, data_dir: str | Path,
                    *, content: str = ""):
        payload = {"content": content[:2000], "allowed_mentions": {"parse": []}}
        return self.request("POST", f"/channels/{channel_id}/messages", payload=payload,
                            file_path=file_path, data_dir=data_dir)


def _multipart_file(file_path: str | Path, data_dir: str | Path | None,
                    payload: Mapping[str, Any]) -> tuple[bytes, str]:
    if data_dir is None:
        raise DiscordError("Generated file uploads require the local data directory.")
    root = Path(data_dir).resolve()
    output = root / "outputs"
    raw_path = Path(file_path)
    if raw_path.is_symlink() or output.is_symlink():
        raise DiscordError("Generated file path is invalid.")
    try:
        path = raw_path.resolve(strict=True)
        path.relative_to(output.resolve(strict=True))
    except Exception:
        raise DiscordError("Only generated files in the local outputs folder can be uploaded.") from None
    cursor = path
    while cursor != output.resolve():
        if cursor.is_symlink():
            raise DiscordError("Generated file path is invalid.")
        cursor = cursor.parent
    if not path.is_file() or path.stat().st_size > MAX_UPLOAD_BYTES:
        raise DiscordError("Generated files must be no larger than 8 MB.")
    name = path.name
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,180}", name) or name in (".", ".."):
        raise DiscordError("Generated file name is invalid for upload.")
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    boundary = "----Mavi" + uuid.uuid4().hex
    metadata = dict(payload)
    metadata["allowed_mentions"] = {"parse": []}
    pieces = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\nContent-Type: application/json\r\n\r\n".encode(),
              json.dumps(metadata, ensure_ascii=False).encode("utf-8"),
              f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"{name}\"\r\nContent-Type: {content_type}\r\n\r\n".encode(),
              path.read_bytes(), f"\r\n--{boundary}--\r\n".encode()]
    return b"".join(pieces), "multipart/form-data; boundary=" + boundary
