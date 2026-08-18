import ast
import dataclasses
import inspect
import unittest
from dataclasses import fields
from pathlib import Path

from kuck_serwis.audit_passive_probe import (
	PassiveProbeAssessment,
	PassiveProbeCode,
	PassiveProbeError,
	PassiveProbeErrorCode,
	PassiveProbeFreshnessCode,
	PassiveProbeFreshnessPlan,
	PassiveProbeObservations,
	plan_passive_probe,
	plan_passive_probe_freshness_v1,
)
from kuck_serwis.operational_policy_v1 import POLICY_REVISION_SHA256

CHECKED_AT = "2026-08-15T12:34:56Z"


def observations(**changes):
	values = {
		"connection_available": True,
		"schema_matches": True,
		"public_ids_valid": True,
		"statuses_valid": True,
		"retention_valid": True,
		"hold_available": True,
		"metrics_available": True,
		"purge_fresh": True,
	}
	values.update(changes)
	return PassiveProbeObservations(**values)


class TestPassiveProbePlanner(unittest.TestCase):
	def assert_code(self, code, call):
		with self.assertRaises(PassiveProbeError) as raised:
			call()
		self.assertIs(raised.exception.code, code)
		self.assertEqual(str(raised.exception), code.value)

	def test_all_exact_observations_produce_point_in_time_success(self):
		result = plan_passive_probe(observations(), checked_at=CHECKED_AT)
		self.assertEqual(result, PassiveProbeAssessment(True, CHECKED_AT, PassiveProbeCode.PASSIVE_OK))
		self.assertEqual(set(result.__slots__), {"ok", "checked_at", "code"})

	def test_each_single_failure_maps_to_exact_code(self):
		cases = (
			("connection_available", PassiveProbeCode.PASSIVE_CONNECTION_UNAVAILABLE),
			("schema_matches", PassiveProbeCode.PASSIVE_SCHEMA_MISMATCH),
			("public_ids_valid", PassiveProbeCode.PASSIVE_PUBLIC_ID_INVALID),
			("statuses_valid", PassiveProbeCode.PASSIVE_STATUS_INVALID),
			("retention_valid", PassiveProbeCode.PASSIVE_RETENTION_INVALID),
			("hold_available", PassiveProbeCode.PASSIVE_HOLD_UNAVAILABLE),
			("metrics_available", PassiveProbeCode.PASSIVE_METRICS_UNAVAILABLE),
			("purge_fresh", PassiveProbeCode.PASSIVE_PURGE_STALE),
		)
		for field_name, expected in cases:
			with self.subTest(field_name=field_name):
				result = plan_passive_probe(observations(**{field_name: False}), checked_at=CHECKED_AT)
				self.assertEqual(result, PassiveProbeAssessment(False, CHECKED_AT, expected))

	def test_multiple_failures_use_closed_precedence(self):
		ordered = (
			("connection_available", PassiveProbeCode.PASSIVE_CONNECTION_UNAVAILABLE),
			("schema_matches", PassiveProbeCode.PASSIVE_SCHEMA_MISMATCH),
			("public_ids_valid", PassiveProbeCode.PASSIVE_PUBLIC_ID_INVALID),
			("statuses_valid", PassiveProbeCode.PASSIVE_STATUS_INVALID),
			("retention_valid", PassiveProbeCode.PASSIVE_RETENTION_INVALID),
			("hold_available", PassiveProbeCode.PASSIVE_HOLD_UNAVAILABLE),
			("metrics_available", PassiveProbeCode.PASSIVE_METRICS_UNAVAILABLE),
			("purge_fresh", PassiveProbeCode.PASSIVE_PURGE_STALE),
		)
		for index, (field_name, expected) in enumerate(ordered):
			changes = {name: False for name, _code in ordered[index:]}
			with self.subTest(field_name=field_name):
				result = plan_passive_probe(observations(**changes), checked_at=CHECKED_AT)
				self.assertIs(result.code, expected)

	def test_taxonomy_is_exactly_the_existing_adr_allowlist(self):
		self.assertEqual(
			tuple(code.value for code in PassiveProbeCode),
			(
				"PASSIVE_NOT_RUN",
				"PASSIVE_OK",
				"PASSIVE_CONNECTION_UNAVAILABLE",
				"PASSIVE_SCHEMA_MISMATCH",
				"PASSIVE_PUBLIC_ID_INVALID",
				"PASSIVE_STATUS_INVALID",
				"PASSIVE_RETENTION_INVALID",
				"PASSIVE_HOLD_UNAVAILABLE",
				"PASSIVE_METRICS_UNAVAILABLE",
				"PASSIVE_PURGE_STALE",
				"PASSIVE_INTERNAL_ERROR",
			),
		)

	def test_initial_and_internal_codes_are_never_planner_outputs(self):
		possible = {
			plan_passive_probe(observations(**{field.name: False}), checked_at=CHECKED_AT).code
			for field in fields(PassiveProbeObservations)
		}
		possible.add(plan_passive_probe(observations(), checked_at=CHECKED_AT).code)
		self.assertNotIn(PassiveProbeCode.PASSIVE_NOT_RUN, possible)
		self.assertNotIn(PassiveProbeCode.PASSIVE_INTERNAL_ERROR, possible)

	def test_every_observation_requires_literal_bool(self):
		for descriptor in fields(PassiveProbeObservations):
			for invalid in (1, 0, None, "true"):
				with self.subTest(field=descriptor.name, invalid=invalid):
					self.assert_code(
						PassiveProbeErrorCode.INVALID_OBSERVATIONS,
						lambda descriptor=descriptor, invalid=invalid: observations(
							**{descriptor.name: invalid}
						),
					)

	def test_checked_at_requires_canonical_second_precision_utc_z(self):
		invalid = (
			"2026-08-15T12:34:56+00:00",
			"2026-08-15T12:34:56.0Z",
			"2026-8-15T12:34:56Z",
			"2026-08-15 12:34:56Z",
			"2026-02-30T12:34:56Z",
			" 2026-08-15T12:34:56Z",
			"2026-08-15T12:34:56Z ",
			1,
			None,
		)
		for value in invalid:
			with self.subTest(value=value):
				self.assert_code(
					PassiveProbeErrorCode.INVALID_CHECKED_AT,
					lambda value=value: plan_passive_probe(observations(), checked_at=value),
				)

	def test_valid_leap_second_precision_date_is_accepted(self):
		value = "2028-02-29T00:00:00Z"
		self.assertEqual(plan_passive_probe(observations(), checked_at=value).checked_at, value)

	def test_outer_type_subclass_and_missing_attributes_fail_code_only(self):
		class ObservationsSubclass(PassiveProbeObservations):
			pass

		for invalid in (
			{},
			object(),
			ObservationsSubclass(*([True] * 8)),
			object.__new__(PassiveProbeObservations),
		):
			with self.subTest(type=type(invalid)):
				self.assert_code(
					PassiveProbeErrorCode.INVALID_OBSERVATIONS,
					lambda invalid=invalid: plan_passive_probe(invalid, checked_at=CHECKED_AT),
				)

	def test_forged_nested_values_are_defensively_reconstructed(self):
		valid = observations()
		forged = object.__new__(PassiveProbeObservations)
		for descriptor in fields(PassiveProbeObservations):
			object.__setattr__(forged, descriptor.name, getattr(valid, descriptor.name))
		object.__setattr__(forged, "metrics_available", 1)
		self.assert_code(
			PassiveProbeErrorCode.INVALID_OBSERVATIONS,
			lambda: plan_passive_probe(forged, checked_at=CHECKED_AT),
		)

	def test_direct_assessment_rejects_inconsistent_or_forged_values(self):
		invalid = (
			(True, CHECKED_AT, PassiveProbeCode.PASSIVE_SCHEMA_MISMATCH),
			(False, CHECKED_AT, PassiveProbeCode.PASSIVE_OK),
			(False, CHECKED_AT, PassiveProbeCode.PASSIVE_NOT_RUN),
			(1, CHECKED_AT, PassiveProbeCode.PASSIVE_OK),
			(False, CHECKED_AT, "PASSIVE_SCHEMA_MISMATCH"),
			(False, "private-marker", PassiveProbeCode.PASSIVE_INTERNAL_ERROR),
		)
		for args in invalid:
			with self.subTest(args=args):
				self.assert_code(
					PassiveProbeErrorCode.INVALID_RESULT,
					lambda args=args: PassiveProbeAssessment(*args),
				)

	def test_adapter_reserved_internal_error_is_a_valid_failed_assessment(self):
		result = PassiveProbeAssessment(
			False,
			CHECKED_AT,
			PassiveProbeCode.PASSIVE_INTERNAL_ERROR,
		)
		self.assertFalse(result.ok)

	def test_equivalent_observations_are_deterministic(self):
		first = observations(schema_matches=False, metrics_available=False)
		second = PassiveProbeObservations(
			purge_fresh=True,
			metrics_available=False,
			hold_available=True,
			retention_valid=True,
			statuses_valid=True,
			public_ids_valid=True,
			schema_matches=False,
			connection_available=True,
		)
		self.assertEqual(
			plan_passive_probe(first, checked_at=CHECKED_AT),
			plan_passive_probe(second, checked_at=CHECKED_AT),
		)

	def test_dtos_are_frozen_slots_and_representations_are_redacted(self):
		input_value = observations()
		result = plan_passive_probe(input_value, checked_at=CHECKED_AT)
		with self.assertRaises(dataclasses.FrozenInstanceError):
			input_value.connection_available = False
		with self.assertRaises(dataclasses.FrozenInstanceError):
			result.ok = False
		self.assertFalse(hasattr(input_value, "__dict__"))
		self.assertFalse(hasattr(result, "__dict__"))
		self.assertNotIn(CHECKED_AT, repr(result))
		self.assertNotIn("connection_available", repr(input_value))

	def test_errors_never_echo_rejected_data(self):
		marker = "private@example.test/secret/path"
		self.assert_code(
			PassiveProbeErrorCode.INVALID_CHECKED_AT,
			lambda: plan_passive_probe(observations(), checked_at=marker),
		)
		try:
			plan_passive_probe(observations(), checked_at=marker)
		except PassiveProbeError as error:
			self.assertNotIn(marker, str(error))
			self.assertNotIn(marker, repr(error))

	def test_planner_exposes_no_readiness_composition_or_runtime_fields(self):
		result = plan_passive_probe(observations(), checked_at=CHECKED_AT)
		for field_name in (
			"capability_ready",
			"sink_ready",
			"schema_ready",
			"retention_signed_off",
			"legal_hold_signed_off",
			"revision",
			"lease",
		):
			self.assertFalse(hasattr(result, field_name))

	def test_module_has_no_framework_database_io_clock_config_or_scheduler_boundary(self):
		module_path = Path(inspect.getfile(plan_passive_probe))
		tree = ast.parse(module_path.read_text(encoding="utf-8"))
		imports = set()
		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				imports.update(alias.name.split(".", 1)[0] for alias in node.names)
			elif isinstance(node, ast.ImportFrom) and node.module:
				imports.add(node.module.split(".", 1)[0])
		self.assertTrue(
			imports.isdisjoint(
				{
					"frappe",
					"os",
					"pathlib",
					"socket",
					"requests",
					"urllib",
					"time",
					"subprocess",
				}
			)
		)
		called = {
			node.func.id
			for node in ast.walk(tree)
			if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
		}
		self.assertTrue(called.isdisjoint({"open", "print", "exec", "eval"}))
		attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
		self.assertTrue(
			attributes.isdisjoint(
				{"now", "utcnow", "connect", "commit", "rollback", "enqueue", "get_conf", "get_hooks"}
			)
		)


