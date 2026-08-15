"""Bounded local-filesystem binding for actor-scoped private repair photos.

This dark adapter accepts only a sealed capability issued by the metadata
snapshot reader.  It does not authorize download, support custom storage, or
claim that the bytes are safe content.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from kuck_serwis.repair_photo_evidence_store import (
	RepairPhotoEvidenceStoreCode,
	RepairPhotoEvidenceStoreError,
	ScopedPrivateFileAccess,
	_validated_file_access,
	revalidate_scoped_repair_photo_file_access,
)
from kuck_serwis.repair_photo_metadata import ScopedRepairPhotoEvidence

MAX_REPAIR_PHOTO_BYTES = 10 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
_RESULT_SEAL = object()


class RepairPhotoStorageBinding(StrEnum):
	LOCAL_PRIVATE_FILE = "LOCAL_PRIVATE_FILE"


class RepairPhotoStorageCode(StrEnum):
	INVALID_INPUT = "INVALID_INPUT"
	UNSUPPORTED_STORAGE = "UNSUPPORTED_STORAGE"
	FILE_BINDING_INVALID = "FILE_BINDING_INVALID"
	FILE_UNSAFE = "FILE_UNSAFE"
	EMPTY_BODY = "EMPTY_BODY"
	BODY_TOO_LARGE = "BODY_TOO_LARGE"
	FILE_CHANGED = "FILE_CHANGED"
	READ_FAILED = "READ_FAILED"


class RepairPhotoStorageError(RuntimeError):
	"""Stable code-only failure without path, File identity, hash, or body."""

	def __init__(self, code: RepairPhotoStorageCode) -> None:
		if type(code) is not RepairPhotoStorageCode:
			raise TypeError("INVALID_ERROR_CODE")
		self.code = code
		super().__init__(code.value)

	def __repr__(self) -> str:
		return f"RepairPhotoStorageError(code={self.code.value!r})"


@dataclass(frozen=True, slots=True, init=False, repr=False)
class BoundRepairPhotoBytes:
	"""Exact bounded bytes read from the local File named by a sealed capability."""

	evidence: ScopedRepairPhotoEvidence = field(repr=False)
	body: bytes = field(repr=False)
	byte_count: int
	content_sha256: str = field(repr=False)
	storage_binding: Literal[RepairPhotoStorageBinding.LOCAL_PRIVATE_FILE]
	_seal: object = field(repr=False, compare=False)

	def __init__(
		self,
		*,
		evidence: ScopedRepairPhotoEvidence,
		body: bytes,
		byte_count: int,
		content_sha256: str,
		storage_binding: Literal[RepairPhotoStorageBinding.LOCAL_PRIVATE_FILE],
		_seal: object,
	) -> None:
		if _seal is not _RESULT_SEAL:
			_raise(RepairPhotoStorageCode.INVALID_INPUT)
		object.__setattr__(self, "evidence", evidence)
		object.__setattr__(self, "body", body)
		object.__setattr__(self, "byte_count", byte_count)
		object.__setattr__(self, "content_sha256", content_sha256)
		object.__setattr__(self, "storage_binding", storage_binding)
		object.__setattr__(self, "_seal", _seal)
		_revalidate_result(self)

	def __repr__(self) -> str:
		return (
			"BoundRepairPhotoBytes(<redacted>, "
			f"position={self.evidence.position!r}, byte_count={self.byte_count!r}, "
			"storage_binding='LOCAL_PRIVATE_FILE')"
		)


@dataclass(frozen=True, slots=True)
class _FileStat:
	device: int
	inode: int
	mode: int
	links: int
	size: int
	modified_ns: int
	changed_ns: int


def read_bound_repair_photo_bytes(access: ScopedPrivateFileAccess) -> BoundRepairPhotoBytes:
	"""Read one local private File and revalidate its DB capability afterwards."""

	validated = _validate_access(access)
	site_root = _runtime_local_site_root()
	result = _read_from_local_site(validated, site_root)
	try:
		revalidate_scoped_repair_photo_file_access(validated)
	except RepairPhotoEvidenceStoreError as error:
		if type(error) is not RepairPhotoEvidenceStoreError:
			_raise(RepairPhotoStorageCode.READ_FAILED)
		if error.code is RepairPhotoEvidenceStoreCode.EVIDENCE_READ_FAILED:
			_raise(RepairPhotoStorageCode.READ_FAILED)
		if error.code is RepairPhotoEvidenceStoreCode.UNSUPPORTED_DATABASE:
			_raise(RepairPhotoStorageCode.UNSUPPORTED_STORAGE)
		_raise(RepairPhotoStorageCode.FILE_BINDING_INVALID)
	except Exception:
		_raise(RepairPhotoStorageCode.READ_FAILED)
	return result


def _runtime_local_site_root() -> str:
	try:
		import frappe

		for hook_name in ("write_file", "delete_file_data_content"):
			hooks = frappe.get_hooks(hook_name)
			if hooks:
				_raise(RepairPhotoStorageCode.UNSUPPORTED_STORAGE)
		site_root = frappe.get_site_path()
	except RepairPhotoStorageError:
		raise
	except Exception:
		_raise(RepairPhotoStorageCode.UNSUPPORTED_STORAGE)
	if type(site_root) is not str or not os.path.isabs(site_root):
		_raise(RepairPhotoStorageCode.UNSUPPORTED_STORAGE)
	return site_root


def _read_from_local_site(access: ScopedPrivateFileAccess, site_root: str) -> BoundRepairPhotoBytes:
	access = _validate_access(access)
	if type(site_root) is not str or not os.path.isabs(site_root):
		_raise(RepairPhotoStorageCode.INVALID_INPUT)
	if not all(hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")):
		_raise(RepairPhotoStorageCode.UNSUPPORTED_STORAGE)

	directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
	file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
	root_fd = private_fd = files_fd = file_fd = None
	try:
		root_before = _safe_lstat(site_root)
		_require_directory(root_before)
		root_fd = os.open(site_root, directory_flags)
		if _snapshot(os.fstat(root_fd)) != root_before:
			_raise(RepairPhotoStorageCode.FILE_CHANGED)
		private_fd = _open_directory("private", root_fd, directory_flags)
		private_before = _snapshot(os.fstat(private_fd))
		files_fd = _open_directory("files", private_fd, directory_flags)
		files_before = _snapshot(os.fstat(files_fd))

		before = _snapshot(os.stat(access._file_basename, dir_fd=files_fd, follow_symlinks=False))
		_require_regular_file(before)
		file_fd = os.open(access._file_basename, file_flags, dir_fd=files_fd)
		opened = _snapshot(os.fstat(file_fd))
		if opened != before:
			_raise(RepairPhotoStorageCode.FILE_CHANGED)
		body, digest = _read_bounded(file_fd)
		after = _snapshot(os.fstat(file_fd))
		if after != opened or len(body) != after.size:
			_raise(RepairPhotoStorageCode.FILE_CHANGED)
		path_after = _snapshot(os.stat(access._file_basename, dir_fd=files_fd, follow_symlinks=False))
		if path_after != opened:
			_raise(RepairPhotoStorageCode.FILE_CHANGED)
		_verify_fresh_path(
			site_root=site_root,
			basename=access._file_basename,
			root_expected=root_before,
			private_expected=private_before,
			files_expected=files_before,
			file_expected=opened,
			directory_flags=directory_flags,
		)
	except RepairPhotoStorageError:
		raise
	except OSError:
		_raise(RepairPhotoStorageCode.READ_FAILED)
	finally:
		for descriptor in (file_fd, files_fd, private_fd, root_fd):
			if descriptor is not None:
				try:
					os.close(descriptor)
				except OSError:
					pass

	return BoundRepairPhotoBytes(
		evidence=access.evidence,
		body=body,
		byte_count=len(body),
		content_sha256=digest,
		storage_binding=RepairPhotoStorageBinding.LOCAL_PRIVATE_FILE,
		_seal=_RESULT_SEAL,
	)


def _open_directory(name: str, parent_fd: int, flags: int) -> int:
	before = _snapshot(os.stat(name, dir_fd=parent_fd, follow_symlinks=False))
	_require_directory(before)
	descriptor = os.open(name, flags, dir_fd=parent_fd)
	if _snapshot(os.fstat(descriptor)) != before:
		os.close(descriptor)
		_raise(RepairPhotoStorageCode.FILE_CHANGED)
	return descriptor


def _verify_fresh_path(
	*,
	site_root: str,
	basename: str,
	root_expected: _FileStat,
	private_expected: _FileStat,
	files_expected: _FileStat,
	file_expected: _FileStat,
	directory_flags: int,
) -> None:
	root_fd = private_fd = files_fd = None
	try:
		if _safe_lstat(site_root) != root_expected:
			_raise(RepairPhotoStorageCode.FILE_CHANGED)
		root_fd = os.open(site_root, directory_flags)
		if _snapshot(os.fstat(root_fd)) != root_expected:
			_raise(RepairPhotoStorageCode.FILE_CHANGED)
		private_fd = _open_directory("private", root_fd, directory_flags)
		if _snapshot(os.fstat(private_fd)) != private_expected:
			_raise(RepairPhotoStorageCode.FILE_CHANGED)
		files_fd = _open_directory("files", private_fd, directory_flags)
		if _snapshot(os.fstat(files_fd)) != files_expected:
			_raise(RepairPhotoStorageCode.FILE_CHANGED)
		current = _snapshot(os.stat(basename, dir_fd=files_fd, follow_symlinks=False))
		if current != file_expected:
			_raise(RepairPhotoStorageCode.FILE_CHANGED)
	finally:
		for descriptor in (files_fd, private_fd, root_fd):
			if descriptor is not None:
				try:
					os.close(descriptor)
				except OSError:
					pass


def _safe_lstat(path: str) -> _FileStat:
	return _snapshot(os.lstat(path))


def _snapshot(value: os.stat_result) -> _FileStat:
	return _FileStat(
		device=value.st_dev,
		inode=value.st_ino,
		mode=value.st_mode,
		links=value.st_nlink,
		size=value.st_size,
		modified_ns=value.st_mtime_ns,
		changed_ns=value.st_ctime_ns,
	)


def _require_directory(value: _FileStat) -> None:
	if not stat.S_ISDIR(value.mode):
		_raise(RepairPhotoStorageCode.FILE_UNSAFE)


def _require_regular_file(value: _FileStat) -> None:
	if not stat.S_ISREG(value.mode) or value.links != 1:
		_raise(RepairPhotoStorageCode.FILE_UNSAFE)
	if value.size == 0:
		_raise(RepairPhotoStorageCode.EMPTY_BODY)
	if value.size > MAX_REPAIR_PHOTO_BYTES:
		_raise(RepairPhotoStorageCode.BODY_TOO_LARGE)


def _read_bounded(descriptor: int) -> tuple[bytes, str]:
	body = bytearray()
	digest = hashlib.sha256()
	while len(body) <= MAX_REPAIR_PHOTO_BYTES:
		remaining = MAX_REPAIR_PHOTO_BYTES + 1 - len(body)
		chunk = os.read(descriptor, min(READ_CHUNK_BYTES, remaining))
		if not chunk:
			break
		body.extend(chunk)
		digest.update(chunk)
	if not body:
		_raise(RepairPhotoStorageCode.EMPTY_BODY)
	if len(body) > MAX_REPAIR_PHOTO_BYTES:
		_raise(RepairPhotoStorageCode.BODY_TOO_LARGE)
	return bytes(body), digest.hexdigest()


def _validate_access(value: object) -> ScopedPrivateFileAccess:
	try:
		return _validated_file_access(value)
	except RepairPhotoEvidenceStoreError:
		_raise(RepairPhotoStorageCode.FILE_BINDING_INVALID)
	except Exception:
		_raise(RepairPhotoStorageCode.INVALID_INPUT)


def _revalidate_result(value: BoundRepairPhotoBytes) -> None:
	if (
		type(value.evidence) is not ScopedRepairPhotoEvidence
		or type(value.body) is not bytes
		or not value.body
		or type(value.byte_count) is not int
		or value.byte_count != len(value.body)
		or not 1 <= value.byte_count <= MAX_REPAIR_PHOTO_BYTES
		or type(value.content_sha256) is not str
		or value.content_sha256 != hashlib.sha256(value.body).hexdigest()
		or value.storage_binding is not RepairPhotoStorageBinding.LOCAL_PRIVATE_FILE
		or value._seal is not _RESULT_SEAL
	):
		_raise(RepairPhotoStorageCode.INVALID_INPUT)
	try:
		ScopedRepairPhotoEvidence(
			repair_id=value.evidence.repair_id,
			position=value.evidence.position,
			is_private=value.evidence.is_private,
			exact_attachment=value.evidence.exact_attachment,
			metadata_only=value.evidence.metadata_only,
		)
	except Exception:
		_raise(RepairPhotoStorageCode.INVALID_INPUT)
	if (
		value.evidence.is_private is not True
		or value.evidence.exact_attachment is not True
		or value.evidence.metadata_only is not True
	):
		_raise(RepairPhotoStorageCode.INVALID_INPUT)


def _raise(code: RepairPhotoStorageCode) -> None:
	raise RepairPhotoStorageError(code) from None
