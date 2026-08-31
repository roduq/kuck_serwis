"""Bounded, fresh-process normalization for public repair intake photos."""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from kuck_serwis.repair_photo_content import RepairPhotoMime, _validate_container

MAX_PHOTO_COUNT = 3
MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_REQUEST_BYTES = 16 * 1024 * 1024
WORKER_TIMEOUT_SECONDS = 8.0


class RepairIntakePhotoError(ValueError):
	"""Code-only rejection safe to map to the public validation response."""


@dataclass(frozen=True, slots=True)
class NormalizedIntakePhoto:
	body: bytes
	sha256: str
	width: int
	height: int


def normalize_intake_photo(body: object) -> NormalizedIntakePhoto:
	if type(body) is not bytes or not 1 <= len(body) <= MAX_INPUT_BYTES:
		raise RepairIntakePhotoError("REPAIR_INTAKE_PHOTO_INVALID")
	worker = Path(__file__).with_name("repair_intake_photo_worker.py")
	try:
		metadata = worker.lstat()
		resolved = worker.resolve(strict=True)
		if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
			raise OSError
		if resolved.parent != Path(__file__).resolve(strict=True).parent:
			raise OSError
		process = subprocess.run(
			(sys.executable, "-I", str(resolved)),
			input=body,
			stdout=subprocess.PIPE,
			stderr=subprocess.DEVNULL,
			timeout=WORKER_TIMEOUT_SECONDS,
			check=False,
			env={},
		)
	except (OSError, subprocess.SubprocessError):
		raise RepairIntakePhotoError("REPAIR_INTAKE_PHOTO_INVALID") from None
	if process.returncode != 0 or len(process.stdout) > MAX_OUTPUT_BYTES + 512:
		raise RepairIntakePhotoError("REPAIR_INTAKE_PHOTO_INVALID")
	try:
		header, normalized = process.stdout.split(b"\n", 1)
		meta = json.loads(header.decode("ascii"))
	except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
		raise RepairIntakePhotoError("REPAIR_INTAKE_PHOTO_INVALID") from None
	if (
		type(meta) is not dict
		or meta.get("version") != 1
		or type(meta.get("width")) is not int
		or type(meta.get("height")) is not int
		or type(meta.get("sha256")) is not str
		or not 1 <= meta["width"] <= 2400
		or not 1 <= meta["height"] <= 2400
		or not 1 <= len(normalized) <= MAX_OUTPUT_BYTES
		or not normalized.startswith(b"\xff\xd8\xff")
		or not normalized.endswith(b"\xff\xd9")
		or hashlib.sha256(normalized).hexdigest() != meta["sha256"]
	):
		raise RepairIntakePhotoError("REPAIR_INTAKE_PHOTO_INVALID")
	return NormalizedIntakePhoto(
		body=normalized,
		sha256=meta["sha256"],
		width=meta["width"],
		height=meta["height"],
	)


def bind_normalized_photo_to_repair(body: object, binding: object) -> bytes:
	"""Add one non-sensitive server binding so Frappe cannot deduplicate repair evidence URLs."""

	if (
		type(body) is not bytes
		or type(binding) is not str
		or len(binding) != 64
		or any(char not in "0123456789abcdef" for char in binding)
	):
		raise RepairIntakePhotoError("REPAIR_INTAKE_PHOTO_INVALID")
	try:
		_validate_container(body, RepairPhotoMime.JPEG)
	except ValueError:
		raise RepairIntakePhotoError("REPAIR_INTAKE_PHOTO_INVALID") from None
	comment = f"KUCK_REPAIR_V1:{binding}".encode("ascii")
	segment = b"\xff\xfe" + (len(comment) + 2).to_bytes(2, "big") + comment
	bound = body[:2] + segment + body[2:]
	try:
		_validate_container(bound, RepairPhotoMime.JPEG)
	except ValueError:
		raise RepairIntakePhotoError("REPAIR_INTAKE_PHOTO_INVALID") from None
	return bound


def normalize_uploaded_photos(files: object) -> tuple[NormalizedIntakePhoto, ...]:
	if type(files) not in (tuple, list) or len(files) > MAX_PHOTO_COUNT:
		raise RepairIntakePhotoError("REPAIR_INTAKE_PHOTO_INVALID")
	result = []
	for upload in files:
		stream = getattr(upload, "stream", upload)
		if not hasattr(stream, "read"):
			raise RepairIntakePhotoError("REPAIR_INTAKE_PHOTO_INVALID")
		body = stream.read(MAX_INPUT_BYTES + 1)
		if type(body) is not bytes:
			body = bytes(body)
		result.append(normalize_intake_photo(body))
	if len({photo.sha256 for photo in result}) != len(result):
		raise RepairIntakePhotoError("REPAIR_INTAKE_PHOTO_DUPLICATE")
	return tuple(result)


def media_fingerprint(photos: tuple[NormalizedIntakePhoto, ...]) -> str:
	manifest = "\0".join(photo.sha256 for photo in photos)
	return hashlib.sha256(f"kuck.repair-intake.photos.v1\0{manifest}".encode()).hexdigest()
