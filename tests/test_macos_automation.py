import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import macos_automation as mac
import app_context


class MacAutomationTests(unittest.TestCase):
    def test_model_target_budget_prioritizes_requested_label_without_geometry(self):
        targets = [{"id": str(i), "role": "AXLink", "label": "Toolbar item", "bounds": {"x": i}} for i in range(80)]
        targets[-1]["label"] = "Chapter 4 accounting practice"
        selected = mac._model_targets(targets, "Open Chapter 4 accounting practice")
        self.assertEqual(len(selected), 32)
        self.assertEqual(selected[0]["id"], "79")
        self.assertTrue(all(set(item) == {"id", "role", "label"} for item in selected))

    def test_accessibility_target_uses_observed_identity_and_rechecks_before_input(self):
        actions = []
        questions = []
        target = {"id": "0.3.2", "role": "AXLink", "label": "Practice exercise", "bounds": {"x": 10, "y": 20, "width": 40, "height": 20}}
        outputs = iter([json.dumps({"action": "target", "target_id": "0.3.2", "reason": "Open the practice exercise page", "risk": "low"}),
                        json.dumps({"action": "done", "text": "Practice page visible."})])
        def helper(payload, **_kwargs):
            if payload["op"] == "windows": return {"windows": [{"window_id": 7, "title": "Course"}]}
            if payload["op"] == "capture": return {"bundle_id": "com.brave.Browser", "window_id": 7, "image_base64": base64.b64encode(b"mock").decode(), "targets": [target]}
            if payload["op"] == "action": actions.append(payload["action"])
            return {}
        def model(messages, **_kwargs):
            self.assertIn('"id":"0.3.2"', messages[-1]["content"])
            return next(outputs)
        context = {"ask": lambda q: questions.append(q) or "yes", "call_model": model, "automation_policy": "routine_navigation"}
        with patch.object(mac, "_call_helper", side_effect=helper):
            mac.run("Open Brave and read the practice exercise", [], context, browser=True)
        self.assertEqual(actions, [{"kind": "target", "target_id": "0.3.2", "expected_label": "Practice exercise", "expected_role": "AXLink", "expected_bounds": target["bounds"]}])
        self.assertEqual(len(questions), 2)  # Task and screen access; ordinary link needs no repeated approval.

    def test_invented_accessibility_target_sends_no_input(self):
        def helper(payload, **_kwargs):
            if payload["op"] == "windows": return {"windows": [{"window_id": 7, "title": "Course"}]}
            if payload["op"] == "capture": return {"bundle_id": "com.brave.Browser", "window_id": 7, "image_base64": base64.b64encode(b"mock").decode(), "targets": []}
            self.assertNotEqual(payload["op"], "action")
            return {}
        context = {"ask": lambda q: "yes", "call_model": lambda *args, **kwargs: json.dumps({"action": "target", "target_id": "0.9", "reason": "Open exercise", "risk": "low"})}
        with patch.object(mac, "_call_helper", side_effect=helper):
            with self.assertRaisesRegex(RuntimeError, "not in the current window"):
                mac.run("Open Brave and read the practice exercise", [], context, browser=True)

    def test_explicit_app_routing_rejects_mentions_quotes_and_conflicts(self):
        self.assertEqual(mac.explicit_app_target("Open Discord"), "discord")
        self.assertEqual(mac.explicit_app_target("Please open disc."), "discord")
        self.assertEqual(mac.explicit_app_target("open Discord and say hi to my friend"), "discord")
        self.assertTrue(mac.is_bare_open("Open Discord", "discord"))
        self.assertFalse(mac.is_bare_open("Open Discord and say hi", "discord"))
        self.assertIsNone(mac.explicit_app_target('How do I "open Discord"?'))
        self.assertIsNone(mac.explicit_app_target("I use Discord for work"))
        self.assertIsNone(mac.explicit_app_target("Open Discord and Chrome"))
        self.assertEqual(mac.route_explicit_target("Open Discord"), "computer")
        self.assertEqual(mac.route_explicit_target("Go into Canvas and work on my assignment"), "browser")
        self.assertEqual(mac.route_explicit_target("Go into Canvas and work on whatever assignment"), "browser")
        self.assertEqual(mac.route_explicit_target("Work on my Canvas assignment"), "browser")
        self.assertEqual(mac.route_explicit_target("https://school.example.edu/courses/123"), "browser")
        self.assertEqual(mac.route_explicit_target("Open https://example.com and check the page"), "browser")
        self.assertTrue(mac.has_unresolved_app_reference("I use Canvas for school"))
        self.assertTrue(mac.is_informational_app_question("What is Canvas?"))
        self.assertIsNone(mac.route_explicit_target("I work in Canvas"))
        self.assertTrue(mac.has_unresolved_app_reference("I use Discord for work"))
        self.assertFalse(mac.has_unresolved_app_reference("How do I use Discord?"))

    def test_bare_open_remembers_and_does_not_call_model(self):
        remembered = []
        helper = []
        context = {"ask": lambda _q: "yes", "remember_app_context": remembered.append}
        with patch.object(mac, "_call_helper", side_effect=lambda data, **_kw: helper.append(data) or {}), \
             patch.object(mac, "_helper_path", return_value=Path("/signed/Mavi")), \
             patch.object(mac, "sys_platform", return_value="darwin"):
            result = mac.run("Open Discord", [], context)
        self.assertIn("Opened Discord", result)
        self.assertEqual(helper, [{"op": "open_app", "bundle_id": "com.hnc.Discord"}])
        self.assertEqual(remembered[-1], {"app": "discord", "name": "Discord", "task": "", "status": "ready", "completed_steps": [], "last_result": "Opened Discord."})

    def test_conflicting_app_target_and_denied_consent_send_no_window_input(self):
        with patch.object(mac, "_call_helper") as helper:
            with self.assertRaisesRegex(RuntimeError, "Name the app"):
                mac.run("Open Discord and Chrome", [], {"ask": lambda _q: "yes"})
            helper.assert_not_called()
        helper.reset_mock()
        with patch.object(mac, "_call_helper", return_value={}) as helper:
            with self.assertRaisesRegex(RuntimeError, "not approved"):
                mac.run("Open Discord and say hi", [], {"ask": lambda _q: "no"})
        self.assertEqual([call.args[0]["op"] for call in helper.call_args_list], ["open_app"])
        self.assertNotIn("action", [call.args[0]["op"] for call in helper.call_args_list])

    def test_compound_open_continues_through_reviewed_actions(self):
        helper = []
        remembered = []
        outputs = iter([
            json.dumps({"action": "type", "text": "Hi, hope you're doing well!", "reason": "message composer", "risk": "low"}),
            json.dumps({"action": "click", "x": 950, "y": 900, "reason": "Send message button", "risk": "consequential"}),
            json.dumps({"action": "done", "text": "The message appears in the conversation."}),
        ])
        calls = []
        def fake_helper(payload, **_kwargs):
            helper.append(payload)
            if payload["op"] == "windows": return {"windows": [{"window_id": 23, "title": "Friend"}]}
            if payload["op"] == "capture":
                calls.append(payload)
                return {"bundle_id": "com.hnc.Discord", "window_id": 23,
                        "image_base64": base64.b64encode(b"mock-jpeg").decode(), "width": 10, "height": 10}
            return {"ok": True}
        def model_call(messages, **_kwargs):
            self.assertIn("Open Discord and say hi to my friend Alex", messages[1]["content"])
            self.assertIn("images", messages[-1])
            return next(outputs)
        context = {"ask": lambda _q: "yes", "call_model": model_call, "model": "local-vision",
                   "remember_app_context": remembered.append}
        with patch.object(mac, "_call_helper", side_effect=fake_helper), patch.object(mac, "_helper_path", return_value=Path("/signed/Mavi")), \
             patch.object(mac, "sys_platform", return_value="darwin"):
            result = mac.run("Open Discord and say hi to my friend Alex", [], context)
        self.assertIn("appears in the conversation", result)
        self.assertEqual([step["op"] for step in helper].count("action"), 2)
        self.assertEqual([step["op"] for step in helper].count("capture"), 3)
        self.assertEqual(remembered[-1]["status"], "done")
        self.assertEqual(remembered[-1]["app"], "discord")
        self.assertIn("Typed approved text", remembered[-1]["completed_steps"])
        self.assertNotIn("Hi, hope", json.dumps(remembered))
        self.assertNotIn("window_id", json.dumps(remembered))
        self.assertNotIn("image_base64", json.dumps(remembered))

    def test_follow_up_uses_remembered_app_without_reopening(self):
        helper = []
        context = {"app_context": {"app": "discord", "name": "Discord", "task": "Prior task: say hi", "status": "ready", "completed_steps": []},
                   "ask": lambda _q: "yes", "call_model": lambda _messages, **_kw: json.dumps({"action": "done", "text": "The conversation is ready."})}
        def fake_helper(payload, **_kwargs):
            helper.append(payload)
            if payload["op"] == "windows": return {"windows": [{"window_id": 7, "title": "Friend"}]}
            if payload["op"] == "capture": return {"bundle_id": "com.hnc.Discord", "window_id": 7, "image_base64": base64.b64encode(b"x").decode()}
            return {}
        with patch.object(mac, "_call_helper", side_effect=fake_helper):
            mac.run("Check the conversation", [], context)
        self.assertNotIn("open_app", [x["op"] for x in helper])

    def test_continue_my_assignment_routes_to_saved_browser_and_preserves_goal(self):
        bookmark = {"app": "brave", "name": "Brave", "task": "Go into Canvas and work on the assignment", "status": "ready", "completed_steps": ["Clicked a reviewed target"]}
        self.assertTrue(app_context.is_app_followup("continue my assignment", bookmark))
        helper = []
        prompts = []
        context = {"app_context": bookmark, "ask": lambda _q: "yes",
                   "call_model": lambda messages, **_kw: prompts.append(messages) or json.dumps({"action": "done", "text": "I need the assignment details to proceed."})}
        def fake_helper(payload, **_kwargs):
            helper.append(payload)
            if payload["op"] == "windows": return {"windows": [{"window_id": 15, "title": "Canvas course"}]}
            if payload["op"] == "capture": return {"bundle_id": "com.brave.Browser", "window_id": 15, "image_base64": base64.b64encode(b"screen").decode()}
            return {}
        with patch.object(mac, "_call_helper", side_effect=fake_helper):
            result = mac.run("continue my assignment", [], context)
        self.assertIn("need the assignment details", result)
        self.assertNotIn("open_app", [item["op"] for item in helper])
        self.assertIn("Go into Canvas and work on the assignment", prompts[0][1]["content"])
        self.assertIn("continue my assignment", prompts[0][1]["content"])
        self.assertIn("ask for the school, course, assignment", prompts[0][0]["content"])

    def test_canvas_without_a_saved_link_uses_default_browser_and_runs_goal(self):
        helper = []
        remembered = []
        model_calls = []
        model_outputs = iter([json.dumps({"action": "question", "text": "Which Canvas course should I open?"}),
                              json.dumps({"action": "done", "text": "I need your school's Canvas link before I can continue."})])
        context = {"ask": lambda _q: "yes", "remember_app_context": remembered.append,
                   "call_model": lambda messages, **_kw: model_calls.append(messages) or next(model_outputs)}
        def fake_helper(payload, **_kwargs):
            helper.append(payload)
            if payload["op"] == "default_browser": return {"bundle_id": "com.brave.Browser", "name": "Brave"}
            if payload["op"] == "windows": return {"windows": [{"window_id": 11, "title": "New Tab"}]}
            if payload["op"] == "capture": return {"bundle_id": "com.brave.Browser", "window_id": 11, "image_base64": base64.b64encode(b"screen").decode()}
            return {}
        answers = iter(["yes", "yes", "I don't have the link yet"])
        context["ask"] = lambda _q: next(answers)
        with patch.object(mac, "_call_helper", side_effect=fake_helper):
            result = mac.run("Go into Canvas and work on my assignment", [], context, browser=True)
        self.assertIn("open_app", [item["op"] for item in helper])
        self.assertNotIn("open_url", [item["op"] for item in helper])
        self.assertEqual(len(model_calls), 2)
        self.assertIn("I need your school's Canvas link", result)
        self.assertEqual(remembered[-1]["app"], "brave")

    def test_bare_https_url_opens_default_browser_without_model(self):
        helper = []
        remembered = []
        def fake_helper(payload, **_kwargs):
            helper.append(payload)
            if payload["op"] == "default_browser": return {"bundle_id": "com.brave.Browser"}
            if payload["op"] == "open_url": return {"bundle_id": "com.brave.Browser"}
            return {}
        with patch.object(mac, "_call_helper", side_effect=fake_helper):
            result = mac.run("https://example.com/course", [], {"remember_app_context": remembered.append}, browser=True)
        self.assertIn("Opened the supplied HTTPS link", result)
        self.assertEqual([x["op"] for x in helper], ["default_browser", "open_url"])
        self.assertEqual(remembered[-1]["app"], "brave")

    def test_explicit_browser_and_https_link_open_in_that_browser_before_task(self):
        helper = []
        prompts = []
        context = {"ask": lambda question: prompts.append(question) or "yes",
                   "call_model": lambda _messages, **_kw: json.dumps({"action": "done", "text": "The assignment page is open."})}
        def fake_helper(payload, **_kwargs):
            helper.append(payload)
            if payload["op"] == "open_app": return {"bundle_id": payload["bundle_id"]}
            if payload["op"] == "open_url": return {"bundle_id": payload["bundle_id"]}
            if payload["op"] == "capability": return {"screen_recording": True, "accessibility": True}
            if payload["op"] == "windows": return {"windows": [{"window_id": 51, "title": "Canvas Assignment"}]}
            if payload["op"] == "capture": return {"bundle_id": "com.brave.Browser", "window_id": 51, "image_base64": base64.b64encode(b"screen").decode()}
            return {}
        with patch.object(mac, "_call_helper", side_effect=fake_helper):
            result = mac.run("Open https://canvas.example.edu/courses/123456/assignments/987654?return=%2Fdashboard%2Fcourses%2F123456 in Brave and open the assignment", [], context, browser=True)
        self.assertIn("assignment page is open", result)
        open_url = next(item for item in helper if item["op"] == "open_url")
        self.assertEqual(open_url["bundle_id"], "com.brave.Browser")
        self.assertEqual(open_url["url"], "https://canvas.example.edu/courses/123456/assignments/987654?return=%2Fdashboard%2Fcourses%2F123456")
        self.assertLess([item["op"] for item in helper].index("open_url"), [item["op"] for item in helper].index("windows"))

    def test_private_handoff_stop_takes_no_second_screenshot(self):
        captures = []
        def fake_helper(payload, **_kwargs):
            if payload["op"] == "windows": return {"windows": [{"window_id": 9, "title": "Sign in"}]}
            if payload["op"] == "capture":
                captures.append(payload); return {"bundle_id": "com.hnc.Discord", "window_id": 9, "image_base64": base64.b64encode(b"x").decode()}
            return {}
        answers = iter(["yes", "yes", "stop"])
        context = {"ask": lambda _q: next(answers), "call_model": lambda _m, **_kw: json.dumps({"action": "question", "text": "Please sign in with your password."})}
        with patch.object(mac, "_call_helper", side_effect=fake_helper):
            with self.assertRaisesRegex(RuntimeError, "Private step paused"):
                mac.run("Open Discord and check my account", [], context)
        self.assertEqual(len(captures), 1)

    def test_missing_macos_permissions_handoff_rechecks_before_windows_or_capture(self):
        calls = []
        remembered = []
        capability_checks = 0
        def fake_helper(payload, **_kwargs):
            nonlocal capability_checks
            calls.append(payload["op"])
            if payload["op"] == "capability":
                capability_checks += 1
                if capability_checks == 1:
                    return {"screen_recording": False, "accessibility": False}
                return {"screen_recording": True, "accessibility": True}
            if payload["op"] == "windows": return {"windows": [{"window_id": 31, "title": "Discord"}]}
            if payload["op"] == "capture": return {"bundle_id": "com.hnc.Discord", "window_id": 31, "image_base64": base64.b64encode(b"screen").decode()}
            return {}
        questions = []
        answers = iter(["yes", "continue", "yes"])
        context = {"ask": lambda q: questions.append(q) or next(answers),
                   "remember_app_context": remembered.append,
                   "call_model": lambda _messages, **_kw: json.dumps({"action": "done", "text": "I need the course page."})}
        with patch.object(mac, "_call_helper", side_effect=fake_helper):
            result = mac.run("Open Discord and check the conversation", [], context)
        self.assertIn("need the course page", result)
        self.assertIn("System Settings", questions[1])
        self.assertIn("Accessibility", questions[1])
        self.assertEqual(calls[:3], ["open_app", "capability", "capability"])
        self.assertLess(calls.index("capability", 1), calls.index("windows"))
        self.assertLess(calls.index("capability", 1), calls.index("capture"))

    def test_still_missing_permission_returns_actionable_error_without_window_access(self):
        calls = []
        remembered = []
        context = {"ask": lambda _q: "continue", "remember_app_context": remembered.append}
        def fake_helper(payload, **_kwargs):
            calls.append(payload["op"])
            if payload["op"] == "capability": return {"screen_recording": False, "accessibility": True}
            return {}
        with patch.object(mac, "_call_helper", side_effect=fake_helper):
            with self.assertRaisesRegex(RuntimeError, "still lacks Screen Recording"):
                mac.run("Open Discord and check the conversation", [], context)
        self.assertNotIn("windows", calls)
        self.assertNotIn("capture", calls)
        self.assertEqual(remembered[-1]["status"], "stopped")
        self.assertIn("still lacks Screen Recording", remembered[-1]["last_result"])

    def test_cancelled_task_does_not_invoke_helper(self):
        class Event:
            def is_set(self): return True
        with patch.object(mac, "_call_helper") as helper:
            with self.assertRaises(InterruptedError): mac.run("Open Discord", [], {"cancelled": Event()})
        helper.assert_not_called()

    def test_helper_uses_exact_executable_without_shell_and_validates_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "MaviHelper"
            path.write_text("mock")
            path.chmod(0o700)
            env = {"MAVI_MAC_HELPER": str(path)}
            completed = SimpleNamespace(stdout='{"ok":true,"result":{"screen_recording":true}}\n', returncode=0)
            with patch.dict(os.environ, env, clear=False), patch.object(mac.subprocess, "run", return_value=completed) as run:
                result = mac._call_helper({"op": "capability"})
            self.assertEqual(result, {"screen_recording": True})
            self.assertEqual(run.call_args.args[0], [str(path.resolve()), "--mavi-automation"])
            self.assertIs(run.call_args.kwargs["shell"], False)
            self.assertEqual(run.call_args.kwargs["stderr"], mac.subprocess.DEVNULL)

    def test_action_schema_rejects_cross_app_shortcuts_and_invalid_risk(self):
        with self.assertRaises(ValueError): mac._validate_action('{"action":"switch_app","app":"safari"}')
        with self.assertRaises(ValueError): mac._validate_action('{"action":"key","key":"COMMAND+Q"}')
        with self.assertRaises(ValueError): mac._validate_action('{"action":"scroll","direction":"down","amount":99999}')
        with self.assertRaises(ValueError): mac._validate_action('{"action":"click","x":1,"y":1,"reason":"link","risk":"anything"}')


if __name__ == "__main__":
    unittest.main()
