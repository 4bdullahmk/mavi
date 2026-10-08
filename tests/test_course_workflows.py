import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
import course_workflows
import model_policy


class CourseWorkflowGuidanceTests(unittest.TestCase):
    def test_connect_accounting_is_limited_to_supplied_or_assigned_materials(self):
        guidance = course_workflows.guidance_for(
            "Use McGraw Hill Connect for my accounting assignment."
        )
        for required in (
            "only on materials the user supplies",
            "Do not fill gaps with web searches or general outside knowledge",
            "ask the user for it",
            "never invent a citation",
            "Distinguish source facts from calculations",
            "Do not train or fine-tune",
            "rounding rules",
        ):
            self.assertIn(required, guidance)

    def test_smartbook_uses_assigned_reading_and_does_not_guess_or_manipulate(self):
        guidance = course_workflows.guidance_for(
            "Open SmartBook and help me study the assigned chapter."
        )
        self.assertIn("only on materials the user supplies", guidance)
        self.assertIn("assigned reading", guidance)
        self.assertIn("do not guess unseen course content", guidance)
        self.assertIn("manipulate completion or progress", guidance)
        self.assertIn("graded submission", guidance)

    def test_guidance_covers_canvas_pearson_and_class_assignments(self):
        for request in (
            "Open Canvas and help with the assignment using my uploaded lecture notes.",
            "Use Pearson to review the homework prompt and assigned chapter.",
            "Help me answer this class assignment from the rubric and lecture slides.",
        ):
            with self.subTest(request=request):
                guidance = course_workflows.guidance_for(request)
                self.assertIn("only on materials the user supplies", guidance)
                self.assertIn("Do not fill gaps with web searches", guidance)

    def test_narrow_followup_inherits_recent_course_context(self):
        prior = ["Use Canvas to review my biology assignment from the lecture slides."]
        guidance = course_workflows.guidance_for("Help with the next question.", prior)
        self.assertIn("only on materials the user supplies", guidance)
        self.assertIn("Do not fill gaps with web searches", guidance)

    def test_unrelated_requests_and_stale_context_do_not_inherit_course_policy(self):
        self.assertEqual(course_workflows.guidance_for("Explain compound interest."), "")
        self.assertEqual(course_workflows.guidance_for("What is McGraw Hill as a company?"), "")
        self.assertEqual(course_workflows.guidance_for("Open SmartBook settings and change my profile."), "")
        self.assertEqual(course_workflows.guidance_for(
            "Write a Python script to rename my photos.",
            ["Use Canvas to review my assignment."],
        ), "")
        self.assertEqual(course_workflows.guidance_for(
            "Help with the next question.",
            ["What is McGraw Hill as a company?"],
        ), "")

    def test_model_policy_carries_course_guidance_only_to_direct_or_narrow_followups(self):
        messages = [
            {"role": "user", "content": "Use Canvas to review my accounting assignment."},
            {"role": "assistant", "content": "I found the assigned reading."},
            {"role": "user", "content": "Help with the next question."},
        ]
        prepared, _ = model_policy.prepare_messages(messages)
        self.assertIn("only on materials the user supplies", prepared[0]["content"])
        self.assertIn("course policies", prepared[0]["content"])

        unrelated, _ = model_policy.prepare_messages([
            {"role": "user", "content": "Use Canvas to review my assignment."},
            {"role": "assistant", "content": "Done."},
            {"role": "user", "content": "Explain how to use Python pathlib."},
        ])
        self.assertNotIn("only on materials the user supplies", unrelated[0]["content"])

        ordinary, _ = model_policy.prepare_messages([
            {"role": "user", "content": "Explain how compound interest works."}
        ])
        self.assertNotIn("course materials", ordinary[0]["content"])

    def test_guidance_has_no_portal_addresses_or_credential_fields(self):
        text = (course_workflows.COURSE_MATERIALS_GUIDANCE
                + course_workflows.CONNECT_ACCOUNTING_GUIDANCE
                + course_workflows.SMARTBOOK_GUIDANCE)
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)
        self.assertNotIn("password=", text.lower())
        self.assertNotIn("train the model", text.lower())


if __name__ == "__main__":
    unittest.main()
