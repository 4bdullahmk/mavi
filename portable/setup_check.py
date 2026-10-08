#!/usr/bin/env python3
"""Secret-free, local-only Mavi setup doctor.

Usage from a source/release root:
  python portable/setup_check.py --json
  python portable/setup_check.py --json --chat-smoke --spreadsheet-smoke

The doctor never starts Mavi, reads workspace history/profile, accesses a
Discord token, downloads dependencies, or contacts a non-loopback service.
"""
from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

OLLAMA_ORIGIN = "http://127.0.0.1:11434"
MAX_API_BYTES = 2_000_000
MAX_MODEL_COUNT = 100
MAX_MODEL_NAME = 120
_SECRETISH_MODEL_NAME = re.compile(r"(?i)(?:\b(?:token|password|secret|credential|api[_ -]?key)\b|\bsk-[A-Za-z0-9_-]{12,}|\bgh[pousr]_[A-Za-z0-9]{20,})")
_DEPENDENCIES = {
    "pillow": "PIL",
    "pdf_read": "pypdf",
    "pdf_create": "reportlab",
    "docx": "docx",
    "xlsx": "openpyxl",
    "pptx": "pptx",
    "image_torch": "torch",
    "image_diffusers": "diffusers",
    "image_transformers": "transformers",
    "image_accelerate": "accelerate",
    "windows_gui": "pyautogui",
    "windows_window_list": "pygetwindow",
}


class _LoopbackOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Keep Ollama probes and smoke requests pinned to the local service."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.port not in (None, 11434)):
            raise ValueError("Local Ollama redirects are not allowed.")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _opener():
    # Ignore proxy environment variables: the request must stay on loopback.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _LoopbackOnlyRedirect())


def _local_json(path: str, payload: dict[str, Any] | None = None, *, timeout: float = 3.0) -> Any:
    if path not in {"/api/tags", "/api/chat"}:
        raise ValueError("Unsupported local setup-check endpoint.")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        OLLAMA_ORIGIN + path,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    with _opener().open(request, timeout=timeout) as response:
        raw = response.read(MAX_API_BYTES + 1)
    if len(raw) > MAX_API_BYTES:
        raise ValueError("Local Ollama response exceeded the safety limit.")
    return json.loads(raw.decode("utf-8"))


def _installed_models(timeout: float) -> tuple[bool, list[dict[str, Any]], str | None]:
    try:
        payload = _local_json("/api/tags", timeout=timeout)
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            return False, [], "invalid_response"
        models = []
        for item in payload["models"][:MAX_MODEL_COUNT]:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            name = "".join(ch for ch in item["name"][:MAX_MODEL_NAME] if ch.isprintable()).strip()
            if not name:
                continue
            if _SECRETISH_MODEL_NAME.search(name):
                name = "[redacted model name]"
            size = item.get("size")
            model = {"name": name}
            if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
                model["size_gb"] = round(size / 1024**3, 2)
            models.append(model)
        return True, models, None
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, UnicodeError, json.JSONDecodeError):
        # Do not include arbitrary exception text in a report.
        return False, [], "ollama_unavailable_or_invalid_response"


def _ram_gb() -> int | None:
    try:
        if os.name == "nt":
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in
                    ("total", "available", "page", "available_page", "virtual", "available_virtual", "extended")]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return round(status.total / 1024**3)
        elif sys.platform == "darwin":
            try:
                value = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.memsize"], capture_output=True,
                                       text=True, timeout=2, check=True, shell=False).stdout.strip()
                return round(int(value) / 1024**3)
            except (OSError, ValueError, subprocess.SubprocessError):
                # Sandboxed macOS may deny sysctl reads; sysconf often remains available.
                return round(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024**3)
        else:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            return round(pages * page_size / 1024**3)
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError, TypeError):
        return None
    return None


def _data_volume_free_gb() -> float | None:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Mavi"
    else:
        base = Path.home() / ".local" / "share" / "Mavi"
    # Only inspect filesystem metadata. Never create or read files in user data.
    for candidate in (base, *base.parents):
        try:
            if candidate.is_dir():
                return round(shutil.disk_usage(candidate).free / 1024**3, 1)
        except OSError:
            continue
    return None


