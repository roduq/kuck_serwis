import ast
import inspect
import unittest
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from kuck_serwis.repair_photo_retention import (
	MAX_RETENTION_CANDIDATES,
	PhotoLegalHoldState,
	PhotoRetentionCandidateDecision,
	PhotoRetentionCounters,
	PhotoRetentionDisposition,
	PhotoRetentionDryRunPlan,
	PhotoRetentionMode,
	PhotoRetentionPolicyEvidence,
	PhotoRetentionPolicyState,
	RepairPhotoRetentionCode,
	RepairPhotoRetentionError,
	RepairPhotoRetentionEvidence,
	plan_repair_photo_retention_dry_run,
)

POLICY_SHA = "a" * 64
OTHER_POLICY_SHA = "b" * 64
EVIDENCE_SHA = "c" * 64
HOLD_SHA = "d" * 64
REPAIR_ID = "rpr_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
OTHER_REPAIR_ID = "rpr_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
ASSESSED_AT = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def policy(state=PhotoRetentionPolicyState.APPROVED, revision=POLICY_SHA):
	return PhotoRetentionPolicyEvidence(policy_revision_sha256=revision, state=state)


def candidate(
	position=1,
	*,
	repair_id=REPAIR_ID,
	file_identity="FILE-A",
	eligible_at=ASSESSED_AT,
	policy_revision_sha256=POLICY_SHA,
	evidence_revision_sha256=EVIDENCE_SHA,
	hold_state=PhotoLegalHoldState.CLEAR,
	hold_revision_sha256=HOLD_SHA,
	lifecycle_eligible=True,
	is_private=True,
	exact_attachment=True,
	child_reference_count=1,
	file_reference_count=1,
	blob_reference_count=1,
):
	return RepairPhotoRetentionEvidence(
		repair_id=repair_id,
		file_identity=file_identity,
		position=position,
		eligible_at=eligible_at,
		policy_revision_sha256=policy_revision_sha256,
		evidence_revision_sha256=evidence_revision_sha256,
		hold_state=hold_state,
		hold_revision_sha256=hold_revision_sha256,
		lifecycle_eligible=lifecycle_eligible,
		is_private=is_private,
		exact_attachment=exact_attachment,
		child_reference_count=child_reference_count,
		file_reference_count=file_reference_count,
		blob_reference_count=blob_reference_count,
	)


def plan(candidates=(), *, assessed_at=ASSESSED_AT, policy_evidence=None):
	return plan_repair_photo_retention_dry_run(
		assessed_at=assessed_at,
		policy=policy() if policy_evidence is None else policy_evidence,
		candidates=candidates,
	)


