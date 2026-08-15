"""Internal fresh-interpreter worker for repair photo structural decoding."""

from __future__ import annotations

import os
import struct
import sys

# ``python -I`` deliberately excludes both cwd and PYTHONPATH.  Executing this
# exact regular file proves its provenance; add only its own repository package
# root so sibling kuck_serwis modules resolve from the same tree.
if __package__ in (None, ""):
	_package_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
	if not _package_root or not os.path.isabs(_package_root):
		raise SystemExit(70)
	sys.path.insert(0, _package_root)

from kuck_serwis.repair_photo_content import (
	MAX_REPAIR_PHOTO_BYTES,
	RepairPhotoContentCode,
	RepairPhotoContentError,
	RepairPhotoMime,
	inspect_supplied_repair_photo_bytes,
)
from kuck_serwis.repair_photo_metadata import ScopedRepairPhotoEvidence

_INPUT_MAGIC = b"KRPDI001"
_OUTPUT_MAGIC = b"KRPDO001"
_PROTOCOL_VERSION = 1
_INPUT_HEADER = struct.Struct(">8sBBI")
_OUTPUT = struct.Struct(">8sBBBBIIII32s")
_STATUS_OK = 0
_STATUS_REJECTED = 1
_WIRE_TO_MIME = {
	1: RepairPhotoMime.JPEG,
	2: RepairPhotoMime.PNG,
	3: RepairPhotoMime.WEBP,
}
_CONTENT_TO_WIRE = {code: index for index, code in enumerate(RepairPhotoContentCode, 1)}
_SYNTHETIC_EVIDENCE = ScopedRepairPhotoEvidence(
	repair_id="rpr_00000000000000000000000000000000",
	position=1,
	is_private=True,
	exact_attachment=True,
	metadata_only=True,
)


def _read_exact(size: int) -> bytes:
	result = bytearray()
	while len(result) < size:
		chunk = sys.stdin.buffer.read(size - len(result))
		if not chunk:
			break
		result.extend(chunk)
	return bytes(result)


def _rejected(code: RepairPhotoContentCode) -> bytes:
	return _OUTPUT.pack(
		_OUTPUT_MAGIC,
		_PROTOCOL_VERSION,
		_STATUS_REJECTED,
		_CONTENT_TO_WIRE[code],
		0,
		0,
		0,
		0,
		0,
		b"\x00" * 32,
	)


def _main() -> int:
	header = _read_exact(_INPUT_HEADER.size)
	if len(header) != _INPUT_HEADER.size:
		sys.stdout.buffer.write(_rejected(RepairPhotoContentCode.INVALID_INPUT))
		return 0
	magic, version, mime_wire, body_length = _INPUT_HEADER.unpack(header)
	mime = _WIRE_TO_MIME.get(mime_wire)
	if (
		magic != _INPUT_MAGIC
		or version != _PROTOCOL_VERSION
		or mime is None
		or not 1 <= body_length <= MAX_REPAIR_PHOTO_BYTES
	):
		sys.stdout.buffer.write(_rejected(RepairPhotoContentCode.INVALID_INPUT))
		return 0
	body = _read_exact(body_length)
	if len(body) != body_length or sys.stdin.buffer.read(1):
		sys.stdout.buffer.write(_rejected(RepairPhotoContentCode.INVALID_INPUT))
		return 0
	try:
		result = inspect_supplied_repair_photo_bytes(
			evidence=_SYNTHETIC_EVIDENCE,
			body=body,
			expected_mime=mime,
		)
	except RepairPhotoContentError as error:
		if type(error) is not RepairPhotoContentError or type(error.code) is not RepairPhotoContentCode:
			return 70
		sys.stdout.buffer.write(_rejected(error.code))
		return 0
	except Exception:
		return 70
	payload = _OUTPUT.pack(
		_OUTPUT_MAGIC,
		_PROTOCOL_VERSION,
		_STATUS_OK,
		0,
		{value: key for key, value in _WIRE_TO_MIME.items()}[result.detected_mime],
		result.width,
		result.height,
		result.frame_count,
		result.byte_count,
		bytes.fromhex(result.content_sha256),
	)
	sys.stdout.buffer.write(payload)
	return 0


if __name__ == "__main__":
	raise SystemExit(_main())