def _gpu_facts() -> dict[str, Any]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {"nvidia_smi": False, "devices": [],
                "note": "No NVIDIA device was reported; Ollama may still use CPU or another supported backend."}
    try:
        result = subprocess.run(
            [executable, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=False, shell=False,
        )
        if result.returncode != 0:
            raise ValueError("query_failed")
        devices = []
        for line in result.stdout[:12_000].splitlines()[:8]:
            fields = [field.strip() for field in line.split(",", 1)]
            if len(fields) != 2:
                continue
            try:
                vram = float(fields[1])
            except ValueError:
                continue
            devices.append({"name": fields[0][:120], "vram_gb": round(vram / 1024, 1)})
        return {"nvidia_smi": True, "devices": devices,
                "note": "Hardware reporting alone does not establish image-model readiness."}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"nvidia_smi": True, "devices": [], "note": "NVIDIA device query failed; image readiness is unverified."}


def _dependency_facts() -> dict[str, bool]:
    result = {}
    for feature, module_name in _DEPENDENCIES.items():
        try:
            result[feature] = importlib.util.find_spec(module_name) is not None
        except (ImportError, ValueError, ModuleNotFoundError):
            result[feature] = False
    return result


def collect_report(*, timeout: float = 3.0, chat_smoke: bool = False,
                   spreadsheet_smoke: bool = False) -> dict[str, Any]:
    """Collect a non-mutating local setup report; optional smokes use temporary data."""
    models_available, models, model_error = _installed_models(timeout)
    dependencies = _dependency_facts()
    ollama_ready = models_available and bool(models)
    core_docs = all(dependencies[key] for key in ("pillow", "pdf_read", "pdf_create", "docx", "xlsx", "pptx"))
    report: dict[str, Any] = {
        "schema_version": 1,
        "overall": "ready" if ollama_ready and core_docs else "partial",
        "overall_scope": "Core chat and document tools only; optional image and Windows control readiness are reported separately.",
        "system": {
            "platform": platform.system(),
            "python": platform.python_version(),
            "ram_gb": _ram_gb(),
            "mavi_data_volume_free_gb": _data_volume_free_gb(),
            "gpu": _gpu_facts(),
        },
        "ollama": {"available": models_available, "models": models, "error": model_error},
        "dependencies": dependencies,
        "capabilities": {
            "chat": {"available": ollama_ready,
                     "status": "ready" if ollama_ready else "needs_setup",
                     "note": "Requires a running local Ollama service and at least one installed model."},
            "documents": {"available": core_docs, "status": "ready" if core_docs else "missing_optional_dependencies",
                          "formats": {"pdf": dependencies["pdf_read"] and dependencies["pdf_create"],
                                      "docx": dependencies["docx"], "xlsx": dependencies["xlsx"],
                                      "pptx": dependencies["pptx"]}},
            "images": {"status": "needs_runtime_validation",
                       "runtime_dependencies": all(dependencies[key] for key in
                           ("image_torch", "image_diffusers", "image_transformers", "image_accelerate", "pillow")),
                       "checkpoint": "not inspected", "note": "A CUDA-capable runtime, supported local checkpoint, and adequate free memory must also be verified on this device."},
            "windows_automation": {"status": "needs_windows_validation" if os.name == "nt" else "windows_only",
                                   "dependencies_present": dependencies["windows_gui"] and dependencies["windows_window_list"],
                                   "note": "This doctor never sends mouse, keyboard, browser, or desktop actions."},
        },
        "smoke_tests": {"chat": None, "spreadsheet": None},
    }
    if chat_smoke:
        report["smoke_tests"]["chat"] = _chat_smoke(models, timeout)
        if not report["smoke_tests"]["chat"]["passed"]:
            report["overall"] = "partial"
    if spreadsheet_smoke:
        report["smoke_tests"]["spreadsheet"] = _spreadsheet_smoke(dependencies)
        if not report["smoke_tests"]["spreadsheet"]["passed"]:
            report["overall"] = "partial"
    return report


def _chat_smoke(models: list[dict[str, Any]], timeout: float) -> dict[str, Any]:
    if not models:
        return {"passed": False, "status": "no_local_model", "duration_ms": 0}
    model = models[0]["name"]
    started = time.monotonic()
    try:
        payload = _local_json("/api/chat", {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with a brief greeting."}],
            "stream": False,
            "think": False,
            "options": {"num_ctx": 1024, "num_predict": 48},
        }, timeout=timeout)
        message = payload.get("message", {}) if isinstance(payload, dict) else {}
        raw = message.get("content", "") if isinstance(message, dict) else ""
        if not isinstance(raw, str) or len(raw) > 64_000:
            raise ValueError("invalid_or_oversized_reply")
        try:
            from model_policy import strip_thinking
            visible = strip_thinking(raw, max_think_chars=16_000).strip()
        except ImportError:
            visible = raw.strip()
        passed = bool(visible)
        return {"passed": passed, "status": "reply_received" if passed else "empty_or_thinking_only_reply",
                "duration_ms": round((time.monotonic() - started) * 1000)}
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, TypeError, json.JSONDecodeError):
        return {"passed": False, "status": "local_chat_failed_or_invalid_response",
                "duration_ms": round((time.monotonic() - started) * 1000)}


