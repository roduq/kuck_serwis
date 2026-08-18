import ast
import hashlib
import inspect
import unittest
import zlib
from dataclasses import FrozenInstanceError
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageFile

import kuck_serwis.repair_photo_content as content_module
from kuck_serwis.repair_photo_content import (
	RepairPhotoContentBinding,
	RepairPhotoContentCode,
	RepairPhotoContentError,
	RepairPhotoDecodeEvidence,
	RepairPhotoMalwareStatus,
	RepairPhotoMime,
	RepairPhotoPolyglotStatus,
	inspect_supplied_repair_photo_bytes,
)
from kuck_serwis.repair_photo_metadata import ScopedRepairPhotoEvidence

REPAIR_ID = "rpr_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def scoped_evidence(**overrides):
	values = {
		"repair_id": REPAIR_ID,
		"position": 1,
		"is_private": True,
		"exact_attachment": True,
		"metadata_only": True,
	}
	values.update(overrides)
	return ScopedRepairPhotoEvidence(**values)


def image_bytes(format_name, *, size=(3, 2), frames=1):
	output = BytesIO()
	images = [Image.new("RGB", size, (index * 17, 40, 80)) for index in range(frames)]
	kwargs = {}
	if frames > 1:
		kwargs = {"save_all": True, "append_images": images[1:], "duration": 10, "loop": 0}
	images[0].save(output, format=format_name, **kwargs)
	return output.getvalue()


def progressive_jpeg_bytes():
	output = BytesIO()
	Image.new("RGB", (3, 2), (20, 40, 80)).save(output, format="JPEG", progressive=True)
	return output.getvalue()


def png_chunks(body):
	offset = 8
	result = []
	while offset < len(body):
		length = int.from_bytes(body[offset : offset + 4], "big")
		chunk_type = body[offset + 4 : offset + 8]
		data = body[offset + 8 : offset + 8 + length]
		result.append((chunk_type, data))
		offset += 12 + length
	return result


def build_png(chunks):
	result = bytearray(b"\x89PNG\r\n\x1a\n")
	for chunk_type, data in chunks:
		result.extend(len(data).to_bytes(4, "big"))
		result.extend(chunk_type)
		result.extend(data)
		result.extend((zlib.crc32(chunk_type + data) & 0xFFFFFFFF).to_bytes(4, "big"))
	return bytes(result)


class FatalProbe(BaseException):
	pass


