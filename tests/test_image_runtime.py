import importlib.util
import json
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
        with mock.patch.object(image_runtime.importlib.util, "find_spec", return_value=object()):
            self.assertTrue(image_runtime.capabilities(self.root)["available"])
            (self.root / "models/qwen-image-edit/mavi-source.json").unlink()
            result = image_runtime.capabilities(self.root)
        self.assertTrue(result["available"])
        self.assertTrue(result["generate_available"])
        self.assertFalse(result["edit_available"])

    def test_rejects_unverified_checkpoint_before_model_import(self):
        (self.root / "models/qwen-image/mavi-source.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "official local image checkpoint"):
            image_runtime.run("a landscape", [], self.context())

    def test_memory_gate_prevents_loading_checkpoint(self):
        with mock.patch.object(image_runtime, "_available_ram_bytes", return_value=1024):
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
        with mock.patch.dict(sys.modules, modules), mock.patch.object(image_runtime, "_available_ram_bytes", return_value=64 * 1024**3):
            result = image_runtime.run("A blue landscape", [], self.context())
        self.assertIn("Saved locally:", result)
        generated = next((self.root / "outputs").glob("mavi-image-*.png"))
        self.assertTrue(generated.is_file())
        self.assertTrue(torch_module.cuda.empty_cache.called)
        self.assertIn("Loading local weights", self.progress[0])

    def test_auto_prefers_local_official_flux_for_generation(self):
        self._prepare_model("flux-klein-4b", "flux_generate")
        modules, _, _ = self._mock_runtime()
        with mock.patch.dict(sys.modules, modules), mock.patch.object(image_runtime, "_available_ram_bytes", return_value=64 * 1024**3):
            image_runtime.run("A blue landscape", [], self.context())
        self.assertIn("FLUX.2 Klein 4B", self.progress[0])

    def test_flux_selection_requires_official_local_checkpoint(self):
        import os
        with mock.patch.dict(os.environ, {"MAVI_IMAGE_BACKEND": "flux"}):
            with self.assertRaisesRegex(ValueError, "FLUX generation was selected"):
                image_runtime.run("A blue landscape", [], self.context())

    def test_cancellation_after_loading_prevents_sampling_and_saving(self):
        modules, _, _ = self._mock_runtime(on_load=self.cancelled.set)
        with mock.patch.dict(sys.modules, modules), mock.patch.object(image_runtime, "_available_ram_bytes", return_value=64 * 1024**3):
            with self.assertRaisesRegex(InterruptedError, "Stopped"):
                image_runtime.run("A blue landscape", [], self.context())
        self.assertFalse((self.root / "outputs").exists())


if __name__ == "__main__":
    unittest.main()
