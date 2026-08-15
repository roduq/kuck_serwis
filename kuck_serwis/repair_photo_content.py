"""Pure structural inspection of caller-supplied repair photo bytes.

This dark primitive proves properties of the exact ``bytes`` supplied by its
caller.  It does not read a Frappe File, prove attachment binding, scan for
malware or polyglots, authorize a download, or make FILE-01 pass.
"""

from __future__ import annotations

import hashlib
import warnings
import zlib
from dataclasses import dataclass, field
from enum import StrEnum
from io import BytesIO
from typing import Literal

from PIL import Image, ImageFile

from kuck_serwis.repair_photo_metadata import ScopedRepairPhotoEvidence

MAX_REPAIR_PHOTO_BYTES = 10 * 1024 * 1024
MAX_REPAIR_PHOTO_DIMENSION = 8192
MAX_REPAIR_PHOTO_PIXELS = 40_000_000
MAX_REPAIR_PHOTO_FRAMES = 1000
MAX_CONTAINER_CHUNKS = 10_000


class RepairPhotoMime(StrEnum):
	JPEG = "image/jpeg"
	PNG = "image/png"
	WEBP = "image/webp"


class RepairPhotoContentBinding(StrEnum):
	CALLER_ASSERTED = "CALLER_ASSERTED"


class RepairPhotoMalwareStatus(StrEnum):
	NOT_SCANNED = "NOT_SCANNED"


class RepairPhotoPolyglotStatus(StrEnum):
	NOT_PROVEN = "NOT_PROVEN"


class RepairPhotoContentCode(StrEnum):
	INVALID_INPUT = "INVALID_INPUT"
	INVALID_EVIDENCE = "INVALID_EVIDENCE"
	EMPTY_BODY = "EMPTY_BODY"
	BODY_TOO_LARGE = "BODY_TOO_LARGE"
	MAGIC_MISMATCH = "MAGIC_MISMATCH"
	MIME_MISMATCH = "MIME_MISMATCH"
	CONTAINER_INVALID = "CONTAINER_INVALID"
	UNSAFE_DECODER_CONFIGURATION = "UNSAFE_DECODER_CONFIGURATION"
	DECODE_FAILED = "DECODE_FAILED"
	DIMENSIONS_INVALID = "DIMENSIONS_INVALID"
	PIXEL_LIMIT_EXCEEDED = "PIXEL_LIMIT_EXCEEDED"
	FRAME_LIMIT_EXCEEDED = "FRAME_LIMIT_EXCEEDED"


class RepairPhotoContentError(ValueError):
	"""Stable code-only failure without body, hash, path, or repair identity."""

	def __init__(self, code: RepairPhotoContentCode) -> None:
		if type(code) is not RepairPhotoContentCode:
			raise TypeError("INVALID_ERROR_CODE")
		self.code = code
		super().__init__(code.value)

	def __repr__(self) -> str:
		return f"RepairPhotoContentError(code={self.code.value!r})"


@dataclass(frozen=True, slots=True, repr=False)
class RepairPhotoDecodeEvidence:
	"""Structural facts about supplied bytes, never an accepted-file proof."""

	repair_id: str = field(repr=False)
	position: int
	content_sha256: str = field(repr=False)
	byte_count: int
	detected_mime: RepairPhotoMime
	width: int
	height: int
	frame_count: int
	decoder_complete: Literal[True]
	content_binding: RepairPhotoContentBinding
	malware_status: RepairPhotoMalwareStatus
	polyglot_status: RepairPhotoPolyglotStatus
	downloadable: Literal[False]

	def __post_init__(self) -> None:
		_revalidate_result_fields(self)

	def __repr__(self) -> str:
		return (
			"RepairPhotoDecodeEvidence(<redacted>, "
			f"position={self.position!r}, byte_count={self.byte_count!r}, "
			f"detected_mime={self.detected_mime.value!r}, width={self.width!r}, "
			f"height={self.height!r}, frame_count={self.frame_count!r}, "
			"content_binding='CALLER_ASSERTED', malware_status='NOT_SCANNED', "
			"polyglot_status='NOT_PROVEN', downloadable=False)"
		)