class TestRepairPhotoContent(unittest.TestCase):
	def setUp(self):
		# Frappe intentionally enables Pillow's process-global truncated-image
		# mode.  This pure decoder must be tested under its required safe value
		# without leaking that test-only value into the surrounding suite.
		decoder_configuration = patch.object(ImageFile, "LOAD_TRUNCATED_IMAGES", False)
		decoder_configuration.start()
		self.addCleanup(decoder_configuration.stop)

	def assert_code(self, code, call):
		with self.assertRaises(RepairPhotoContentError) as raised:
			call()
		self.assertIs(raised.exception.code, code)
		self.assertEqual(str(raised.exception), code.value)
		self.assertEqual(repr(raised.exception), f"RepairPhotoContentError(code={code.value!r})")

	def inspect(self, body, mime, *, evidence=None):
		return inspect_supplied_repair_photo_bytes(
			evidence=evidence or scoped_evidence(), body=body, expected_mime=mime
		)

	def test_jpeg_png_and_webp_are_fully_decoded(self):
		for format_name, mime in (
			("JPEG", RepairPhotoMime.JPEG),
			("PNG", RepairPhotoMime.PNG),
			("WEBP", RepairPhotoMime.WEBP),
		):
			with self.subTest(format_name=format_name):
				body = image_bytes(format_name)
				result = self.inspect(body, mime)
				self.assertEqual((result.width, result.height, result.frame_count), (3, 2, 1))
				self.assertEqual(result.content_sha256, hashlib.sha256(body).hexdigest())
				self.assertEqual(result.byte_count, len(body))
				self.assertIs(result.detected_mime, mime)
				self.assertIs(result.decoder_complete, True)

	def test_result_states_are_honest_and_non_downloadable(self):
		result = self.inspect(image_bytes("PNG"), RepairPhotoMime.PNG)
		self.assertIs(result.content_binding, RepairPhotoContentBinding.CALLER_ASSERTED)
		self.assertIs(result.malware_status, RepairPhotoMalwareStatus.NOT_SCANNED)
		self.assertIs(result.polyglot_status, RepairPhotoPolyglotStatus.NOT_PROVEN)
		self.assertIs(result.downloadable, False)

	def test_progressive_jpeg_with_multiple_scans_is_complete(self):
		result = self.inspect(progressive_jpeg_bytes(), RepairPhotoMime.JPEG)
		self.assertEqual((result.width, result.height, result.frame_count), (3, 2, 1))

	def test_result_is_deterministic_frozen_and_redacted(self):
		body = image_bytes("PNG")
		first = self.inspect(body, RepairPhotoMime.PNG)
		second = self.inspect(body, RepairPhotoMime.PNG)
		self.assertEqual(first, second)
		text = repr(first)
		for marker in (REPAIR_ID, first.content_sha256, repr(body[:16])):
			self.assertNotIn(marker, text)
		with self.assertRaises(FrozenInstanceError):
			first.downloadable = True

	def test_exact_bytes_only_and_empty_body(self):
		for value in (bytearray(b"x"), memoryview(b"x"), "x", None):
			with self.subTest(value=type(value)):
				self.assert_code(
					RepairPhotoContentCode.INVALID_INPUT,
					lambda value=value: self.inspect(value, RepairPhotoMime.PNG),
				)
		self.assert_code(RepairPhotoContentCode.EMPTY_BODY, lambda: self.inspect(b"", RepairPhotoMime.PNG))

	def test_exact_body_size_boundary_and_over(self):
		body = image_bytes("PNG")
		with patch.object(content_module, "MAX_REPAIR_PHOTO_BYTES", len(body)):
			self.assertEqual(self.inspect(body, RepairPhotoMime.PNG).byte_count, len(body))
		with patch.object(content_module, "MAX_REPAIR_PHOTO_BYTES", len(body) - 1):
			with patch.object(content_module.Image, "open", side_effect=AssertionError("DECODER_CALLED")):
				self.assert_code(
					RepairPhotoContentCode.BODY_TOO_LARGE,
					lambda: self.inspect(body, RepairPhotoMime.PNG),
				)

	def test_expected_mime_requires_exact_enum(self):
		body = image_bytes("PNG")
		for value in ("image/png", str.__new__(type("Mime", (str,), {}), "image/png"), None):
			self.assert_code(
				RepairPhotoContentCode.INVALID_INPUT,
				lambda value=value: self.inspect(body, value),
			)

	def test_magic_and_expected_mime_mismatch_fail_closed(self):
		self.assert_code(
			RepairPhotoContentCode.MAGIC_MISMATCH,
			lambda: self.inspect(b"<svg></svg>", RepairPhotoMime.PNG),
		)
		self.assert_code(
			RepairPhotoContentCode.MIME_MISMATCH,
			lambda: self.inspect(image_bytes("JPEG"), RepairPhotoMime.PNG),
		)

	def test_truncated_containers_are_rejected_before_decode(self):
		for body, mime in (
			(image_bytes("JPEG")[:-1], RepairPhotoMime.JPEG),
			(image_bytes("PNG")[:-1], RepairPhotoMime.PNG),
			(image_bytes("WEBP")[:-1], RepairPhotoMime.WEBP),
		):
			with self.subTest(mime=mime):
				with patch.object(content_module.Image, "open", side_effect=AssertionError("DECODER_CALLED")):
					self.assert_code(
						RepairPhotoContentCode.CONTAINER_INVALID, lambda: self.inspect(body, mime)
					)

	def test_trailing_bytes_are_rejected_for_every_container(self):
		for format_name, mime in (
			("JPEG", RepairPhotoMime.JPEG),
			("PNG", RepairPhotoMime.PNG),
			("WEBP", RepairPhotoMime.WEBP),
		):
			with self.subTest(format_name=format_name):
				self.assert_code(
					RepairPhotoContentCode.CONTAINER_INVALID,
					lambda format_name=format_name, mime=mime: self.inspect(
						image_bytes(format_name) + b"TRAILING", mime
					),
				)
		jpeg = image_bytes("JPEG")
		self.assert_code(
			RepairPhotoContentCode.CONTAINER_INVALID,
			lambda: self.inspect(jpeg + b"EMBEDDED_TRAIL" + b"\xff\xd9", RepairPhotoMime.JPEG),
		)

	def test_png_crc_duplicate_ihdr_missing_idat_and_early_iend_are_rejected(self):
		body = image_bytes("PNG")
		bad_crc = bytearray(body)
		bad_crc[29] ^= 1
		chunks = png_chunks(body)
		cases = (
			bytes(bad_crc),
			build_png((chunks[0], chunks[0], *chunks[1:])),
			build_png(tuple(chunk for chunk in chunks if chunk[0] != b"IDAT")),
			build_png((chunks[0], (b"IEND", b""), *chunks[1:])),
		)
		for value in cases:
			with self.subTest(size=len(value)):
				self.assert_code(
					RepairPhotoContentCode.CONTAINER_INVALID,
					lambda value=value: self.inspect(value, RepairPhotoMime.PNG),
				)

	def test_png_chunk_count_is_bounded(self):
		body = image_bytes("PNG")
		with patch.object(content_module, "MAX_CONTAINER_CHUNKS", 2):
			self.assert_code(
				RepairPhotoContentCode.CONTAINER_INVALID,
				lambda: self.inspect(body, RepairPhotoMime.PNG),
			)

	def test_webp_riff_length_and_missing_image_payload_are_rejected(self):
		body = image_bytes("WEBP")
		bad_length = body[:4] + (len(body) - 9).to_bytes(4, "little") + body[8:]
		empty = b"RIFF" + (4).to_bytes(4, "little") + b"WEBP"
		for value in (bad_length, empty):
			self.assert_code(
				RepairPhotoContentCode.CONTAINER_INVALID,
				lambda value=value: self.inspect(value, RepairPhotoMime.WEBP),
			)
		odd_chunk = b"XMP " + (1).to_bytes(4, "little") + b"x" + b"!"
		body_with_bad_padding = body[:4] + (len(body) + len(odd_chunk) - 8).to_bytes(4, "little") + body[8:]
		body_with_bad_padding += odd_chunk
		self.assert_code(
			RepairPhotoContentCode.CONTAINER_INVALID,
			lambda: self.inspect(body_with_bad_padding, RepairPhotoMime.WEBP),
		)

	def test_corrupt_but_container_complete_image_is_decode_failed(self):
		chunks = png_chunks(image_bytes("PNG"))
		corrupt = build_png(
			tuple((kind, bytes(len(data))) if kind == b"IDAT" else (kind, data) for kind, data in chunks)
		)
		self.assert_code(
			RepairPhotoContentCode.DECODE_FAILED,
			lambda: self.inspect(corrupt, RepairPhotoMime.PNG),
		)

	def test_dimension_and_pixel_exact_boundaries(self):
		body = image_bytes("PNG", size=(4, 3))
		with (
			patch.object(content_module, "MAX_REPAIR_PHOTO_DIMENSION", 4),
			patch.object(content_module, "MAX_REPAIR_PHOTO_PIXELS", 12),
		):
			self.assertEqual((self.inspect(body, RepairPhotoMime.PNG).width), 4)
		with patch.object(content_module, "MAX_REPAIR_PHOTO_DIMENSION", 3):
			self.assert_code(
				RepairPhotoContentCode.DIMENSIONS_INVALID,
				lambda: self.inspect(body, RepairPhotoMime.PNG),
			)
		with patch.object(content_module, "MAX_REPAIR_PHOTO_PIXELS", 11):
			self.assert_code(
				RepairPhotoContentCode.PIXEL_LIMIT_EXCEEDED,
				lambda: self.inspect(body, RepairPhotoMime.PNG),
			)

	def test_every_animated_webp_frame_is_loaded_and_frame_limit_is_exact(self):
		body = image_bytes("WEBP", frames=2)
		with patch.object(content_module, "MAX_REPAIR_PHOTO_FRAMES", 2):
			self.assertEqual(self.inspect(body, RepairPhotoMime.WEBP).frame_count, 2)
		with patch.object(content_module, "MAX_REPAIR_PHOTO_FRAMES", 1):
			self.assert_code(
				RepairPhotoContentCode.FRAME_LIMIT_EXCEEDED,
				lambda: self.inspect(body, RepairPhotoMime.WEBP),
			)

	def test_gif_svg_pdf_and_avif_like_inputs_are_not_accepted(self):
		gif = image_bytes("GIF")
		for body in (gif, b"<svg/>", b"%PDF-1.7", b"\x00\x00\x00\x18ftypavif"):
			self.assert_code(
				RepairPhotoContentCode.MAGIC_MISMATCH,
				lambda body=body: self.inspect(body, RepairPhotoMime.PNG),
			)

	def test_evidence_is_exact_defensive_private_and_attached(self):
		body = image_bytes("PNG")
		for value in (
			object(),
			object.__new__(ScopedRepairPhotoEvidence),
			scoped_evidence(is_private=False),
			scoped_evidence(exact_attachment=False),
		):
			with self.subTest(value=type(value)):
				self.assert_code(
					RepairPhotoContentCode.INVALID_EVIDENCE,
					lambda value=value: self.inspect(body, RepairPhotoMime.PNG, evidence=value),
				)

	def test_mutated_frozen_evidence_is_reconstructed(self):
		value = scoped_evidence()
		object.__setattr__(value, "is_private", 1)
		self.assert_code(
			RepairPhotoContentCode.INVALID_EVIDENCE,
			lambda: self.inspect(image_bytes("PNG"), RepairPhotoMime.PNG, evidence=value),
		)

	def test_result_constructor_rejects_dishonest_or_forged_fields(self):
		valid = self.inspect(image_bytes("PNG"), RepairPhotoMime.PNG)
		values = {field: getattr(valid, field) for field in valid.__dataclass_fields__}
		for field, value in (
			("content_binding", "CALLER_ASSERTED"),
			("malware_status", "CLEAN"),
			("polyglot_status", "CLEAN"),
			("downloadable", 0),
			("decoder_complete", 1),
			("content_sha256", "0" * 63),
			("repair_id", "NAP-INTERNAL"),
		):
			with self.subTest(field=field):
				forged = {**values, field: value}
				self.assert_code(
					RepairPhotoContentCode.INVALID_INPUT,
					lambda forged=forged: RepairPhotoDecodeEvidence(**forged),
				)

	def test_runtime_decoder_failure_is_code_only_and_does_not_echo(self):
		marker = "synthetic-private-path-and-person@example.test"
		with patch.object(content_module.Image, "open", side_effect=RuntimeError(marker)):
			with self.assertRaises(RepairPhotoContentError) as raised:
				self.inspect(image_bytes("PNG"), RepairPhotoMime.PNG)
		self.assertIs(raised.exception.code, RepairPhotoContentCode.DECODE_FAILED)
		self.assertNotIn(marker, str(raised.exception) + repr(raised.exception))

	def test_base_exception_is_not_caught(self):
		with patch.object(content_module.Image, "open", side_effect=FatalProbe):
			with self.assertRaises(FatalProbe):
				self.inspect(image_bytes("PNG"), RepairPhotoMime.PNG)

	def test_pillow_truncated_image_global_is_never_changed(self):
		before = ImageFile.LOAD_TRUNCATED_IMAGES
		try:
			ImageFile.LOAD_TRUNCATED_IMAGES = True
			self.assert_code(
				RepairPhotoContentCode.UNSAFE_DECODER_CONFIGURATION,
				lambda: self.inspect(image_bytes("PNG"), RepairPhotoMime.PNG),
			)
			self.assertIs(ImageFile.LOAD_TRUNCATED_IMAGES, True)
		finally:
			ImageFile.LOAD_TRUNCATED_IMAGES = before
		self.assertIs(ImageFile.LOAD_TRUNCATED_IMAGES, before)

	def test_module_has_no_frappe_io_network_subprocess_or_time_boundary(self):
		path = Path(inspect.getfile(inspect_supplied_repair_photo_bytes))
		tree = ast.parse(path.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if isinstance(node, (ast.Import, ast.ImportFrom)):
				names = [alias.name.split(".", 1)[0] for alias in node.names]
				self.assertTrue(
					set(names).isdisjoint(
						{"frappe", "os", "pathlib", "socket", "subprocess", "requests", "urllib", "time"}
					)
				)
			if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
				self.assertNotIn(node.func.id, {"open", "exec", "eval", "compile", "__import__"})

	def test_module_language_does_not_claim_file01_or_safe_attachment(self):
		source = Path(inspect.getfile(inspect_supplied_repair_photo_bytes)).read_text(encoding="utf-8")
		for forbidden in (
			"file-01 passed",
			"file-01: pass",
			"malware-free",
			"safe attachment",
			"attachment verified",
		):
			self.assertNotIn(forbidden, source.lower())
		self.assertIn("does not read a Frappe File", source)
		self.assertIn("does not", source.lower())
		self.assertIn("make FILE-01 pass", source)


if __name__ == "__main__":
	unittest.main()
