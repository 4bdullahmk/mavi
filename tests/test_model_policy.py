import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
import model_policy as policy


class ModelPolicyTests(unittest.TestCase):
    def test_role_budgets_and_explicit_overrides(self):
        examples = {
            "routine": [{"role": "user", "content": "Hi"}],
            "narration": [{"role": "system", "content": "Give a brief progress update."}],
            "router": [{"role": "system", "content": "Classify the request and route it."}],
            "code": [{"role": "user", "content": "Please write code for this."}],
            "analysis": [{"role": "system", "content": "You are the evidence analyst."}],
        }
        for expected, messages in examples.items():
            with self.subTest(role=expected):
                self.assertEqual(policy.classify_role(messages), expected)
                self.assertEqual(policy.policy_for(expected)["num_predict"], policy.ROLE_BUDGETS[expected])
        self.assertEqual(policy.classify_role(examples["routine"], "analysis"), "analysis")
        with self.assertRaises(ValueError):
            policy.policy_for("unknown")

    def test_prepare_messages_appends_initiative_without_mutating_input(self):
        source = [{"role": "system", "content": "Return JSON only."},
                  {"role": "user", "content": "Route this request."}]
        prepared, generation = policy.prepare_messages(source)
        self.assertEqual(source[0]["content"], "Return JSON only.")
        self.assertIn("Return JSON only.", prepared[0]["content"])
        self.assertIn("allowed tools", prepared[0]["content"])
        self.assertIn("approval", prepared[0]["content"])
        self.assertEqual(generation, {"role": "router", "num_predict": policy.ROLE_BUDGETS["router"]})
        with self.assertRaises(ValueError):
            policy.prepare_messages([{"role": "user", "content": "x"}, None])

    def test_course_site_guidance_is_conditional_and_does_not_mutate_input(self):
        source = [{"role": "system", "content": "Be a concise tutor."},
                  {"role": "user", "content": "Open my Pearson course page and help me study."}]
        prepared, _ = policy.prepare_messages(source)
        self.assertIn("never request or enter passwords", prepared[0]["content"])
        self.assertIn("wait for explicit approval", prepared[0]["content"])
        self.assertEqual(source[0]["content"], "Be a concise tutor.")

        for name in ("Canvas", "McGraw Hill", "McGraw-Hill", "McGrawHill"):
            with self.subTest(site=name):
                prepared, _ = policy.prepare_messages([{"role": "user", "content": f"Study my {name} course."}])
                self.assertIn("course policies", prepared[0]["content"])

        prepared, _ = policy.prepare_messages([{"role": "user", "content": "Explain photosynthesis."}])
        self.assertNotIn("For Canvas, Pearson", prepared[0]["content"])

    def test_every_two_chunk_boundary_hides_complete_thinking_block(self):
        source = "Visible before. <think>private draft and tool speculation</think> Visible after."
        expected = "Visible before.  Visible after."
        for split in range(len(source) + 1):
            filter_ = policy.ThinkingFilter()
            result = filter_.feed(source[:split]) + filter_.feed(source[split:]) + filter_.finish()
            with self.subTest(split=split):
                self.assertEqual(result, expected)

    def test_single_character_chunks_handle_partial_open_and_close_tags(self):
        source = "A</think> B<think>draft text</think> C"
        filter_ = policy.ThinkingFilter()
        result = "".join(filter_.feed(char) for char in source) + filter_.finish()
        self.assertEqual(result, "A B C")

    def test_partial_prefix_is_removed_and_nested_blocks_do_not_leak(self):
        self.assertEqual(policy.strip_thinking("</think>Final answer"), "Final answer")
        self.assertEqual(policy.strip_thinking("Planning text without opener.</think>Final answer"), "Final answer")
        self.assertEqual(policy.strip_thinking("ok<think>outer<think>inner</think>outer tail</think>done"), "okdone")

    def test_unclosed_thinking_never_displays_and_has_bounded_memory(self):
        filter_ = policy.ThinkingFilter(max_think_chars=12)
        self.assertEqual(filter_.feed("User answer <think>secret draft "), "User answer ")
        self.assertEqual(filter_.feed("x" * 10_000), "")
        self.assertTrue(filter_.budget_exceeded)
        self.assertLessEqual(len(filter_._buffer), len(filter_.CLOSE) - 1)
        self.assertEqual(filter_.finish(), "")
        self.assertEqual(policy.strip_thinking("visible<think>unfinished"), "visible")

    def test_closed_thinking_can_be_followed_by_visible_final_answer(self):
        filter_ = policy.ThinkingFilter(max_think_chars=2)
        pieces = [filter_.feed("<think>"), filter_.feed("long private chain"),
                  filter_.feed("</think>"), filter_.feed("Answer")]
        self.assertTrue(filter_.budget_exceeded)
        self.assertEqual("".join(pieces) + filter_.finish(), "Answer")


if __name__ == "__main__":
    unittest.main()
