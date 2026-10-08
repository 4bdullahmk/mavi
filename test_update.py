"""Focused tests for safe staging of portable Mavi updates."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import stat
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

MODULE_PATH = Path(__file__).with_name('Update-Mavi.py')
SPEC = importlib.util.spec_from_file_location('mavi_update_module', MODULE_PATH)
update = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(update)


class UpdateArchiveTests(unittest.TestCase):
    def make_zip(self, path: Path, entries: list[tuple[str, bytes, int | None]]) -> Path:
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data, mode in entries:
                info = zipfile.ZipInfo(name)
                # Preserve deliberately hostile ZIP names on Windows too.
                info.filename = name
                info.orig_filename = name
                if mode is not None:
                    info.external_attr = mode << 16
                archive.writestr(info, data)
        return path

    def test_valid_release_extracts_to_new_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self.make_zip(root / 'mavi.zip', [
                ('Mavi/Start-Mavi.cmd', b'@echo off\n', None),
                ('Mavi/portable/server.py', b'print("local")\n', None),
            ])
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            launcher = update.install_verified_archive(archive, root / 'releases', digest)
            self.assertTrue(launcher.is_file())
            self.assertEqual((launcher.parent / 'portable' / 'server.py').read_bytes(), b'print("local")\n')

    def test_unsafe_names_duplicates_and_overlaps_are_rejected(self):
        unsafe = [
            ('../outside.txt', b'x', None),
            ('/rooted.txt', b'x', None),
            ('C:/drive.txt', b'x', None),
            ('folder\\escape.txt', b'x', None),
            ('file:stream.txt', b'x', None),
            ('CON.txt', b'x', None),
            ('folder/../escape.txt', b'x', None),
        ]
        for entry in unsafe:
            with self.subTest(name=entry[0]), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive_path = self.make_zip(root / 'bad.zip', [entry])
                target = root / 'stage'
                target.mkdir()
                with zipfile.ZipFile(archive_path) as archive:
                    with self.assertRaises(ValueError):
                        update.extract_archive(archive, target)

        for entries in (
            [('Asset.txt', b'a', None), ('asset.TXT', b'b', None)],
            [('folder', b'a', None), ('folder/item.txt', b'b', None)],
            [('folder/item.txt', b'a', None), ('folder', b'b', None)],
        ):
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive_path = self.make_zip(root / 'bad.zip', entries)
                target = root / 'stage'
                target.mkdir()
                with zipfile.ZipFile(archive_path) as archive:
                    with self.assertRaises(ValueError):
                        update.extract_archive(archive, target)

    def test_symlink_and_expansion_limits_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = self.make_zip(root / 'bad.zip', [('link', b'target', stat.S_IFLNK | 0o777)])
            target = root / 'stage'; target.mkdir()
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaisesRegex(ValueError, 'link or special file'):
                    update.extract_archive(archive, target)

            archive_path = self.make_zip(root / 'large.zip', [('payload.bin', b'12345', None)])
            target2 = root / 'stage2'; target2.mkdir()
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaisesRegex(ValueError, 'per-file'):
                    update.extract_archive(archive, target2, max_file_bytes=4)
            target3 = root / 'stage3'; target3.mkdir()
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaisesRegex(ValueError, 'expanded size'):
                    update.extract_archive(archive, target3, max_total_bytes=4)

    def test_invalid_archive_rolls_back_stage_and_existing_target_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self.make_zip(root / 'mavi.zip', [('../escape', b'bad', None)])
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            target = root / 'releases' / f'mavi-{os.getpid()}'
            with self.assertRaises(ValueError):
                update.install_verified_archive(archive, root / 'releases', digest)
            self.assertFalse(target.exists())

            target.mkdir()
            marker = target / 'keep.txt'; marker.write_text('keep', encoding='utf-8')
            with self.assertRaises(FileExistsError):
                update.install_verified_archive(archive, root / 'releases', digest)
            self.assertEqual(marker.read_text(encoding='utf-8'), 'keep')

    def test_hash_is_streamed_and_mismatch_rolls_back(self):
        class TrackingStream:
            def __init__(self, data: bytes): self.data = data; self.calls = 0
            def read(self, size: int):
                self.calls += 1
                return self.data[:size] if self.calls == 1 else b''
        tracked = TrackingStream(b'example')
        self.assertEqual(update.sha256_stream(tracked), hashlib.sha256(b'example').hexdigest())
        self.assertGreater(tracked.calls, 1)
        with self.assertRaisesRegex(ValueError, '500 MB'):
            update.sha256_stream(TrackingStream(b'example'), max_bytes=3)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self.make_zip(root / 'mavi.zip', [('x', b'x', None)])
            with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                update.install_verified_archive(archive, root / 'releases', '0' * 64)
            self.assertFalse((root / 'releases' / f'mavi-{os.getpid()}').exists())

    def test_member_count_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = self.make_zip(root / 'many.zip', [('a.txt', b'a', None), ('b.txt', b'b', None)])
            target = root / 'stage'; target.mkdir()
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaisesRegex(ValueError, 'too many'):
                    update.extract_archive(archive, target, max_entries=1)

    def test_download_archive_limit_is_checked_before_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self.make_zip(root / 'mavi.zip', [('x', b'payload', None)])
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with patch.object(update, 'MAX_ARCHIVE_BYTES', 3):
                with self.assertRaisesRegex(ValueError, '500 MB'):
                    update.install_verified_archive(archive, root / 'releases', digest)
            self.assertFalse((root / 'releases').exists())


if __name__ == '__main__':
    unittest.main()
