"""Mocked dispatch tests: these exercise routing without contacting Discord."""
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
import server
import discord_transport


CHANNEL = "123456789012345678"


def message(mid, user, command, attachments=None):
    return {"id": str(mid), "channel_id": CHANNEL,
            "author": {"id": user, "bot": False}, "content": command,
            "attachments": attachments or []}


class FakeStop:
    def __init__(self, polls):
        self.polls, self.count, self.stopped = polls, 0, False
    def is_set(self): return self.stopped
    def wait(self, _seconds):
        self.count += 1
        return self.stopped or self.count > self.polls
    def set(self): self.stopped = True


class FakeClient:
    def __init__(self, responses, on_reply=None):
        self.responses, self.poll_index, self.calls, self.on_reply = responses, 0, [], on_reply
    def request(self, method, path, *, payload=None, cancelled=None, file_path=None, data_dir=None):
        self.calls.append((method, path, payload, file_path, data_dir))
        if method == "GET" and path == "/users/@me": return {"bot": True}
        if method == "GET" and path.startswith("/channels/") and "/messages?limit=1" in path:
            return [{"id": "1"}]
        if method == "GET" and path.startswith("/channels/") and "/messages?after=" in path:
            result = self.responses[self.poll_index] if self.poll_index < len(self.responses) else []
            self.poll_index += 1
            return result
        if method == "GET" and path.startswith("/channels/"):
            return {"type": 0, "guild_id": "987654321098765432"}
        if method == "POST" and payload and self.on_reply:
            self.on_reply(payload)
        return {}


class DiscordDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = pathlib.Path(self.temp.name)
        self.old_data, self.old_discord = server.DATA, dict(server.DISCORD)
        self.old_active = server.ACTIVE
        server.DATA = self.data
        server.DISCORD.clear()
        server.DISCORD.update(configured=True, connected=False, status="test", allow_tasks=True)
        server.JOBS.clear()
        server.ACTIVE = None

    def tearDown(self):
        server.DATA = self.old_data
        server.DISCORD.clear()
        server.DISCORD.update(self.old_discord)
        server.JOBS.clear()
        server.ACTIVE = self.old_active
        self.temp.cleanup()


    def run_loop(self, responses, *, allow_tasks=True, new_job=None, downloader=None,
                 stop=None, client=None):
        server.DISCORD["allow_tasks"] = allow_tasks
        stop = stop or FakeStop(len(responses))
        client = client or FakeClient(responses)
        previous_stop = server.DISCORD_STOP
        server.DISCORD_STOP = stop
        with patch.object(discord_transport, "DiscordClient", return_value=client), \
             patch.object(server, "new_job", side_effect=new_job) if new_job else patch.object(server, "new_job") as job_mock:
            if not new_job:
                job_mock.side_effect = AssertionError("unexpected new_job")
            try:
                if downloader is None:
                    server.discord_loop("fake-token", CHANNEL, {"user-a", "user-b"}, stop, allow_tasks=allow_tasks)
                else:
                    with patch.object(discord_transport, "download_attachments", side_effect=downloader):
                        server.discord_loop("fake-token", CHANNEL, {"user-a", "user-b"}, stop, allow_tasks=allow_tasks)
            finally:
                server.DISCORD_STOP = previous_stop
        return client

    @staticmethod
    def job_factory(captured, *, status="queued", mode="chat", content=""):
        def create(body, owner):
            job_id = "job-" + str(len(captured) + 1)
            job = {"id": job_id, "_owner": owner, "status": status,
                   "mode": body["mode"], "progress": "Working locally", "content": content,
                   "error": "", "_cancel": threading.Event(), "_steer": [],
                   "_answer": "", "_answer_event": threading.Event(), "question": "",
                   "artifacts": []}
            server.JOBS[job_id] = job
            captured.append((body, owner, job))
            return {"job_id": job_id}
        return create

    def test_image_and_edit_route_with_attachment_data(self):
        captured = []
        edit_file = {"name": "reference.png", "data_base64": "iVBORw0KGgo="}
        responses = [[message(2, "user-a", "!mavi image a blue bird"),
                      message(3, "user-a", "!mavi edit turn it into watercolor",
                              [{"url": "https://cdn.discordapp.com/reference.png"}])]]
        client = self.run_loop(responses, new_job=self.job_factory(captured),
                               downloader=lambda *_a, **_k: [edit_file])
        self.assertEqual([call[0]["mode"] for call in captured], ["image", "image"])
        self.assertEqual(captured[0][0]["attachments"], [])
        self.assertEqual(captured[1][0]["attachments"], [edit_file])
        self.assertTrue(any(c[2] and "Accepted." in c[2]["embeds"][0]["description"] for c in client.calls))

    def test_remote_tools_disabled_but_local_chat_command_still_routes(self):
        captured = []
        responses = [[message(2, "user-a", "!mavi task organize files"),
                      message(3, "user-a", "!mavi image a landscape"),
                      message(4, "user-a", "!mavi edit revise it", [{"url": "https://cdn.discordapp.com/a.png"}]),
                      message(5, "user-a", "!mavi ask explain this")]]
        client = self.run_loop(responses, allow_tasks=False, new_job=self.job_factory(captured))
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][0]["mode"], "chat")
        descriptions = [call[2]["embeds"][0]["description"] for call in client.calls
                        if call[2] and call[2].get("embeds")]
        self.assertGreaterEqual(sum("Local task access is off" in d for d in descriptions), 3)

    def test_rejects_bad_attachment_before_job_creation(self):
        responses = [[message(2, "user-a", "!mavi edit make it brighter",
                              [{"url": "https://files.example.com/photo.png"}])]]
        client = self.run_loop(responses, new_job=None)
        descriptions = [call[2]["embeds"][0]["description"] for call in client.calls
                        if call[2] and call[2].get("embeds")]
        self.assertTrue(any("approved Discord CDN host" in d for d in descriptions))
        self.assertFalse(server.JOBS)

    def test_stop_and_answer_are_scoped_to_issuing_user(self):
        captured = []
        local_cancel = threading.Event()
        remote_cancel_during_other_user_actions = []
        server.JOBS["local"] = {"id": "local", "_owner": "local", "status": "running",
                                "_cancel": local_cancel}
        server.ACTIVE = "local"
        responses = [[message(2, "user-a", "!mavi ask wait for my input"),
                      message(3, "user-b", "!mavi stop"),
                      message(4, "user-b", "!mavi answer wrong user"),
                      message(5, "user-a", "!mavi answer proceed")]]
        def inspect_reply(payload):
            desc = payload.get("embeds", [{}])[0].get("description", "")
            if "There is no running task from your account" in desc and captured:
                remote_cancel_during_other_user_actions.append(captured[0][2]["_cancel"].is_set())
        client = FakeClient(responses, on_reply=inspect_reply)
        self.run_loop(responses, new_job=self.job_factory(captured, status="waiting"), client=client)
        remote = captured[0][2]
        self.assertEqual(remote_cancel_during_other_user_actions, [False, False])
        # Disconnect cleanup revokes this remote-owned job after the polling loop exits.
        self.assertTrue(remote["_cancel"].is_set())
        self.assertEqual(remote["_answer"], "proceed")
        self.assertTrue(remote["_answer_event"].is_set())
        self.assertFalse(local_cancel.is_set())

    def test_disconnect_revokes_inflight_remote_job_only(self):
        captured = []
        local_cancel = threading.Event()
        server.JOBS["local"] = {"id": "local", "_owner": "local", "status": "running",
                                "_cancel": local_cancel}
        server.ACTIVE = "local"
        self.run_loop([[message(2, "user-a", "!mavi ask keep working")]],
                      new_job=self.job_factory(captured, status="running"))
        self.assertTrue(captured[0][2]["_cancel"].is_set())
        self.assertFalse(local_cancel.is_set())

    def test_waiting_prompt_and_completed_artifact_are_delivered(self):
        captured = []
        outputs = self.data / "outputs"
        outputs.mkdir()
        rendered = outputs / "render.png"
        rendered.write_bytes(b"generated png bytes")
        job = None

        def on_reply(payload):
            nonlocal job
            desc = payload.get("embeds", [{}])[0].get("description", "")
            if job and "Answer received." in desc:
                job.update(status="completed", content="Finished locally.",
                           artifacts=[{"name": "render.png", "url": "/api/artifact?name=render.png", "size": rendered.stat().st_size}])

        client = FakeClient([[message(2, "user-a", "!mavi ask draw a simple icon")],
                             [message(3, "user-a", "!mavi answer use blue")]], on_reply=on_reply)
        make_job = self.job_factory(captured, status="waiting")
        def create(body, owner):
            nonlocal job
            result = make_job(body, owner)
            job = captured[-1][2]
            job["question"] = "Which shade of blue?"
            return result
        self.run_loop(client.responses, new_job=create, client=client)
        descriptions = [call[2]["embeds"][0]["description"] for call in client.calls
                        if call[2] and call[2].get("embeds")]
        self.assertTrue(any("Which shade of blue?" in d for d in descriptions))
        self.assertTrue(any("Finished locally." in d for d in descriptions))
        self.assertTrue(any(call[3] == rendered for call in client.calls))

    def test_failed_artifact_upload_does_not_stop_polling(self):
        outputs = self.data / "outputs"
        outputs.mkdir()
        rendered = outputs / "render.png"
        rendered.write_bytes(b"generated png bytes")

        class UploadFailClient(FakeClient):
            def request(self, method, path, *, payload=None, cancelled=None, file_path=None, data_dir=None):
                if file_path is not None:
                    self.calls.append((method, path, payload, file_path, data_dir))
                    raise RuntimeError("simulated upload failure")
                return super().request(method, path, payload=payload, cancelled=cancelled,
                                       file_path=file_path, data_dir=data_dir)

        captured = []
        def completed_job(body, owner):
            result = self.job_factory(captured, status="completed", content="Work completed.")(body, owner)
            captured[-1][2]["artifacts"] = [{"name": "render.png", "size": rendered.stat().st_size}]
            return result
        client = UploadFailClient([[message(2, "user-a", "!mavi ask make a file")],
                                   [message(3, "user-a", "!mavi help")]])
        self.run_loop(client.responses, new_job=completed_job, client=client)
        descriptions = [call[2]["embeds"][0]["description"] for call in client.calls
                        if call[2] and call[2].get("embeds")]
        self.assertTrue(any("A file could not be delivered" in d for d in descriptions))
        self.assertTrue(any("!mavi ask <request>" in d for d in descriptions))
        self.assertEqual(client.poll_index, 2)
        self.assertFalse(server.DISCORD["status"].startswith("Connection stopped:"))


class AutomationScopeIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data, self.old_state = server.DATA, server.STATE
        self.old_jobs, self.old_active = server.JOBS, server.ACTIVE
        server.DATA = pathlib.Path(self.temp.name)
        server.STATE = {"chats": [], "settings": {"project_path": ""}}
        server.JOBS = {}
        server.ACTIVE = None

    def tearDown(self):
        server.DATA, server.STATE = self.old_data, self.old_state
        server.JOBS, server.ACTIVE = self.old_jobs, self.old_active
        self.temp.cleanup()

    def _new_job(self, *, scope, owner="local"):
        with patch.object(server, "choose_model", return_value="qwen3:8b"), \
                patch.object(server, "persist"), \
                patch.object(server.threading, "Thread") as thread:
            result = server.new_job({"text": "review this window", "mode": "computer", "automation_scope": scope}, owner=owner)
            thread.return_value.start.assert_called_once()
        return server.JOBS[result["job_id"]]

    def test_local_scope_is_job_scoped_and_remote_scope_is_forced_single_app(self):
        local = self._new_job(scope="whole_computer")
        self.assertEqual(local["_automation_scope"], "whole_computer")
        local["status"] = "done"
        server.ACTIVE = None
        remote = self._new_job(scope="whole_computer", owner="discord:user-a")
        self.assertEqual(remote["_automation_scope"], "single_app")

    def test_invalid_scope_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "valid computer-control scope"):
            server.new_job({"text": "review this window", "mode": "computer", "automation_scope": "shell"})


if __name__ == "__main__":
    unittest.main()
