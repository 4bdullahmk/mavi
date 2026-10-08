#!/usr/bin/env python3
"""Build a deterministic, source-only Windows candidate ZIP and SHA-256 file.

Usage: python scripts/package_source.py SOURCE_ROOT OUTPUT_DIR VERSION
The output directory must be outside SOURCE_ROOT so release artifacts cannot
be swept back into a later source package.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
import zipfile
from pathlib import Path

_ROOT_FILES = ("README.md", "WINDOWS.md", "AGENTS.md", "CHANGELOG.md", "INSTALL_WITH_CODEX.md", "UPDATING.md",
               "Start-Mavi.cmd", "Install-Mavi.cmd", "Install-Mavi.ps1",
               "Update-Mavi.cmd", "Update-Mavi.py", "Setup-Models.py", "test_update.py")
_PORTABLE_SUFFIXES = {".py", ".txt", ".html", ".css", ".js", ".png"}
_EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                   ".venv", "venv", "dist", "build", "data", "outputs", "logs",
                   "models", "training", "adapters", "cache", "caches"}


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def collect_source(source_root: Path) -> list[tuple[Path, str]]:
    root = source_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Source root must be a directory.")
    selected: list[Path] = []
    for name in _ROOT_FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Required source file is missing or unsafe: {name}")
        selected.append(path)
    app_dir = root / "app"
    if app_dir.is_symlink() or not app_dir.is_dir():
        raise ValueError("The application helper folder is missing or unsafe.")
    # Portable tools dynamically import these helpers. Include the complete
    # non-personal Python source set so future dispatch additions don't silently
    # ship without their dependency closure. Native Swift sources aren't needed
    # to launch the Windows portable client.
    for path in sorted(app_dir.glob("*.py")):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are not allowed in source packages: app/{path.name}")
        if path.name in {"pack_icon.py", "package_release.py"}:
            continue
        selected.append(path)
    app_names = {path.name for path in selected if path.parent == app_dir}
    required_app = {"FileTools.py", "DeveloperAgent.py", "ProjectReadCache.py", "CADAgent.py",
                    "CADTools.py", "ProductSpecs.py", "MeshTools.py", "StockTools.py",
                    "SelfUpdate.py", "SpreadsheetTools.py", "PresentationTools.py"}
    if not required_app <= app_names:
        raise ValueError("The source package is missing application Python helpers.")
    portable = root / "portable"
    if not portable.is_dir() or portable.is_symlink():
        raise ValueError("The portable source folder is missing or unsafe.")
    for path in sorted(portable.rglob("*")):
        relative = path.relative_to(portable)
        if path.is_symlink():
            raise ValueError(f"Symbolic links are not allowed in source packages: portable/{relative}")
        if any(part in _EXCLUDED_PARTS or part.startswith(".") for part in relative.parts):
            continue
        if path.is_dir():
            continue
        if not path.is_file() or path.suffix.lower() not in _PORTABLE_SUFFIXES:
            continue
        selected.append(path)
    # Ship the data-free acceptance suite so a recipient can ask Codex to run
    # the package/update checks against the exact source they installed.
    tests = root / "tests"
    if tests.exists():
        if tests.is_symlink() or not tests.is_dir():
            raise ValueError("The test source folder is unsafe.")
        for path in sorted(tests.rglob("*.py")):
            relative = path.relative_to(tests)
            if path.is_symlink() or any(part.startswith(".") for part in relative.parts):
                raise ValueError(f"Test sources may not contain symbolic or hidden paths: tests/{relative}")
            if path.is_file():
                selected.append(path)
    scripts = root / "scripts"
    if scripts.exists():
        if scripts.is_symlink() or not scripts.is_dir():
            raise ValueError("The release-check source folder is unsafe.")
        for path in sorted(scripts.glob("*.py")):
            if path.is_symlink():
                raise ValueError(f"Symbolic links are not allowed in source packages: scripts/{path.name}")
            if path.is_file():
                selected.append(path)
    result = []
    for path in selected:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Only regular source files may be packaged.")
        relative = path.relative_to(root).as_posix()
        result.append((path, relative))
    names = [name.casefold() for _, name in result]
    if len(names) != len(set(names)):
        raise ValueError("The source tree contains duplicate package paths.")
    if "portable/server.py" not in names or "portable/requirements.txt" not in names:
        raise ValueError("The source package is missing portable startup files.")
    if not {"setup-models.py", "agents.md", "changelog.md"} <= {Path(n).name.casefold() for _, n in result}:
        raise ValueError("The source package is missing setup or release notes.")
    return sorted(result, key=lambda item: item[1].casefold())


def package_source(source_root: Path, output_dir: Path, version: str) -> tuple[Path, Path]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}", version):
        raise ValueError("Version must use only letters, digits, dot, underscore, or hyphen.")
    root = source_root.resolve(strict=True)
    output_dir = output_dir.resolve(strict=False)
    if _inside(output_dir, root):
        raise ValueError("Choose an output directory outside the source tree.")
    files = collect_source(root)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"Mavi-Windows-{version}.zip"
    sidecar = archive.with_suffix(archive.suffix + ".sha256")
    prefix = f"Mavi-Windows-{version}/"
    temp = archive.with_name(archive.name + ".tmp")
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for path, relative in files:
                info = zipfile.ZipInfo(prefix + relative, (2020, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                zf.writestr(info, path.read_bytes())
        digest = hashlib.sha256(temp.read_bytes()).hexdigest()
        os.replace(temp, archive)
        side_temp = sidecar.with_name(sidecar.name + ".tmp")
        side_temp.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
        os.replace(side_temp, sidecar)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    return archive, sidecar


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        raise ValueError("Usage: package_source.py SOURCE_ROOT OUTPUT_DIR VERSION")
    archive, sidecar = package_source(Path(argv[1]), Path(argv[2]), argv[3])
    print(f"{archive}\n{sidecar}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except Exception as error:
        print(f"Source packaging failed: {error}", file=sys.stderr)
        raise SystemExit(1)
