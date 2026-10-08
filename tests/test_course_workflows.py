import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
import course_workflows
import model_policy


class CourseWorkflowGuidanceTests(unittest.TestCase):
    def test_connect_accounting_gets_preservation_and_calculation_guidance(self):
        guidance = course_workflows.guidance_for(
            "Use McGraw Hill Connect for my accounting assignment."
        )
        self.assertIn("units", guidance)
        self.assertIn("rounding", guidance)
        self.assertIn("unsaved work", guidance)
        self.assertIn("explicitly approves", guidance)

    def test_smartbook_gets_study_guidance_and_no_progress_manipulation(self):
        guidance = course_workflows.guidance_for(
            "Open SmartBook and help me study the assigned chapter."
        )
        self.assertIn("teach", guidance)
        self.assertIn("practice example", guidance)
        self.assertIn("do not guess", guidance)
        self.assertIn("manipulate completion or progress", guidance)

    def test_workflow_guidance_is_empty_for_unmatched_requests(self):
        for text in (
            "Explain compound interest without opening a course site.",
            "What is McGraw Hill as a company?",
            "Open SmartBook settings and change my profile.",
        ):
            with self.subTest(text=text):
                self.assertEqual(course_workflows.guidance_for(text), "")

    def test_model_policy_imports_specific_guidance_only_for_course_tasks(self):
        messages = [{"role": "user", "content": "Help with my Connect accounting assignment."}]
        prepared, _ = model_policy.prepare_messages(messages)
        self.assertIn("For McGraw Hill Connect accounting work", prepared[0]["content"])
        self.assertIn("course policies", prepared[0]["content"])

        ordinary, _ = model_policy.prepare_messages([
            {"role": "user", "content": "Explain how compound interest works."}
        ])
        self.assertNotIn("SmartBook", ordinary[0]["content"])
        self.assertNotIn("Connect accounting work", ordinary[0]["content"])

    def test_guidance_has_no_portal_addresses_or_credential_fields(self):
        text = course_workflows.CONNECT_ACCOUNTING_GUIDANCE + course_workflows.SMARTBOOK_GUIDANCE
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)
        self.assertNotIn("password=", text.lower())
        self.assertNotIn("train the model", text.lower())


if __name__ == "__main__":
    unittest.main()