class TestPassiveProbeFreshnessPlanner(unittest.TestCase):
	def assert_code(self, code, call):
		with self.assertRaises(PassiveProbeError) as raised:
			call()
		self.assertIs(raised.exception.code, code)
		self.assertEqual(str(raised.exception), code.value)

	def plan(self, assessment, checked_at="2026-08-15T12:34:56Z"):
		return plan_passive_probe_freshness_v1(assessment, checked_at=checked_at)

	def assert_fail_codes(self, assessment, *codes, checked_at="2026-08-15T12:34:56Z"):
		result = self.plan(assessment, checked_at)
		self.assertFalse(result.fresh)
		self.assertEqual(result.codes, codes)
		return result

	def test_only_passive_ok_from_zero_through_120_seconds_is_fresh(self):
		for probe_time in ("2026-08-15T12:34:56Z", "2026-08-15T12:32:56Z"):
			with self.subTest(probe_time=probe_time):
				result = self.plan(PassiveProbeAssessment(True, probe_time, PassiveProbeCode.PASSIVE_OK))
				self.assertTrue(result.fresh)
				self.assertEqual(result.codes, (PassiveProbeFreshnessCode.FRESH,))
				self.assertEqual(result.policy_revision_sha256, POLICY_REVISION_SHA256)

	def test_121_seconds_is_stale(self):
		self.assert_fail_codes(
			PassiveProbeAssessment(True, "2026-08-15T12:32:55Z", PassiveProbeCode.PASSIVE_OK),
			PassiveProbeFreshnessCode.STALE,
		)

	def test_missing_assessment_fails_closed(self):
		self.assert_fail_codes(None, PassiveProbeFreshnessCode.MISSING)

	def test_failed_assessment_is_never_fresh_and_keeps_time_failures(self):
		cases = (
			("2026-08-15T12:34:56Z", (PassiveProbeFreshnessCode.FAILED,)),
			(
				"2026-08-15T12:34:57Z",
				(PassiveProbeFreshnessCode.FAILED, PassiveProbeFreshnessCode.FUTURE),
			),
			(
				"2026-08-15T12:32:55Z",
				(PassiveProbeFreshnessCode.FAILED, PassiveProbeFreshnessCode.STALE),
			),
		)
		for probe_time, expected in cases:
			with self.subTest(probe_time=probe_time):
				self.assert_fail_codes(
					PassiveProbeAssessment(
						False,
						probe_time,
						PassiveProbeCode.PASSIVE_CONNECTION_UNAVAILABLE,
					),
					*expected,
				)

	def test_success_from_the_future_fails_closed(self):
		self.assert_fail_codes(
			PassiveProbeAssessment(True, "2026-08-15T12:34:57Z", PassiveProbeCode.PASSIVE_OK),
			PassiveProbeFreshnessCode.FUTURE,
		)

	def test_assessment_requires_exact_type_and_is_defensively_rebuilt(self):
		class AssessmentSubclass(PassiveProbeAssessment):
			pass

		for invalid in ({}, object(), AssessmentSubclass(True, CHECKED_AT, PassiveProbeCode.PASSIVE_OK)):
			with self.subTest(value_type=type(invalid).__name__):
				self.assert_code(
					PassiveProbeErrorCode.INVALID_ASSESSMENT,
					lambda invalid=invalid: self.plan(invalid),
				)

		forged = object.__new__(PassiveProbeAssessment)
		object.__setattr__(forged, "ok", True)
		object.__setattr__(forged, "checked_at", CHECKED_AT)
		object.__setattr__(forged, "code", "PASSIVE_OK")
		self.assert_code(PassiveProbeErrorCode.INVALID_ASSESSMENT, lambda: self.plan(forged))

	def test_evaluation_time_requires_exact_canonical_utc(self):
		assessment = PassiveProbeAssessment(True, CHECKED_AT, PassiveProbeCode.PASSIVE_OK)
		for invalid in ("2026-08-15T12:34:56+00:00", "2026-02-30T12:34:56Z", 1, None):
			with self.subTest(invalid=invalid):
				self.assert_code(
					PassiveProbeErrorCode.INVALID_CHECKED_AT,
					lambda invalid=invalid: self.plan(assessment, invalid),
				)

	def test_result_is_frozen_redacted_code_only_and_never_authorizes_operations(self):
		result = self.plan(PassiveProbeAssessment(True, CHECKED_AT, PassiveProbeCode.PASSIVE_OK))
		with self.assertRaises(dataclasses.FrozenInstanceError):
			result.fresh = False
		self.assertFalse(hasattr(result, "__dict__"))
		self.assertNotIn(CHECKED_AT, repr(result))
		for field_name in (
			"purge_authorized",
			"delivery_authorized",
			"activation_authorized",
			"capability_ready",
			"readiness_evidence_ok",
		):
			self.assertIs(getattr(result, field_name), False)
		self.assertTrue(
			{field.name for field in fields(PassiveProbeFreshnessPlan)}.isdisjoint(
				{"email", "user", "customer", "repair_id", "file_id", "hold_id", "hostname", "url"}
			)
		)

	def test_direct_result_rejects_inconsistent_flags_codes_and_digest(self):
		class DigestSubclass(str):
			pass

		invalid = (
			(True, (PassiveProbeFreshnessCode.STALE,), POLICY_REVISION_SHA256, {}),
			(False, (PassiveProbeFreshnessCode.FRESH,), POLICY_REVISION_SHA256, {}),
			(False, (), POLICY_REVISION_SHA256, {}),
			(
				False,
				(PassiveProbeFreshnessCode.MISSING, PassiveProbeFreshnessCode.FAILED),
				POLICY_REVISION_SHA256,
				{},
			),
			(
				False,
				(PassiveProbeFreshnessCode.FUTURE, PassiveProbeFreshnessCode.STALE),
				POLICY_REVISION_SHA256,
				{},
			),
			(
				False,
				(PassiveProbeFreshnessCode.STALE, PassiveProbeFreshnessCode.FAILED),
				POLICY_REVISION_SHA256,
				{},
			),
			(True, (PassiveProbeFreshnessCode.FRESH,), "0" * 64, {}),
			(True, (PassiveProbeFreshnessCode.FRESH,), 1, {}),
			(True, (PassiveProbeFreshnessCode.FRESH,), DigestSubclass(POLICY_REVISION_SHA256), {}),
			(True, (PassiveProbeFreshnessCode.FRESH,), POLICY_REVISION_SHA256, {"capability_ready": True}),
			(
				True,
				(PassiveProbeFreshnessCode.FRESH,),
				POLICY_REVISION_SHA256,
				{"readiness_evidence_ok": True},
			),
		)
		for fresh, codes, digest, flags in invalid:
			with self.subTest(fresh=fresh, codes=codes, flags=flags):
				self.assert_code(
					PassiveProbeErrorCode.INVALID_RESULT,
					lambda fresh=fresh, codes=codes, digest=digest, flags=flags: PassiveProbeFreshnessPlan(
						fresh,
						codes,
						digest,
						**flags,
					),
				)


if __name__ == "__main__":
	unittest.main()
