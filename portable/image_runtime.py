"""Official local image pipelines with bounded inputs and honest progress.

Real generation on target Windows hardware remains a release gate.
"""
import ctypes
import gc
import importlib.util
import json
import os
import platform
import selectors
import subprocess
import sys
import time
import uuid
from pathlib import Path

MODELS = {"generate": "Qwen/Qwen-Image-2512", "edit": "Qwen/Qwen-Image-Edit-2511",
          "flux_generate": "black-forest-labs/FLUX.2-klein-4B"}
MAX_ATTACHMENTS = 6
MAX_IMAGES = 3
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_SIDE = 1024
MIN_FREE_VRAM_GIB = 8
RAM_MARGIN_GIB = 6
RAM_MARGIN_RATIO = 0.15
MAC_MODELS = {
    "generate": "AbstractFramework/qwen-image-2512-4bit",
    "edit": "AbstractFramework/qwen-image-edit-2511-4bit",
}


def _official_model(model_path: Path, mode: str) -> bool:
    try:
        marker = json.loads((model_path / "mavi-source.json").read_text(encoding="utf-8"))
        return marker.get("repository") == MODELS[mode] and (model_path / "model_index.json").is_file()
    except (OSError, ValueError, TypeError):
        return False


def capabilities(data_dir):
    if sys.platform == "darwin":
        return _mac_capabilities(data_dir)
    packages = all(importlib.util.find_spec(name) is not None
                   for name in ("torch", "diffusers", "transformers", "accelerate", "PIL"))
    root = Path(data_dir) / "models"
    installed = {
        "generate": _official_model(root / "qwen-image", "generate"),
        "edit": _official_model(root / "qwen-image-edit", "edit"),
        "flux_generate": _official_model(root / "flux-klein-4b", "flux_generate"),
    }
    ready = packages and any(installed.values())
    if not packages:
        reason = "Install the optional image runtime and a CUDA-compatible PyTorch build."
    elif not any(installed.values()):
        reason = "Install an official local Qwen image checkpoint. Create and edit checkpoints are installed separately."
    else:
        modes = []
        if installed["generate"]:
            modes.append("Qwen generation")
        if installed["edit"]:
            modes.append("Qwen editing")
        if installed["flux_generate"]:
            modes.append("FLUX.2 Klein 4B generation")
        reason = f"Local official checkpoint available for {', '.join(modes)}. Free system RAM and CUDA capacity are checked before loading."
    return {"available": ready, "reason": reason,
            "generate_available": packages and (installed["generate"] or installed["flux_generate"]),
            "qwen_generate_available": packages and installed["generate"],
            "flux_generate_available": packages and installed["flux_generate"],
            "edit_available": packages and installed["edit"]}


def _mac_runtime_candidates(data_dir: str | os.PathLike) -> list[Path]:
    """Find an existing per-user MLX-Gen environment without installing anything."""
    data = Path(data_dir).expanduser().resolve()
    roots = []
    configured = os.environ.get("MAVI_IMAGE_RUNTIME") or os.environ.get("MAVI_MLX_RUNTIME")
    if configured:
        roots.append(Path(configured).expanduser())
    app_support = data.parent
    roots.extend((app_support / "Runtime", app_support / "runtime", app_support / "recovered-runtime"))
    result = []
    seen = set()
    for root in roots:
        try:
            resolved = root.resolve(strict=True)
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        python = _mac_python(resolved)
        package_paths = (resolved / "venv" / "lib" / "python3.11" / "site-packages" / "mflux" / "cli" / "mlx_gen.py",
                         resolved / "lib" / "python3.11" / "site-packages" / "mflux" / "cli" / "mlx_gen.py")
        if python and any(package.is_file() for package in package_paths):
            result.append(resolved)
    return result


