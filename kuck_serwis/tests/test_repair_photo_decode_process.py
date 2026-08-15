import ast
import hashlib
import inspect
import os
import struct
import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageFile

import kuck_serwis.repair_photo_decode_process as process_module
import kuck_serwis.repair_photo_storage as storage_module
from kuck_serwis.repair_photo_content import (
	RepairPhotoContentCode,
	RepairPhotoContentError,
	RepairPhotoMime,
)
from kuck_serwis.repair_photo_decode_process import (
	RepairPhotoDecodeProcessCode,
	RepairPhotoDecodeProcessError,
	inspect_bound_repair_photo_content,
)
from kuck_serwis.repair_photo_metadata import ScopedRepairPhotoEvidence
from kuck_serwis.repair_photo_storage import BoundRepairPhotoBytes, RepairPhotoStorageBinding

REPAIR_ID = "rpr_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def image_bytes(mime, *, size=(3, 2)):
	format_name = {
		RepairPhotoMime.JPEG: "JPEG",
		RepairPhotoMime.PNG: "PNG",
		RepairPhotoMime.WEBP: "WEBP",
	}[mime]
	output = BytesIO()
	Image.new("RGB", size, (20, 40, 60)).save(output, format=format_name)
	return output.getvalue()


def bound(body):
	evidence = ScopedRepairPhotoEvidence(
		repair_id=REPAIR_ID,
		position=2,
		is_private=True,
		exact_attachment=True,
		metadata_only=True,
	)
	return BoundRepairPhotoBytes(
		evidence=evidence,
		body=body,
		byte_count=len(body),
		content_sha256=hashlib.sha256(body).hexdigest(),
		storage_binding=RepairPhotoStorageBinding.LOCAL_PRIVATE_FILE,
		_seal=storage_module._RESULT_SEAL,
	)


def success_payload(photo, mime, *, digest=None, count=None, status=0, code=0):
	return process_module._OUTPUT.pack(
		process_module._OUTPUT_MAGIC,
		process_module._PROTOCOL_VERSION,
		status,
		code,
		process_module._MIME_TO_WIRE[mime],
		3,
		2,
		1,
		photo.byte_count if count is None else count,
		bytes.fromhex(photo.content_sha256) if digest is None else digest,
	)


