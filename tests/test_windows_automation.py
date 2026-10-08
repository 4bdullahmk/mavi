import unittest
from unittest.mock import patch
from unittest.mock import Mock

from portable import windows_automation as automation


class FakeWindow:
    _hWnd = 7
    left = 10
    top = 20
    width = 200
    height = 100


class FakeScreen:
    size = (800, 600)


class WindowsAutomationActionTests(unittest.TestCase):
    def setUp(self):
        self.original_grace = automation.FOCUS_GRACE_SECONDS
        automation.FOCUS_GRACE_SECONDS = 0
        self.gui = Mock()
        self.gui.screenshot.return_value = FakeScreen()
        self.context = {"ask": Mock(return_value="yes")}
        self.window = FakeWindow()
        self.window_api = Mock()
        self.window_api.getActiveWindow.return_value = self.window

    def tearDown(self):
        automation.FOCUS_GRACE_SECONDS = self.original_grace

    def test_click_maps_normalized_position_inside_selected_window(self):
        action = {"action": "click", "x": 1000, "y": 0, "reason": "open tab", "risk": "low"}
        automation._execute_action(action, self.gui, self.window_api, self.window, self.context, "open tab")
        self.gui.click.assert_called_once_with(209, 20)
        self.context["ask"].assert_called_once()

    def test_routine_navigation_skips_only_plain_navigation_click(self):
        self.context["automation_policy"] = "routine_navigation"
        action = {"action": "click", "x": 500, "y": 500, "reason": "open navigation tab", "risk": "low"}
        automation._execute_action(action, self.gui, self.window_api, self.window, self.context, "open navigation tab")
        self.gui.click.assert_called_once()
        self.context["ask"].assert_not_called()

    def test_routine_navigation_does_not_skip_consequential_click_or_enter(self):
        self.context["automation_policy"] = "routine_navigation"
        self.context["ask"].return_value = "no"
        click = {"action": "click", "x": 500, "y": 500, "reason": "open settings page", "risk": "consequential"}
        with self.assertRaisesRegex(RuntimeError, "declined"):
            automation._execute_action(click, self.gui, self.window_api, self.window, self.context, "open settings page")
        enter = {"action": "key", "key": "ENTER", "risk": "low"}
        with self.assertRaisesRegex(RuntimeError, "declined"):
            automation._execute_action(enter, self.gui, self.window_api, self.window, self.context, "")
        self.gui.click.assert_not_called()
        self.gui.press.assert_not_called()

    def test_routine_navigation_still_requires_approval_before_typing(self):
        self.context["automation_policy"] = "routine_navigation"
        self.context["ask"].return_value = "no"
        action = {"action": "type", "text": "hello there", "risk": "low"}
        with self.assertRaisesRegex(RuntimeError, "declined"):
            automation._execute_action(action, self.gui, self.window_api, self.window, self.context, "hello there")
        self.gui.write.assert_not_called()

    def test_routine_navigation_only_skips_scroll_and_safe_navigation_keys(self):
        self.context["automation_policy"] = "routine_navigation"
        automation._execute_action({"action": "scroll", "direction": "down", "amount": 200, "risk": "low"}, self.gui, self.window_api, self.window, self.context, "")
        automation._execute_action({"action": "key", "key": "TAB", "risk": "low"}, self.gui, self.window_api, self.window, self.context, "")
        self.context["ask"].assert_not_called()
        self.gui.scroll.assert_called_once()
        self.gui.press.assert_called_once_with("tab")

    def test_explicit_app_routing_and_question_negation(self):
        self.assertEqual(automation.route_explicit_target("Open Brave and visit https://example.com"), "browser")
        self.assertEqual(automation.route_explicit_target("work in Webex and review the meeting window"), "computer")
        self.assertEqual(automation.route_explicit_target("open Excel and review the workbook"), "computer")
        self.assertEqual(automation.route_explicit_target("open File Explorer and find the report"), "computer")
        self.assertEqual(automation.route_explicit_target("open Word and format the document"), "computer")
        self.assertIsNone(automation.route_explicit_target("How do I open Chrome?"))
        self.assertIsNone(automation.route_explicit_target("I do not want to use Chrome"))
        self.assertIsNone(automation.route_explicit_target("What is the difference between Chrome and Firefox?"))
        self.assertIsNone(automation._explicit_app_target('Explain the phrase "open Brave"'))
        self.assertIsNone(automation._explicit_app_target("I use Chrome for work"))
        self.assertIsNone(automation._explicit_app_target("Open Chrome or Brave"))
        self.assertTrue(automation.has_unresolved_app_reference('Explain the phrase "open Brave"'))
        self.assertTrue(automation.has_unresolved_app_reference("I use Chrome for work"))
        self.assertTrue(automation.has_unresolved_app_reference("Open Chrome or Brave"))

    def test_bare_https_address_uses_default_browser_route(self):
        self.assertEqual(automation.route_explicit_target("https://example.com/path"), "browser")
        self.assertEqual(automation.route_explicit_target("Please explain https://example.com/path"), None)

    def test_running_window_is_matched_to_requested_app(self):
        class AppWindow(FakeWindow):
            title = "Webex Meeting"

            def activate(self):
                self.activated = True

        window = AppWindow()
        api = Mock()
        api.getAllWindows.return_value = [window]
        api.getActiveWindow.return_value = None
        with patch.object(automation, "_window_process_path", return_value=None):
            self.assertEqual(automation._app_windows(api, "webex"), [window])
            self.assertTrue(automation._prepare_app_target(api, "webex", self.context))
        self.assertTrue(window.activated)

    def test_unique_explicit_app_target_uses_one_task_consent(self):
        window = FakeWindow()
        window.title = "Brave Browser"
        context = {"ask": Mock(return_value="yes"), "call_model": lambda *_args, **_kwargs: '{"action":"done","text":"Finished"}'}
        with patch.object(automation, "_deps", return_value=(self.gui, self.window_api, object())), \
                patch.object(automation, "_prepare_app_target", return_value=True), \
                patch.object(automation, "_target_window", return_value=(window, window.title)), \
                patch.object(automation, "_focus_grace"), \
                patch.object(automation, "_screenshot", return_value="image"), \
                patch.object(automation, "_ask_focus") as ask_focus:
            result = automation.run("Open Brave", None, context, browser=True)
        self.assertEqual(result, "Finished")
        ask_focus.assert_not_called()
        self.assertEqual(context["ask"].call_count, 1)

    def test_course_materials_policy_reaches_synthetic_window_turn(self):
        window = FakeWindow()
        window.title = "Canvas assignment"
        seen = []
        context = {"ask": Mock(return_value="yes"),
                   "call_model": lambda messages, **_kwargs: seen.append(messages) or '{"action":"done","text":"Need the assigned reading."}'}
        with patch.object(automation, "_deps", return_value=(self.gui, self.window_api, object())), \
                patch.object(automation, "_prepare_app_target", return_value=True), \
                patch.object(automation, "_target_window", return_value=(window, window.title)), \
                patch.object(automation, "_focus_grace"), \
                patch.object(automation, "_screenshot", return_value="screen"), \
                patch.object(automation, "_active_matches", return_value=True):
            automation.run("Open Brave and help with my Canvas assignment from the lecture slides.", None, context)
        system = seen[0][0]["content"]
        self.assertIn("only on materials the user supplies", system)
        self.assertIn("Do not fill gaps with web searches", system)

    def test_ambiguous_explicit_app_still_confirms_selected_window(self):
        window = FakeWindow()
        window.title = "Brave Browser"
        context = {"ask": Mock(return_value="yes"), "call_model": lambda *_args, **_kwargs: '{"action":"done","text":"Finished"}'}
        with patch.object(automation, "_deps", return_value=(self.gui, self.window_api, object())), \
                patch.object(automation, "_prepare_app_target", return_value=False), \
                patch.object(automation, "_target_window", return_value=(window, window.title)), \
                patch.object(automation, "_focus_grace"), \
                patch.object(automation, "_screenshot", return_value="image"), \
                patch.object(automation, "_ask_focus") as ask_focus:
            result = automation.run("Open Brave", None, context, browser=True)
        self.assertEqual(result, "Finished")
        ask_focus.assert_called_once()

    def test_app_launch_is_direct_executable_without_shell_or_profile_flags(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / "chrome.exe"
            executable.write_bytes(b"fixture")
            with patch.object(automation.subprocess, "Popen") as launch:
                automation._launch_app(str(executable), "https://example.com")
            args, kwargs = launch.call_args
            self.assertEqual(args[0], [str(executable), "https://example.com"])
            self.assertFalse(kwargs["shell"])
            self.assertFalse(any("--user-data-dir" in arg or "--incognito" in arg for arg in args[0]))

    def test_consequential_click_requires_confirmation(self):
        self.context["ask"].return_value = "no"
        action = {"action": "click", "x": 100, "y": 100, "reason": "submit the form", "risk": "consequential"}
        with self.assertRaisesRegex(RuntimeError, "declined"):
            automation._execute_action(action, self.gui, self.window_api, self.window, self.context, "submit the form")
        self.gui.click.assert_not_called()

    def test_credential_text_is_handed_to_user_without_typing(self):
        action = {"action": "type", "text": "hunter2", "reason": "password field", "risk": "credential"}
        result = automation._execute_action(action, self.gui, self.window_api, self.window, self.context, "enter password")
        self.assertEqual(result, automation.PRIVATE_HANDOFF)
        self.gui.write.assert_not_called()
        self.context["ask"].assert_called_once()

    def test_credential_click_uses_continue_handoff_without_clicking(self):
        action = {"action": "click", "x": 10, "y": 10, "reason": "login", "risk": "credential"}
        result = automation._execute_action(action, self.gui, self.window_api, self.window, self.context, "log in")
        self.assertEqual(result, automation.PRIVATE_HANDOFF)
        self.gui.click.assert_not_called()
        self.assertIn("continue", self.context["ask"].call_args.args[0])

    def test_terminal_shortcuts_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "navigation keys only"):
            automation._validate_action('{"action":"key","key":"CTRL+C","risk":"low"}')

    def test_switch_action_requires_allowlisted_app_and_exact_shape(self):
        for app in ("brave", "chrome", "edge", "firefox", "webex", "explorer", "notepad", "word", "excel"):
            self.assertEqual(automation._validate_action('{"action":"switch_app","app":"' + app + '"}'), {"action": "switch_app", "app": app})
        with self.assertRaisesRegex(ValueError, "limited to"):
            automation._validate_action('{"action":"switch_app","app":"calculator"}')
        with self.assertRaisesRegex(ValueError, "contain only"):
            automation._validate_action('{"action":"switch_app","app":"webex","risk":"low"}')

    def test_windows_app_identity_requires_exact_resolved_executable_path(self):
        window = FakeWindow()
        with patch.object(automation, "_window_process_path", return_value=r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE"), \
                patch.object(automation, "_resolve_app_executable", return_value=r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE"):
            self.assertTrue(automation._window_matches_app(window, "word"))
        with patch.object(automation, "_window_process_path", return_value=r"C:\Temp\WINWORD.EXE"), \
                patch.object(automation, "_resolve_app_executable", return_value=r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE"):
            self.assertFalse(automation._window_matches_app(window, "word"))

    def test_file_explorer_matching_excludes_windows_desktop_shell(self):
        class ExplorerWindow(FakeWindow):
            title = "Program Manager"

        window = ExplorerWindow()
        with patch.object(automation, "_window_process_path", return_value=r"C:\Windows\explorer.exe"), \
                patch.object(automation, "_resolve_app_executable", return_value=r"C:\Windows\explorer.exe"):
            self.assertFalse(automation._window_matches_app(window, "explorer"))
            window.title = "Documents"
            self.assertTrue(automation._window_matches_app(window, "explorer"))

    def test_switch_app_is_disallowed_in_single_app_scope(self):
        with patch.object(automation, "_prepare_app_target") as prepare:
            with self.assertRaisesRegex(RuntimeError, "limited to one app"):
                automation._switch_app_target("webex", self.window_api, {"ask": Mock(return_value="yes"), "automation_scope": "single_app"})
        prepare.assert_not_called()

    def test_switch_app_always_requires_separate_approval(self):
        target = FakeWindow()
        target.title = "Webex Meeting"
        context = {"ask": Mock(return_value="yes"), "automation_scope": "whole_computer", "automation_policy": "routine_navigation"}
        with patch.object(automation, "_prepare_app_target", return_value=True) as prepare, \
                patch.object(automation, "_target_window", return_value=(target, target.title)), \
                patch.object(automation, "_active_matches", return_value=True):
            window, title = automation._switch_app_target("webex", self.window_api, context)
        self.assertIs(window, target)
        self.assertEqual(title, target.title)
        prepare.assert_called_once()
        context["ask"].assert_called_once()
        self.assertIn("always needs your approval", context["ask"].call_args.args[0])

    def test_switch_app_decline_does_not_open_or_retarget(self):
        context = {"ask": Mock(return_value="no"), "automation_scope": "whole_computer"}
        with patch.object(automation, "_prepare_app_target") as prepare:
            with self.assertRaisesRegex(RuntimeError, "declined"):
                automation._switch_app_target("webex", self.window_api, context)
        prepare.assert_not_called()

    def test_switch_app_rejects_changed_or_wrong_active_window(self):
        target = FakeWindow()
        target.title = "Webex Meeting"
        context = {"ask": Mock(return_value="yes"), "automation_scope": "whole_computer"}
        with patch.object(automation, "_prepare_app_target", return_value=True), \
                patch.object(automation, "_target_window", return_value=(target, target.title)), \
                patch.object(automation, "_active_matches", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "did not retarget"):
                automation._switch_app_target("webex", self.window_api, context)

    def test_whole_computer_loop_rebinds_to_verified_app_after_approved_switch(self):
        initial = FakeWindow()
        initial.title = "Browser"
        target = FakeWindow()
        target.title = "Webex Meeting"
        seen = []
        responses = iter(['{"action":"switch_app","app":"webex"}', '{"action":"done","text":"Reviewed both windows"}'])
        context = {
            "ask": Mock(return_value="yes"),
            "automation_scope": "whole_computer",
            "call_model": lambda messages, **_kwargs: seen.append(messages) or next(responses),
        }
        with patch.object(automation, "_deps", return_value=(self.gui, self.window_api, object())), \
                patch.object(automation, "_target_window", return_value=(initial, initial.title)), \
                patch.object(automation, "_switch_app_target", return_value=(target, target.title)) as switch, \
                patch.object(automation, "_focus_grace"), \
                patch.object(automation, "_ask_focus"), \
                patch.object(automation, "_active_matches", return_value=True), \
                patch.object(automation, "_screenshot", side_effect=["initial-frame", "webex-frame"]):
            result = automation.run("Review the page, then switch to Webex", None, context)
        self.assertEqual(result, "Reviewed both windows")
        switch.assert_called_once_with("webex", self.window_api, context)
        self.assertIn("whole-computer is enabled", seen[0][0]["content"])
        self.assertIn("The user approved switching apps", seen[1][-2]["content"])
        self.assertEqual(seen[1][-1]["images"], ["webex-frame"])

    def test_click_bounds_are_checked_before_action(self):
        with self.assertRaisesRegex(ValueError, "coordinates"):
            automation._validate_action('{"action":"click","x":1001,"y":1,"reason":"open","risk":"low"}')

    def test_typed_line_breaks_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "line breaks"):
            automation._validate_action('{"action":"type","text":"first\\nsecond","risk":"low"}')

    def test_sign_in_mention_is_not_mistaken_for_a_disclosed_secret(self):
        self.assertEqual(automation._user_supplied("Wait while I log in to the site", None), "Wait while I log in to the site")
        with self.assertRaisesRegex(ValueError, "possible secrets"):
            automation._user_supplied("My password is hunter2", None)
        with self.assertRaisesRegex(ValueError, "possible secrets"):
            automation._user_supplied("Use me@example.com to sign in", None)

    def test_focus_change_during_confirmation_prevents_action(self):
        self.context["ask"].side_effect = lambda _prompt: setattr(self.window_api.getActiveWindow, "return_value", None) or "yes"
        action = {"action": "click", "x": 100, "y": 100, "reason": "submit form", "risk": "consequential"}
        with self.assertRaisesRegex(RuntimeError, "refocused"):
            automation._execute_action(action, self.gui, self.window_api, self.window, self.context, "submit")
        self.gui.click.assert_not_called()

    def test_shift_tab_uses_chord(self):
        automation._execute_action({"action": "key", "key": "SHIFT+TAB", "risk": "low"}, self.gui, self.window_api, self.window, self.context, "")
        self.gui.hotkey.assert_called_once_with("shift", "tab")
        self.gui.press.assert_not_called()

    def test_scroll_pixels_are_converted_to_wheel_ticks(self):
        action = {"action": "scroll", "direction": "down", "amount": 400}
        automation._execute_action(action, self.gui, self.window_api, self.window, self.context, "")
        self.gui.scroll.assert_called_once_with(-4)

    def test_model_drafted_text_requires_exact_preview_approval(self):
        self.context["ask"].return_value = "yes"
        action = {"action": "type", "text": "A model drafted sentence", "risk": "low"}
        automation._execute_action(action, self.gui, self.window_api, self.window, self.context, "write a sentence")
        self.assertIn("A model drafted sentence", self.context["ask"].call_args.args[0])
        self.gui.write.assert_called_once_with("A model drafted sentence", interval=0.01)

    def test_model_drafted_text_is_not_typed_without_preview_approval(self):
        self.context["ask"].return_value = "no"
        action = {"action": "type", "text": "A model drafted sentence", "risk": "low"}
        with self.assertRaisesRegex(RuntimeError, "declined"):
            automation._execute_action(action, self.gui, self.window_api, self.window, self.context, "write a sentence")
        self.gui.write.assert_not_called()

    def _run_loop(self, model_call, context=None):
        window = FakeWindow()
        window.title = "Example app"
        window_api = Mock()
        window_api.getActiveWindow.return_value = window
        ctx = {"ask": Mock(return_value="yes"), "call_model": model_call, **(context or {})}
        events = []

        def screenshot(*_args):
            events.append("screenshot")
            return "image"

        def ask(prompt):
            events.append("ask:" + prompt)
            return "yes" if "Allow" in prompt else "continue"

        ctx["ask"] = ask
        with patch.object(automation, "_deps", return_value=(self.gui, window_api, object())), \
                patch.object(automation, "_target_window", return_value=(window, window.title)), \
                patch.object(automation, "_focus_grace"), \
                patch.object(automation, "_screenshot", side_effect=screenshot):
            result = automation.run("complete this task", None, ctx)
        return result, events

    def test_private_login_handoff_resumes_same_window_after_continue(self):
        responses = iter([
            '{"action":"question","text":"Please log in to continue"}',
            '{"action":"done","text":"Finished"}',
        ])
        result, events = self._run_loop(lambda *_args, **_kwargs: next(responses))
        handoff_index = next(i for i, event in enumerate(events) if event.startswith("ask:This step requires a private sign-in"))
        self.assertEqual(result, "Finished")
        self.assertEqual(events[handoff_index + 1], "screenshot")

    def test_steering_is_drained_at_step_boundary_and_added_to_context(self):
        seen = []
        model_responses = iter(['{"action":"done","text":"Finished"}'])
        result, _events = self._run_loop(
            lambda messages, **_kwargs: seen.append(messages) or next(model_responses),
            {"drain_steer": lambda: ["Focus on the billing address"]},
        )
        self.assertEqual(result, "Finished")
        self.assertTrue(any("Focus on the billing address" in str(message) for message in seen[0]))


if __name__ == "__main__":
    unittest.main()
