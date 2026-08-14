import ast
import dataclasses
import inspect
import unittest
from pathlib import Path

from kuck_serwis.audit_probe_evidence import (
	ActiveProbeMappingError,
	ActiveProbeMappingErrorCode,
	map_active_probe_result,
)
from kuck_serwis.audit_readiness import ActiveProbeEvidence

CHECKED_AT = "2026-08-14T12:00:00Z"
FAILURE_CODES = (
	"KEY_UNAVAILABLE",
	"SINK_ACK_INVALID",
	"SINK_UNAVAILABLE",
	"VERIFY_UNAVAILABLE",
	"VERIFY_COUNT_MISMATCH",
	"VERIFY_CONTENT_MISMATCH",
)


def _result(**changes):
	value = {
		"ok": True,
		"checked_at": CHECKED_AT,
		"probe_version": "repair-audit-active/v1",
		"codes": ["ACTIVE_CANARY_OK"],
	}
	value.update(changes)
	return value


class TestActiveProbeEvidenceMapper(unittest.TestCase):
	def assert_invalid(self, value):
		with self.assertRaises(ActiveProbeMappingError) as caught:
			map_active_probe_result(value)
		self.assertIs(caught.exception.code, ActiveProbeMappingErrorCode.INVALID_PROBE_RESULT)
		self.assertEqual(str(caught.exception), "INVALID_PROBE_RESULT")

	def test_exact_success_maps_to_frozen_point_in_time_evidence(self):
		evidence = map_active_probe_result(_result())

		self.assertEqual(evidence, ActiveProbeEvidence(ok=True, checked_at=CHECKED_AT))
		self.assertIs(type(evidence), ActiveProbeEvidence)
		self.assertEqual(set(evidence.__slots__), {"ok", "checked_at"})
		with self.assertRaises(dataclasses.FrozenInstanceError):
			evidence.ok = False

	def test_each_existing_failure_code_maps_to_failed_evidence(self):
		for code in FAILURE_CODES:
			with self.subTest(code=code):
				evidence = map_active_probe_result(_result(ok=False, codes=[code]))
				self.assertEqual(evidence, ActiveProbeEvidence(False, CHECKED_AT))

	def test_allowlist_matches_the_real_active_probe_without_importing_frappe(self):
		tree = ast.parse((Path(__file__).parents[1] / "audit_health.py").read_text(encoding="utf-8"))
		constants = {}
		for node in tree.body:
			if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
				continue
			if node.target.id in {"PROBE_VERSION", "_SUCCESS_CODE"}:
				constants[node.target.id] = ast.literal_eval(node.value)
			elif node.target.id == "_CONTROL_CODES":
				self.assertIsInstance(node.value, ast.Call)
				self.assertIsInstance(node.value.args[0], ast.Set)
				values = []
				for element in node.value.args[0].elts:
					if isinstance(element, ast.Name):
						values.append(constants[element.id])
					else:
						values.append(ast.literal_eval(element))
				constants[node.target.id] = frozenset(values)

		self.assertEqual(constants["PROBE_VERSION"], "repair-audit-active/v1")
		self.assertEqual(constants["_SUCCESS_CODE"], "ACTIVE_CANARY_OK")
		self.assertEqual(
			constants["_CONTROL_CODES"],
			frozenset({"ACTIVE_CANARY_OK", *FAILURE_CODES}),
		)

	def test_input_must_be_an_exact_builtin_dict(self):
		class DictSubclass(dict):
			pass

		for value in (None, (), [], DictSubclass(_result())):
			with self.subTest(value_type=type(value).__name__):
				self.assert_invalid(value)

	def test_exact_keys_are_required_without_extras(self):
		missing = _result()
		del missing["codes"]
		extra = _result(extra="value")

		self.assert_invalid(missing)
		self.assert_invalid(extra)

	def test_key_types_must_be_exact_strings(self):
		class StringKey(str):
			pass

		value = _result()
		value[StringKey("ok")] = value.pop("ok")
		self.assert_invalid(value)

	def test_ok_requires_a_literal_boolean(self):
		for value in (1, 0, None, "true", 1.0):
			with self.subTest(value=value):
				self.assert_invalid(_result(ok=value))

	def test_version_is_exact_and_requires_a_builtin_string(self):
		class StringSubclass(str):
			pass

		for value in (
			"repair-audit-active/v2",
			"repair-audit-active/v1 ",
			StringSubclass("repair-audit-active/v1"),
			None,
		):
			with self.subTest(value_type=type(value).__name__):
				self.assert_invalid(_result(probe_version=value))

	def test_codes_requires_an_exact_one_element_builtin_list(self):
		class ListSubclass(list):
			pass

		for value in (
			(),
			("ACTIVE_CANARY_OK",),
			[],
			["ACTIVE_CANARY_OK", "ACTIVE_CANARY_OK"],
			ListSubclass(["ACTIVE_CANARY_OK"]),
		):
			with self.subTest(value_type=type(value).__name__, length=len(value)):
				self.assert_invalid(_result(codes=value))

	def test_code_requires_an_exact_builtin_string(self):
		class StringSubclass(str):
			pass

		for value in (None, 1, StringSubclass("ACTIVE_CANARY_OK")):
			with self.subTest(value_type=type(value).__name__):
				self.assert_invalid(_result(codes=[value]))

	def test_unknown_code_is_rejected_for_both_outcomes(self):
		self.assert_invalid(_result(codes=["UNKNOWN_CODE"]))
		self.assert_invalid(_result(ok=False, codes=["UNKNOWN_CODE"]))

	def test_success_and_failure_codes_cannot_be_crossed(self):
		for code in FAILURE_CODES:
			with self.subTest(code=code):
				self.assert_invalid(_result(codes=[code]))
		self.assert_invalid(_result(ok=False, codes=["ACTIVE_CANARY_OK"]))

	def test_checked_at_is_defensively_revalidated_as_canonical_utc(self):
		invalid = (
			"2026-08-14 12:00:00Z",
			"2026-08-14T12:00:00+00:00",
			"2026-08-14T12:00:00.000Z",
			"2026-02-30T12:00:00Z",
			1,
			None,
		)
		for value in invalid:
			with self.subTest(value=value):
				self.assert_invalid(_result(checked_at=value))

	def test_equivalent_input_is_deterministic_and_input_is_not_mutated(self):
		value = _result()
		before = {**value, "codes": list(value["codes"])}

		first = map_active_probe_result(value)
		second = map_active_probe_result(
			{
				"codes": ["ACTIVE_CANARY_OK"],
				"probe_version": "repair-audit-active/v1",
				"checked_at": CHECKED_AT,
				"ok": True,
			}
		)

		self.assertEqual(first, second)
		self.assertEqual(value, before)

	def test_rejected_values_are_never_echoed(self):
		private = "rejected-value/private-path"
		with self.assertRaises(ActiveProbeMappingError) as caught:
			map_active_probe_result(_result(probe_version=private, codes=[private]))

		self.assertEqual(repr(caught.exception), "ActiveProbeMappingError('INVALID_PROBE_RESULT')")
		self.assertNotIn(private, repr(caught.exception))

	def test_output_repr_does_not_expose_probe_timestamp(self):
		evidence = map_active_probe_result(_result())
		self.assertNotIn(CHECKED_AT, repr(evidence))

	def test_module_is_pure_and_has_no_runtime_boundary_imports(self):
		source = inspect.getsource(inspect.getmodule(map_active_probe_result))
		for forbidden in ("import frappe", "audit_health", "frappe.db", "datetime.now", "open("):
			with self.subTest(forbidden=forbidden):
				self.assertNotIn(forbidden, source)

	def test_mapper_exposes_no_readiness_composition_fields(self):
		evidence = map_active_probe_result(_result())
		for fieldname in (
			"sink_ready",
			"schema_ready",
			"retention_signed_off",
			"legal_hold_signed_off",
			"alerting_owner_ready",
			"alert_threshold_ready",
			"rollback_ready",
			"runbook_ready",
		):
			self.assertFalse(hasattr(evidence, fieldname))


if __name__ == "__main__":
	unittest.main()