_PIL_FORMAT = {
	RepairPhotoMime.JPEG: "JPEG",
	RepairPhotoMime.PNG: "PNG",
	RepairPhotoMime.WEBP: "WEBP",
}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def inspect_supplied_repair_photo_bytes(
	*,
	evidence: ScopedRepairPhotoEvidence,
	body: bytes,
	expected_mime: RepairPhotoMime,
) -> RepairPhotoDecodeEvidence:
	"""Inspect bounded caller-supplied bytes without performing any I/O."""

	validated = _revalidate_scoped_evidence(evidence)
	if type(body) is not bytes:
		_raise(RepairPhotoContentCode.INVALID_INPUT)
	if not body:
		_raise(RepairPhotoContentCode.EMPTY_BODY)
	if len(body) > MAX_REPAIR_PHOTO_BYTES:
		_raise(RepairPhotoContentCode.BODY_TOO_LARGE)
	if type(expected_mime) is not RepairPhotoMime:
		_raise(RepairPhotoContentCode.INVALID_INPUT)

	detected_mime = _detect_mime(body)
	if detected_mime is not expected_mime:
		_raise(RepairPhotoContentCode.MIME_MISMATCH)
	_validate_container(body, detected_mime)
	width, height, frame_count = _decode_all_frames(body, detected_mime)
	return RepairPhotoDecodeEvidence(
		repair_id=validated.repair_id,
		position=validated.position,
		content_sha256=hashlib.sha256(body).hexdigest(),
		byte_count=len(body),
		detected_mime=detected_mime,
		width=width,
		height=height,
		frame_count=frame_count,
		decoder_complete=True,
		content_binding=RepairPhotoContentBinding.CALLER_ASSERTED,
		malware_status=RepairPhotoMalwareStatus.NOT_SCANNED,
		polyglot_status=RepairPhotoPolyglotStatus.NOT_PROVEN,
		downloadable=False,
	)


def _revalidate_scoped_evidence(value: object) -> ScopedRepairPhotoEvidence:
	if type(value) is not ScopedRepairPhotoEvidence:
		_raise(RepairPhotoContentCode.INVALID_EVIDENCE)
	try:
		result = ScopedRepairPhotoEvidence(
			repair_id=value.repair_id,
			position=value.position,
			is_private=value.is_private,
			exact_attachment=value.exact_attachment,
			metadata_only=value.metadata_only,
		)
	except Exception:
		_raise(RepairPhotoContentCode.INVALID_EVIDENCE)
	if (
		result.is_private is not True
		or result.exact_attachment is not True
		or result.metadata_only is not True
	):
		_raise(RepairPhotoContentCode.INVALID_EVIDENCE)
	return result


def _detect_mime(body: bytes) -> RepairPhotoMime:
	if body.startswith(b"\xff\xd8\xff"):
		return RepairPhotoMime.JPEG
	if body.startswith(_PNG_SIGNATURE):
		return RepairPhotoMime.PNG
	if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
		return RepairPhotoMime.WEBP
	_raise(RepairPhotoContentCode.MAGIC_MISMATCH)


def _validate_container(body: bytes, mime: RepairPhotoMime) -> None:
	if mime is RepairPhotoMime.JPEG:
		_validate_jpeg(body)
	elif mime is RepairPhotoMime.PNG:
		_validate_png(body)
	else:
		_validate_webp(body)


