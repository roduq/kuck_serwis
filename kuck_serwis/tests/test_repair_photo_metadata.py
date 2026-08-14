import ast
import inspect
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

from kuck_serwis.repair_photo_metadata import (
	MAX_PHOTO_POSITION,
	MAX_PHOTOS_PER_REPAIR,
	RepairPhotoMetadata,
	RepairPhotoMetadataCode,
	RepairPhotoMetadataError,
	RepairPhotoMetadataState,
	ScopedRepairPhotoEvidence,
	plan_repair_photo_metadata,
)

REPAIR_ID = "rpr_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
OTHER_REPAIR_ID = "rpr_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


def _evidence(
	position=1,
	*,
	repair_id=REPAIR_ID,
	is_private=True,
	exact_attachment=True,
	metadata_only=True,
):
	return ScopedRepairPhotoEvidence(
		repair_id=repair_id,
		position=position,
		is_private=is_private,
		exact_attachment=exact_attachment,
		metadata_only=metadata_only,
	)


def _plan(evidence=(), *, actor_scope_confirmed=True, repair_id=REPAIR_ID):
	return plan_repair_photo_metadata(
		actor_scope_confirmed=actor_scope_confirmed,
		repair_id=repair_id,
		evidence=evidence,
	)


class TestRepairPhotoMetadata(unittest.TestCase):
	def assert_code(self, code, call):
		with self.assertRaises(RepairPhotoMetadataError) as raised:
			call()
		self.assertIs(raised.exception.code, code)
		self.assertEqual(str(raised.exception), code.value)

	def test_empty_metadata_is_valid_and_immutable(self):
		result = _plan()
		self.assertEqual(result, ())
		self.assertIs(type(result), tuple)

	def test_canonical_order_and_exact_output_contract(self):
		result = _plan((_evidence(3), _evidence(1), _evidence(2)))
		self.assertEqual(tuple(item.position for item in result), (1, 2, 3))
		self.assertTrue(all(item.state is RepairPhotoMetadataState.METADATA_ONLY for item in result))
		self.assertEqual(tuple(field.name for field in fields(RepairPhotoMetadata)), ("position", "state"))

	def test_order_independent_deterministic_replay(self):
		forward = _plan(tuple(_evidence(position) for position in (1, 2, 3)))
		reverse = _plan(tuple(_evidence(position) for position in (3, 2, 1)))
		self.assertEqual(forward, reverse)
		self.assertEqual(repr(forward), repr(reverse))

	def test_actor_scope_requires_literal_true(self):
		for invalid in (False, 1, None, "true"):
			with self.subTest(invalid=invalid):
				self.assert_code(
					RepairPhotoMetadataCode.INVALID_INPUT,
					lambda invalid=invalid: _plan(actor_scope_confirmed=invalid),
				)

	def test_existing_opaque_public_repair_id_contract_is_exact(self):
		for invalid in (
			"",
			"rpr_short",
			"RPR_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
			"rpr_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa!",
			1,
			None,
		):
			with self.subTest(invalid=invalid):
				self.assert_code(
					RepairPhotoMetadataCode.INVALID_REPAIR_ID,
					lambda invalid=invalid: _plan(repair_id=invalid),
				)

	def test_every_evidence_must_match_requested_repair(self):
		self.assert_code(
			RepairPhotoMetadataCode.REPAIR_BINDING_MISMATCH,
			lambda: _plan((_evidence(), _evidence(2, repair_id=OTHER_REPAIR_ID))),
		)

	def test_public_photo_fails_closed(self):
		self.assert_code(
			RepairPhotoMetadataCode.PHOTO_NOT_PRIVATE,
			lambda: _plan((_evidence(is_private=False),)),
		)

	def test_wrong_attachment_fails_closed(self):
		self.assert_code(
			RepairPhotoMetadataCode.ATTACHMENT_MISMATCH,
			lambda: _plan((_evidence(exact_attachment=False),)),
		)

	def test_evidence_flags_are_exact_booleans_and_metadata_is_literal_true(self):
		for kwargs in (
			{"is_private": 1},
			{"exact_attachment": 1},
			{"metadata_only": 1},
			{"metadata_only": False},
		):
			with self.subTest(kwargs=kwargs):
				self.assert_code(
					RepairPhotoMetadataCode.INVALID_INPUT, lambda kwargs=kwargs: _evidence(**kwargs)
				)

	def test_position_is_positive_bounded_exact_integer(self):
		for invalid in (True, False, 0, -1, MAX_PHOTO_POSITION + 1, "1", None):
			with self.subTest(invalid=invalid):
				self.assert_code(
					RepairPhotoMetadataCode.INVALID_POSITION,
					lambda invalid=invalid: _evidence(invalid),
				)
		self.assertEqual(_plan((_evidence(MAX_PHOTO_POSITION),))[0].position, MAX_PHOTO_POSITION)

	def test_duplicate_position_fails_closed(self):
		self.assert_code(
			RepairPhotoMetadataCode.DUPLICATE_POSITION,
			lambda: _plan((_evidence(2), _evidence(2))),
		)

	def test_count_is_bounded_before_nested_revalidation(self):
		forged = object.__new__(ScopedRepairPhotoEvidence)
		oversized = tuple(forged for _ in range(MAX_PHOTOS_PER_REPAIR + 1))
		self.assert_code(RepairPhotoMetadataCode.TOO_MANY_PHOTOS, lambda: _plan(oversized))

	def test_maximum_count_is_accepted(self):
		result = _plan(tuple(_evidence(position) for position in range(1, MAX_PHOTOS_PER_REPAIR + 1)))
		self.assertEqual(len(result), MAX_PHOTOS_PER_REPAIR)

	def test_outer_container_requires_exact_tuple(self):
		for invalid in ([], iter(()), None, {_evidence()}):
			with self.subTest(type=type(invalid)):
				self.assert_code(
					RepairPhotoMetadataCode.INVALID_INPUT,
					lambda invalid=invalid: _plan(invalid),
				)

	def test_nested_type_and_missing_attributes_fail_code_only(self):
		for invalid in (object(), object.__new__(ScopedRepairPhotoEvidence)):
			with self.subTest(type=type(invalid)):
				self.assert_code(
					RepairPhotoMetadataCode.INVALID_INPUT,
					lambda invalid=invalid: _plan((invalid,)),
				)

	def test_forged_nested_values_are_revalidated(self):
		forged = object.__new__(ScopedRepairPhotoEvidence)
		object.__setattr__(forged, "repair_id", REPAIR_ID)
		object.__setattr__(forged, "position", 1)
		object.__setattr__(forged, "is_private", 1)
		object.__setattr__(forged, "exact_attachment", True)
		object.__setattr__(forged, "metadata_only", True)
		self.assert_code(RepairPhotoMetadataCode.INVALID_INPUT, lambda: _plan((forged,)))

	def test_output_rejects_forged_state_and_bool_position(self):
		self.assert_code(
			RepairPhotoMetadataCode.INVALID_INPUT,
			lambda: RepairPhotoMetadata(position=1, state="METADATA_ONLY"),
		)
		self.assert_code(
			RepairPhotoMetadataCode.INVALID_POSITION,
			lambda: RepairPhotoMetadata(position=True),
		)

	def test_dtos_are_frozen(self):
		evidence = _evidence()
		metadata = RepairPhotoMetadata(position=1)
		with self.assertRaises(FrozenInstanceError):
			evidence.position = 2
		with self.assertRaises(FrozenInstanceError):
			metadata.position = 2

	def test_representations_and_errors_are_redacted(self):
		marker_id = "rpr_" + "MARKER" + "X" * 26
		evidence = _evidence(repair_id=marker_id)
		result = _plan((evidence,), repair_id=marker_id)
		for rendered in (repr(evidence), repr(result), repr(result[0])):
			self.assertNotIn(marker_id, rendered)
		self.assert_code(
			RepairPhotoMetadataCode.REPAIR_BINDING_MISMATCH,
			lambda: _plan((evidence,), repair_id=REPAIR_ID),
		)

	def test_output_has_no_storage_identity_or_content_fields(self):
		field_names = {field.name for field in fields(RepairPhotoMetadata)}
		for forbidden in {
			"repair_id",
			"user",
			"customer",
			"file",
			"path",
			"url",
			"mime",
			"hash",
			"size",
			"token",
			"body",
		}:
			self.assertNotIn(forbidden, field_names)

	def test_module_has_no_framework_or_io_boundary(self):
		module_path = Path(inspect.getfile(plan_repair_photo_metadata))
		tree = ast.parse(module_path.read_text(encoding="utf-8"))
		imports = set()
		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				imports.update(alias.name.split(".", 1)[0] for alias in node.names)
			elif isinstance(node, ast.ImportFrom) and node.module:
				imports.add(node.module.split(".", 1)[0])
		self.assertTrue(imports.isdisjoint({"frappe", "os", "pathlib", "socket", "requests", "urllib"}))
		called_names = {
			node.func.id
			for node in ast.walk(tree)
			if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
		}
		self.assertTrue(called_names.isdisjoint({"open", "print", "exec", "eval"}))


if __name__ == "__main__":
	unittest.main()