def _spreadsheet_smoke(dependencies: dict[str, bool]) -> dict[str, Any]:
    if not dependencies.get("xlsx"):
        return {"passed": False, "status": "openpyxl_missing"}
    spec = {
        "filename": "Mavi Setup Check.xlsx",
        "sheets": [{
            "name": "Check",
            "rows": [["Item", "Value"], ["Example", 2], ["Total", {"type": "formula", "value": "=SUM(B2:B2)"}]],
            "tables": [{"name": "SetupCheck", "range": "A1:B3"}],
            "freeze_panes": "A2",
        }],
    }
    try:
        # The helper requires a DATA/outputs-like destination. A disposable
        # directory ensures no user's Mavi files are touched.
        from spreadsheet_runtime import create_workbook
        with tempfile.TemporaryDirectory(prefix="mavi-setup-check-") as temporary:
            output = Path(temporary) / "outputs"
            output.mkdir()
            workbook_path = create_workbook(spec, output)
            if not workbook_path.is_file() or workbook_path.stat().st_size > 1_000_000:
                raise ValueError("invalid_workbook_file")
            import openpyxl
            book = openpyxl.load_workbook(workbook_path, data_only=False, read_only=False)
            try:
                sheet = book["Check"]
                passed = sheet["B3"].value == "=SUM(B2:B2)" and len(sheet.tables) == 1
            finally:
                book.close()
            return {"passed": passed, "status": "workbook_created_and_formula_validated" if passed else "workbook_validation_failed"}
    except Exception:
        # Intentionally avoid reporting exception text: it may include local paths.
        return {"passed": False, "status": "workbook_create_or_validation_failed"}


def _timeout_arg(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("timeout must be a finite number from 1 to 60 seconds") from None
    if not math.isfinite(number) or not 1 <= number <= 60:
        raise argparse.ArgumentTypeError("timeout must be from 1 to 60 seconds")
    return number


def _render_text(report: dict[str, Any]) -> str:
    system, ollama = report["system"], report["ollama"]
    models = ", ".join(item["name"] for item in ollama["models"]) or "none detected"
    lines = [
        f"Mavi setup: {report['overall']}",
        report["overall_scope"],
        f"System: {system['platform']} · Python {system['python']} · RAM {system['ram_gb'] or 'unknown'} GB · free disk {system['mavi_data_volume_free_gb'] if system['mavi_data_volume_free_gb'] is not None else 'unknown'} GB",
        f"GPU: {', '.join(device['name'] for device in system['gpu']['devices']) or 'not reported by NVIDIA tools'}",
        f"Ollama: {'available' if ollama['available'] else 'unavailable'} · models: {models}",
    ]
    for name, check in report["smoke_tests"].items():
        if check is not None:
            lines.append(f"{name.title()} smoke: {'passed' if check['passed'] else 'failed'} ({check['status']})")
    lines.append("Image capability remains unverified until a compatible local checkpoint and device memory are validated.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print a machine-readable JSON report.")
    parser.add_argument("--chat-smoke", action="store_true", help="Make one bounded local Ollama chat request.")
    parser.add_argument("--spreadsheet-smoke", action="store_true", help="Create and validate a temporary XLSX workbook.")
    parser.add_argument("--timeout", type=_timeout_arg, default=15.0,
                        help="Maximum seconds for each local Ollama request (1–60; default 15).")
    args = parser.parse_args(argv)
    try:
        report = collect_report(timeout=args.timeout, chat_smoke=args.chat_smoke,
                                spreadsheet_smoke=args.spreadsheet_smoke)
    except Exception:
        report = {"schema_version": 1, "overall": "partial", "error": "doctor_failed_without_details"}
        print(json.dumps(report) if args.json else "Mavi setup doctor could not complete; details were withheld.")
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True) if args.json else _render_text(report))
    if any(check is not None and not check.get("passed") for check in report["smoke_tests"].values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
