"""Fresh-process boundary for structural inspection of bound repair photo bytes.

The child is a deterministic decoder process, not an AV scanner or sandbox.  It
receives only bounded image bytes and a MIME discriminator over stdin.  Repair,
actor, File, and storage identities never cross the process boundary.
"""

from __future__ import annotations

import hashlib
import os
import selectors
import stat
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from kuck_serwis.repair_photo_content import (
	MAX_REPAIR_PHOTO_BYTES,
	RepairPhotoContentBinding,
	RepairPhotoContentCode,
	RepairPhotoDecodeEvidence,
	RepairPhotoMalwareStatus,
	RepairPhotoMime,
	RepairPhotoPolyglotStatus,
)
from kuck_serwis.repair_photo_storage import (
	_RESULT_SEAL,
	BoundRepairPhotoBytes,
	RepairPhotoStorageError,
	_revalidate_result,
)

WORKER_TIMEOUT_SECONDS = 5.0
MAX_WORKER_OUTPUT_BYTES = 60
_INPUT_MAGIC = b"KRPDI001"
_OUTPUT_MAGIC = b"KRPDO001"
_PROTOCOL_VERSION = 1
_INPUT_HEADER = struct.Struct(">8sBBI")
_OUTPUT = struct.Struct(">8sBBBBIIII32s")
_STATUS_OK = 0
_STATUS_REJECTED = 1
_MIME_TO_WIRE = {
	RepairPhotoMime.JPEG: 1,
	RepairPhotoMime.PNG: 2,
	RepairPhotoMime.WEBP: 3,
}
_WIRE_TO_MIME = {value: key for key, value in _MIME_TO_WIRE.items()}
_CONTENT_TO_WIRE = {code: index for index, code in enumerate(RepairPhotoContentCode, 1)}
_WIRE_TO_CONTENT = {value: key for key, value in _CONTENT_TO_WIRE.items()}


class RepairPhotoDecodeProcessCode(StrEnum):
	INVALID_INPUT = "INVALID_INPUT"
	INVALID_BOUND_BYTES = "INVALID_BOUND_BYTES"
	WORKER_UNAVAILABLE = "WORKER_UNAVAILABLE"
	WORKER_TIMEOUT = "WORKER_TIMEOUT"
	WORKER_FAILED = "WORKER_FAILED"
	OUTPUT_TOO_LARGE = "OUTPUT_TOO_LARGE"
	PROTOCOL_INVALID = "PROTOCOL_INVALID"
	RESULT_MISMATCH = "RESULT_MISMATCH"
	CONTENT_REJECTED = "CONTENT_REJECTED"


class RepairPhotoDecodeProcessError(RuntimeError):
	"""Stable code-only failure with an optional allowlisted decoder reason."""

	def __init__(
		self,
		code: RepairPhotoDecodeProcessCode,
		*,
		content_code: RepairPhotoContentCode | None = None,
	) -> None:
		if type(code) is not RepairPhotoDecodeProcessCode:
			raise TypeError("INVALID_ERROR_CODE")
		if content_code is not None and type(content_code) is not RepairPhotoContentCode:
			raise TypeError("INVALID_CONTENT_CODE")
		if (code is RepairPhotoDecodeProcessCode.CONTENT_REJECTED) is (content_code is None):
			raise TypeError("INVALID_CONTENT_CODE")
		self.code = code
		self.content_code = content_code
		super().__init__(code.value)

	def __repr__(self) -> str:
		return f"RepairPhotoDecodeProcessError(code={self.code.value!r})"


@dataclass(frozen=True, slots=True, repr=False)
class _WorkerExecution:
	returncode: int
	stdout: bytes


