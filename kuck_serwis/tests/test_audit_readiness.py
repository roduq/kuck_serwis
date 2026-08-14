import dataclasses
import unittest

from kuck_serwis.audit_readiness import (
	MAX_PROBE_AGE_SECONDS,
	ActiveProbeEvidence,
	AuditReadinessError,
	AuditReadinessErrorCode,
	AuditReadinessEvidence,
	AuditReadinessPlan,
	ReadinessCode,
	plan_audit_readiness,
)

ASSESSED_AT = "2026-08-14T12:00:00Z"


def _complete_evidence(**changes):
	values = {
		"active_probe": ActiveProbeEvidence(True, "2026-08-14T11:55:00Z"),
		"sink_ready": True,
		"schema_ready": True,
		"retention_signed_off": True,
		"legal_hold_signed_off": True,
		"alerting_owner_ready": True,
		"alert_threshold_ready": True,
		"rollback_ready": True,
		"runbook_ready": True,
	}
	values.update(changes)
	return AuditReadinessEvidence(**values)


def _plan(evidence, **changes):
	arguments = {"checked_at": ASSESSED_AT, "max_probe_age_seconds": 600}
	arguments.update(changes)
	return plan_audit_readiness(evidence, **arguments)


class TestAuditReadinessPlanner(unittest.TestCase):
	def test_complete_fresh_evidence_is_ready(self):
		result = _plan(_complete_evidence())
		self.assertEqual(result, AuditReadinessPlan(True, (ReadinessCode.READY,)))
		self.assertEqual(set(result.__slots__), {"capability_ready", "codes"})

	def test_defaults_never_enable_capability_and_report_every_missing_gate(self):
		result = _plan(AuditReadinessEvidence())
		self.assertFalse(result.capability_ready)
		self.assertEqual(
			result.codes,
			(
				ReadinessCode.ACTIVE_PROBE_MISSING,
				ReadinessCode.SINK_NOT_READY,
				ReadinessCode.SCHEMA_NOT_READY,
				ReadinessCode.RETENTION_NOT_SIGNED_OFF,
				ReadinessCode.LEGAL_HOLD_NOT_SIGNED_OFF,
				ReadinessCode.ALERTING_OWNER_NOT_READY,
				ReadinessCode.ALERT_THRESHOLD_NOT_READY,
				ReadinessCode.ROLLBACK_NOT_READY,
				ReadinessCode.RUNBOOK_NOT_READY,
			),
		)

	def test_each_required_boolean_gate_fails_closed(self):
		cases = {
			"sink_ready": ReadinessCode.SINK_NOT_READY,
			"schema_ready": ReadinessCode.SCHEMA_NOT_READY,
			"retention_signed_off": ReadinessCode.RETENTION_NOT_SIGNED_OFF,
			"legal_hold_signed_off": ReadinessCode.LEGAL_HOLD_NOT_SIGNED_OFF,
			"alerting_owner_ready": ReadinessCode.ALERTING_OWNER_NOT_READY,
			"alert_threshold_ready": ReadinessCode.ALERT_THRESHOLD_NOT_READY,
			"rollback_ready": ReadinessCode.ROLLBACK_NOT_READY,
			"runbook_ready": ReadinessCode.RUNBOOK_NOT_READY,
		}
		for fieldname, code in cases.items():
			with self.subTest(fieldname=fieldname):
				result = _plan(_complete_evidence(**{fieldname: False}))
				self.assertEqual(result.codes, (code,))

	def test_failed_probe_is_not_ready_even_when_fresh(self):
		result = _plan(_complete_evidence(active_probe=ActiveProbeEvidence(False, "2026-08-14T11:59:59Z")))
		self.assertEqual(result.codes, (ReadinessCode.ACTIVE_PROBE_FAILED,))

	def test_probe_at_max_age_boundary_is_fresh_and_one_second_older_is_stale(self):
		boundary = _complete_evidence(active_probe=ActiveProbeEvidence(True, "2026-08-14T11:50:00Z"))
		self.assertTrue(_plan(boundary).capability_ready)

		stale = _complete_evidence(active_probe=ActiveProbeEvidence(True, "2026-08-14T11:49:59Z"))
		self.assertEqual(_plan(stale).codes, (ReadinessCode.ACTIVE_PROBE_STALE,))

	def test_future_probe_is_fail_closed(self):
		future = _complete_evidence(active_probe=ActiveProbeEvidence(True, "2026-08-14T12:00:01Z"))
		self.assertEqual(_plan(future).codes, (ReadinessCode.ACTIVE_PROBE_FUTURE,))

	def test_timestamps_must_be_exact_canonical_utc(self):
		invalid_values = (
			"2026-08-14T12:00:00+00:00",
			"2026-08-14T12:00:00.000Z",
			"2026-8-14T12:00:00Z",
			"2026-02-30T12:00:00Z",
			" 2026-08-14T12:00:00Z",
			1,
		)
		for value in invalid_values:
			with self.subTest(value=value):
				with self.assertRaises(AuditReadinessError) as caught:
					ActiveProbeEvidence(True, value)
				self.assertIs(caught.exception.code, AuditReadinessErrorCode.INVALID_PROBE)

		with self.assertRaises(AuditReadinessError) as caught:
			_plan(_complete_evidence(), checked_at="2026-08-14T14:00:00+02:00")
		self.assertIs(caught.exception.code, AuditReadinessErrorCode.INVALID_CHECKED_AT)

	def test_max_age_is_a_bounded_exact_integer(self):
		for value in (True, False, 0, -1, MAX_PROBE_AGE_SECONDS + 1, 1.0, "600"):
			with self.subTest(value=value):
				with self.assertRaises(AuditReadinessError) as caught:
					_plan(_complete_evidence(), max_probe_age_seconds=value)
				self.assertIs(caught.exception.code, AuditReadinessErrorCode.INVALID_MAX_AGE)

	def test_every_boolean_requires_literal_bool(self):
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
			with self.subTest(fieldname=fieldname):
				with self.assertRaises(AuditReadinessError) as caught:
					_complete_evidence(**{fieldname: 1})
				self.assertIs(caught.exception.code, AuditReadinessErrorCode.INVALID_EVIDENCE)

		for value in (1, 0, None, "true"):
			with self.subTest(probe_status=value):
				with self.assertRaises(AuditReadinessError) as caught:
					ActiveProbeEvidence(value, ASSESSED_AT)
				self.assertIs(caught.exception.code, AuditReadinessErrorCode.INVALID_PROBE)

	def test_forged_outer_and_nested_dtos_are_sanitized(self):
		forged_outer = object.__new__(AuditReadinessEvidence)
		with self.assertRaises(AuditReadinessError) as caught:
			_plan(forged_outer)
		self.assertIs(caught.exception.code, AuditReadinessErrorCode.INVALID_EVIDENCE)

		forged_probe = object.__new__(ActiveProbeEvidence)
		object.__setattr__(forged_probe, "ok", True)
		with self.assertRaises(AuditReadinessError) as caught:
			AuditReadinessEvidence(active_probe=forged_probe)
		self.assertIs(caught.exception.code, AuditReadinessErrorCode.INVALID_PROBE)

	def test_wrong_or_subclassed_evidence_is_rejected(self):
		class EvidenceSubclass(AuditReadinessEvidence):
			pass

		for value in ({}, EvidenceSubclass()):
			with self.subTest(value_type=type(value).__name__):
				with self.assertRaises(AuditReadinessError) as caught:
					_plan(value)
				self.assertIs(caught.exception.code, AuditReadinessErrorCode.INVALID_EVIDENCE)

	def test_equivalent_keyword_order_has_identical_plan(self):
		first = _complete_evidence()
		second = AuditReadinessEvidence(
			runbook_ready=True,
			rollback_ready=True,
			alert_threshold_ready=True,
			alerting_owner_ready=True,
			legal_hold_signed_off=True,
			retention_signed_off=True,
			schema_ready=True,
			sink_ready=True,
			active_probe=ActiveProbeEvidence(True, "2026-08-14T11:55:00Z"),
		)
		self.assertEqual(_plan(first), _plan(second))

	def test_result_is_frozen_and_rejects_inconsistent_or_noncanonical_codes(self):
		result = _plan(_complete_evidence())
		with self.assertRaises(dataclasses.FrozenInstanceError):
			result.capability_ready = False

		invalid = (
			(True, (ReadinessCode.SINK_NOT_READY,)),
			(False, (ReadinessCode.READY,)),
			(False, ()),
			(False, (ReadinessCode.SCHEMA_NOT_READY, ReadinessCode.SINK_NOT_READY)),
			(False, (ReadinessCode.SINK_NOT_READY, ReadinessCode.SINK_NOT_READY)),
			(1, (ReadinessCode.READY,)),
		)
		for ready, codes in invalid:
			with self.subTest(ready=ready, codes=codes):
				with self.assertRaises(AuditReadinessError) as caught:
					AuditReadinessPlan(ready, codes)
				self.assertIs(caught.exception.code, AuditReadinessErrorCode.INVALID_RESULT)

	def test_errors_and_results_never_echo_rejected_values(self):
		private = "private@example.test/secret/path"
		with self.assertRaises(AuditReadinessError) as caught:
			ActiveProbeEvidence(True, private)
		self.assertEqual(str(caught.exception), "INVALID_PROBE")
		self.assertNotIn(private, repr(caught.exception))

		result = _plan(AuditReadinessEvidence())
		self.assertNotIn("@", repr(result))
		self.assertNotIn("/", repr(result))


if __name__ == "__main__":
	unittest.main()