class TestRepairPhotoRetention(unittest.TestCase):
	def assert_code(self, code, call):
		with self.assertRaises(RepairPhotoRetentionError) as raised:
			call()
		self.assertIs(raised.exception.code, code)
		self.assertEqual(str(raised.exception), code.value)

	def test_empty_plan_is_frozen_dry_run_only(self):
		result = plan()
		self.assertIs(result.mode, PhotoRetentionMode.DRY_RUN_ONLY)
		self.assertEqual(result.candidates, ())
		self.assertEqual(result.counters.total, 0)
		with self.assertRaises(FrozenInstanceError):
			result.mode = "OTHER"

	def test_due_at_exact_assessment_boundary_is_dry_run_eligible(self):
		result = plan((candidate(),))
		self.assertIs(result.candidates[0].disposition, PhotoRetentionDisposition.DRY_RUN_ELIGIBLE)
		self.assertEqual(result.counters.dry_run_eligible, 1)

	def test_future_eligibility_is_kept(self):
		result = plan((candidate(eligible_at=ASSESSED_AT + timedelta(microseconds=1)),))
		self.assertIs(result.candidates[0].disposition, PhotoRetentionDisposition.KEEP_NOT_DUE)

	def test_unapproved_and_revoked_policy_keep_every_candidate(self):
		for state in (PhotoRetentionPolicyState.UNAPPROVED, PhotoRetentionPolicyState.REVOKED):
			with self.subTest(state=state):
				result = plan((candidate(),), policy_evidence=policy(state))
				self.assertIs(
					result.candidates[0].disposition,
					PhotoRetentionDisposition.KEEP_POLICY_UNAPPROVED,
				)

	def test_candidate_policy_revision_mismatch_is_kept(self):
		result = plan((candidate(policy_revision_sha256=OTHER_POLICY_SHA),))
		self.assertIs(
			result.candidates[0].disposition,
			PhotoRetentionDisposition.KEEP_POLICY_UNAPPROVED,
		)

	def test_active_hold_always_keeps_even_unsafe_due_candidate(self):
		result = plan((candidate(hold_state=PhotoLegalHoldState.ACTIVE, lifecycle_eligible=False),))
		self.assertIs(result.candidates[0].disposition, PhotoRetentionDisposition.KEEP_ACTIVE_HOLD)

	def test_unknown_hold_fails_closed_to_keep(self):
		result = plan((candidate(hold_state=PhotoLegalHoldState.UNKNOWN, hold_revision_sha256=None),))
		self.assertIs(result.candidates[0].disposition, PhotoRetentionDisposition.KEEP_HOLD_UNKNOWN)

	def test_hold_revision_contract_is_exact(self):
		for kwargs in (
			{"hold_state": PhotoLegalHoldState.UNKNOWN, "hold_revision_sha256": HOLD_SHA},
			{"hold_state": PhotoLegalHoldState.CLEAR, "hold_revision_sha256": None},
			{"hold_state": PhotoLegalHoldState.ACTIVE, "hold_revision_sha256": "D" * 64},
		):
			with self.subTest(kwargs=kwargs):
				self.assert_code(
					RepairPhotoRetentionCode.INVALID_INPUT, lambda kwargs=kwargs: candidate(**kwargs)
				)

	def test_each_unsafe_evidence_flag_or_count_is_kept(self):
		cases = (
			{"lifecycle_eligible": False},
			{"is_private": False},
			{"exact_attachment": False},
			{"child_reference_count": 0},
			{"child_reference_count": 2},
			{"file_reference_count": 0},
			{"file_reference_count": 2},
			{"blob_reference_count": 0},
			{"blob_reference_count": 2},
		)
		for kwargs in cases:
			with self.subTest(kwargs=kwargs):
				result = plan((candidate(**kwargs),))
				self.assertIs(
					result.candidates[0].disposition,
					PhotoRetentionDisposition.KEEP_UNSAFE_EVIDENCE,
				)

	def test_boolean_flags_and_integer_counts_are_not_interchangeable(self):
		for kwargs in (
			{"lifecycle_eligible": 1},
			{"is_private": 1},
			{"exact_attachment": 1},
			{"child_reference_count": True},
			{"file_reference_count": False},
			{"blob_reference_count": "1"},
		):
			with self.subTest(kwargs=kwargs):
				self.assert_code(
					RepairPhotoRetentionCode.INVALID_INPUT, lambda kwargs=kwargs: candidate(**kwargs)
				)

	def test_assessed_and_eligible_times_require_exact_builtin_utc(self):
		invalid = (
			datetime(2026, 8, 15, 12, 0),
			datetime(2026, 8, 15, 14, 0, tzinfo=timezone(timedelta(hours=2))),
			"2026-08-15T12:00:00Z",
			None,
		)
		for value in invalid:
			with self.subTest(value=value):
				self.assert_code(
					RepairPhotoRetentionCode.INVALID_INPUT, lambda value=value: plan(assessed_at=value)
				)
				self.assert_code(
					RepairPhotoRetentionCode.INVALID_INPUT,
					lambda value=value: candidate(eligible_at=value),
				)

	def test_folded_utc_time_is_rejected(self):
		self.assert_code(
			RepairPhotoRetentionCode.INVALID_INPUT,
			lambda: candidate(eligible_at=ASSESSED_AT.replace(fold=1)),
		)

	def test_ids_positions_and_hashes_are_strict(self):
		for kwargs in (
			{"repair_id": "SER-1"},
			{"file_identity": "FILE WITH SPACE"},
			{"file_identity": "../FILE"},
			{"position": True},
			{"position": 0},
			{"position": 1001},
			{"policy_revision_sha256": "A" * 64},
			{"evidence_revision_sha256": "x" * 64},
		):
			with self.subTest(kwargs=kwargs):
				self.assert_code(
					RepairPhotoRetentionCode.INVALID_INPUT, lambda kwargs=kwargs: candidate(**kwargs)
				)

	def test_policy_shape_and_enum_are_exact(self):
		for call in (
			lambda: policy(state="APPROVED"),
			lambda: policy(revision="A" * 64),
			lambda: plan(policy_evidence=object()),
		):
			self.assert_code(RepairPhotoRetentionCode.INVALID_INPUT, call)

	def test_outer_candidate_container_is_exact_bounded_tuple(self):
		for value in ([], iter(()), None, {candidate()}):
			with self.subTest(type=type(value)):
				self.assert_code(RepairPhotoRetentionCode.INVALID_INPUT, lambda value=value: plan(value))
		forged = object.__new__(RepairPhotoRetentionEvidence)
		oversized = tuple(forged for _ in range(MAX_RETENTION_CANDIDATES + 1))
		self.assert_code(RepairPhotoRetentionCode.TOO_MANY_CANDIDATES, lambda: plan(oversized))

	def test_maximum_candidate_count_is_accepted(self):
		values = tuple(
			candidate(
				position=(index % 1000) + 1,
				repair_id=f"rpr_{index:032d}",
				file_identity=f"FILE-{index}",
			)
			for index in range(MAX_RETENTION_CANDIDATES)
		)
		self.assertEqual(plan(values).counters.total, MAX_RETENTION_CANDIDATES)

	def test_nested_type_missing_and_forged_attributes_fail_code_only(self):
		for invalid in (object(), object.__new__(RepairPhotoRetentionEvidence)):
			with self.subTest(type=type(invalid)):
				self.assert_code(
					RepairPhotoRetentionCode.INVALID_INPUT, lambda invalid=invalid: plan((invalid,))
				)
		valid = candidate()
		forged = object.__new__(RepairPhotoRetentionEvidence)
		for descriptor in fields(RepairPhotoRetentionEvidence):
			object.__setattr__(forged, descriptor.name, getattr(valid, descriptor.name))
		object.__setattr__(forged, "is_private", 1)
		self.assert_code(RepairPhotoRetentionCode.INVALID_INPUT, lambda: plan((forged,)))

	def test_forged_policy_is_defensively_reconstructed(self):
		forged = object.__new__(PhotoRetentionPolicyEvidence)
		object.__setattr__(forged, "policy_revision_sha256", POLICY_SHA)
		object.__setattr__(forged, "state", "APPROVED")
		self.assert_code(RepairPhotoRetentionCode.INVALID_INPUT, lambda: plan(policy_evidence=forged))

	def test_duplicate_file_identity_and_position_fail_closed(self):
		for values in (
			(candidate(1), candidate(2)),
			(candidate(1), candidate(1, file_identity="FILE-B")),
		):
			with self.subTest(values=values):
				self.assert_code(
					RepairPhotoRetentionCode.DUPLICATE_CANDIDATE, lambda values=values: plan(values)
				)

	def test_same_position_in_different_repairs_is_allowed(self):
		result = plan(
			(
				candidate(1),
				candidate(1, repair_id=OTHER_REPAIR_ID, file_identity="FILE-B"),
			)
		)
		self.assertEqual(result.counters.dry_run_eligible, 2)

	def test_order_independent_replay_has_stable_decisions_and_counters(self):
		first = candidate(1, eligible_at=ASSESSED_AT - timedelta(days=2), file_identity="FILE-A")
		second = candidate(2, eligible_at=ASSESSED_AT - timedelta(days=1), file_identity="FILE-B")
		self.assertEqual(plan((first, second)), plan((second, first)))

	def test_mixed_plan_has_exact_counters(self):
		values = (
			candidate(1, file_identity="A"),
			candidate(2, file_identity="B", eligible_at=ASSESSED_AT + timedelta(days=1)),
			candidate(3, file_identity="C", hold_state=PhotoLegalHoldState.ACTIVE),
			candidate(
				4, file_identity="D", hold_state=PhotoLegalHoldState.UNKNOWN, hold_revision_sha256=None
			),
			candidate(5, file_identity="E", is_private=False),
			candidate(6, file_identity="F", policy_revision_sha256=OTHER_POLICY_SHA),
		)
		result = plan(values)
		self.assertEqual(
			result.counters,
			PhotoRetentionCounters(
				total=6,
				keep_policy_unapproved=1,
				keep_not_due=1,
				keep_active_hold=1,
				keep_hold_unknown=1,
				keep_unsafe_evidence=1,
				dry_run_eligible=1,
			),
		)

	def test_direct_forged_output_contracts_fail_closed(self):
		decision = PhotoRetentionCandidateDecision(
			candidate_fingerprint_sha256="e" * 64,
			disposition=PhotoRetentionDisposition.DRY_RUN_ELIGIBLE,
		)
		counters = PhotoRetentionCounters(1, 0, 0, 0, 0, 0, 1)
		for kwargs in (
			{"mode": "DRY_RUN_ONLY"},
			{"policy_state": "APPROVED"},
			{"candidates": [decision]},
			{"candidates": (), "counters": counters},
		):
			base = {
				"mode": PhotoRetentionMode.DRY_RUN_ONLY,
				"assessed_at": ASSESSED_AT,
				"policy_state": PhotoRetentionPolicyState.APPROVED,
				"candidates": (decision,),
				"counters": counters,
			}
			base.update(kwargs)
			with self.subTest(kwargs=kwargs):
				self.assert_code(
					RepairPhotoRetentionCode.INVALID_INPUT, lambda base=base: PhotoRetentionDryRunPlan(**base)
				)

	def test_counter_invariants_reject_bool_negative_and_wrong_sum(self):
		for args in (
			(True, 0, 0, 0, 0, 0, 0),
			(1, -1, 0, 0, 0, 0, 2),
			(1, 0, 0, 0, 0, 0, 0),
		):
			with self.subTest(args=args):
				self.assert_code(
					RepairPhotoRetentionCode.INVALID_INPUT, lambda args=args: PhotoRetentionCounters(*args)
				)

	def test_output_rejects_mismatched_counters_and_duplicate_fingerprints(self):
		decision = PhotoRetentionCandidateDecision(
			candidate_fingerprint_sha256="e" * 64,
			disposition=PhotoRetentionDisposition.DRY_RUN_ELIGIBLE,
		)
		wrong = PhotoRetentionCounters(1, 1, 0, 0, 0, 0, 0)
		self.assert_code(
			RepairPhotoRetentionCode.INVALID_INPUT,
			lambda: PhotoRetentionDryRunPlan(
				mode=PhotoRetentionMode.DRY_RUN_ONLY,
				assessed_at=ASSESSED_AT,
				policy_state=PhotoRetentionPolicyState.APPROVED,
				candidates=(decision,),
				counters=wrong,
			),
		)
		duplicated = PhotoRetentionCounters(2, 0, 0, 0, 0, 0, 2)
		self.assert_code(
			RepairPhotoRetentionCode.INVALID_INPUT,
			lambda: PhotoRetentionDryRunPlan(
				mode=PhotoRetentionMode.DRY_RUN_ONLY,
				assessed_at=ASSESSED_AT,
				policy_state=PhotoRetentionPolicyState.APPROVED,
				candidates=(decision, decision),
				counters=duplicated,
			),
		)

	def test_dtos_are_frozen_slots_and_outputs_have_no_storage_address(self):
		item = candidate()
		with self.assertRaises(FrozenInstanceError):
			item.position = 2
		self.assertFalse(hasattr(item, "__dict__"))
		output_fields = {field.name for field in fields(PhotoRetentionCandidateDecision)}
		for forbidden in {"repair_id", "file_identity", "path", "url", "body", "blob", "delete"}:
			self.assertNotIn(forbidden, output_fields)

	def test_representations_errors_and_results_are_redacted(self):
		markers = (REPAIR_ID, "FILE-MARKER", POLICY_SHA, EVIDENCE_SHA, HOLD_SHA)
		item = candidate(file_identity="FILE-MARKER")
		result = plan((item,))
		rendered = " ".join((repr(item), repr(policy()), repr(result), repr(result.candidates[0])))
		for marker in markers:
			self.assertNotIn(marker, rendered)
		self.assert_code(RepairPhotoRetentionCode.DUPLICATE_CANDIDATE, lambda: plan((item, item)))

	def test_fingerprint_changes_with_evidence_but_never_exposes_inputs(self):
		first = plan((candidate(),)).candidates[0].candidate_fingerprint_sha256
		second = (
			plan((candidate(evidence_revision_sha256="e" * 64),)).candidates[0].candidate_fingerprint_sha256
		)
		self.assertNotEqual(first, second)
		self.assertEqual(len(first), 64)
		self.assertNotIn("FILE", first)

	def test_module_has_no_framework_io_clock_or_deletion_boundary(self):
		module_path = Path(inspect.getfile(plan_repair_photo_retention_dry_run))
		tree = ast.parse(module_path.read_text(encoding="utf-8"))
		imports = set()
		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				imports.update(alias.name.split(".", 1)[0] for alias in node.names)
			elif isinstance(node, ast.ImportFrom) and node.module:
				imports.add(node.module.split(".", 1)[0])
		self.assertTrue(
			imports.isdisjoint(
				{"frappe", "os", "pathlib", "socket", "requests", "urllib", "time", "subprocess"}
			)
		)
		called = {
			node.func.id
			for node in ast.walk(tree)
			if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
		}
		self.assertTrue(called.isdisjoint({"open", "print", "exec", "eval", "remove", "unlink"}))
		attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
		self.assertTrue(attributes.isdisjoint({"delete", "unlink", "remove", "commit", "rollback", "now"}))


if __name__ == "__main__":
	unittest.main()