def inspect_bound_repair_photo_content(
	*,
	bound_bytes: BoundRepairPhotoBytes,
	expected_mime: RepairPhotoMime,
) -> RepairPhotoDecodeEvidence:
	"""Inspect one exact G0-80 result in a fresh isolated Python interpreter."""

	validated = _validate_bound_bytes(bound_bytes)
	if type(expected_mime) is not RepairPhotoMime:
		_raise(RepairPhotoDecodeProcessCode.INVALID_INPUT)
	packet = (
		_INPUT_HEADER.pack(
			_INPUT_MAGIC,
			_PROTOCOL_VERSION,
			_MIME_TO_WIRE[expected_mime],
			validated.byte_count,
		)
		+ validated.body
	)
	execution = _run_worker(packet)
	if type(execution) is not _WorkerExecution:
		_raise(RepairPhotoDecodeProcessCode.WORKER_FAILED)
	if type(execution.returncode) is not int or type(execution.stdout) is not bytes:
		_raise(RepairPhotoDecodeProcessCode.WORKER_FAILED)
	if execution.returncode != 0:
		_raise(RepairPhotoDecodeProcessCode.WORKER_FAILED)
	return _decode_result(execution.stdout, validated, expected_mime)


def _validate_bound_bytes(value: object) -> BoundRepairPhotoBytes:
	if type(value) is not BoundRepairPhotoBytes:
		_raise(RepairPhotoDecodeProcessCode.INVALID_BOUND_BYTES)
	try:
		if value._seal is not _RESULT_SEAL:
			raise ValueError
		_revalidate_result(value)
	except (AttributeError, TypeError, ValueError, RepairPhotoStorageError):
		_raise(RepairPhotoDecodeProcessCode.INVALID_BOUND_BYTES)
	if value.byte_count != len(value.body):
		_raise(RepairPhotoDecodeProcessCode.INVALID_BOUND_BYTES)
	if value.content_sha256 != hashlib.sha256(value.body).hexdigest():
		_raise(RepairPhotoDecodeProcessCode.INVALID_BOUND_BYTES)
	return value


def _worker_argv() -> tuple[str, str, str]:
	if type(sys.executable) is not str or not os.path.isabs(sys.executable):
		_raise(RepairPhotoDecodeProcessCode.WORKER_UNAVAILABLE)
	declared = Path(__file__).with_name("repair_photo_decode_worker.py")
	try:
		metadata = declared.lstat()
		resolved = declared.resolve(strict=True)
		resolved_metadata = resolved.stat()
	except OSError:
		_raise(RepairPhotoDecodeProcessCode.WORKER_UNAVAILABLE)
	if (
		stat.S_ISLNK(metadata.st_mode)
		or not stat.S_ISREG(metadata.st_mode)
		or not stat.S_ISREG(resolved_metadata.st_mode)
		or resolved.parent != Path(__file__).resolve(strict=True).parent
	):
		_raise(RepairPhotoDecodeProcessCode.WORKER_UNAVAILABLE)
	return (sys.executable, "-I", str(resolved))


def _run_worker(packet: bytes) -> _WorkerExecution:
	argv = _worker_argv()
	try:
		process = subprocess.Popen(
			argv,
			stdin=subprocess.PIPE,
			stdout=subprocess.PIPE,
			stderr=subprocess.DEVNULL,
			close_fds=True,
			env={},
			shell=False,
		)
	except (OSError, subprocess.SubprocessError):
		_raise(RepairPhotoDecodeProcessCode.WORKER_UNAVAILABLE)
	if process.stdin is None or process.stdout is None:
		process.kill()
		process.wait()
		_raise(RepairPhotoDecodeProcessCode.WORKER_FAILED)
	try:
		stdout = _exchange_bounded(process, packet)
	except RepairPhotoDecodeProcessError:
		_kill_and_wait(process)
		raise
	returncode = process.wait()
	return _WorkerExecution(returncode=returncode, stdout=stdout)


