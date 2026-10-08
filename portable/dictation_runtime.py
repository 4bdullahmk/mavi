"""Optional offline WAV transcription using an explicitly installed local model."""
from __future__ import annotations

import base64
import importlib.util
import os
import tempfile
import wave
from pathlib import Path
from typing import Any

MAX_WAV_BYTES = 120 * 1024 * 1024
MAX_DURATION_SECONDS = 30 * 60


def capability(data_dir: str | os.PathLike | None = None) -> dict[str, Any]:
    if importlib.util.find_spec("faster_whisper") is None:
        return {"available": False, "reason": "Offline dictation needs the optional faster-whisper package and a local CTranslate2 Whisper model. Mavi does not download models automatically."}
    if data_dir is None:
        return {"available": False, "reason": "Choose Mavi's local data folder before checking the offline Whisper model."}
    model_dir = Path(data_dir) / "models" / "whisper"
    if model_dir.is_symlink() or not model_dir.is_dir() or not (model_dir / "model.bin").is_file():
        return {"available": False, "reason": f"Place a compatible local CTranslate2 Whisper model in {model_dir}. No network download is attempted."}
    return {"available": True, "reason": "Offline dictation is ready with a local Whisper model."}


def _data_dir(context: dict[str, Any]) -> Path:
    value = context.get("data_dir")
    if not isinstance(value, (str, os.PathLike)):
        raise ValueError("Mavi's local data folder is not configured.")
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError("Mavi's local data folder cannot be a symbolic link.")
    root = raw.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cancelled(context: dict[str, Any]) -> bool:
    event = context.get("cancelled")
    return bool(event and callable(getattr(event, "is_set", None)) and event.is_set())


def _check_cancelled(context: dict[str, Any]) -> None:
    if _cancelled(context):
        raise InterruptedError("Dictation was stopped by the user.")


def _upload_path(item: Any, root: Path) -> Path | None:
    raw = item.get("path") if isinstance(item, dict) else None
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if candidate.is_symlink():
        raise ValueError("Attached audio cannot be a symbolic link.")
    resolved = candidate.resolve(strict=True)
    permitted = [root / "uploads", root / "attachments"]
    if any(folder.is_symlink() for folder in permitted):
        raise ValueError("Audio upload folders cannot be symbolic links.")
    if not any(folder.exists() and resolved.is_relative_to(folder.resolve()) for folder in permitted):
        raise ValueError("Audio must be staged in Mavi's local uploads or attachments folder.")
    if not resolved.is_file() or resolved.suffix.lower() != ".wav":
        raise ValueError("Attach a WAV recording.")
    return resolved


def _inline_wav(item: Any, root: Path) -> Path | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name", ""))
    encoded = item.get("base64")
    if Path(name).suffix.lower() != ".wav" or not isinstance(encoded, str):
        return None
    if len(encoded) > (MAX_WAV_BYTES * 4 // 3 + 16):
        raise ValueError("WAV attachment exceeds the 120 MB limit.")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("WAV attachment data is malformed.") from error
    if not payload or len(payload) > MAX_WAV_BYTES:
        raise ValueError("WAV attachment exceeds the 120 MB limit.")
    folder = root / "uploads"
    if folder.is_symlink():
        raise ValueError("Mavi's uploads folder cannot be a symbolic link.")
    folder.mkdir(parents=True, exist_ok=True)
    fd, name_on_disk = tempfile.mkstemp(prefix=".mavi-dictation-", suffix=".wav", dir=folder)
    os.close(fd)
    path = Path(name_on_disk)
    path.write_bytes(payload)
    return path


def _validated_wav(path: Path) -> None:
    if path.stat().st_size > MAX_WAV_BYTES:
        raise ValueError("WAV recording exceeds the 120 MB limit.")
    try:
        with wave.open(str(path), "rb") as audio:
            channels = audio.getnchannels()
            rate = audio.getframerate()
            frames = audio.getnframes()
            width = audio.getsampwidth()
            compression = audio.getcomptype()
            duration = frames / max(rate, 1)
    except (wave.Error, OSError) as error:
        raise ValueError("Choose a readable PCM WAV recording.") from error
    if compression != "NONE" or channels not in (1, 2) or not 8_000 <= rate <= 192_000 or width not in (1, 2, 3, 4):
        raise ValueError("WAV must be mono or stereo PCM audio at 8–192 kHz.")
    if duration <= 0 or duration > MAX_DURATION_SECONDS:
        raise ValueError("WAV recording must be from 1 second to 30 minutes.")


def run(attachments: Any, context: dict[str, Any]) -> str:
    if not isinstance(context, dict):
        raise ValueError("Dictation context is missing.")
    root = _data_dir(context)
    model_dir = root / "models" / "whisper"
    if importlib.util.find_spec("faster_whisper") is None:
        raise RuntimeError("Offline dictation is not ready. Install faster-whisper in Mavi's Python environment, then restart the server.")
    if model_dir.is_symlink() or not model_dir.is_dir() or not (model_dir / "model.bin").is_file():
        raise RuntimeError(f"Offline dictation is not ready. Place a compatible CTranslate2 model at {model_dir}. Mavi will not download model weights.")
    items = attachments if isinstance(attachments, (list, tuple)) else [attachments] if attachments else []
    if not items:
        raise ValueError("Attach a WAV recording to transcribe.")
    audio_path = None
    temporary = None
    for item in items[:6]:
        audio_path = _upload_path(item, root)
        if audio_path is None:
            temporary = _inline_wav(item, root)
            audio_path = temporary
        if audio_path is not None:
            break
    if audio_path is None:
        raise ValueError("Attach a WAV recording. The upload must include the original WAV file, not only its filename or extracted text.")
    try:
        _validated_wav(audio_path)
        _check_cancelled(context)
        progress = context.get("progress")
        if callable(progress):
            progress("Transcribing the attached WAV locally.")
        from faster_whisper import WhisperModel
        try:
            model = WhisperModel(str(model_dir), device="auto", compute_type="int8", local_files_only=True)
        except TypeError:
            # Older package builds omit the flag; an existing local directory is still passed.
            model = WhisperModel(str(model_dir), device="auto", compute_type="int8")
        segments, info = model.transcribe(str(audio_path), beam_size=5, vad_filter=True)
        text = []
        for segment in segments:
            _check_cancelled(context)
            piece = segment.text.strip()
            if piece:
                text.append(piece)
            if sum(map(len, text)) > 50_000:
                raise ValueError("Transcript exceeds the 50,000 character limit.")
        if not text:
            raise RuntimeError("No speech was recognized in the WAV recording.")
        language = getattr(info, "language", None)
        prefix = f"Detected language: {language}.\n\n" if language else ""
        return prefix + " ".join(text)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
