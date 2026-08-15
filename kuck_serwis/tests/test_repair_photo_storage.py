import ast
import hashlib
import inspect
import os
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import kuck_serwis.repair_photo_storage as storage_module
from kuck_serwis.repair_photo_evidence_store import (
	RepairPhotoEvidenceStoreCode,
	RepairPhotoEvidenceStoreError,
	_issue_actor_scoped_repair_access,
	read_scoped_repair_photo_file_access,
)
from kuck_serwis.repair_photo_storage import (
	BoundRepairPhotoBytes,
	RepairPhotoStorageBinding,
	RepairPhotoStorageCode,
	RepairPhotoStorageError,
	_read_from_local_site,
	read_bound_repair_photo_bytes,
)

REPAIR_NAME = "NAP-2026-00001"
REPAIR_ID = "rpr_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
ACTOR = "synthetic@example.test"
FILE_ID = "G080-FILE"
BASENAME = "synthetic.png"
URL = f"/private/files/{BASENAME}"
REVISION = "2026-08-15 10:11:12.123456"


def actor_access():
	return _issue_actor_scoped_repair_access(
		repair_name=REPAIR_NAME, repair_id=REPAIR_ID, actor_identity=ACTOR
	)


def rows(*, basename=BASENAME, url=URL, file_identity=FILE_ID, revision=REVISION):
	return (
		("REPEATABLE-READ", "META", 1, 1, 1, 0, None, None, None, None, None, None, None, None),
		(
			"REPEATABLE-READ",
			"PHOTO",
			None,
			None,
			None,
			None,
			1,
			url,
			len(url),
			0,
			1,
			file_identity,
			basename,
			revision,
		),
	)


def capability():
	return read_scoped_repair_photo_file_access(actor_access(), reader=lambda **_kwargs: rows())[0]


