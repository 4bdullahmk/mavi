"""Regression tests for conversation-local app context and clear API."""
import copy
import io
import json
import pathlib
import sys
import tempfile
import time
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
import app_context
import server
import macos_automation


class AppContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = {
            "DATA": server.DATA,
            "STATE": server.STATE,
            "JOBS": server.JOBS,
            "ACTIVE": server.ACTIVE,
        }
        server.DATA = pathlib.Path(self.temp.name)
        server.STATE = {
            "chats": [], "profile": "",
            "settings": copy.deepcopy(server.DEFAULT_SETTINGS),
        }
        server.JOBS = {}
        server.ACTIVE = None
        self.patched = [
            patch.object(server, "models", return_value=[]),
            patch.object(server, "hardware", return_value={}),
            patch.object(server, "capabilities", return_value={"chat": {"available": True, "reason": ""}}),
        ]
        for item in self.patched:
            item.start()

    def tearDown(self):
        for item in reversed(self.patched):
            item.stop()
        for name, value in self.old.items():
            setattr(server, name, value)
        self.temp.cleanup()

    def request(self, path, body=None):
        payload = json.dumps(body).encode() if body is not None else b""
        handler = object.__new__(server.Handler)
        handler.headers = {
            "Host": "127.0.0.1:8765",
            "Cookie": f"mavi_session={server.TOKEN}",
            "Origin": "http://127.0.0.1:8765",
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        }
        handler.server = SimpleNamespace(server_port=8765)
        handler.path = path
        handler.rfile = io.BytesIO(payload)
        captured = {}
        handler.host_ok = lambda: True
        handler.authorized = lambda: True
        handler.send = lambda value, status=200: captured.update(status=status, payload=value)
        handler.do_POST()
        return captured["status"], captured["payload"]

    def test_context_cleaner_persists_only_bounded_context_not_window_or_permission_state(self):
        saved = {
            "app": "excel", "name": "Spoofed name", "task": "Review the workbook",
            "window_id": 9821, "process_id": 77, "permissions": ["whole_computer"],
            "automation_policy": "routine_navigation", "bundle_id": "private.app.bundle",
            "last_result": "Finished review", "completed_steps": ["Read summary"],
        }
        cleaned = app_context.clean_app_context(saved)
        self.assertEqual(cleaned["name"], "Excel")
        self.assertEqual(cleaned["task"], "Review the workbook")
        self.assertEqual(set(cleaned), {"app", "name", "task", "status", "last_result", "completed_steps"})
        self.assertLessEqual(len(app_context.clean_app_context({"app": "excel", "task": "x" * 20_000})["task"]), 6000)

        chat = {"id": "chat-1", "title": "Workbook", "messages": [], "app_context": saved}
        server.STATE["chats"].append(chat)
        server.persist()
        server.STATE["chats"].clear()
        server.load()
        loaded = server.STATE["chats"][0]["app_context"]
        self.assertEqual(loaded, cleaned)
        # Once normalized state is next saved, no ignored metadata is rewritten.
        server.persist()
        raw = (server.DATA / "workspace.json").read_text()
        self.assertNotIn("window_id", raw)
        self.assertNotIn("process_id", raw)
        self.assertNotIn("permissions", raw)
        self.assertNotIn("automation_policy", raw)

    def test_new_job_inherits_only_local_chat_context_and_per_send_approval(self):
        bookmark = app_context.clean_app_context({"app": "excel", "task": "Review this workbook", "window_id": 44,
                                                  "automation_policy": "routine_navigation"})
        chat = {"id": "local-chat", "title": "Workbook", "messages": [], "app_context": bookmark}
        server.STATE["chats"].append(chat)
        with patch.object(server, "choose_model", return_value="qwen3:8b"), patch.object(server.threading, "Thread"):
            local = server.new_job({"chat_id": "local-chat", "text": "Continue the previous task", "mode": "auto",
                                    "automation_policy": "routine_navigation", "automation_scope": "whole_computer"})
            local_job = server.JOBS[local["job_id"]]
            self.assertEqual(local_job["_app_context"], bookmark)
            self.assertEqual(local_job["_automation_policy"], "routine_navigation")
            self.assertEqual(local_job["_automation_scope"], "whole_computer")
            local_job["status"] = "completed"

            remote = server.new_job({"chat_id": "local-chat", "text": "Continue the previous task", "mode": "auto",
                                     "app_context": {"app": "excel"}, "automation_policy": "routine_navigation",
                                     "automation_scope": "whole_computer"}, owner="discord:user-fixture")
        remote_job = server.JOBS[remote["job_id"]]
        self.assertIsNone(remote_job["_app_context"])
        self.assertEqual(remote_job["_automation_policy"], "ask_each")
        self.assertEqual(remote_job["_automation_scope"], "single_app")
        self.assertNotEqual(remote["chat_id"], "local-chat")

    def test_followup_routes_through_darwin_adapter_and_canvas_is_browser_intent(self):
        with patch.object(server.sys, "platform", "darwin"):
            adapter = server.automation_backend()
            self.assertEqual(adapter.__name__, "macos_automation")
            job = {"_model": "qwen3:8b", "_cancel": threading.Event(),
                   "_app_context": app_context.clean_app_context({"app": "discord", "task": "Reply in chat"})}
            with patch.object(server, "models", side_effect=AssertionError("explicit targets and follow-ups must not invoke router")):
                self.assertEqual(server.route_task("Go into Canvas and work on my assignment", [], job), "browser")
                self.assertEqual(server.route_task("Open Finder and find the report", [], job), "computer")
                self.assertEqual(server.route_task("Open Discord", [], job), "computer")
                self.assertEqual(server.route_task("Continue the previous task", [], job), "computer")
        self.assertIsNone(app_context.clean_app_context({"app": "unsupported-app", "task": "anything"}))


    def _automation_job(self, request):
        chat = {"id": "automation-chat", "title": "App task",
                "messages": [{"role": "user", "content": request}]}
        server.STATE["chats"].append(chat)
        job = {
            "id": "automation-job", "chat_id": chat["id"], "status": "queued", "progress": "Starting",
            "content": "", "error": "", "mode": "computer", "agent_events": [], "started": time.time(),
            "_cancel": threading.Event(), "_owner": "local", "_steer": [], "_model": "qwen3:8b",
            "_answer_event": threading.Event(), "_answer": "", "_automation_policy": "ask_each",
            "_automation_scope": "single_app", "_app_context": None,
        }
        return job, chat

    def test_darwin_bare_app_open_does_not_require_vision_but_compound_does(self):
        bare_job, bare_chat = self._automation_job("Open Discord")
        with patch.object(server, "models", return_value=[]) as models, \
             patch.object(server, "automation_backend", return_value=macos_automation), \
             patch.object(macos_automation, "run", return_value="Opened Discord.") as run:
            server.run_job(bare_job, bare_chat, "Open Discord", [])
        self.assertEqual(bare_job["status"], "completed")
        run.assert_called_once()
        models.assert_not_called()

        compound_job, compound_chat = self._automation_job("Open Discord and say hello")
        with patch.object(server, "models", return_value=[]) as models, \
             patch.object(server, "automation_backend", return_value=macos_automation), \
             patch.object(macos_automation, "run") as run:
            server.run_job(compound_job, compound_chat, "Open Discord and say hello", [])
        self.assertEqual(compound_job["status"], "failed")
        self.assertIn("vision model", compound_job["error"])
        models.assert_called_once()
        run.assert_not_called()

    def test_clear_api_only_clears_context_and_refuses_active_chat_job(self):
        chat = {"id": "chat-clear", "title": "Task", "messages": [],
                "app_context": app_context.clean_app_context({"app": "word", "task": "Review draft"})}
        server.STATE["chats"].append(chat)
        server.JOBS["done"] = {"chat_id": chat["id"], "status": "completed"}
        status, response = self.request("/api/chat-context", {"chat_id": chat["id"], "clear": True})
        self.assertEqual(status, 200)
        self.assertTrue(response["ok"])
        self.assertNotIn("app_context", chat)
        self.assertNotIn("app_context", json.loads((server.DATA / "workspace.json").read_text())["chats"][0])

        chat["app_context"] = app_context.clean_app_context({"app": "word", "task": "Review draft"})
        server.JOBS["running"] = {"chat_id": chat["id"], "status": "running"}
        status, _ = self.request("/api/chat-context", {"chat_id": chat["id"], "clear": True})
        self.assertEqual(status, 400)
        self.assertIn("app_context", chat)

    def test_clear_api_rejects_unknown_chat_and_non_clear_requests(self):
        self.assertEqual(self.request("/api/chat-context", {"chat_id": "missing", "clear": True})[0], 400)
        chat = {"id": "chat-no-clear", "title": "Task", "messages": [],
                "app_context": app_context.clean_app_context({"app": "safari", "task": "Read page"})}
        server.STATE["chats"].append(chat)
        self.assertEqual(self.request("/api/chat-context", {"chat_id": chat["id"], "clear": False})[0], 400)
        self.assertIn("app_context", chat)


if __name__ == "__main__":
    unittest.main()
