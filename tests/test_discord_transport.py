import json
import io
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
import discord_transport as dt


class DiscordTransportTests(unittest.TestCase):
    def test_command_grammar_and_bound(self):
        self.assertEqual(dt.parse_command("!mavi ask hi").body, "hi")
        self.assertEqual(dt.parse_command("!mavi").name, "help")
        self.assertIsNone(dt.parse_command("hello"))
        self.assertIsNone(dt.parse_command("!mavi shell run"))
        with self.assertRaises(dt.DiscordError):
            dt.parse_command("!mavi task " + "x" * 4001)

    def test_allowlist_and_bot_webhook_rejection(self):
        msg = {"channel_id": "c", "author": {"id": "u"}}
        self.assertTrue(dt.authorized_message(msg, allowed_channels={"c"}, allowed_users={"u"}))
        self.assertFalse(dt.authorized_message({**msg, "webhook_id": "w"}, allowed_channels={"c"}, allowed_users={"u"}))
        bot_msg = {"channel_id": "c", "author": {"id": "u", "bot": True}}
        self.assertFalse(dt.authorized_message(bot_msg, allowed_channels={"c"}, allowed_users={"u"}))

    def test_cdn_url_policy(self):
        for url in ("http://cdn.discordapp.com/a.png", "https://example.org/a.png",
                    "https://user@cdn.discordapp.com/a.png"):
            with self.assertRaises(dt.DiscordError):
                dt._checked_cdn_url(url)
        self.assertTrue(dt._checked_cdn_url("https://cdn.discordapp.com/a.png"))

    def test_upload_restricts_directory_size_and_mentions(self):
        with tempfile.TemporaryDirectory() as td:
            data = pathlib.Path(td)
            out = data / "outputs"
            out.mkdir()
            file = out / "generated.png"
            file.write_bytes(b"image")
            body, ctype = dt._multipart_file(file, data, {"content": "hi"})
            self.assertIn(b'"allowed_mentions": {"parse": []}', body)
            self.assertIn(b"generated.png", body)
            self.assertTrue(ctype.startswith("multipart/form-data; boundary="))
            elsewhere = data / "other.txt"
            elsewhere.write_text("x")
            with self.assertRaises(dt.DiscordError):
                dt._multipart_file(elsewhere, data, {})
            unsafe = out / "bad:name.txt"
            unsafe.write_text("x")
            with self.assertRaises(dt.DiscordError):
                dt._multipart_file(unsafe, data, {})
            windows_style = r"C:\\outputs\\generated.png"
            with self.assertRaises(dt.DiscordError):
                dt._multipart_file(windows_style, data, {})

    def test_upload_rejects_symlink_and_oversize(self):
        with tempfile.TemporaryDirectory() as td:
            data = pathlib.Path(td)
            out = data / "outputs"
            out.mkdir()
            target = data / "outside.txt"
            target.write_text("x")
            link = out / "linked.txt"
            link.symlink_to(target)
            with self.assertRaises(dt.DiscordError):
                dt._multipart_file(link, data, {})
            big = out / "big.bin"
            big.write_bytes(b"x" * (dt.MAX_UPLOAD_BYTES + 1))
            with self.assertRaises(dt.DiscordError):
                dt._multipart_file(big, data, {})

    def test_auth_api_redirect_is_forbidden_without_leaking_token(self):
        class RedirectingOpener:
            def open(self, req, timeout):
                self.req = req
                raise dt.DiscordError("Discord API redirects are not allowed.")
        opener = RedirectingOpener()
        client = dt.DiscordClient("secret-token", opener=opener)
        with self.assertRaises(dt.DiscordError) as caught:
            client.request("GET", "/users/@me")
        self.assertNotIn("secret-token", str(caught.exception))
        self.assertEqual(opener.req.get_header("Authorization"), "Bot secret-token")

    def test_429_backoff_observes_cancellation(self):
        class LimitedOpener:
            def __init__(self): self.calls = 0
            def open(self, req, timeout):
                self.calls += 1
                raise dt.urllib.error.HTTPError(req.full_url, 429, "limited", {}, io.BytesIO(b'{"retry_after":1}'))
        class Cancel:
            def is_set(self): return False
            def wait(self, delay): self.delay = delay; return True
        opener, cancel = LimitedOpener(), Cancel()
        client = dt.DiscordClient("token", opener=opener)
        with self.assertRaises(InterruptedError):
            client.request("GET", "/users/@me", cancelled=cancel)
        self.assertEqual(opener.calls, 1)

    def test_image_download_signature_and_count_limit(self):
        png = b"\x89PNG\r\n\x1a\n" + b"content"
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return "https://cdn.discordapp.com/x.png"
            def read(self, limit): return png
        class Opener:
            def open(self, request, timeout): return Response()
        with tempfile.TemporaryDirectory() as td, patch.object(dt, "_opener", return_value=Opener()):
            result = dt.download_attachments([{"url": "https://cdn.discordapp.com/x.png"}], td)
            self.assertEqual(result[0]["name"], "x.png")
            self.assertEqual(dt.base64.b64decode(result[0]["data_base64"]), png)
            with self.assertRaises(dt.DiscordError):
                dt.download_attachments([{"url": "https://cdn.discordapp.com/x.png"}] * 7, td)

    def test_retry_after_nan_is_safely_bounded(self):
        class LimitedOpener:
            calls = 0
            def open(self, req, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise dt.urllib.error.HTTPError(req.full_url, 429, "limited", {},
                                                    io.BytesIO(b'{"retry_after": NaN}'))
                return type("Response", (), {"__enter__": lambda s:s, "__exit__":lambda *a:None,
                                              "read":lambda s,n:b'{}'})()
        delays = []
        opener = LimitedOpener()
        client = dt.DiscordClient("token", opener=opener, sleep=delays.append)
        self.assertEqual(client.request("GET", "/users/@me"), {})
        self.assertEqual(delays, [1.0])


if __name__ == "__main__":
    unittest.main()
