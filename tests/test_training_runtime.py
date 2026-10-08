import contextlib
import importlib.util
import io
import json
import sys
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("training_runtime", ROOT / "portable" / "training_runtime.py")
training_runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = training_runtime
SPEC.loader.exec_module(training_runtime)


class TrainingRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.dataset = self.root / "examples.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rows):
        self.dataset.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    def test_validate_prompt_completion_without_printing_examples(self):
        self.write([{"prompt": "Question text", "completion": "Answer text"}])
        examples = training_runtime.validate_dataset(self.dataset)
        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0].prompt, "Question text")

    def test_validate_two_message_schema(self):
        self.write([{"messages": [{"role": "user", "content": "Q"},
                                  {"role": "assistant", "content": "A"}]}])
        self.assertEqual(training_runtime.validate_dataset(self.dataset)[0].completion, "A")

    def test_reject_unknown_fields_to_prevent_secret_metadata(self):
        self.write([{"prompt": "Q", "completion": "A", "api_key": "hidden"}])
        with self.assertRaisesRegex(training_runtime.DatasetError, "prompt/completion"):
            training_runtime.validate_dataset(self.dataset)

    def test_reject_possible_credentials_without_echoing_value(self):
        secret = "ghp_" + "A" * 32
        self.write([{"prompt": "Help", "completion": f"Use this: {secret}"}])
        with self.assertRaisesRegex(training_runtime.DatasetError, "possible credential") as error:
            training_runtime.validate_dataset(self.dataset)
        self.assertNotIn(secret, str(error.exception))

    def test_reject_invalid_json_and_empty_fields(self):
        self.dataset.write_text('{"prompt":\n', encoding="utf-8")
        with self.assertRaisesRegex(training_runtime.DatasetError, "invalid JSON"):
            training_runtime.validate_dataset(self.dataset)
        self.write([{"prompt": " ", "completion": "A"}])
        with self.assertRaisesRegex(training_runtime.DatasetError, "non-empty"):
            training_runtime.validate_dataset(self.dataset)

    def test_reject_non_jsonl_and_oversize_text(self):
        self.dataset = self.root / "examples.txt"
        self.dataset.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(training_runtime.DatasetError, "\.jsonl"):
            training_runtime.validate_dataset(self.dataset)
        self.dataset = self.root / "examples.jsonl"
        self.write([{"prompt": "x" * (training_runtime.MAX_TEXT_CHARS + 1), "completion": "A"}])
        with self.assertRaisesRegex(training_runtime.DatasetError, "character limit"):
            training_runtime.validate_dataset(self.dataset)

    def test_safe_output_name(self):
        self.assertEqual(training_runtime.safe_output_name("support-v1"), "support-v1")
        for value in ("../escape", "", ".", "contains spaces", "x" * 65):
            with self.subTest(value=value), self.assertRaises(ValueError):
                training_runtime.safe_output_name(value)

    def test_cuda_capacity_is_conservative(self):
        self.assertFalse(training_runtime.check_cuda_capacity({"available": False})[0])
        self.assertFalse(training_runtime.check_cuda_capacity({"available": True, "total_vram_gb": 12, "free_vram_gb": 5})[0])
        self.assertTrue(training_runtime.check_cuda_capacity({"available": True, "total_vram_gb": 8, "free_vram_gb": 6})[0])

    def test_validate_cli_is_offline_and_does_not_echo_dataset_contents(self):
        self.write([{"prompt": "PRIVATE_PROMPT", "completion": "PRIVATE_ANSWER"}])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = training_runtime.main(["validate", "--dataset", str(self.dataset)])
        self.assertEqual(result, 0)
        self.assertNotIn("PRIVATE_PROMPT", output.getvalue())
        self.assertNotIn("PRIVATE_ANSWER", output.getvalue())
        self.assertIn("no model, GPU or output files accessed", output.getvalue())

    def test_prepare_does_not_need_training_dependencies_or_create_output(self):
        self.write([{"prompt": "Q", "completion": "A"}])
        with mock.patch.object(training_runtime, "dependency_status", return_value={
            "torch": False, "transformers": False, "peft": False, "accelerate": False
        }), mock.patch.object(training_runtime, "inspect_cuda", return_value={"available": False, "reason": "missing"}), \
             mock.patch.object(training_runtime, "model_cached_locally", return_value=False), \
             mock.patch.object(training_runtime, "data_root", return_value=self.root / "mavi-data"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = training_runtime.main(["prepare", "--dataset", str(self.dataset), "--name", "dry-run"])
        self.assertEqual(result, 0)
        self.assertIn("dry run", output.getvalue())
        self.assertFalse((self.root / "mavi-data").exists())

    def test_train_requires_explicit_confirmation(self):
        self.write([{"prompt": "Q", "completion": "A"}])
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            result = training_runtime.main(["train", "--dataset", str(self.dataset), "--name", "candidate"])
        self.assertEqual(result, 2)
        self.assertIn("--confirm-local-training", error.getvalue())


if __name__ == "__main__":
    unittest.main()