class TestRepairPhotoDecodeProcess(unittest.TestCase):
	def assert_code(self, code, call, *, content_code=None):
		with self.assertRaises(RepairPhotoDecodeProcessError) as raised:
			call()
		self.assertIs(raised.exception.code, code)
		self.assertIs(raised.exception.content_code, content_code)
		self.assertEqual(str(raised.exception), code.value)
		self.assertEqual(
			repr(raised.exception),
			f"RepairPhotoDecodeProcessError(code={code.value!r})",
		)

	def test_real_fresh_child_decodes_jpeg_png_and_webp_with_parent_flag_true(self):
		original = ImageFile.LOAD_TRUNCATED_IMAGES
		try:
			ImageFile.LOAD_TRUNCATED_IMAGES = True
			for mime in RepairPhotoMime:
				with self.subTest(mime=mime.value):
					photo = bound(image_bytes(mime))
					result = inspect_bound_repair_photo_content(
						bound_bytes=photo,
						expected_mime=mime,
					)
					self.assertEqual((result.width, result.height, result.frame_count), (3, 2, 1))
					self.assertEqual(result.content_sha256, photo.content_sha256)
					self.assertEqual(result.byte_count, photo.byte_count)
					self.assertEqual(result.repair_id, REPAIR_ID)
					self.assertEqual(result.position, 2)
					self.assertTrue(result.decoder_complete)
					self.assertFalse(result.downloadable)
					self.assertIs(ImageFile.LOAD_TRUNCATED_IMAGES, True)
		finally:
			ImageFile.LOAD_TRUNCATED_IMAGES = original
		self.assertIs(ImageFile.LOAD_TRUNCATED_IMAGES, original)

	def test_exact_worker_file_provenance_is_independent_of_cwd_and_pythonpath(self):
		argv = process_module._worker_argv()
		expected = Path(inspect.getfile(process_module)).with_name("repair_photo_decode_worker.py").resolve()
		self.assertEqual(argv, (sys.executable, "-I", str(expected)))
		self.assertTrue(expected.is_file())
		self.assertFalse(expected.is_symlink())
		self.assertEqual(expected.parent, Path(inspect.getfile(process_module)).resolve().parent)
		self.assertNotIn("-m", argv)

	def test_real_child_rejects_invalid_content_with_allowlisted_reason(self):
		photo = bound(b"not-an-image")
		self.assert_code(
			RepairPhotoDecodeProcessCode.CONTENT_REJECTED,
			lambda: inspect_bound_repair_photo_content(
				bound_bytes=photo,
				expected_mime=RepairPhotoMime.PNG,
			),
			content_code=RepairPhotoContentCode.MAGIC_MISMATCH,
		)

	def test_exact_sealed_bound_result_is_required_and_revalidated(self):
		photo = bound(image_bytes(RepairPhotoMime.PNG))
		for invalid in (object(), None, "bound", object.__new__(BoundRepairPhotoBytes)):
			self.assert_code(
				RepairPhotoDecodeProcessCode.INVALID_BOUND_BYTES,
				lambda invalid=invalid: inspect_bound_repair_photo_content(
					bound_bytes=invalid,
					expected_mime=RepairPhotoMime.PNG,
				),
			)
		object.__setattr__(photo, "content_sha256", "0" * 64)
		self.assert_code(
			RepairPhotoDecodeProcessCode.INVALID_BOUND_BYTES,
			lambda: inspect_bound_repair_photo_content(
				bound_bytes=photo,
				expected_mime=RepairPhotoMime.PNG,
			),
		)
		forged = bound(image_bytes(RepairPhotoMime.PNG))
		object.__setattr__(forged, "_seal", object())
		self.assert_code(
			RepairPhotoDecodeProcessCode.INVALID_BOUND_BYTES,
			lambda: inspect_bound_repair_photo_content(
				bound_bytes=forged,
				expected_mime=RepairPhotoMime.PNG,
			),
		)

	def test_forged_nested_evidence_and_count_fail_before_worker(self):
		photo = bound(image_bytes(RepairPhotoMime.PNG))
		for name, value in (("byte_count", True), ("evidence", object()), ("body", b"changed")):
			with self.subTest(name=name):
				forged = bound(image_bytes(RepairPhotoMime.PNG))
				object.__setattr__(forged, name, value)
				with patch.object(process_module, "_run_worker") as worker:
					self.assert_code(
						RepairPhotoDecodeProcessCode.INVALID_BOUND_BYTES,
						lambda: inspect_bound_repair_photo_content(
							bound_bytes=forged,
							expected_mime=RepairPhotoMime.PNG,
						),
					)
					worker.assert_not_called()
		self.assertEqual(photo.evidence.repair_id, REPAIR_ID)

	def test_expected_mime_is_exact_enum_and_body_never_enters_argv(self):
		photo = bound(image_bytes(RepairPhotoMime.PNG))
		for invalid in ("image/png", None, 1):
			self.assert_code(
				RepairPhotoDecodeProcessCode.INVALID_INPUT,
				lambda invalid=invalid: inspect_bound_repair_photo_content(
					bound_bytes=photo,
					expected_mime=invalid,
				),
			)
		argv_text = " ".join(process_module._worker_argv())
		self.assertNotIn(photo.content_sha256, argv_text)
		self.assertNotIn(REPAIR_ID, argv_text)
		self.assertNotIn(repr(photo.body), argv_text)

	def test_result_and_error_repr_are_redacted_and_result_is_frozen(self):
		photo = bound(image_bytes(RepairPhotoMime.PNG))
		result = inspect_bound_repair_photo_content(
			bound_bytes=photo,
			expected_mime=RepairPhotoMime.PNG,
		)
		text = repr(result)
		for marker in (REPAIR_ID, photo.content_sha256, repr(photo.body)):
			self.assertNotIn(marker, text)
		with self.assertRaises(FrozenInstanceError):
			result.width = 9

	def test_nonzero_and_signal_return_are_worker_failed_without_echo(self):
		photo = bound(image_bytes(RepairPhotoMime.PNG))
		for returncode in (1, -9, 127):
			with patch.object(
				process_module,
				"_run_worker",
				return_value=process_module._WorkerExecution(returncode, b"private-marker"),
			):
				self.assert_code(
					RepairPhotoDecodeProcessCode.WORKER_FAILED,
					lambda: inspect_bound_repair_photo_content(
						bound_bytes=photo,
						expected_mime=RepairPhotoMime.PNG,
					),
				)

	def test_forged_execution_result_is_worker_failed(self):
		photo = bound(image_bytes(RepairPhotoMime.PNG))
		for result in (object(), SimpleNamespace(returncode=True, stdout=b"")):
			with patch.object(process_module, "_run_worker", return_value=result):
				self.assert_code(
					RepairPhotoDecodeProcessCode.WORKER_FAILED,
					lambda: inspect_bound_repair_photo_content(
						bound_bytes=photo,
						expected_mime=RepairPhotoMime.PNG,
					),
				)

	def test_protocol_rejects_short_long_bad_magic_version_status_and_code(self):
		photo = bound(image_bytes(RepairPhotoMime.PNG))
		valid = success_payload(photo, RepairPhotoMime.PNG)
		cases = (
			b"",
			valid[:-1],
			valid + b"x",
			b"BADMAGIC" + valid[8:],
			valid[:8] + bytes([2]) + valid[9:],
			success_payload(photo, RepairPhotoMime.PNG, status=2),
			success_payload(photo, RepairPhotoMime.PNG, code=1),
		)
		for payload in cases:
			with self.subTest(size=len(payload)):
				with patch.object(
					process_module,
					"_run_worker",
					return_value=process_module._WorkerExecution(0, payload),
				):
					self.assert_code(
						RepairPhotoDecodeProcessCode.PROTOCOL_INVALID,
						lambda: inspect_bound_repair_photo_content(
							bound_bytes=photo,
							expected_mime=RepairPhotoMime.PNG,
						),
					)

	def test_protocol_rejects_unknown_or_noncanonical_child_error(self):
		photo = bound(image_bytes(RepairPhotoMime.PNG))
		for code, width, digest in ((255, 0, b"\x00" * 32), (1, 1, b"\x00" * 32), (1, 0, b"x" * 32)):
			payload = process_module._OUTPUT.pack(
				process_module._OUTPUT_MAGIC,
				1,
				process_module._STATUS_REJECTED,
				code,
				0,
				width,
				0,
				0,
				0,
				digest,
			)
			with patch.object(
				process_module,
				"_run_worker",
				return_value=process_module._WorkerExecution(0, payload),
			):
				self.assert_code(
					RepairPhotoDecodeProcessCode.PROTOCOL_INVALID,
					lambda: inspect_bound_repair_photo_content(
						bound_bytes=photo,
						expected_mime=RepairPhotoMime.PNG,
					),
				)

	def test_hash_count_or_mime_mismatch_is_result_mismatch(self):
		photo = bound(image_bytes(RepairPhotoMime.PNG))
		cases = (
			success_payload(photo, RepairPhotoMime.PNG, digest=b"x" * 32),
			success_payload(photo, RepairPhotoMime.PNG, count=photo.byte_count - 1),
			success_payload(photo, RepairPhotoMime.JPEG),
		)
		for payload in cases:
			with patch.object(
				process_module,
				"_run_worker",
				return_value=process_module._WorkerExecution(0, payload),
			):
				self.assert_code(
					RepairPhotoDecodeProcessCode.RESULT_MISMATCH,
					lambda: inspect_bound_repair_photo_content(
						bound_bytes=photo,
						expected_mime=RepairPhotoMime.PNG,
					),
				)

	def test_invalid_dimensions_are_protocol_failure(self):
		photo = bound(image_bytes(RepairPhotoMime.PNG))
		payload = bytearray(success_payload(photo, RepairPhotoMime.PNG))
		struct.pack_into(">I", payload, 12, 0)
		with patch.object(
			process_module,
			"_run_worker",
			return_value=process_module._WorkerExecution(0, bytes(payload)),
		):
			self.assert_code(
				RepairPhotoDecodeProcessCode.PROTOCOL_INVALID,
				lambda: inspect_bound_repair_photo_content(
					bound_bytes=photo,
					expected_mime=RepairPhotoMime.PNG,
				),
			)

	def test_spawn_uses_closed_fixed_boundary(self):
		captured = {}

		class FakeProcess:
			stdin = object()
			stdout = object()

			def wait(self):
				return 0

		def fake_popen(argv, **kwargs):
			captured["argv"] = argv
			captured.update(kwargs)
			return FakeProcess()

		with (
			patch.object(process_module.subprocess, "Popen", side_effect=fake_popen),
			patch.object(process_module, "_exchange_bounded", return_value=b"x"),
		):
			result = process_module._run_worker(b"body")
		self.assertEqual(result, process_module._WorkerExecution(0, b"x"))
		self.assertEqual(captured["argv"], process_module._worker_argv())
		self.assertIs(captured["shell"], False)
		self.assertIs(captured["close_fds"], True)
		self.assertEqual(captured["env"], {})
		self.assertIs(captured["stderr"], subprocess.DEVNULL)
		self.assertNotIn("cwd", captured)

	def test_spawn_failure_is_code_only(self):
		with patch.object(process_module.subprocess, "Popen", side_effect=OSError("private")):
			self.assert_code(
				RepairPhotoDecodeProcessCode.WORKER_UNAVAILABLE,
				lambda: process_module._run_worker(b"private-body"),
			)

	def test_exchange_failure_kills_and_waits(self):
		class FakeProcess:
			stdin = object()
			stdout = object()
			killed = False
			waited = False

			def poll(self):
				return None

			def kill(self):
				self.killed = True

			def wait(self):
				self.waited = True
				return -9

		fake = FakeProcess()
		with (
			patch.object(process_module.subprocess, "Popen", return_value=fake),
			patch.object(
				process_module,
				"_exchange_bounded",
				side_effect=RepairPhotoDecodeProcessError(RepairPhotoDecodeProcessCode.WORKER_TIMEOUT),
			),
		):
			self.assert_code(
				RepairPhotoDecodeProcessCode.WORKER_TIMEOUT,
				lambda: process_module._run_worker(b"body"),
			)
		self.assertTrue(fake.killed)
		self.assertTrue(fake.waited)

	def test_output_cap_equals_exact_protocol_size(self):
		self.assertEqual(process_module.MAX_WORKER_OUTPUT_BYTES, process_module._OUTPUT.size)
		self.assertEqual(process_module.MAX_WORKER_OUTPUT_BYTES, 60)

	def test_worker_protocol_rejects_trailing_input_without_echo(self):
		worker = process_module._worker_argv()
		packet = (
			process_module._INPUT_HEADER.pack(
				process_module._INPUT_MAGIC,
				1,
				process_module._MIME_TO_WIRE[RepairPhotoMime.PNG],
				1,
			)
			+ b"x!private-marker"
		)
		completed = subprocess.run(
			worker,
			input=packet,
			capture_output=True,
			check=False,
			timeout=5,
			env={},
		)
		self.assertEqual(completed.returncode, 0)
		self.assertEqual(len(completed.stdout), 60)
		self.assertNotIn(b"private-marker", completed.stdout + completed.stderr)

	def test_worker_and_parent_sources_have_no_forbidden_io_or_identity_fields(self):
		worker_path = Path(process_module._worker_argv()[2])
		worker_source = worker_path.read_text(encoding="utf-8")
		worker_tree = ast.parse(worker_source)
		imports = set()
		for node in ast.walk(worker_tree):
			if isinstance(node, ast.Import):
				imports.update(alias.name.split(".", 1)[0] for alias in node.names)
			elif isinstance(node, ast.ImportFrom) and node.module:
				imports.add(node.module.split(".", 1)[0])
		for forbidden in (
			"frappe",
			"requests",
			"socket",
			"tempfile",
			"redis",
			"rq",
			"subprocess",
		):
			self.assertNotIn(forbidden, imports)
			self.assertNotIn(forbidden, worker_source)
		self.assertNotIn("repair_name", worker_source)
		self.assertNotIn("actor_identity", worker_source)
		self.assertNotIn("file_identity", worker_source)
		self.assertNotIn("open(", worker_source)
		self.assertIn("sys.stdin.buffer", worker_source)
		self.assertIn("sys.stdout.buffer", worker_source)

	def test_error_constructor_rejects_forged_codes(self):
		with self.assertRaises(TypeError):
			RepairPhotoDecodeProcessError("WORKER_FAILED")
		with self.assertRaises(TypeError):
			RepairPhotoDecodeProcessError(
				RepairPhotoDecodeProcessCode.CONTENT_REJECTED,
				content_code="DECODE_FAILED",
			)
		with self.assertRaises(TypeError):
			RepairPhotoDecodeProcessError(RepairPhotoDecodeProcessCode.CONTENT_REJECTED)

	def test_no_public_runner_executable_site_path_or_timeout_inputs(self):
		parameters = inspect.signature(inspect_bound_repair_photo_content).parameters
		self.assertEqual(tuple(parameters), ("bound_bytes", "expected_mime"))
		for forbidden in ("runner", "executable", "site", "path", "timeout", "cwd", "env"):
			self.assertNotIn(forbidden, parameters)


if __name__ == "__main__":
	unittest.main()