def _exchange_bounded(process: subprocess.Popen[bytes], packet: bytes) -> bytes:
	assert process.stdin is not None
	assert process.stdout is not None
	input_fd = process.stdin.fileno()
	output_fd = process.stdout.fileno()
	os.set_blocking(input_fd, False)
	os.set_blocking(output_fd, False)
	selector = selectors.DefaultSelector()
	selector.register(input_fd, selectors.EVENT_WRITE)
	selector.register(output_fd, selectors.EVENT_READ)
	deadline = time.monotonic() + WORKER_TIMEOUT_SECONDS
	input_offset = 0
	output = bytearray()
	output_open = True
	try:
		while output_open or process.poll() is None:
			remaining = deadline - time.monotonic()
			if remaining <= 0:
				_raise(RepairPhotoDecodeProcessCode.WORKER_TIMEOUT)
			events = selector.select(remaining)
			if not events and process.poll() is None:
				_raise(RepairPhotoDecodeProcessCode.WORKER_TIMEOUT)
			for key, mask in events:
				if key.fd == input_fd and mask & selectors.EVENT_WRITE:
					try:
						written = os.write(input_fd, packet[input_offset : input_offset + 65_536])
					except BrokenPipeError:
						written = 0
						input_offset = len(packet)
					input_offset += written
					if input_offset >= len(packet):
						selector.unregister(input_fd)
						process.stdin.close()
				if key.fd == output_fd and mask & selectors.EVENT_READ:
					chunk = os.read(output_fd, MAX_WORKER_OUTPUT_BYTES + 1 - len(output))
					if chunk:
						output.extend(chunk)
						if len(output) > MAX_WORKER_OUTPUT_BYTES:
							_raise(RepairPhotoDecodeProcessCode.OUTPUT_TOO_LARGE)
					else:
						selector.unregister(output_fd)
						output_open = False
			if process.poll() is not None and output_open:
				chunk = os.read(output_fd, MAX_WORKER_OUTPUT_BYTES + 1 - len(output))
				if chunk:
					output.extend(chunk)
					if len(output) > MAX_WORKER_OUTPUT_BYTES:
						_raise(RepairPhotoDecodeProcessCode.OUTPUT_TOO_LARGE)
				else:
					selector.unregister(output_fd)
					output_open = False
	finally:
		selector.close()
		if not process.stdin.closed:
			process.stdin.close()
		process.stdout.close()
	return bytes(output)


def _kill_and_wait(process: subprocess.Popen[bytes]) -> None:
	if process.poll() is None:
		process.kill()
	process.wait()


def _decode_result(
	payload: bytes,
	bound_bytes: BoundRepairPhotoBytes,
	expected_mime: RepairPhotoMime,
) -> RepairPhotoDecodeEvidence:
	if len(payload) != _OUTPUT.size:
		_raise(RepairPhotoDecodeProcessCode.PROTOCOL_INVALID)
	try:
		magic, version, status, code, mime_wire, width, height, frames, count, digest = _OUTPUT.unpack(
			payload
		)
	except struct.error:
		_raise(RepairPhotoDecodeProcessCode.PROTOCOL_INVALID)
	if magic != _OUTPUT_MAGIC or version != _PROTOCOL_VERSION:
		_raise(RepairPhotoDecodeProcessCode.PROTOCOL_INVALID)
	if status == _STATUS_REJECTED:
		if any((mime_wire, width, height, frames, count)) or digest != b"\x00" * 32:
			_raise(RepairPhotoDecodeProcessCode.PROTOCOL_INVALID)
		content_code = _WIRE_TO_CONTENT.get(code)
		if content_code is None:
			_raise(RepairPhotoDecodeProcessCode.PROTOCOL_INVALID)
		raise RepairPhotoDecodeProcessError(
			RepairPhotoDecodeProcessCode.CONTENT_REJECTED,
			content_code=content_code,
		) from None
	if status != _STATUS_OK or code != 0:
		_raise(RepairPhotoDecodeProcessCode.PROTOCOL_INVALID)
	detected_mime = _WIRE_TO_MIME.get(mime_wire)
	if (
		detected_mime is not expected_mime
		or count != bound_bytes.byte_count
		or digest.hex() != bound_bytes.content_sha256
	):
		_raise(RepairPhotoDecodeProcessCode.RESULT_MISMATCH)
	try:
		return RepairPhotoDecodeEvidence(
			repair_id=bound_bytes.evidence.repair_id,
			position=bound_bytes.evidence.position,
			content_sha256=bound_bytes.content_sha256,
			byte_count=count,
			detected_mime=detected_mime,
			width=width,
			height=height,
			frame_count=frames,
			decoder_complete=True,
			content_binding=RepairPhotoContentBinding.CALLER_ASSERTED,
			malware_status=RepairPhotoMalwareStatus.NOT_SCANNED,
			polyglot_status=RepairPhotoPolyglotStatus.NOT_PROVEN,
			downloadable=False,
		)
	except Exception:
		_raise(RepairPhotoDecodeProcessCode.PROTOCOL_INVALID)


def _raise(code: RepairPhotoDecodeProcessCode) -> None:
	raise RepairPhotoDecodeProcessError(code) from None