class TestRepairPhotoStorage(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory(prefix="g080-")
		self.addCleanup(self.temp.cleanup)
		self.site = Path(self.temp.name) / "site"
		self.files = self.site / "private" / "files"
		self.files.mkdir(parents=True)

	def write(self, body=b"bounded-private-photo"):
		path = self.files / BASENAME
		path.write_bytes(body)
		return path

	def assert_code(self, code, call):
		with self.assertRaises(RepairPhotoStorageError) as raised:
			call()
		self.assertIs(raised.exception.code, code)
		self.assertEqual(str(raised.exception), code.value)
		self.assertEqual(repr(raised.exception), f"RepairPhotoStorageError(code={code.value!r})")

	def local_read(self, *, proof=None):
		return _read_from_local_site(proof or capability(), str(self.site))

	def test_exact_local_bytes_sha_result_are_frozen_and_redacted(self):
		body = b"bounded-private-photo"
		self.write(body)
		result = self.local_read()
		self.assertIs(type(result), BoundRepairPhotoBytes)
		self.assertEqual(result.body, body)
		self.assertEqual(result.byte_count, len(body))
		self.assertEqual(result.content_sha256, hashlib.sha256(body).hexdigest())
		self.assertIs(result.storage_binding, RepairPhotoStorageBinding.LOCAL_PRIVATE_FILE)
		text = repr(result)
		for marker in (REPAIR_ID, FILE_ID, URL, BASENAME, result.content_sha256, repr(body)):
			self.assertNotIn(marker, text)
		with self.assertRaises(FrozenInstanceError):
			result.body = b"changed"

	def test_public_boundary_has_no_site_root_and_revalidates_after_read(self):
		self.write()
		calls = []
		with (
			patch.object(storage_module, "_runtime_local_site_root", return_value=str(self.site)),
			patch.object(
				storage_module,
				"revalidate_scoped_repair_photo_file_access",
				side_effect=lambda value: calls.append(value),
			),
		):
			result = read_bound_repair_photo_bytes(capability())
		self.assertEqual(result.byte_count, len(result.body))
		self.assertEqual(len(calls), 1)
		self.assertNotIn("site_root", inspect.signature(read_bound_repair_photo_bytes).parameters)

	def test_stale_or_cross_actor_revalidation_discards_read_result(self):
		self.write()
		for error in (
			RepairPhotoEvidenceStoreError(RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE),
			RepairPhotoEvidenceStoreError(RepairPhotoEvidenceStoreCode.SCOPED_REPAIR_NOT_FOUND),
		):
			with (
				patch.object(storage_module, "_runtime_local_site_root", return_value=str(self.site)),
				patch.object(storage_module, "revalidate_scoped_repair_photo_file_access", side_effect=error),
			):
				self.assert_code(
					RepairPhotoStorageCode.FILE_BINDING_INVALID,
					lambda: read_bound_repair_photo_bytes(capability()),
				)
		with (
			patch.object(storage_module, "_runtime_local_site_root", return_value=str(self.site)),
			patch.object(
				storage_module,
				"revalidate_scoped_repair_photo_file_access",
				side_effect=RepairPhotoEvidenceStoreError(RepairPhotoEvidenceStoreCode.EVIDENCE_READ_FAILED),
			),
		):
			self.assert_code(
				RepairPhotoStorageCode.READ_FAILED,
				lambda: read_bound_repair_photo_bytes(capability()),
			)

	def test_custom_write_or_delete_hook_is_unsupported_before_filesystem(self):
		for custom_hook in ("write_file", "delete_file_data_content"):
			fake = SimpleNamespace(
				get_hooks=lambda name, custom_hook=custom_hook: ["custom.backend"]
				if name == custom_hook
				else [],
				get_site_path=lambda: str(self.site),
			)
			with patch.dict(sys.modules, {"frappe": fake}):
				self.assert_code(
					RepairPhotoStorageCode.UNSUPPORTED_STORAGE,
					lambda: read_bound_repair_photo_bytes(capability()),
				)

	def test_empty_hooks_use_exact_trusted_runtime_site_root(self):
		body = b"local"
		self.write(body)
		fake = SimpleNamespace(get_hooks=lambda _name: [], get_site_path=lambda: str(self.site))
		with (
			patch.dict(sys.modules, {"frappe": fake}),
			patch.object(storage_module, "revalidate_scoped_repair_photo_file_access"),
		):
			self.assertEqual(read_bound_repair_photo_bytes(capability()).body, body)

	def test_remote_nested_and_traversal_bindings_never_issue_capability(self):
		cases = (
			{"url": "https://storage.example.test/private.png", "basename": BASENAME},
			{"url": "/private/files/nested/synthetic.png", "basename": "nested/synthetic.png"},
			{"url": "/private/files/../synthetic.png", "basename": "../synthetic.png"},
			{"url": "/private/files/%2fetc", "basename": "%2fetc"},
			{"url": "/private/files/other.png", "basename": BASENAME},
		)
		for values in cases:
			with self.assertRaises(RepairPhotoEvidenceStoreError):
				read_scoped_repair_photo_file_access(
					actor_access(), reader=lambda values=values, **_kwargs: rows(**values)
				)

	def test_symlink_site_private_files_and_file_are_rejected(self):
		outside = Path(self.temp.name) / "outside"
		outside.mkdir()
		(outside / BASENAME).write_bytes(b"outside")
		cases = []
		site_link = Path(self.temp.name) / "site-link"
		site_link.symlink_to(self.site, target_is_directory=True)
		cases.append(site_link)

		private_target = Path(self.temp.name) / "private-target"
		(private_target / "files").mkdir(parents=True)
		private_site = Path(self.temp.name) / "private-site"
		private_site.mkdir()
		(private_site / "private").symlink_to(private_target, target_is_directory=True)
		cases.append(private_site)

		files_site = Path(self.temp.name) / "files-site"
		(files_site / "private").mkdir(parents=True)
		(files_site / "private" / "files").symlink_to(outside, target_is_directory=True)
		cases.append(files_site)

		for root in cases:
			with self.subTest(root=root.name):
				self.assert_code(
					RepairPhotoStorageCode.FILE_UNSAFE,
					lambda root=root: _read_from_local_site(capability(), str(root)),
				)

		(self.files / BASENAME).symlink_to(outside / BASENAME)
		self.assert_code(RepairPhotoStorageCode.FILE_UNSAFE, self.local_read)

	def test_hardlink_directory_fifo_and_missing_are_rejected_without_blocking(self):
		other = Path(self.temp.name) / "other.bin"
		other.write_bytes(b"other")
		os.link(other, self.files / BASENAME)
		self.assert_code(RepairPhotoStorageCode.FILE_UNSAFE, self.local_read)
		(self.files / BASENAME).unlink()
		(self.files / BASENAME).mkdir()
		self.assert_code(RepairPhotoStorageCode.FILE_UNSAFE, self.local_read)
		(self.files / BASENAME).rmdir()
		os.mkfifo(self.files / BASENAME)
		self.assert_code(RepairPhotoStorageCode.FILE_UNSAFE, self.local_read)
		(self.files / BASENAME).unlink()
		self.assert_code(RepairPhotoStorageCode.READ_FAILED, self.local_read)

	def test_empty_exact_cap_and_cap_plus_one_are_bounded(self):
		self.write(b"")
		self.assert_code(RepairPhotoStorageCode.EMPTY_BODY, self.local_read)
		for size, expected in ((8, None), (9, RepairPhotoStorageCode.BODY_TOO_LARGE)):
			self.write(b"x" * size)
			with patch.object(storage_module, "MAX_REPAIR_PHOTO_BYTES", 8):
				if expected is None:
					self.assertEqual(self.local_read().byte_count, 8)
				else:
					self.assert_code(expected, self.local_read)

	def test_growth_during_read_is_detected_with_cap_plus_one(self):
		path = self.write(b"abcd")
		real_read = os.read
		mutated = False

		def changing_read(descriptor, size):
			nonlocal mutated
			chunk = real_read(descriptor, size)
			if chunk and not mutated:
				mutated = True
				with path.open("ab") as handle:
					handle.write(b"efghij")
			return chunk

		with (
			patch.object(storage_module, "MAX_REPAIR_PHOTO_BYTES", 8),
			patch.object(storage_module.os, "read", side_effect=changing_read),
		):
			self.assert_code(RepairPhotoStorageCode.BODY_TOO_LARGE, self.local_read)

	def test_truncation_and_metadata_mutation_during_read_are_detected(self):
		path = self.write(b"abcdefgh")
		real_read = os.read
		mutated = False

		def truncating_read(descriptor, size):
			nonlocal mutated
			chunk = real_read(descriptor, min(size, 4))
			if chunk and not mutated:
				mutated = True
				path.write_bytes(b"ab")
			return chunk

		with patch.object(storage_module.os, "read", side_effect=truncating_read):
			self.assert_code(RepairPhotoStorageCode.FILE_CHANGED, self.local_read)

	def test_inode_swap_between_stat_and_open_is_detected(self):
		path = self.write(b"first")
		replacement = self.files / "replacement.png"
		replacement.write_bytes(b"second")
		real_open = os.open
		swapped = False

		def swapping_open(name, flags, *args, **kwargs):
			nonlocal swapped
			if name == BASENAME and not swapped:
				swapped = True
				path.unlink()
				replacement.rename(path)
			return real_open(name, flags, *args, **kwargs)

		with patch.object(storage_module.os, "open", side_effect=swapping_open):
			self.assert_code(RepairPhotoStorageCode.FILE_CHANGED, self.local_read)

	def test_path_inode_swap_after_open_is_detected(self):
		path = self.write(b"first-body")
		replacement = self.files / "replacement.png"
		replacement.write_bytes(b"second-body")
		real_read = os.read
		swapped = False

		def swapping_read(descriptor, size):
			nonlocal swapped
			chunk = real_read(descriptor, size)
			if chunk and not swapped:
				swapped = True
				path.unlink()
				replacement.rename(path)
			return chunk

		with patch.object(storage_module.os, "read", side_effect=swapping_read):
			self.assert_code(RepairPhotoStorageCode.FILE_CHANGED, self.local_read)

	def test_private_files_directory_swap_after_open_is_detected_from_fresh_root_walk(self):
		self.write(b"first-body")
		real_read = os.read
		swapped = False

		def swapping_read(descriptor, size):
			nonlocal swapped
			chunk = real_read(descriptor, size)
			if chunk and not swapped:
				swapped = True
				old_files = self.site / "private" / "old-files"
				self.files.rename(old_files)
				self.files.mkdir()
				(self.files / BASENAME).write_bytes(b"second-body")
			return chunk

		with patch.object(storage_module.os, "read", side_effect=swapping_read):
			self.assert_code(RepairPhotoStorageCode.FILE_CHANGED, self.local_read)

	def test_forged_capability_and_result_fail_code_only(self):
		self.assert_code(
			RepairPhotoStorageCode.FILE_BINDING_INVALID,
			lambda: _read_from_local_site(object(), str(self.site)),
		)
		self.write()
		valid = self.local_read()
		values = {name: getattr(valid, name) for name in valid.__dataclass_fields__}
		for field, value in (
			("body", bytearray(valid.body)),
			("byte_count", True),
			("content_sha256", "0" * 64),
			("storage_binding", "LOCAL_PRIVATE_FILE"),
		):
			with self.subTest(field=field):
				with self.assertRaises(RepairPhotoStorageError):
					BoundRepairPhotoBytes(**{**values, field: value})

	def test_module_has_no_http_md5_or_file_get_content_fallback(self):
		path = Path(inspect.getfile(read_bound_repair_photo_bytes))
		source = path.read_text(encoding="utf-8")
		tree = ast.parse(source)
		imports = {
			alias.name.split(".", 1)[0]
			for node in ast.walk(tree)
			for alias in (node.names if isinstance(node, ast.Import) else ())
		}
		self.assertTrue(imports.isdisjoint({"requests", "urllib", "socket"}))
		self.assertNotIn("md5", source.lower())
		self.assertNotIn("get_content", source)


if __name__ == "__main__":
	unittest.main()
