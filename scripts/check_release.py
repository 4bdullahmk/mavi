#!/usr/bin/env python3
"""Scan the entire source tree (tracked or not) for private release material."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "dist", "build"}
FORBIDDEN_SUFFIXES = {
    ".gguf", ".safetensors", ".jsonl", ".p12", ".pfx", ".pem", ".key", ".sqlite", ".db",
    ".log", ".pyc", ".pt", ".pth", ".onnx", ".ckpt", ".model", ".bin", ".adapter",
}
PATTERNS = (
    (re.compile(r"/Users/[A-Za-z][^/\s]*/"), "absolute macOS user path"),
    (re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\", re.IGNORECASE), "absolute Windows user path"),
    (re.compile(r"/home/[A-Za-z][^/\s]*/"), "absolute Linux user path"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private-key material"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "GitHub credential-shaped content"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{30,}\b"), "API-key-shaped content"),
    (re.compile(r"\b[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{25,}\b"),
     "Discord-token-shaped content"),
)


def _ignored(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def scan_source(root: Path) -> tuple[list[str], int]:
    """Return hygiene issues and count every regular source file inspected."""
    root = root.resolve()
    issues: list[str] = []
    inspected = 0
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if _ignored(relative):
            continue
        if path.is_symlink():
            issues.append(f"{relative}: symbolic link is not permitted in release source")
            continue
        if not path.is_file():
            continue
        inspected += 1
        if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name.lower().startswith(".env"):
            issues.append(f"{relative}: user data, credential, or model file type")
            continue
        if path.suffix.lower() in {".icns", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern, description in PATTERNS:
            if pattern.search(text):
                issues.append(f"{relative}: {description}")
    return issues, inspected


def main() -> int:
    issues, count = scan_source(ROOT)
    if issues:
        print("\n".join(issues))
        return 1
    print(f"Source hygiene passed: {count} files checked, including untracked source; no prohibited data files, links, credentials, or home paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