def _mac_python(runtime: Path) -> Path | None:
    for candidate in (runtime / "venv" / "bin" / "python3", runtime / "bin" / "python3"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _mac_snapshot(runtime: Path, mode: str) -> Path | None:
    """Accept only complete snapshots in the expected official, quantized HF cache."""
    repo = MAC_MODELS[mode]
    repo_dir = runtime / "hf-cache" / "hub" / ("models--" + repo.replace("/", "--"))
    cache_root = repo_dir.parent
    snapshots = repo_dir / "snapshots"
    try:
        candidates = sorted((item for item in snapshots.iterdir() if item.is_dir()), reverse=True)
    except OSError:
        return None
    for snapshot in candidates:
        try:
            real_snapshot = snapshot.resolve(strict=True)
            if not real_snapshot.is_relative_to(cache_root.resolve(strict=True)):
                continue
            complete = True
            total_weights = 0
            for section in ("transformer", "text_encoder", "vae"):
                directory = real_snapshot / section
                index_path = directory / "model.safetensors.index.json"
                index = json.loads(index_path.read_text(encoding="utf-8"))
                weights = index.get("weight_map")
                if not isinstance(weights, dict) or not weights:
                    complete = False
                    break
                for filename in set(weights.values()):
                    if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".safetensors"):
                        complete = False
                        break
                    weight = directory / filename
                    resolved_weight = weight.resolve(strict=True)
                    allowed_weight_roots = (real_snapshot, cache_root / "blobs")
                    if not weight.is_file() or not any(
                        resolved_weight.is_relative_to(allowed.resolve(strict=True)) for allowed in allowed_weight_roots
                    ):
                        complete = False
                        break
                    total_weights += weight.stat().st_size
                if not complete:
                    break
            tokenizer = real_snapshot / "tokenizer" / "tokenizer.json"
            if complete and tokenizer.is_file() and total_weights > 0:
                return real_snapshot
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return None


def _mac_runtime_status(data_dir: str | os.PathLike) -> tuple[Path | None, dict[str, Path | None]]:
    for runtime in _mac_runtime_candidates(data_dir):
        snapshots = {mode: _mac_snapshot(runtime, mode) for mode in MAC_MODELS}
        if any(snapshots.values()):
            return runtime, snapshots
    return None, {mode: None for mode in MAC_MODELS}


def _mac_capabilities(data_dir) -> dict[str, object]:
    if platform.machine().lower() not in ("arm64", "aarch64"):
        return {"available": False, "reason": "Local MLX image generation requires an Apple Silicon Mac.",
                "generate_available": False, "edit_available": False}
    runtime, snapshots = _mac_runtime_status(data_dir)
    generate = snapshots["generate"] is not None
    edit = snapshots["edit"] is not None
    if not runtime:
        reason = ("No installed Apple Silicon MLX image runtime or complete official Qwen Image checkpoint was found. "
                  "Image setup is optional and requires a separate large local runtime and model download.")
    else:
        modes = []
        if generate:
            modes.append("Qwen Image generation")
        if edit:
            modes.append("Qwen Image editing")
        reason = f"Local MLX runtime ready for {' and '.join(modes)}. Available RAM is checked before loading; no model is downloaded automatically."
        if not generate:
            reason += " Generation checkpoint is not installed."
        if not edit:
            reason += " Edit checkpoint is not installed."
    return {"available": bool(generate or edit), "reason": reason,
            "generate_available": generate, "qwen_generate_available": generate,
            "flux_generate_available": False, "edit_available": edit,
            "backend": "mlx" if runtime else None}


def _checkpoint(root: Path, mode: str, has_images: bool) -> tuple[str, Path]:
    """Prefer the smaller, four-step FLUX generator when installed; edits stay Qwen."""
    if has_images:
        return "qwen-edit", root / "models" / "qwen-image-edit"
    preference = os.environ.get("MAVI_IMAGE_BACKEND", "auto").strip().lower()
    flux_path = root / "models" / "flux-klein-4b"
    qwen_path = root / "models" / "qwen-image"
    flux_ready = _official_model(flux_path, "flux_generate")
    qwen_ready = _official_model(qwen_path, "generate")
    if preference not in ("auto", "flux", "qwen"):
        raise ValueError("MAVI_IMAGE_BACKEND must be auto, flux, or qwen.")
    if preference == "flux" and not flux_ready:
        raise ValueError("FLUX generation was selected, but its official local checkpoint is not installed.")
    if preference == "qwen" and not qwen_ready:
        raise ValueError("Qwen generation was selected, but its official local checkpoint is not installed.")
    if preference == "flux" or (preference == "auto" and flux_ready):
        return "flux", flux_path
    if qwen_ready:
        return "qwen", qwen_path
    raise ValueError("Install an official local image checkpoint with Setup-Models.py first. This workflow loads only its configured model folder.")


