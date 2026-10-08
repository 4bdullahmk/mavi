#!/usr/bin/env python3
"""Offline speech-to-text helper for Mavi."""
import contextlib
import json
import os
import sys
import time
from pathlib import Path

MODEL = Path.home() / 'Library/Application Support/Mavi/Runtime/whisper-turbo'
FFMPEG = '/opt/homebrew/bin'


def main() -> int:
    if len(sys.argv) != 2:
        raise ValueError('Expected the path to one recorded WAV file.')
    audio = Path(sys.argv[1]).expanduser().resolve()
    if not audio.is_file() or audio.suffix.lower() != '.wav':
        raise ValueError('The recording is missing or is not a WAV file.')
    if not MODEL.is_dir():
        raise RuntimeError(f'Offline Whisper model is missing: {MODEL}')

    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['PATH'] = FFMPEG + os.pathsep + os.environ.get('PATH', '')
    started = time.monotonic()

    # mlx_whisper and its dependencies may print progress. Keep stdout reserved
    # for the single machine-readable response consumed by Swift.
    with contextlib.redirect_stdout(sys.stderr):
        import mlx_whisper
        result = mlx_whisper.transcribe(str(audio), path_or_hf_repo=str(MODEL))

    text = result.get('text', '').strip() if isinstance(result, dict) else ''
    if not text:
        raise RuntimeError('No speech was recognized. Try a clearer or longer recording.')
    print(json.dumps({'text': text, 'seconds': round(time.monotonic() - started, 2)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
