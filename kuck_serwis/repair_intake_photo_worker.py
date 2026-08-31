"""Isolated canonical image decoder for untrusted public intake bytes."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

# ``python -I`` excludes cwd/PYTHONPATH; admit only this package's repository root.
if __package__ in (None, ""):
	_package_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
	if not _package_root or not os.path.isabs(_package_root):
		raise SystemExit(70)
	sys.path.insert(0, _package_root)

from kuck_serwis.repair_photo_content import RepairPhotoContentError, _detect_mime, _validate_container

MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_DIMENSION = 4096
MAX_PIXELS = 16_000_000
OUTPUT_LONG_EDGE = 2400
ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})


def _read_input() -> bytes:
	body = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
	if not 1 <= len(body) <= MAX_INPUT_BYTES:
		raise ValueError
	return body


def _decode(body: bytes) -> tuple[bytes, int, int]:
	detected_mime = _detect_mime(body)
	_validate_container(body, detected_mime)
	Image.MAX_IMAGE_PIXELS = MAX_PIXELS
	with warnings.catch_warnings():
		warnings.simplefilter("error", Image.DecompressionBombWarning)
		with Image.open(io.BytesIO(body)) as probe:
			if probe.format not in ALLOWED_FORMATS or getattr(probe, "n_frames", 1) != 1:
				raise ValueError
			width, height = probe.size
			if width < 1 or height < 1 or max(width, height) > MAX_DIMENSION or width * height > MAX_PIXELS:
				raise ValueError
			probe.verify()
		with Image.open(io.BytesIO(body)) as source:
			if source.format not in ALLOWED_FORMATS or getattr(source, "n_frames", 1) != 1:
				raise ValueError
			source.load()
			image = ImageOps.exif_transpose(source)
			image.thumbnail((OUTPUT_LONG_EDGE, OUTPUT_LONG_EDGE), Image.Resampling.LANCZOS)
			if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
				alpha = image.convert("RGBA")
				background = Image.new("RGB", alpha.size, "white")
				background.paste(alpha, mask=alpha.getchannel("A"))
				image = background
			else:
				image = image.convert("RGB")
			for quality in (86, 78, 70):
				output = io.BytesIO()
				image.save(output, "JPEG", quality=quality, optimize=True, progressive=False)
				encoded = output.getvalue()
				if len(encoded) <= MAX_OUTPUT_BYTES:
					return encoded, image.width, image.height
		raise ValueError


def main() -> int:
	try:
		encoded, width, height = _decode(_read_input())
	except (
		ValueError,
		OSError,
		UnidentifiedImageError,
		Image.DecompressionBombError,
		RepairPhotoContentError,
	):
		return 65
	meta = json.dumps(
		{"version": 1, "width": width, "height": height, "sha256": hashlib.sha256(encoded).hexdigest()},
		separators=(",", ":"),
		sort_keys=True,
	).encode("ascii")
	sys.stdout.buffer.write(meta + b"\n" + encoded)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