def _available_ram_bytes() -> int:
    """Return currently available physical memory without requiring an extra package."""
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        state = MEMORYSTATUSEX()
        state.dwLength = ctypes.sizeof(state)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            raise ValueError("Windows could not report available system memory; image loading was stopped.")
        return int(state.ullAvailPhys)
    try:
        return int(os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        raise ValueError("Available system memory could not be measured; image loading was stopped.") from None


def _weight_bytes(model_path: Path) -> int:
    total = 0
    for path in model_path.rglob("*"):
        if path.is_symlink():
            continue
        if path.is_file() and path.suffix.lower() in (".safetensors", ".bin", ".pt"):
            total += path.stat().st_size
    return total


def _check_model_memory(model_path: Path) -> tuple[int, int]:
    weight_bytes = _weight_bytes(model_path)
    if weight_bytes <= 0:
        raise ValueError("The official checkpoint has no readable weight files. Reinstall it before generating images.")
    margin = max(RAM_MARGIN_GIB * 1024**3, int(weight_bytes * RAM_MARGIN_RATIO))
    required = weight_bytes + margin
    available = _available_ram_bytes()
    if available < required:
        raise ValueError(
            "Image generation was stopped before model loading to protect system memory. "
            f"This checkpoint has about {weight_bytes / 1024**3:.1f} GiB of weights and the conservative preflight "
            f"requires about {required / 1024**3:.1f} GiB of currently available RAM; "
            f"the system reports {available / 1024**3:.1f} GiB free. Close other apps or use a higher-memory computer."
        )
    return weight_bytes, available


def _read_images(attachments, root: Path, cancelled, *, return_paths: bool = False):
    if len(attachments) > MAX_ATTACHMENTS:
        raise ValueError(f"Use at most {MAX_ATTACHMENTS} attachments.")
    root = root.resolve(strict=True)
    upload_root = root / "uploads"
    try:
        safe_upload_root = upload_root.resolve(strict=True)
    except OSError:
        safe_upload_root = upload_root.resolve()
    if not safe_upload_root.is_relative_to(root):
        raise ValueError("The upload folder must remain inside Mavi's data folder.")

    Image = None
    images = []
    for attachment in attachments:
        if cancelled.is_set():
            raise InterruptedError("Stopped")
        if "path" not in attachment:
            continue
        path = Path(attachment["path"]).resolve(strict=True)
        if not path.is_relative_to(safe_upload_root) or path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            continue
        if len(images) >= MAX_IMAGES:
            raise ValueError(f"Use at most {MAX_IMAGES} source images.")
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("Each reference image must be 20 MB or smaller.")
        if Image is None:
            from PIL import Image
        try:
            with Image.open(path) as image:
                if image.format not in ("PNG", "JPEG"):
                    raise ValueError("Image editing accepts PNG or JPEG files only.")
                if image.width < 1 or image.height < 1 or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("A reference image exceeds the 20-megapixel limit.")
                image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
                images.append(path if return_paths else image.convert("RGB").copy())
        except (OSError, Image.DecompressionBombError) as error:
            raise ValueError("A reference image could not be safely decoded.") from error
    return images


def _text_prompt(text: str) -> str:
    request = str(text)[:6000]
    guidance = (
        "Do not add unrequested text, lettering, symbols, labels, or watermarks. "
        "If visible text is requested, include only the requested wording, in the language explicitly specified; "
        "use English only when the request gives no language. Preserve the user's prompt and do not invent text."
    )
    return request + "\n\n" + guidance


class _MLXProgressReporter:
    """Forward only bounded numeric step counts from MLX-Gen JSONL events."""

    def __init__(self, progress, clock=time.monotonic, minimum_interval: float = 2.0):
        self.progress = progress
        self.clock = clock
        self.minimum_interval = minimum_interval
        self.first_step = None
        self.first_step_at = None
        self.last_step = 0
        self.last_update_at = 0.0

    def consume_line(self, line: str) -> bool:
        if not isinstance(line, str) or len(line) > 8192:
            return False
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            return False
        if not isinstance(event, dict) or event.get("type") != "runtime" or event.get("phase") not in ("denoise", "complete", "generated"):
            return False
        step, total = event.get("step"), event.get("total_steps")
        if type(step) is not int or type(total) is not int or not (0 < step <= total <= 250):
            return False
        now = self.clock()
        if step < self.last_step:
            return False
        self.last_step = step
        if self.first_step is None:
            self.first_step = step
            self.first_step_at = now
        final = step == total or event.get("phase") in ("complete", "generated")
        if not final and now - self.last_update_at < self.minimum_interval:
            return True
        message = f"Image step {step}/{total}"
        observed = step - self.first_step
        elapsed = now - self.first_step_at
        if observed >= 2 and elapsed > 0 and step < total:
            eta = max(0, round(elapsed / observed * (total - step)))
            message += f" · about {eta}s remaining"
        self.progress(message)
        self.last_update_at = now
        return True


def run(text, attachments, context):
    data_path = Path(context["data_dir"])
    if data_path.is_symlink():
        raise ValueError("Invalid Mavi data folder.")
    root = data_path.resolve(strict=True)
    progress = context["progress"]
    cancelled = context["cancelled"]
    if cancelled.is_set():
        raise InterruptedError("Stopped")

    images = _read_images(attachments, root, cancelled, return_paths=sys.platform == "darwin")
    mode = "edit" if images else "generate"
    if sys.platform == "darwin":
        return _run_mlx(text, images, mode, root, progress, cancelled)
    backend, model_path = _checkpoint(root, mode, bool(images))
    marker_mode = "edit" if backend == "qwen-edit" else "flux_generate" if backend == "flux" else "generate"
    if not _official_model(model_path, marker_mode):
        raise ValueError("Install the expected local official checkpoint with Setup-Models.py first. This workflow loads only its configured model folder.")
    weight_bytes, available_ram = _check_model_memory(model_path)

    try:
        import torch
        if backend == "flux":
            from diffusers import Flux2KleinPipeline
        else:
            from diffusers import QwenImagePipeline, QwenImageEditPlusPipeline
    except ImportError as error:
        raise ValueError("Install the optional image packages and a compatible CUDA PyTorch build first.") from error
    if not torch.cuda.is_available():
        raise ValueError("Image generation needs a supported NVIDIA GPU with CUDA PyTorch. No compatible CUDA device is available.")
    try:
        free_vram, total_vram = torch.cuda.mem_get_info()
    except Exception as error:
        raise ValueError("CUDA could not report free GPU memory; image generation was stopped before loading.") from error
    if free_vram < MIN_FREE_VRAM_GIB * 1024**3:
        raise ValueError(
            f"This candidate requires at least {MIN_FREE_VRAM_GIB} GiB free GPU memory as a conservative safety gate; "
            "passing it does not guarantee the model will fit. "
            f"CUDA reports {free_vram / 1024**3:.1f} GiB free of {total_vram / 1024**3:.1f} GiB. Close GPU apps and retry."
        )
    if hasattr(torch.cuda, "is_bf16_supported") and not torch.cuda.is_bf16_supported():
        raise ValueError("This CUDA device does not support the checkpoint's BF16 weights.")

    started = time.monotonic()
    progress(
        f"Loading local weights ({weight_bytes / 1024**3:.1f} GiB; {available_ram / 1024**3:.1f} GiB free RAM); "
        f"{'FLUX.2 Klein 4B' if backend == 'flux' else 'Qwen'}; "
        "CPU offload lowers GPU use but may be slow. Stop takes effect after loading."
    )
    if cancelled.is_set():
        raise InterruptedError("Stopped")
    pipe = None
    temporary = None
    try:
        if backend == "flux":
            pipeline_type = Flux2KleinPipeline
        else:
            pipeline_type = QwenImageEditPlusPipeline if images else QwenImagePipeline
        pipe = pipeline_type.from_pretrained(
            str(model_path), local_files_only=True, torch_dtype=torch.bfloat16
        )
        if backend == "flux":
            pipe.enable_model_cpu_offload()
        else:
            pipe.enable_sequential_cpu_offload()
        pipe.set_progress_bar_config(disable=True)
        if cancelled.is_set():
            raise InterruptedError("Stopped")

        steps = 4 if backend == "flux" else 24
        step_started = time.monotonic()

        def step_end(pipeline, step, timestep, values):
            if cancelled.is_set():
                raise InterruptedError("Stopped")
            done = step + 1
            elapsed = time.monotonic() - step_started
            remaining = round(elapsed / done * (steps - done))
            progress(f"Image step {done}/{steps} · approximately {remaining}s remaining")
            return values

        options = {"prompt": _text_prompt(text), "num_inference_steps": steps,
                   "callback_on_step_end": step_end, "num_images_per_prompt": 1}
        if backend == "flux":
            options["guidance_scale"] = 1.0
            options["height"] = options["width"] = 768
        else:
            options.update(negative_prompt=" ", true_cfg_scale=4.0)
        if images:
            options["image"] = images
            if backend == "qwen-edit":
                options["guidance_scale"] = 1.0
        elif backend == "qwen":
            options.update(width=768, height=768)
        if cancelled.is_set():
            raise InterruptedError("Stopped")
        with torch.inference_mode():
            result = pipe(**options).images[0]
        if cancelled.is_set():
            raise InterruptedError("Stopped")

        output = root / "outputs"
        output.mkdir(parents=True, exist_ok=True)
        if output.is_symlink() or not output.resolve().is_relative_to(root):
            raise ValueError("Output folder must remain inside Mavi's data folder.")
        if result.width < 1 or result.height < 1 or result.width * result.height > MAX_IMAGE_PIXELS:
            raise ValueError("The generated image exceeded the 20-megapixel output limit.")
        target = output / ("mavi-image-" + uuid.uuid4().hex[:16] + ".png")
        temporary = target.with_suffix(".png.tmp")
        result.save(temporary, format="PNG")
        if temporary.stat().st_size > 50 * 1024 * 1024:
            temporary.unlink(missing_ok=True)
            raise ValueError("The generated PNG exceeded the 50 MB output limit and was discarded.")
        if cancelled.is_set():
            temporary.unlink(missing_ok=True)
            raise InterruptedError("Stopped")
        os.replace(temporary, target)
        return f"Saved locally: {target}\nTime taken: {round(time.monotonic() - started, 1)} seconds."
    except (torch.cuda.OutOfMemoryError, MemoryError) as error:
        raise RuntimeError(
            "Image generation ran out of GPU or system memory. No image was saved. "
            "Close other GPU-heavy apps or use a computer with more available memory."
        ) from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        images.clear()
        if pipe is not None:
            del pipe
        gc.collect()
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass


def _run_mlx(text: str, images, mode: str, data_root: Path, progress, cancelled) -> str:
    if platform.machine().lower() not in ("arm64", "aarch64"):
        raise ValueError("Local MLX image generation requires an Apple Silicon Mac.")
    runtime, snapshots = _mac_runtime_status(data_root)
    snapshot = snapshots.get(mode)
    if runtime is None or snapshot is None:
        label = "editing" if mode == "edit" else "generation"
        raise ValueError(f"The local Apple Silicon image runtime or official Qwen checkpoint for {label} is not installed. Mavi did not download anything.")

    weight_bytes = sum(path.stat().st_size for path in snapshot.rglob("*.safetensors") if path.is_file())
    if weight_bytes <= 0:
        raise ValueError("The cached official image checkpoint has no readable weights. Generation was stopped.")
    available_ram = _available_ram_bytes()
    margin = max(RAM_MARGIN_GIB * 1024**3, int(weight_bytes * RAM_MARGIN_RATIO))
    required = weight_bytes + margin
    if available_ram < required:
        raise ValueError(
            "Image generation was stopped before loading to protect unified memory. "
            f"This checkpoint has about {weight_bytes / 1024**3:.1f} GiB of weights and conservatively needs "
            f"about {required / 1024**3:.1f} GiB of currently available memory; macOS reports "
            f"{available_ram / 1024**3:.1f} GiB free. Close other apps or retry later."
        )

    python = _mac_python(runtime)
    if python is None:
        raise ValueError("The local MLX image runtime Python environment is missing or not executable.")
    output = data_root / "outputs"
    output.mkdir(parents=True, exist_ok=True)
    if output.is_symlink() or not output.resolve().is_relative_to(data_root):
        raise ValueError("Output folder must remain inside Mavi's data folder.")
    target = output / ("mavi-image-" + uuid.uuid4().hex[:16] + ".png")
    temporary = target.with_name(target.stem + ".partial.png")
    prompt = _text_prompt(text)
    args = [str(python), "-m", "mflux.cli.mlx_gen", "generate", "--model", MAC_MODELS[mode],
            "--prompt", prompt, "--width", "768", "--height", "768", "--steps", "28",
            "--low-ram", "--replace", "--json-events", "--output", str(temporary)]
    if images:
        if len(images) == 1:
            args.extend(["--image", str(images[0])])
        else:
            args.append("--images")
            args.extend(str(path) for path in images)

    env = os.environ.copy()
    env["HF_HOME"] = str(runtime / "hf-cache")
    env["HF_HUB_CACHE"] = str(runtime / "hf-cache" / "hub")
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    progress(f"Loading the local MLX Qwen Image checkpoint ({weight_bytes / 1024**3:.1f} GiB weights; {available_ram / 1024**3:.1f} GiB memory free). No checkpoint download will occur.")
    if cancelled.is_set():
        raise InterruptedError("Stopped")

    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=env, close_fds=True)
    line_buffer = bytearray()
    reporter = _MLXProgressReporter(progress)
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    selector.register(process.stdout, selectors.EVENT_READ)
    started = time.monotonic()
    try:
        while process.poll() is None or selector.get_map():
            if cancelled.is_set():
                try:
                    process.terminate()
                except OSError:
                    pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                raise InterruptedError("Stopped")
            events = selector.select(timeout=0.25)
            for key, _ in events:
                chunk = os.read(key.fd, 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                line_buffer.extend(chunk)
                while b"\n" in line_buffer:
                    raw_line, _, remainder = line_buffer.partition(b"\n")
                    line_buffer = bytearray(remainder)
                    reporter.consume_line(raw_line.decode("utf-8", errors="ignore"))
                if len(line_buffer) > 8192:
                    line_buffer.clear()
            if process.poll() is not None and not events:
                # A final read will deliver EOF and clear the selector.
                continue
        status = process.wait()
    finally:
        selector.close()
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if process.stdout:
            process.stdout.close()
        if cancelled.is_set() or process.returncode not in (0, None):
            temporary.unlink(missing_ok=True)

    if cancelled.is_set():
        temporary.unlink(missing_ok=True)
        raise InterruptedError("Stopped")
    if status != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Local MLX image generation failed or did not produce an output. Check the installed runtime and local checkpoint, then try again.")
    if temporary.stat().st_size > 50 * 1024 * 1024:
        temporary.unlink(missing_ok=True)
        raise ValueError("The generated PNG exceeded the 50 MB limit and was discarded.")
    try:
        from PIL import Image
        with Image.open(temporary) as generated:
            if generated.format != "PNG" or generated.width < 1 or generated.height < 1 or generated.width * generated.height > MAX_IMAGE_PIXELS:
                raise ValueError("The local image runtime returned an unsupported or oversized image.")
            generated.verify()
    except (OSError, ModuleNotFoundError) as error:
        temporary.unlink(missing_ok=True)
        raise ValueError("The local image runtime did not return a readable PNG.") from error
    os.replace(temporary, target)
    return f"Saved locally: {target}\nTime taken: {round(time.monotonic() - started, 1)} seconds."
