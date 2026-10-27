import hashlib
import io
import os
import stat
import tarfile
import tempfile
from pathlib import Path
from unittest import skipUnless
from zipfile import ZipFile, ZipInfo

from django.test import SimpleTestCase

from common.utils import file_is_text, hash_file, process_file, seek_end


class ArchiveTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.output = self.root / "output"
        self.output.mkdir()

    def zip(self, members):
        path = self.root / "archive.zip"
        with ZipFile(path, "w") as archive:
            for name, data in members:
                archive.writestr(name, data)
        return path

    def tar(self, members):
        path = self.root / "archive.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for member, data in members:
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return path

    def test_zip_and_tar_use_requested_directory(self):
        for path in (
            self.zip([("nested/zip.txt", b"zip")]),
            self.tar([(tarfile.TarInfo("nested/tar.txt"), b"tar")]),
        ):
            process_file(str(path), sleep=0, extract_directory=str(self.output))
        self.assertEqual((self.output / "nested/zip.txt").read_bytes(), b"zip")
        self.assertEqual((self.output / "nested/tar.txt").read_bytes(), b"tar")

    def test_zip_and_tar_validate_all_paths_before_extracting(self):
        for name in ("../escape.txt", "/escape.txt", "C:/escape.txt", "..\\escape.txt"):
            paths = (
                self.zip([("allowed.txt", b"ok"), (name, b"bad")]),
                self.tar(
                    [
                        (tarfile.TarInfo("allowed.txt"), b"ok"),
                        (tarfile.TarInfo(name), b"bad"),
                    ]
                ),
            )
            for path in paths:
                with (
                    self.subTest(name=name, path=path),
                    self.assertLogs("common.utils", level="ERROR"),
                ):
                    with self.assertRaises(ValueError):
                        process_file(
                            str(path), sleep=0, extract_directory=str(self.output)
                        )
                self.assertFalse((self.output / "allowed.txt").exists())
                self.assertFalse((self.root / "escape.txt").exists())

    def test_tar_rejects_links_and_special_files(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE):
            member = tarfile.TarInfo("unsafe")
            member.type = kind
            member.linkname = "../outside"
            path = self.tar([(member, b"")])
            with (
                self.assertLogs("common.utils", level="ERROR"),
                self.assertRaises(ValueError),
            ):
                process_file(str(path), sleep=0, extract_directory=str(self.output))

    def test_zip_rejects_symlinks(self):
        member = ZipInfo("unsafe")
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        path = self.zip([(member, b"../outside")])
        with (
            self.assertLogs("common.utils", level="ERROR"),
            self.assertRaises(ValueError),
        ):
            process_file(str(path), sleep=0, extract_directory=str(self.output))

    @skipUnless(hasattr(os, "symlink"), "Requires symbolic links")
    def test_existing_symlink_cannot_escape_destination(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.output / "link").symlink_to(outside, target_is_directory=True)
        for path in (
            self.zip([("link/escape.txt", b"bad")]),
            self.tar([(tarfile.TarInfo("link/escape.txt"), b"bad")]),
        ):
            with (
                self.assertLogs("common.utils", level="ERROR"),
                self.assertRaises(ValueError),
            ):
                process_file(str(path), sleep=0, extract_directory=str(self.output))
        self.assertFalse((outside / "escape.txt").exists())

    def test_file_utilities_and_chunked_hash(self):
        path = self.root / "file.txt"
        path.write_bytes(b"hello world")
        self.assertTrue(file_is_text(path))
        self.assertEqual(seek_end(path, 5), b"world")
        self.assertEqual(seek_end(path, 100), b"")
        expected = hashlib.sha256(b"hello world").hexdigest()
        self.assertEqual(hash_file(path, func=hashlib.sha256), expected)
        self.assertEqual(hash_file(path, func=hashlib.sha256, chunks=2), expected)
        self.assertIsNone(hash_file(self.root / "missing"))
        self.assertEqual(process_file(str(path), sleep=0), str(path))

    def test_binary_file_is_not_text(self):
        path = self.root / "file.bin"
        path.write_bytes(b"\x00\x01\x02")
        self.assertFalse(file_is_text(path))
