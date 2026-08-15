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
	PassiveProbeObservations,
	plan_passive_probe,
)

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


if __name__ == "__main__":
	unittest.main()
