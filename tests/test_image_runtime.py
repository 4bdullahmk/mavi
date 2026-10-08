import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
import image_runtime


class FakeImage:
    format = "PNG"
    width = 512
    height = 512

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def thumbnail(self, *_args, **_kwargs): pass
    def convert(self, _mode): return self
    def copy(self): return self


class ImageRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.cancelled = threading.Event()
        self.progress = []
        self._prepare_model("qwen-image", "generate")
        self._prepare_model("qwen-image-edit", "edit")

    def tearDown(self):
        self.tmp.cleanup()

    def _prepare_model(self, folder, mode):
        path = self.root / "models" / folder
        path.mkdir(parents=True, exist_ok=True)
        (path / "model_index.json").write_text("{}", encoding="utf-8")
        (path / "mavi-source.json").write_text(json.dumps({"repository": image_runtime.MODELS[mode]}), encoding="utf-8")
        (path / "weights.safetensors").write_bytes(b"x" * 128)

    def context(self):
        return {"data_dir": self.root, "cancelled": self.cancelled, "progress": self.progress.append}

    def _mock_runtime(self, on_load=None):
        image_module = types.ModuleType("PIL.Image")
        image_module.open = mock.Mock(return_value=FakeImage())
        image_module.Resampling = types.SimpleNamespace(LANCZOS=0)
        image_module.DecompressionBombError = type("DecompressionBombError", (Exception,), {})
        image_file_module = types.ModuleType("PIL.ImageFile")
        pil_module = types.ModuleType("PIL")
        pil_module.Image = image_module
        pil_module.ImageFile = image_file_module

        class FakeOutput:
            width = 512
            height = 512

            def save(self, path, format=None):
                pathlib.Path(path).write_bytes(b"png-fixture")

        class FakePipeline:
            @classmethod
            def from_pretrained(cls, path, **kwargs):
                self.pipeline_arguments = (path, kwargs)
                if on_load:
                    on_load()
                return cls()

            def enable_sequential_cpu_offload(self): self.offload_enabled = True
            def enable_model_cpu_offload(self): self.offload_enabled = True
            def set_progress_bar_config(self, **kwargs): self.progress_config = kwargs
            def __call__(self, **kwargs):
                self.inference_arguments = kwargs
                callback = kwargs["callback_on_step_end"]
                callback(self, 0, None, {})
                return types.SimpleNamespace(images=[FakeOutput()])

        torch_module = types.ModuleType("torch")
        torch_module.bfloat16 = object()
        torch_module.cuda = types.SimpleNamespace(
            is_available=lambda: True,
            mem_get_info=lambda: (12 * 1024**3, 16 * 1024**3),
            is_bf16_supported=lambda: True,
            empty_cache=mock.Mock(),
            OutOfMemoryError=type("OutOfMemoryError", (RuntimeError,), {}),
        )
        class InferenceMode:
            def __enter__(self): pass
            def __exit__(self, *_): return False
        torch_module.inference_mode = InferenceMode

        diffusers_module = types.ModuleType("diffusers")
        diffusers_module.QwenImagePipeline = FakePipeline
        diffusers_module.QwenImageEditPlusPipeline = FakePipeline
        diffusers_module.Flux2KleinPipeline = FakePipeline
        modules = {"torch": torch_module, "PIL": pil_module, "PIL.Image": image_module,
                   "PIL.ImageFile": image_file_module, "diffusers": diffusers_module}
        return modules, image_module, torch_module

    def test_capability_requires_both_expected_local_models_and_dependencies(self):
        with mock.patch.object(image_runtime.sys, "platform", "win32"), \
             mock.patch.object(image_runtime.importlib.util, "find_spec", return_value=object()):
            self.assertTrue(image_runtime.capabilities(self.root)["available"])
            (self.root / "models/qwen-image-edit/mavi-source.json").unlink()
            result = image_runtime.capabilities(self.root)
        self.assertTrue(result["available"])
        self.assertTrue(result["generate_available"])
        self.assertFalse(result["edit_available"])

    def _prepare_mlx_runtime(self, root, include_edit=True):
        runtime = root / "recovered-runtime"
        python = runtime / "venv/bin/python3"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        python.chmod(0o755)
        module = runtime / "venv/lib/python3.11/site-packages/mflux/cli/mlx_gen.py"
        module.parent.mkdir(parents=True)
        module.write_text("", encoding="utf-8")
        for mode, repo in image_runtime.MAC_MODELS.items():
            if mode == "edit" and not include_edit:
                continue
            snapshot = runtime / "hf-cache/hub" / ("models--" + repo.replace("/", "--")) / "snapshots/test-snapshot"
            for section in ("transformer", "text_encoder", "vae"):
                folder = snapshot / section
                folder.mkdir(parents=True, exist_ok=True)
                weight = "part.safetensors"
                (folder / weight).write_bytes(b"weights")
                (folder / "model.safetensors.index.json").write_text(
                    json.dumps({"weight_map": {"fixture.weight": weight}}), encoding="utf-8")
            tokenizer = snapshot / "tokenizer/tokenizer.json"
            tokenizer.parent.mkdir()
            tokenizer.write_text("{}", encoding="utf-8")
        return runtime

    def test_macos_capabilities_use_complete_local_mlx_models_without_cuda(self):
        support = self.root / "Application Support/Mavi"
        data = support / "data"
        data.mkdir(parents=True)
        self._prepare_mlx_runtime(support)
        with mock.patch.object(image_runtime.sys, "platform", "darwin"), \
             mock.patch.object(image_runtime.platform, "machine", return_value="arm64"):
            result = image_runtime.capabilities(data)
        self.assertTrue(result["available"])
        self.assertEqual(result["backend"], "mlx")
        self.assertTrue(result["generate_available"])
        self.assertTrue(result["edit_available"])

    def test_macos_capability_does_not_report_cuda_setup_instructions(self):
        data = self.root / "Application Support/Mavi/data"
        data.mkdir(parents=True)
        with mock.patch.object(image_runtime.sys, "platform", "darwin"), \
             mock.patch.object(image_runtime.platform, "machine", return_value="arm64"):
            result = image_runtime.capabilities(data)
        self.assertFalse(result["available"])
        self.assertNotIn("CUDA", result["reason"])
        self.assertIn("Apple Silicon MLX", result["reason"])

    def test_mlx_progress_parses_only_numeric_step_events_and_measured_eta(self):
        now = [10.0]
        messages = []
        reporter = image_runtime._MLXProgressReporter(messages.append, clock=lambda: now[0], minimum_interval=2.0)
        def event(step, phase="denoise", **extra):
            return json.dumps({"type": "runtime", "phase": phase, "step": step, "total_steps": 4,
                               "task": "private prompt must not appear", **extra})
        self.assertTrue(reporter.consume_line(event(1)))
        self.assertEqual(messages, ["Image step 1/4"])
        now[0] = 11.0
        reporter.consume_line(event(2))
        self.assertEqual(len(messages), 1)
        now[0] = 12.0
        reporter.consume_line(event(3, phase="denoise", error="do not echo this text"))
        self.assertEqual(messages[-1], "Image step 3/4 · about 1s remaining")
        self.assertNotIn("private", " ".join(messages))
        self.assertNotIn("echo", " ".join(messages))
        now[0] = 12.1
        reporter.consume_line(event(4, phase="complete"))
        self.assertEqual(messages[-1], "Image step 4/4")
        self.assertFalse(reporter.consume_line("not json"))
        self.assertFalse(reporter.consume_line(event(3)))

    def test_mlx_progress_rejects_invalid_or_unbounded_counts(self):
        messages = []
        reporter = image_runtime._MLXProgressReporter(messages.append, clock=lambda: 10.0)
        for step, total in ((0, 28), (29, 28), (1, 0), (1, 1000000), (True, 28)):
            line = json.dumps({"type": "runtime", "phase": "denoise", "step": step, "total_steps": total})
            self.assertFalse(reporter.consume_line(line))
        self.assertFalse(reporter.consume_line(json.dumps({"type": "other", "phase": "denoise", "step": 1, "total_steps": 28})))
        self.assertEqual(messages, [])

    def test_macos_run_routes_to_local_mlx_backend(self):
        with mock.patch.object(image_runtime.sys, "platform", "darwin"), \
             mock.patch.object(image_runtime, "_run_mlx", return_value="Saved locally") as run_mlx:
            result = image_runtime.run("A blue landscape", [], self.context())
        self.assertEqual(result, "Saved locally")
        self.assertEqual(run_mlx.call_args.args[2], "generate")

    def test_macos_generation_uses_local_cli_offline_and_saves_into_data(self):
        support = self.root / "Application Support/Mavi"
        data = support / "data"
        data.mkdir(parents=True)
        runtime = self._prepare_mlx_runtime(support, include_edit=False)
        python = runtime / "venv/bin/python3"
        marker = self.root / "child-env.txt"
        python.write_text(
            "#!/bin/sh\nprintf '%s %s' \"$HF_HUB_OFFLINE\" \"$TRANSFORMERS_OFFLINE\" > \"$MAVI_TEST_MARKER\"\n"
            "while [ $# -gt 0 ]; do if [ \"$1\" = '--output' ]; then shift; printf 'png' > \"$1\"; fi; shift; done\n"
            "echo '{\"type\":\"runtime\",\"phase\":\"denoise\",\"step\":1,\"total_steps\":28}'\n", encoding="utf-8")
        python.chmod(0o755)

        class Generated:
            format = "PNG"
            width = 512
            height = 512
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def verify(self): pass

        image_module = types.ModuleType("PIL.Image")
        image_module.open = mock.Mock(return_value=Generated())
        pil_module = types.ModuleType("PIL")
        pil_module.Image = image_module
        with mock.patch.object(image_runtime.sys, "platform", "darwin"), \
             mock.patch.object(image_runtime.platform, "machine", return_value="arm64"), \
             mock.patch.object(image_runtime, "_available_ram_bytes", return_value=64 * 1024**3), \
             mock.patch.dict(os.environ, {"MAVI_TEST_MARKER": str(marker)}), \
             mock.patch.dict(sys.modules, {"PIL": pil_module, "PIL.Image": image_module}):
            result = image_runtime.run("A blue landscape", [], {"data_dir": data, "cancelled": self.cancelled, "progress": self.progress.append})
        self.assertIn("Saved locally:", result)
        self.assertEqual(marker.read_text(encoding="utf-8"), "1 1")
        self.assertEqual(len(list((data / "outputs").glob("mavi-image-*.png"))), 1)
        self.assertTrue(any(message == "Image step 1/28" for message in self.progress))

    def test_macos_edit_forwards_only_validated_uploaded_reference_paths(self):
        support = self.root / "Application Support/Mavi"
        data = support / "data"
        upload = data / "uploads"
        upload.mkdir(parents=True)
        inputs = [upload / "reference-1.png", upload / "reference-2.png"]
        for path in inputs:
            path.write_bytes(b"fixture")
        runtime = self._prepare_mlx_runtime(support)
        python = runtime / "venv/bin/python3"
        args_marker = self.root / "child-args.txt"
        env_marker = self.root / "child-env.txt"
        python.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$MAVI_TEST_ARGS\"\n"
            "printf '%s %s' \"$HF_HUB_OFFLINE\" \"$TRANSFORMERS_OFFLINE\" > \"$MAVI_TEST_MARKER\"\n"
            "while [ $# -gt 0 ]; do if [ \"$1\" = '--output' ]; then shift; printf 'png' > \"$1\"; fi; shift; done\n"
            "echo 'local edit complete'\n", encoding="utf-8")
        python.chmod(0o755)

        class SourceImage(FakeImage):
            def thumbnail(self, *_args, **_kwargs): pass

        class Generated:
            format = "PNG"
            width = 512
            height = 512
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def verify(self): pass

        image_module = types.ModuleType("PIL.Image")
        image_module.open = mock.Mock(side_effect=[SourceImage(), SourceImage(), Generated()])
        image_module.Resampling = types.SimpleNamespace(LANCZOS=0)
        pil_module = types.ModuleType("PIL")
        pil_module.Image = image_module
        with mock.patch.object(image_runtime.sys, "platform", "darwin"), \
             mock.patch.object(image_runtime.platform, "machine", return_value="arm64"), \
             mock.patch.object(image_runtime, "_available_ram_bytes", return_value=64 * 1024**3), \
             mock.patch.dict(os.environ, {"MAVI_TEST_MARKER": str(env_marker), "MAVI_TEST_ARGS": str(args_marker)}), \
             mock.patch.dict(sys.modules, {"PIL": pil_module, "PIL.Image": image_module}):
            image_runtime.run("Edit these references", [{"path": path} for path in inputs],
                              {"data_dir": data, "cancelled": self.cancelled, "progress": self.progress.append})
        args = args_marker.read_text(encoding="utf-8").splitlines()
        self.assertIn("--images", args)
        self.assertIn(str(inputs[0].resolve()), args)
        self.assertIn(str(inputs[1].resolve()), args)
        self.assertEqual(env_marker.read_text(encoding="utf-8"), "1 1")

    def test_macos_checkpoint_rejects_cache_symlinks_outside_hf_root(self):
        support = self.root / "Application Support/Mavi"
        data = support / "data"
        data.mkdir(parents=True)
        runtime = self._prepare_mlx_runtime(support, include_edit=False)
        snapshot = runtime / "hf-cache/hub/models--AbstractFramework--qwen-image-2512-4bit/snapshots/test-snapshot"
        external = self.root / "external-weight.safetensors"
        external.write_bytes(b"outside")
        weight = snapshot / "transformer/part.safetensors"
        weight.unlink()
        weight.symlink_to(external)
        with mock.patch.object(image_runtime.sys, "platform", "darwin"), \
             mock.patch.object(image_runtime.platform, "machine", return_value="arm64"):
            result = image_runtime.capabilities(data)
        self.assertFalse(result["generate_available"])

    def test_rejects_unverified_checkpoint_before_model_import(self):
        (self.root / "models/qwen-image/mavi-source.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(image_runtime.sys, "platform", "win32"), \
             self.assertRaisesRegex(ValueError, "official local image checkpoint"):
            image_runtime.run("a landscape", [], self.context())

    def test_memory_gate_prevents_loading_checkpoint(self):
        with mock.patch.object(image_runtime.sys, "platform", "win32"), \
             mock.patch.object(image_runtime, "_available_ram_bytes", return_value=1024):
            with self.assertRaisesRegex(ValueError, "protect system memory"):
                image_runtime.run("a landscape", [], self.context())

    def test_rejects_too_many_attachments_before_decoding(self):
        upload = self.root / "uploads"
        upload.mkdir()
        attachments = []
        for index in range(7):
            path = upload / f"{index}.png"
            path.write_bytes(b"fixture")
            attachments.append({"path": path})
        modules, image_module, _ = self._mock_runtime()
        with mock.patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(ValueError, "at most 6"):
                image_runtime._read_images(attachments, self.root, self.cancelled)
        image_module.open.assert_not_called()

    def test_fourth_image_is_rejected_before_decode(self):
        upload = self.root / "uploads"
        upload.mkdir()
        attachments = []
        for index in range(4):
            path = upload / f"{index}.png"
            path.write_bytes(b"fixture")
            attachments.append({"path": path})
        modules, image_module, _ = self._mock_runtime()
        with mock.patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(ValueError, "at most 3 source images"):
                image_runtime._read_images(attachments, self.root, self.cancelled)
        self.assertEqual(image_module.open.call_count, 3)

    def test_generation_is_local_offloaded_and_prompt_discourages_unrequested_text(self):
        modules, _, torch_module = self._mock_runtime()
        with mock.patch.object(image_runtime.sys, "platform", "win32"), mock.patch.dict(sys.modules, modules), mock.patch.object(image_runtime, "_available_ram_bytes", return_value=64 * 1024**3):
            result = image_runtime.run("A blue landscape", [], self.context())
        self.assertIn("Saved locally:", result)
        generated = next((self.root / "outputs").glob("mavi-image-*.png"))
        self.assertTrue(generated.is_file())
        self.assertTrue(torch_module.cuda.empty_cache.called)
        self.assertIn("Loading local weights", self.progress[0])

    def test_auto_prefers_local_official_flux_for_generation(self):
        self._prepare_model("flux-klein-4b", "flux_generate")
        modules, _, _ = self._mock_runtime()
        with mock.patch.object(image_runtime.sys, "platform", "win32"), mock.patch.dict(sys.modules, modules), mock.patch.object(image_runtime, "_available_ram_bytes", return_value=64 * 1024**3):
            image_runtime.run("A blue landscape", [], self.context())
        self.assertIn("FLUX.2 Klein 4B", self.progress[0])

    def test_flux_selection_requires_official_local_checkpoint(self):
        import os
        with mock.patch.object(image_runtime.sys, "platform", "win32"), mock.patch.dict(os.environ, {"MAVI_IMAGE_BACKEND": "flux"}):
            with self.assertRaisesRegex(ValueError, "FLUX generation was selected"):
                image_runtime.run("A blue landscape", [], self.context())

    def test_cancellation_after_loading_prevents_sampling_and_saving(self):
        modules, _, _ = self._mock_runtime(on_load=self.cancelled.set)
        with mock.patch.object(image_runtime.sys, "platform", "win32"), mock.patch.dict(sys.modules, modules), mock.patch.object(image_runtime, "_available_ram_bytes", return_value=64 * 1024**3):
            with self.assertRaisesRegex(InterruptedError, "Stopped"):
                image_runtime.run("A blue landscape", [], self.context())
        self.assertFalse((self.root / "outputs").exists())


if __name__ == "__main__":
    unittest.main()
