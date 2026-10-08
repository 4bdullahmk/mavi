import hashlib
import re
import threading
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from portable import tools_runtime


class ProposalReviewTests(unittest.TestCase):
    def test_empty_project_does_not_fall_back_to_working_directory(self):
        with self.assertRaisesRegex(ValueError, "Choose a project folder"):
            tools_runtime._code("change this", {"project_path": ""})

    def test_candidate_edit_requires_matching_hash(self):
        import portable.tools_runtime as runtime

        class Developer:
            @staticmethod
            def safe_path(root, name):
                path = root / name
                if not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError("outside candidate")
                return path

        with tempfile.TemporaryDirectory() as folder:
            candidate = Path(folder)
            source = candidate / "main.py"
            source.write_text("before = 1\n", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            edit = {"path": "main.py", "content": "after = 2\n", "sha256": digest}
            runtime._apply_candidate_edits(candidate, Developer, [edit])
            self.assertEqual(source.read_text(encoding="utf-8"), "after = 2\n")

            edit["sha256"] = digest
            with self.assertRaisesRegex(ValueError, "changed after its proposal"):
                runtime._apply_candidate_edits(candidate, Developer, [edit])

    def test_project_proposal_rejects_stale_source_before_writing(self):
        class Developer:
            @staticmethod
            def safe_path(root, name):
                return root / name

        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder).resolve()
            source = project / "main.py"
            source.write_text("newer = True\n", encoding="utf-8")
            edit = {"path": "main.py", "content": "proposed = True\n", "sha256": "stale"}
            with self.assertRaisesRegex(ValueError, "changed since"):
                tools_runtime._apply_project_edits(project, Developer, [edit])
            self.assertEqual(source.read_text(encoding="utf-8"), "newer = True\n")

    def test_developer_model_prompt_includes_schema(self):
        captured = {}

        class Developer:
            SCHEMA = {"type": "object", "required": ["action"]}
            MODEL = "local"

            def parse_answer(self, value):
                import json
                return json.loads(value)

            def run(self, _project, _task):
                self.ask([{"role": "system", "content": "coder instructions"}, {"role": "user", "content": "request"}])
                return {"summary": "No edit", "edits": [], "diff": ""}

        def call_model(_context, messages, model=None):
            captured["messages"] = messages
            return '{"action":"done"}'

        with tempfile.TemporaryDirectory() as folder:
            with patch.object(tools_runtime, "_module", return_value=Developer()), patch.object(tools_runtime, "_call_model", side_effect=call_model):
                result = tools_runtime._code("inspect", {"project_path": folder, "model": "qwen3:8b"})
        self.assertEqual(result, "No edit")
        system = captured["messages"][0]["content"]
        self.assertTrue(system.startswith("Return exactly one JSON object matching this schema"))
        self.assertIn('"required": ["action"]', system)

    def test_update_applies_only_to_isolated_candidate_and_compiles(self):
        class Developer:
            SCHEMA = {"type": "object", "required": ["action"]}
            MODEL = "local"

            @staticmethod
            def safe_path(root, name):
                target = root / name
                if not target.resolve().is_relative_to(root.resolve()):
                    raise ValueError("outside candidate")
                return target

            @staticmethod
            def run(candidate, _task):
                return {
                    "summary": "Candidate test edit",
                    "edits": [{"path": "candidate_probe.py", "content": "value = 42\n", "sha256": None}],
                    "diff": "+value = 42\n",
                }

        with tempfile.TemporaryDirectory() as folder:
            context = {"data_dir": folder, "model": "qwen3:8b", "call_model": lambda *_a, **_k: "{}"}
            with patch.object(tools_runtime, "_ollama_models", return_value=["qwen3:8b"]), patch.object(tools_runtime, "_module", return_value=Developer()):
                result = tools_runtime._update("Add a harmless source value", context)
            candidate_text = re.search(r"candidate: (.+)", result).group(1)
            candidate = Path(candidate_text)
            self.assertEqual((candidate / "candidate_probe.py").read_text(encoding="utf-8"), "value = 42\n")
            self.assertTrue((candidate.parent / "Mavi-source.patch").is_file())
            self.assertIn("tests were executed", result)

    def test_worker_fleet_uses_multiple_models_reuses_evidence_and_emits_neural_events(self):
        calls = []
        events = []
        simultaneous = threading.Barrier(2)

        def call_model(messages, model=None):
            calls.append((model, messages))
            role = messages[0]["content"]
            if "evidence analyst" in role or "independent planner" in role:
                simultaneous.wait(timeout=2)
            if "evidence analyst" in role:
                return "Finding from [E1]."
            if "independent planner" in role:
                return "Draft based on [E1]."
            return "Verified answer citing [E1]."

        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / "README.md").write_text("Mavi project evidence: use local setup.\n", encoding="utf-8")
            context = {"project_path": str(project), "model": "qwen3:8b", "call_model": call_model, "agent_event": events.append}
            with patch.object(tools_runtime, "_ollama_models", return_value=["qwen3:8b", "qwen3-coder:30b", "qwen3:4b"]), \
                 patch.object(tools_runtime, "_ollama_model_sizes", return_value={"qwen3:8b": 1024**3, "qwen3-coder:30b": 2 * 1024**3}), \
                 patch.object(tools_runtime, "_ollama_running_models", return_value={}), \
                 patch.object(tools_runtime, "_available_system_ram_bytes", return_value=32 * 1024**3):
                result_one = tools_runtime._worker_fleet("Summarize this project", [], context)
                result_two = tools_runtime._worker_fleet("Summarize this project", [], context)
        self.assertIn("[E1] README.md", result_one)
        self.assertIn("Read-cache hits: 0", result_one)
        self.assertIn("Read-cache hits: 1", result_two)
        self.assertEqual({model for model, _ in calls}, {"qwen3:8b", "qwen3-coder:30b"})
        self.assertIn("lead synthesizer qwen3-coder:30b", result_one)
        final_nodes = {}
        for event in events:
            self.assertLessEqual(len(event["summary"]), 280)
            self.assertNotIn("Mavi project evidence", event["summary"])
            final_nodes[event["agent_id"]] = event
        self.assertEqual(len(final_nodes), 8)
        self.assertTrue(any(node["parent_id"] is None and node["status"] == "completed" for node in final_nodes.values()))
        self.assertTrue(any(node["name"] == "Evidence analyst" and node["status"] == "completed" for node in final_nodes.values()))
        self.assertTrue(any(node["name"] == "Lead synthesizer" and node["model"] == "qwen3-coder:30b" for node in final_nodes.values()))

    def test_worker_lead_uses_largest_selected_model_with_preference_as_tiebreaker(self):
        models = ["qwen3:8b", "qwen3-coder:30b"]
        self.assertEqual(tools_runtime._fleet_lead_model(models, {models[0]: 1, models[1]: 2}, models[0]), models[1])
        self.assertEqual(tools_runtime._fleet_lead_model(models, {models[0]: 2, models[1]: 2}, models[1]), models[1])

    def test_worker_fleet_cancellation_marks_agents_stopped(self):
        cancelled = threading.Event()
        events = []

        def call_model(_messages, model=None):
            cancelled.set()
            return "Partial worker output."

        context = {"model": "qwen3:8b", "call_model": call_model, "cancelled": cancelled, "agent_event": events.append}
        with patch.object(tools_runtime, "_ollama_models", return_value=["qwen3:8b", "qwen3:4b"]), \
             patch.object(tools_runtime, "_ollama_model_sizes", return_value={}), \
             patch.object(tools_runtime, "_ollama_running_models", return_value={}), \
             patch.object(tools_runtime, "_available_system_ram_bytes", return_value=32 * 1024**3):
            with self.assertRaises(InterruptedError):
                tools_runtime._worker_fleet("Analyze this", [], context)
        self.assertTrue(any(event["name"] == "Local worker fleet" and event["status"] == "stopped" for event in events))

    def test_worker_fleet_capacity_requires_known_sizes_and_reserves_memory(self):
        names = ["qwen3:8b", "qwen3-coder:30b"]
        gib = 1024**3
        self.assertEqual(tools_runtime._fleet_parallel_workers(names, {}, 64 * gib)[0], 1)
        self.assertEqual(tools_runtime._fleet_parallel_workers(names, {name: 8 * gib for name in names}, 20 * gib)[0], 1)
        workers, reason = tools_runtime._fleet_parallel_workers(names, {name: 2 * gib for name in names}, 20 * gib)
        self.assertEqual(workers, 2)
        self.assertIn("concurrent", reason)


if __name__ == "__main__":
    unittest.main()