def _validate_jpeg(body: bytes) -> None:
	if len(body) < 4 or body[:2] != b"\xff\xd8":
		_raise(RepairPhotoContentCode.CONTAINER_INVALID)
	offset = 2
	marker_count = 0
	seen_scan = False
	while offset < len(body):
		marker_count += 1
		if marker_count > MAX_CONTAINER_CHUNKS or body[offset] != 0xFF:
			_raise(RepairPhotoContentCode.CONTAINER_INVALID)
		marker_start = offset
		while offset < len(body) and body[offset] == 0xFF:
			offset += 1
		if offset >= len(body):
			_raise(RepairPhotoContentCode.CONTAINER_INVALID)
		marker = body[offset]
		offset += 1
		if marker == 0xD9:
			if not seen_scan or offset != len(body):
				_raise(RepairPhotoContentCode.CONTAINER_INVALID)
			return
		if marker in {0x00, 0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
			_raise(RepairPhotoContentCode.CONTAINER_INVALID)
		if offset + 2 > len(body):
			_raise(RepairPhotoContentCode.CONTAINER_INVALID)
		segment_length = int.from_bytes(body[offset : offset + 2], "big")
		if segment_length < 2 or offset + segment_length > len(body):
			_raise(RepairPhotoContentCode.CONTAINER_INVALID)
		offset += segment_length
		if marker == 0xDA:
			seen_scan = True
			offset = _jpeg_marker_after_scan(body, offset)
			if offset <= marker_start:
				_raise(RepairPhotoContentCode.CONTAINER_INVALID)
	_raise(RepairPhotoContentCode.CONTAINER_INVALID)


def _jpeg_marker_after_scan(body: bytes, offset: int) -> int:
	while offset < len(body):
		if body[offset] != 0xFF:
			offset += 1
			continue
		marker_start = offset
		while offset < len(body) and body[offset] == 0xFF:
			offset += 1
		if offset >= len(body):
			_raise(RepairPhotoContentCode.CONTAINER_INVALID)
		marker = body[offset]
		if marker == 0x00 or 0xD0 <= marker <= 0xD7:
			offset += 1
			continue
		return marker_start
	_raise(RepairPhotoContentCode.CONTAINER_INVALID)


def _validate_png(body: bytes) -> None:
	offset = len(_PNG_SIGNATURE)
	chunk_count = 0
	seen_ihdr = False
	seen_idat = False
	seen_iend = False
	while offset < len(body):
		chunk_count += 1
		if chunk_count > MAX_CONTAINER_CHUNKS or offset + 12 > len(body):
			_raise(RepairPhotoContentCode.CONTAINER_INVALID)
		length = int.from_bytes(body[offset : offset + 4], "big")
		chunk_type = body[offset + 4 : offset + 8]
		data_end = offset + 8 + length
		chunk_end = data_end + 4
		if data_end < offset + 8 or chunk_end > len(body):
			_raise(RepairPhotoContentCode.CONTAINER_INVALID)
		stored_crc = int.from_bytes(body[data_end:chunk_end], "big")
		actual_crc = zlib.crc32(chunk_type + body[offset + 8 : data_end]) & 0xFFFFFFFF
		if stored_crc != actual_crc:
			_raise(RepairPhotoContentCode.CONTAINER_INVALID)
		if chunk_type == b"IHDR":
			if chunk_count != 1 or seen_ihdr or length != 13:
				_raise(RepairPhotoContentCode.CONTAINER_INVALID)
			seen_ihdr = True
		elif not seen_ihdr:
			_raise(RepairPhotoContentCode.CONTAINER_INVALID)
		if chunk_type == b"IDAT":
			seen_idat = True
		if chunk_type == b"IEND":
			if seen_iend or length != 0 or not seen_idat or chunk_end != len(body):
				_raise(RepairPhotoContentCode.CONTAINER_INVALID)
			seen_iend = True
		offset = chunk_end
	if not (seen_ihdr and seen_idat and seen_iend) or offset != len(body):
		_raise(RepairPhotoContentCode.CONTAINER_INVALID)


def _validate_webp(body: bytes) -> None:
	if len(body) < 20 or int.from_bytes(body[4:8], "little") != len(body) - 8:
		_raise(RepairPhotoContentCode.CONTAINER_INVALID)
	offset = 12
	chunk_count = 0
	seen_image_data = False
	while offset < len(body):
		chunk_count += 1
		if chunk_count > MAX_CONTAINER_CHUNKS or offset + 8 > len(body):
			_raise(RepairPhotoContentCode.CONTAINER_INVALID)
		chunk_type = body[offset : offset + 4]
		length = int.from_bytes(body[offset + 4 : offset + 8], "little")
		data_end = offset + 8 + length
		chunk_end = data_end + (length & 1)
		if data_end < offset + 8 or chunk_end > len(body):
			_raise(RepairPhotoContentCode.CONTAINER_INVALID)
		if length & 1 and body[data_end] != 0:
			_raise(RepairPhotoContentCode.CONTAINER_INVALID)
		if chunk_type in {b"VP8 ", b"VP8L", b"ANMF"}:
			seen_image_data = True
		offset = chunk_end
	if not seen_image_data or offset != len(body):
		_raise(RepairPhotoContentCode.CONTAINER_INVALID)


def _decode_all_frames(body: bytes, mime: RepairPhotoMime) -> tuple[int, int, int]:
	# Frappe core may globally enable truncated-image loading.  Changing that
	# process-global flag here would be racy, so this primitive becomes unavailable
	# rather than overstating decoder completeness.
	if ImageFile.LOAD_TRUNCATED_IMAGES is not False:
		_raise(RepairPhotoContentCode.UNSAFE_DECODER_CONFIGURATION)
	try:
		with warnings.catch_warnings():
			warnings.simplefilter("error", Image.DecompressionBombWarning)
			with Image.open(BytesIO(body), formats=[_PIL_FORMAT[mime]]) as probe:
				if probe.format != _PIL_FORMAT[mime]:
					_raise(RepairPhotoContentCode.MIME_MISMATCH)
				probe.verify()
			with Image.open(BytesIO(body), formats=[_PIL_FORMAT[mime]]) as image:
				if image.format != _PIL_FORMAT[mime]:
					_raise(RepairPhotoContentCode.MIME_MISMATCH)
				width, height = image.size
				_validate_dimensions(width, height)
				frame_count = getattr(image, "n_frames", 1)
				if type(frame_count) is not int or frame_count < 1:
					_raise(RepairPhotoContentCode.DECODE_FAILED)
				if frame_count > MAX_REPAIR_PHOTO_FRAMES:
					_raise(RepairPhotoContentCode.FRAME_LIMIT_EXCEEDED)
				for index in range(frame_count):
					image.seek(index)
					_validate_dimensions(*image.size)
					image.load()
				return width, height, frame_count
	except RepairPhotoContentError:
		raise
	except Exception:
		_raise(RepairPhotoContentCode.DECODE_FAILED)


def _validate_dimensions(width: object, height: object) -> None:
	if type(width) is not int or type(height) is not int or width < 1 or height < 1:
		_raise(RepairPhotoContentCode.DIMENSIONS_INVALID)
	if width > MAX_REPAIR_PHOTO_DIMENSION or height > MAX_REPAIR_PHOTO_DIMENSION:
		_raise(RepairPhotoContentCode.DIMENSIONS_INVALID)
	if width * height > MAX_REPAIR_PHOTO_PIXELS:
		_raise(RepairPhotoContentCode.PIXEL_LIMIT_EXCEEDED)


def _revalidate_result_fields(value: RepairPhotoDecodeEvidence) -> None:
	if (
		type(value.repair_id) is not str
		or type(value.position) is not int
		or type(value.content_sha256) is not str
		or len(value.content_sha256) != 64
		or any(char not in "0123456789abcdef" for char in value.content_sha256)
		or type(value.byte_count) is not int
		or not 1 <= value.byte_count <= MAX_REPAIR_PHOTO_BYTES
		or type(value.detected_mime) is not RepairPhotoMime
		or type(value.width) is not int
		or type(value.height) is not int
		or type(value.frame_count) is not int
		or value.decoder_complete is not True
		or value.content_binding is not RepairPhotoContentBinding.CALLER_ASSERTED
		or value.malware_status is not RepairPhotoMalwareStatus.NOT_SCANNED
		or value.polyglot_status is not RepairPhotoPolyglotStatus.NOT_PROVEN
		or value.downloadable is not False
	):
		_raise(RepairPhotoContentCode.INVALID_INPUT)
	try:
		ScopedRepairPhotoEvidence(
			repair_id=value.repair_id,
			position=value.position,
			is_private=True,
			exact_attachment=True,
			metadata_only=True,
		)
	except Exception:
		_raise(RepairPhotoContentCode.INVALID_INPUT)
	_validate_dimensions(value.width, value.height)
	if not 1 <= value.frame_count <= MAX_REPAIR_PHOTO_FRAMES:
		_raise(RepairPhotoContentCode.INVALID_INPUT)


def _raise(code: RepairPhotoContentCode) -> None:
	raise RepairPhotoContentError(code) from None
