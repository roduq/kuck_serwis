from __future__ import annotations

import io
import unittest

from PIL import Image

from kuck_serwis.repair_intake_photo import (
	MAX_INPUT_BYTES,
	RepairIntakePhotoError,
	bind_normalized_photo_to_repair,
	media_fingerprint,
	normalize_intake_photo,
	normalize_uploaded_photos,
)


def _image(fmt: str, *, size=(160, 90), mode="RGB") -> bytes:
	output = io.BytesIO()
	Image.new(mode, size, (20, 40, 60, 128) if mode == "RGBA" else (20, 40, 60)).save(output, fmt)
	return output.getvalue()


class _Upload:
	def __init__(self, body: bytes):
		self.stream = io.BytesIO(body)


class TestRepairIntakePhoto(unittest.TestCase):
	def test_normalizes_jpeg_png_webp_to_metadata_free_jpeg(self):
		for fmt, mode in (("JPEG", "RGB"), ("PNG", "RGBA"), ("WEBP", "RGBA")):
			with self.subTest(fmt=fmt):
				result = normalize_intake_photo(_image(fmt, mode=mode))
				self.assertTrue(result.body.startswith(b"\xff\xd8\xff"))
				self.assertTrue(result.body.endswith(b"\xff\xd9"))
				self.assertEqual((result.width, result.height), (160, 90))
				with Image.open(io.BytesIO(result.body)) as image:
					self.assertEqual(image.format, "JPEG")
					self.assertEqual(image.getexif(), {})

	def test_rejects_non_image_trailing_polyglot_empty_and_oversize(self):
		jpeg = _image("JPEG")
		for body in (b"<svg/>", jpeg + b"<script>x</script>", b"", b"x" * (MAX_INPUT_BYTES + 1)):
			with self.subTest(size=len(body)), self.assertRaises(RepairIntakePhotoError):
				normalize_intake_photo(body)

	def test_rejects_four_or_duplicate_photos(self):
		first = _image("JPEG")
		second = _image("PNG")
		with self.assertRaises(RepairIntakePhotoError):
			normalize_uploaded_photos([_Upload(first)] * 4)
		with self.assertRaises(RepairIntakePhotoError):
			normalize_uploaded_photos([_Upload(first), _Upload(first)])
		photos = normalize_uploaded_photos([_Upload(first), _Upload(second)])
		self.assertEqual(len(photos), 2)
		self.assertEqual(media_fingerprint(photos), media_fingerprint(photos))
		self.assertNotEqual(media_fingerprint(photos), media_fingerprint(tuple(reversed(photos))))

	def test_repair_binding_is_structurally_valid_and_unique(self):
		source = normalize_intake_photo(_image("JPEG"))
		first = bind_normalized_photo_to_repair(source.body, "a" * 64)
		second = bind_normalized_photo_to_repair(source.body, "b" * 64)
		self.assertNotEqual(first, source.body)
		self.assertNotEqual(first, second)
		self.assertTrue(first.startswith(b"\xff\xd8\xff"))
		self.assertTrue(first.endswith(b"\xff\xd9"))
		with Image.open(io.BytesIO(first)) as image:
			image.load()
			self.assertEqual(image.format, "JPEG")

		def frappe_reencode(body):
			with Image.open(io.BytesIO(body)) as image:
				output = io.BytesIO()
				image.save(output, "JPEG", exif=b"")
				return output.getvalue()

		self.assertNotEqual(frappe_reencode(first), frappe_reencode(source.body))
		self.assertNotEqual(frappe_reencode(first), frappe_reencode(second))


if __name__ == "__main__":
	unittest.main()
