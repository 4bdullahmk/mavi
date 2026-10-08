#!/usr/bin/env python3
"""Verify a downloaded Mavi ZIP and extract it to a fresh per-user release folder."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path, PurePosixPath

MAX_ARCHIVE_BYTES = 500 * 1024 * 1024
MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_ENTRIES = 10_000
HASH_CHUNK_BYTES = 1024 * 1024
_INVALID_WINDOWS = set('<>:"|?*')
_RESERVED_WINDOWS = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)), "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}


def sha256_stream(stream, *, max_bytes: int | None = None) -> str:
    digest = hashlib.sha256()
    total = 0
    while chunk := stream.read(HASH_CHUNK_BYTES):
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            raise ValueError('The ZIP exceeds the 500 MB download limit.')
        digest.update(chunk)
    return digest.hexdigest()


def read_expected_hash(sidecar: Path) -> str:
    if sidecar.stat().st_size > 4096:
        raise ValueError('The SHA-256 sidecar is too large.')
    fields = sidecar.read_text(encoding='utf-8').strip().split()
    if not fields or not re.fullmatch(r'[0-9a-fA-F]{64}', fields[0]):
        raise ValueError('The SHA-256 sidecar is malformed.')
    return fields[0].lower()


def _safe_member_name(raw: str) -> tuple[tuple[str, ...], bool]:
    if not raw or '\\' in raw or raw.startswith('/') or re.match(r'^[A-Za-z]:', raw):
        raise ValueError('The ZIP contains an unsafe path.')
    is_dir = raw.endswith('/')
    trimmed = raw[:-1] if is_dir else raw
    if not trimmed:
        raise ValueError('The ZIP contains an unsafe path.')
    parts = trimmed.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise ValueError('The ZIP contains an unsafe path.')
    for part in parts:
        if part[-1:] in ('.', ' ') or any(ord(char) < 32 or char in _INVALID_WINDOWS for char in part):
            raise ValueError('The ZIP contains a name that is invalid on Windows.')
        device_name = part.split('.', 1)[0].rstrip(' .').upper()
        if device_name in _RESERVED_WINDOWS:
            raise ValueError('The ZIP contains a reserved Windows filename.')
    # PurePosixPath is used only after explicit Windows and POSIX path checks.
    pure = PurePosixPath(*parts)
    if pure.is_absolute():
        raise ValueError('The ZIP contains an unsafe path.')
    return tuple(parts), is_dir


def _validate_members(infos: list[zipfile.ZipInfo], *, max_entries: int, max_file_bytes: int, max_total_bytes: int) -> None:
    if len(infos) > max_entries:
        raise ValueError('The ZIP contains too many files.')
    seen: set[tuple[str, ...]] = set()
    files: set[tuple[str, ...]] = set()
    directories: set[tuple[str, ...]] = set()
    total = 0
    for info in infos:
        parts, is_dir = _safe_member_name(info.filename)
        key = tuple(part.casefold() for part in parts)
        if key in seen:
            raise ValueError('The ZIP contains duplicate paths.')
        seen.add(key)
        kind = (info.external_attr >> 16) & 0o170000
        if kind not in (0, 0o040000, 0o100000):
            raise ValueError('The ZIP contains a link or special file.')
        if kind == 0o040000 and not is_dir:
            raise ValueError('The ZIP contains an invalid directory entry.')
        if kind == 0o100000 and is_dir:
            raise ValueError('The ZIP contains an invalid file entry.')
        for end in range(1, len(key)):
            if key[:end] in files:
                raise ValueError('The ZIP contains overlapping file and directory paths.')
            directories.add(key[:end])
        if is_dir:
            if key in files:
                raise ValueError('The ZIP contains overlapping file and directory paths.')
            directories.add(key)
            if info.file_size != 0:
                raise ValueError('The ZIP contains an invalid directory entry.')
            continue
        if key in directories:
            raise ValueError('The ZIP contains overlapping file and directory paths.')
        if info.file_size < 0 or info.file_size > max_file_bytes:
            raise ValueError('A ZIP file exceeds the per-file size limit.')
        total += info.file_size
        if total > max_total_bytes:
            raise ValueError('The ZIP exceeds the expanded size limit.')
        files.add(key)


def extract_archive(zf: zipfile.ZipFile, target: Path, *, max_entries: int = MAX_ENTRIES,
                    max_file_bytes: int = MAX_FILE_BYTES, max_total_bytes: int = MAX_TOTAL_BYTES) -> None:
    infos = zf.infolist()
    _validate_members(infos, max_entries=max_entries, max_file_bytes=max_file_bytes, max_total_bytes=max_total_bytes)
    root = target.resolve(strict=True)
    written = 0
    for info in infos:
        parts, is_dir = _safe_member_name(info.filename)
        destination = target.joinpath(*parts)
        if not destination.resolve(strict=False).is_relative_to(root):
            raise ValueError('The ZIP contains a path outside its release folder.')
        if is_dir:
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as source, destination.open('xb') as output:
            member_written = 0
            while chunk := source.read(HASH_CHUNK_BYTES):
                member_written += len(chunk)
                written += len(chunk)
                if member_written > MAX_FILE_BYTES or written > MAX_TOTAL_BYTES:
                    raise ValueError('The ZIP expanded beyond its size limit.')
                output.write(chunk)
        if member_written != info.file_size:
            raise ValueError('A ZIP file did not match its declared size.')


def install_verified_archive(archive: Path, releases: Path, expected_hash: str) -> Path:
    if not archive.is_file():
        raise ValueError('Choose a regular ZIP file.')
    archive_size = archive.stat().st_size
    if archive_size > MAX_ARCHIVE_BYTES:
        raise ValueError('The ZIP exceeds the 500 MB download limit.')
    releases.mkdir(parents=True, exist_ok=True)
    target = releases / (archive.stem + '-' + str(os.getpid()))
    target.mkdir()  # Exclusive creation: never overwrite an existing release folder.
    try:
        with archive.open('rb') as stream:
            if sha256_stream(stream, max_bytes=MAX_ARCHIVE_BYTES).lower() != expected_hash.lower():
                raise ValueError('SHA-256 mismatch. Keep the current Mavi folder and download the release again.')
            stream.seek(0)
            with zipfile.ZipFile(stream) as zf:
                if len(zf.infolist()) > MAX_ENTRIES:
                    raise ValueError('The ZIP contains too many files.')
                extract_archive(zf, target)
        launchers = list(target.rglob('Start-Mavi.cmd'))
        if len(launchers) != 1 or not (launchers[0].parent / 'portable' / 'server.py').is_file():
            raise ValueError('The ZIP does not contain one complete Windows Mavi release.')
        return launchers[0]
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def main() -> int:
    if len(sys.argv) != 2:
        raise ValueError('Pass the downloaded release ZIP path.')
    archive = Path(sys.argv[1]).expanduser().resolve(strict=True)
    if archive.suffix.lower() != '.zip':
        raise ValueError('Choose a .zip release file.')
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('The ZIP exceeds the 500 MB download limit.')
    sidecar_candidates = [Path(str(archive) + '.sha256'), archive.with_suffix('.sha256')]
    sidecar = next((path for path in sidecar_candidates if path.is_file()), None)
    if sidecar is None:
        raise ValueError('Download the matching .sha256 sidecar from the same GitHub release.')
    expected_hash = read_expected_hash(sidecar)

    local_app_data = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
    launcher = install_verified_archive(archive, local_app_data / 'Mavi' / 'Releases', expected_hash)
    print('Update verified and extracted. Your current release folder and local data were left in place.')
    print(f'Start the new release with: "{launcher}"')
    if hasattr(os, 'startfile'):
        os.startfile(str(launcher.parent))
    else:
        print('Open that folder in Windows File Explorer to start the new release.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f'Mavi update failed: {error}', file=sys.stderr)
        raise SystemExit(1)
