import ast
import dataclasses
import inspect
import unittest

from kuck_serwis.operational_policy_v1 import (
	MODERATOR_ROLE,
	POLICY_ID,
	POLICY_OWNER,
	POLICY_REVISION_SHA256,
	POLICY_VERSION,
	AlertCode,
	AlertDecision,
	AlertObservation,
	AlertSeverity,
	AlertSignal,
	AlertTarget,
	BackupClass,
	BackupControlEvidence,
	EvidenceType,
	LegalHoldControlEvidence,
	OperationalPolicyError,
	OperationalPolicyErrorCode,
	PolicyApprovalEvidence,
	PolicyReadinessCode,
	RecipientRouteEvidence,
	RetentionAnchor,
	build_operational_policy_v1,
	evaluate_alert_observation_v1,
	plan_operational_policy_readiness_v1,
	retention_rule_for_v1,
)

POLICY = build_operational_policy_v1()
DAY = 86_400


def approvals(**changes):
	values = {
		"policy_revision_sha256": POLICY_REVISION_SHA256,
		"business_approved": True,
		"legal_approved": True,
		"operations_approved": True,
	}
	values.update(changes)
	return PolicyApprovalEvidence(**values)


def recipients(**changes):
	values = {
		"policy_revision_sha256": POLICY_REVISION_SHA256,
		"moderator_role_configured": True,
		"primary_user_count": 1,
		"escalation_user_count": 1,
		"distinct_user_count": 2,
		"all_users_enabled": True,
		"all_emails_valid": True,
		"delivery_test_age_days": 30,
	}
	values.update(changes)
	return RecipientRouteEvidence(**values)


def holds(**changes):
	values = {
		"policy_revision_sha256": POLICY_REVISION_SHA256,
		"registry_readable": True,
		"approver_configured": True,
		"purge_operator_separate": True,
		"review_interval_days": 90,
		"overdue_hold_count": 0,
		"unknown_hold_count": 0,
	}
	values.update(changes)
	return LegalHoldControlEvidence(**values)


def backups(**changes):
	values = {
		"policy_revision_sha256": POLICY_REVISION_SHA256,
		"off_host_copy_ready": True,
		"rpo_hours": 24,
		"rto_hours": 8,
		"restore_drill_interval_days": 90,
		"latest_verified_backup_age_seconds": 26 * 60 * 60,
		"latest_restore_drill_age_days": 90,
	}
	values.update(changes)
	return BackupControlEvidence(**values)


def readiness(**changes):
	values = {
		"approvals": approvals(),
		"recipients": recipients(),
		"holds": holds(),
		"backups": backups(),
	}
	values.update(changes)
	return plan_operational_policy_readiness_v1(POLICY, **values)


def decision(signal, **changes):
	return evaluate_alert_observation_v1(POLICY, AlertObservation(signal, **changes))


class TestOperationalPolicyManifest(unittest.TestCase):
	def assert_code(self, code, call):
		with self.assertRaises(OperationalPolicyError) as raised:
			call()
		self.assertIs(raised.exception.code, code)
		self.assertEqual(str(raised.exception), code.value)

	def test_identity_owner_and_digest_are_exact(self):
		self.assertEqual(POLICY.policy_id, POLICY_ID)
		self.assertEqual(POLICY.version, POLICY_VERSION)
		self.assertEqual(POLICY.owner, POLICY_OWNER)
		self.assertEqual(
			POLICY.policy_revision_sha256,
			"d5f14c0bdc2a55ff0ea42319c7ccd3b218ed5c0d787a17e2c3cc287ee31591d1",
		)
		self.assertEqual(
			repr(POLICY),
			"OperationalPolicyV1(policy_id='kuck-operational-evidence/v1', version=1, <redacted>)",
		)

	def test_retention_rules_are_complete_unique_and_exact(self):
		actual = {
			rule.evidence_type: (rule.retention_days, rule.anchor, rule.legal_hold_overrides)
			for rule in POLICY.retention_rules
		}
		self.assertEqual(len(actual), len(POLICY.retention_rules))
		self.assertEqual(
			actual,
			{
				EvidenceType.REPAIR_AUDIT_EVENT: (180, RetentionAnchor.CREATED_AT, True),
				EvidenceType.READINESS_ALERT_EVIDENCE: (180, RetentionAnchor.CREATED_AT, True),
				EvidenceType.OPERATIONAL_METRICS: (90, RetentionAnchor.CREATED_AT, True),
				EvidenceType.REPAIR_PHOTO: (1095, RetentionAnchor.REPAIR_TERMINAL_AT, True),
				EvidenceType.SEO_SNAPSHOT: (90, RetentionAnchor.CREATED_AT, True),
			},
		)

	def test_rule_lookup_rejects_unknown_or_string_evidence_type(self):
		self.assertEqual(
			retention_rule_for_v1(POLICY, EvidenceType.REPAIR_PHOTO).retention_days,
			1095,
		)
		for value in ("REPAIR_PHOTO", None, object()):
			with self.subTest(value=value):
				self.assert_code(
					OperationalPolicyErrorCode.INVALID_POLICY,
					lambda value=value: retention_rule_for_v1(POLICY, value),
				)

	def test_backup_rotation_and_recovery_targets_are_exact(self):
		self.assertEqual(
			{rule.backup_class: rule.retention_days for rule in POLICY.backup_rotation},
			{BackupClass.DAILY: 14, BackupClass.WEEKLY: 56, BackupClass.MONTHLY: 180},
		)
		self.assertEqual(
			(
				POLICY.restore_clone_days,
				POLICY.backup_rpo_hours,
				POLICY.backup_rto_hours,
				POLICY.restore_drill_days,
				POLICY.legal_hold_review_days,
			),
			(7, 24, 8, 90, 90),
		)

	def test_recipient_model_is_role_based_minimum_two_without_data_access_claim(self):
		self.assertEqual(POLICY.recipient_role, MODERATOR_ROLE)
		self.assertEqual(POLICY.minimum_distinct_recipients, 2)
		self.assertEqual(POLICY.delivery_test_max_age_days, 30)
		self.assertNotIn("permission", {field.name for field in dataclasses.fields(POLICY)})
		self.assertNotIn("email", {field.name for field in dataclasses.fields(POLICY)})

	def test_manifest_and_nested_values_are_frozen(self):
		for value, field_name, replacement in (
			(POLICY, "version", 2),
			(POLICY.retention_rules[0], "retention_days", 1),
			(POLICY.backup_rotation[0], "retention_days", 1),
			(POLICY.alert_thresholds, "active_probe_fresh_seconds", 1),
		):
			with self.subTest(value_type=type(value).__name__, field_name=field_name):
				with self.assertRaises(dataclasses.FrozenInstanceError):
					setattr(value, field_name, replacement)

	def test_forged_or_modified_policy_fails_closed(self):
		forged = object.__new__(type(POLICY))
		self.assert_code(
			OperationalPolicyErrorCode.INVALID_POLICY,
			lambda: plan_operational_policy_readiness_v1(
				forged,
				approvals=approvals(),
				recipients=recipients(),
				holds=holds(),
				backups=backups(),
			),
		)
		modified = dataclasses.replace(POLICY, restore_clone_days=8)
		self.assert_code(
			OperationalPolicyErrorCode.POLICY_REVISION_MISMATCH,
			lambda: evaluate_alert_observation_v1(modified, AlertObservation(AlertSignal.AUDIT_SINK)),
		)

	def test_module_is_pure_and_has_no_runtime_integration_import(self):
		module = inspect.getmodule(build_operational_policy_v1)
		tree = ast.parse(inspect.getsource(module))
		imports = {
			alias.name.split(".")[0]
			for node in ast.walk(tree)
			if isinstance(node, ast.Import)
			for alias in node.names
		}
		imports.update(
			node.module.split(".")[0]
			for node in ast.walk(tree)
			if isinstance(node, ast.ImportFrom) and node.module
		)
		self.assertTrue(imports.isdisjoint({"frappe", "requests", "socket", "subprocess", "pathlib", "os"}))
		self.assertFalse(any(isinstance(node, (ast.With, ast.AsyncWith)) for node in ast.walk(tree)))


class TestOperationalPolicyReadiness(unittest.TestCase):
	def test_complete_exact_evidence_is_policy_ready_but_authorizes_nothing(self):
		result = readiness()
		self.assertTrue(result.policy_ready)
		self.assertEqual(result.codes, (PolicyReadinessCode.READY,))
		self.assertFalse(result.purge_authorized)
		self.assertFalse(result.delivery_authorized)
		self.assertFalse(result.activation_authorized)

	def test_each_approval_is_an_independent_fail_closed_gate(self):
		cases = (
			("business_approved", PolicyReadinessCode.BUSINESS_APPROVAL_MISSING),
			("legal_approved", PolicyReadinessCode.LEGAL_APPROVAL_MISSING),
			("operations_approved", PolicyReadinessCode.OPERATIONS_APPROVAL_MISSING),
		)
		for field_name, code in cases:
			with self.subTest(field_name=field_name):
				self.assertEqual(readiness(approvals=approvals(**{field_name: False})).codes, (code,))

	def test_recipient_route_requires_role_primary_escalation_and_two_distinct_users(self):
		cases = (
			({"moderator_role_configured": False}, PolicyReadinessCode.MODERATOR_ROLE_NOT_READY),
			({"primary_user_count": 0}, PolicyReadinessCode.PRIMARY_RECIPIENT_NOT_READY),
			({"escalation_user_count": 0}, PolicyReadinessCode.ESCALATION_RECIPIENT_NOT_READY),
			({"distinct_user_count": 1}, PolicyReadinessCode.RECIPIENTS_NOT_DISTINCT),
		)
		for changes, code in cases:
			with self.subTest(changes=changes):
				self.assertEqual(readiness(recipients=recipients(**changes)).codes, (code,))

	def test_disabled_user_invalid_email_or_stale_delivery_test_blocks_readiness(self):
		for changes in ({"all_users_enabled": False}, {"all_emails_valid": False}):
			with self.subTest(changes=changes):
				self.assertEqual(
					readiness(recipients=recipients(**changes)).codes,
					(PolicyReadinessCode.RECIPIENT_USER_STATE_NOT_READY,),
				)
		self.assertEqual(
			readiness(recipients=recipients(delivery_test_age_days=31)).codes,
			(PolicyReadinessCode.DELIVERY_TEST_NOT_READY,),
		)

	def test_delivery_test_boundary_is_closed_at_thirty_days(self):
		self.assertTrue(readiness(recipients=recipients(delivery_test_age_days=30)).policy_ready)

	def test_hold_registry_approver_segregation_unknown_and_overdue_fail_closed(self):
		cases = (
			({"registry_readable": False}, PolicyReadinessCode.HOLD_REGISTRY_NOT_READY),
			({"approver_configured": False}, PolicyReadinessCode.HOLD_APPROVER_NOT_READY),
			({"purge_operator_separate": False}, PolicyReadinessCode.HOLD_SEGREGATION_NOT_READY),
			({"review_interval_days": 91}, PolicyReadinessCode.HOLD_REVIEW_NOT_READY),
			({"overdue_hold_count": 1}, PolicyReadinessCode.HOLD_REVIEW_NOT_READY),
			({"unknown_hold_count": 1}, PolicyReadinessCode.HOLD_REVIEW_NOT_READY),
		)
		for changes, code in cases:
			with self.subTest(changes=changes):
				result = readiness(holds=holds(**changes))
				self.assertEqual(result.codes, (code,))
				self.assertFalse(result.purge_authorized)

	def test_backup_targets_offhost_freshness_and_restore_drill_are_exact_gates(self):
		cases = (
			({"off_host_copy_ready": False}, PolicyReadinessCode.BACKUP_OFF_HOST_NOT_READY),
			({"rpo_hours": 23}, PolicyReadinessCode.BACKUP_TARGETS_NOT_READY),
			({"rto_hours": 9}, PolicyReadinessCode.BACKUP_TARGETS_NOT_READY),
			({"restore_drill_interval_days": 91}, PolicyReadinessCode.BACKUP_TARGETS_NOT_READY),
			(
				{"latest_verified_backup_age_seconds": 26 * 60 * 60 + 1},
				PolicyReadinessCode.BACKUP_FRESHNESS_NOT_READY,
			),
			({"latest_restore_drill_age_days": 91}, PolicyReadinessCode.RESTORE_DRILL_NOT_READY),
		)
		for changes, code in cases:
			with self.subTest(changes=changes):
				self.assertEqual(readiness(backups=backups(**changes)).codes, (code,))

	def test_every_evidence_must_bind_to_exact_policy_digest(self):
		wrong = "f" * 64
		for changes in (
			{"approvals": approvals(policy_revision_sha256=wrong)},
			{"recipients": recipients(policy_revision_sha256=wrong)},
			{"holds": holds(policy_revision_sha256=wrong)},
			{"backups": backups(policy_revision_sha256=wrong)},
		):
			with self.subTest(changes=tuple(changes)):
				with self.assertRaises(OperationalPolicyError) as raised:
					readiness(**changes)
				self.assertIs(raised.exception.code, OperationalPolicyErrorCode.POLICY_REVISION_MISMATCH)

	def test_literal_boolean_and_bounded_count_validation(self):
		for call in (
			lambda: approvals(business_approved=1),
			lambda: recipients(primary_user_count=True),
			lambda: holds(overdue_hold_count=-1),
			lambda: backups(off_host_copy_ready=1),
		):
			with self.subTest(call=call):
				with self.assertRaises(OperationalPolicyError):
					call()

	def test_multiple_failures_have_stable_enum_order(self):
		result = readiness(
			approvals=approvals(business_approved=False, operations_approved=False),
			recipients=recipients(primary_user_count=0, distinct_user_count=1),
		)
		self.assertEqual(
			result.codes,
			(
				PolicyReadinessCode.BUSINESS_APPROVAL_MISSING,
				PolicyReadinessCode.OPERATIONS_APPROVAL_MISSING,
				PolicyReadinessCode.PRIMARY_RECIPIENT_NOT_READY,
				PolicyReadinessCode.RECIPIENTS_NOT_DISTINCT,
			),
		)


class TestAlertObservationEvaluator(unittest.TestCase):
	def assert_decision(self, result, severity, code):
		self.assertEqual((result.severity, result.code), (severity, code))
		self.assertEqual(result.readiness_evidence_ok, severity is AlertSeverity.NONE)
		self.assertFalse(result.purge_authorized)
		self.assertFalse(result.delivery_authorized)
		self.assertFalse(result.activation_authorized)

	def test_no_breach_is_code_only_ok_without_escalation(self):
		result = decision(AlertSignal.AUDIT_SINK)
		self.assertEqual(result, AlertDecision(AlertSeverity.NONE, AlertCode.OK, True, ()))

	def test_sink_and_schema_integrity_counts_are_immediate_critical(self):
		cases = (
			(AlertSignal.AUDIT_SINK, AlertCode.AUDIT_SINK_FAILURE),
			(AlertSignal.PUBLIC_ID_INVALID, AlertCode.PUBLIC_ID_INVALID),
			(AlertSignal.STATUS_INVALID, AlertCode.STATUS_INVALID),
		)
		for signal, code in cases:
			with self.subTest(signal=signal):
				self.assert_decision(decision(signal, count=1), AlertSeverity.CRITICAL, code)

	def test_active_probe_boundaries(self):
		self.assert_decision(
			decision(AlertSignal.ACTIVE_PROBE, count=1, age_seconds=600),
			AlertSeverity.WARNING,
			AlertCode.ACTIVE_PROBE_WARNING,
		)
		for changes in ({"count": 2}, {"age_seconds": 601}):
			self.assert_decision(
				decision(AlertSignal.ACTIVE_PROBE, **changes),
				AlertSeverity.CRITICAL,
				AlertCode.ACTIVE_PROBE_CRITICAL,
			)

	def test_passive_probe_boundaries(self):
		self.assert_decision(
			decision(AlertSignal.PASSIVE_PROBE, count=1, age_seconds=120),
			AlertSeverity.WARNING,
			AlertCode.PASSIVE_PROBE_WARNING,
		)
		self.assert_decision(
			decision(AlertSignal.PASSIVE_PROBE, age_seconds=121),
			AlertSeverity.CRITICAL,
			AlertCode.PASSIVE_PROBE_CRITICAL,
		)

	def test_collector_and_metrics_freshness_boundaries(self):
		self.assert_decision(
			decision(AlertSignal.COLLECTOR, age_seconds=90), AlertSeverity.NONE, AlertCode.OK
		)
		self.assert_decision(
			decision(AlertSignal.COLLECTOR, age_seconds=91),
			AlertSeverity.WARNING,
			AlertCode.COLLECTOR_WARNING,
		)
		self.assert_decision(
			decision(AlertSignal.COLLECTOR, age_seconds=121),
			AlertSeverity.CRITICAL,
			AlertCode.COLLECTOR_CRITICAL,
		)
		self.assert_decision(
			decision(AlertSignal.METRICS_EXPORTER, age_seconds=121),
			AlertSeverity.CRITICAL,
			AlertCode.METRICS_EXPORTER_STALE,
		)

	def test_dependency_count_and_rate_use_warning_and_critical_formula(self):
		self.assert_decision(
			decision(AlertSignal.DEPENDENCY_UNAVAILABLE, count=5, rate_basis_points=100),
			AlertSeverity.NONE,
			AlertCode.OK,
		)
		self.assert_decision(
			decision(AlertSignal.DEPENDENCY_UNAVAILABLE, count=5, rate_basis_points=101),
			AlertSeverity.WARNING,
			AlertCode.DEPENDENCY_UNAVAILABLE_WARNING,
		)
		for changes in ({"count": 20}, {"rate_basis_points": 501}):
			self.assert_decision(
				decision(AlertSignal.DEPENDENCY_UNAVAILABLE, **changes),
				AlertSeverity.CRITICAL,
				AlertCode.DEPENDENCY_UNAVAILABLE_CRITICAL,
			)

	def test_latency_boundaries_use_exact_windows(self):
		self.assert_decision(
			decision(AlertSignal.PUBLIC_CONTRACT_LATENCY_10M, latency_ms=501),
			AlertSeverity.WARNING,
			AlertCode.PUBLIC_CONTRACT_LATENCY_WARNING,
		)
		self.assert_decision(
			decision(AlertSignal.PUBLIC_CONTRACT_LATENCY_5M, latency_ms=1001),
			AlertSeverity.CRITICAL,
			AlertCode.PUBLIC_CONTRACT_LATENCY_CRITICAL,
		)

	def test_audit_purge_checks_run_and_cutoff_age(self):
		self.assert_decision(
			decision(AlertSignal.AUDIT_PURGE, age_seconds=26 * 3600 + 1),
			AlertSeverity.WARNING,
			AlertCode.AUDIT_PURGE_WARNING,
		)
		self.assert_decision(
			decision(AlertSignal.AUDIT_PURGE, secondary_age_seconds=72 * 3600 + 1),
			AlertSeverity.CRITICAL,
			AlertCode.AUDIT_PURGE_CRITICAL,
		)

	def test_photo_dry_run_and_apply_boundaries(self):
		self.assert_decision(
			decision(AlertSignal.PHOTO_RETENTION_DRY_RUN, age_seconds=26 * 3600 + 1),
			AlertSeverity.WARNING,
			AlertCode.PHOTO_DRY_RUN_WARNING,
		)
		self.assert_decision(
			decision(AlertSignal.PHOTO_RETENTION_DRY_RUN, age_seconds=48 * 3600 + 1),
			AlertSeverity.CRITICAL,
			AlertCode.PHOTO_DRY_RUN_CRITICAL,
		)
		self.assert_decision(
			decision(AlertSignal.PHOTO_RETENTION_APPLY, age_seconds=8 * DAY + 1),
			AlertSeverity.WARNING,
			AlertCode.PHOTO_APPLY_WARNING,
		)
		self.assert_decision(
			decision(AlertSignal.PHOTO_RETENTION_APPLY, secondary_age_seconds=14 * DAY + 1),
			AlertSeverity.CRITICAL,
			AlertCode.PHOTO_APPLY_CRITICAL,
		)

	def test_hold_unknown_overdue_or_unreadable_is_critical(self):
		for changes in ({"count": 1}, {"secondary_count": 1}, {"integrity_ok": False}):
			with self.subTest(changes=changes):
				self.assert_decision(
					decision(AlertSignal.LEGAL_HOLD_CONTROL, **changes),
					AlertSeverity.CRITICAL,
					AlertCode.LEGAL_HOLD_CONTROL_FAILURE,
				)

	def test_backup_age_and_integrity_are_fail_closed(self):
		self.assert_decision(
			decision(AlertSignal.BACKUP, age_seconds=26 * 3600 + 1),
			AlertSeverity.WARNING,
			AlertCode.BACKUP_WARNING,
		)
		for changes in ({"age_seconds": 48 * 3600 + 1}, {"integrity_ok": False}):
			self.assert_decision(
				decision(AlertSignal.BACKUP, **changes),
				AlertSeverity.CRITICAL,
				AlertCode.BACKUP_CRITICAL,
			)

	def test_restore_drill_and_capacity_boundaries(self):
		self.assert_decision(
			decision(AlertSignal.RESTORE_DRILL, age_seconds=90 * DAY + 1),
			AlertSeverity.WARNING,
			AlertCode.RESTORE_DRILL_WARNING,
		)
		self.assert_decision(
			decision(AlertSignal.RESTORE_DRILL, age_seconds=100 * DAY + 1),
			AlertSeverity.CRITICAL,
			AlertCode.RESTORE_DRILL_CRITICAL,
		)
		self.assert_decision(
			decision(AlertSignal.CAPACITY, days_to_warning_capacity=30),
			AlertSeverity.WARNING,
			AlertCode.CAPACITY_WARNING,
		)
		self.assert_decision(
			decision(AlertSignal.CAPACITY, current_basis_points=9000),
			AlertSeverity.CRITICAL,
			AlertCode.CAPACITY_CRITICAL,
		)

	def test_alert_routing_failure_is_itself_critical(self):
		self.assert_decision(
			decision(AlertSignal.ALERT_ROUTING, integrity_ok=False),
			AlertSeverity.CRITICAL,
			AlertCode.ALERT_ROUTING_FAILURE,
		)

	def test_escalation_schedules_are_exact_and_authorize_no_delivery(self):
		warning = decision(AlertSignal.COLLECTOR, age_seconds=91)
		self.assertEqual(
			tuple((step.after_seconds, step.target) for step in warning.escalation),
			((0, AlertTarget.PRIMARY), (4 * 3600, AlertTarget.ESCALATION), (24 * 3600, AlertTarget.ALL)),
		)
		critical = decision(AlertSignal.AUDIT_SINK, count=1)
		self.assertEqual(
			tuple((step.after_seconds, step.target) for step in critical.escalation),
			(
				(0, AlertTarget.PRIMARY),
				(15 * 60, AlertTarget.ESCALATION),
				(60 * 60, AlertTarget.BUSINESS_OWNER),
				(4 * 3600, AlertTarget.ALL),
			),
		)
		self.assertFalse(warning.delivery_authorized)
		self.assertFalse(critical.delivery_authorized)

	def test_observation_is_redacted_and_rejects_nonliteral_or_negative_values(self):
		value = AlertObservation(AlertSignal.DEPENDENCY_UNAVAILABLE, count=5, rate_basis_points=101)
		self.assertEqual(repr(value), "AlertObservation(signal='DEPENDENCY_UNAVAILABLE', <redacted>)")
		for call in (
			lambda: AlertObservation("AUDIT_SINK"),
			lambda: AlertObservation(AlertSignal.AUDIT_SINK, count=True),
			lambda: AlertObservation(AlertSignal.AUDIT_SINK, age_seconds=-1),
			lambda: AlertObservation(AlertSignal.DEPENDENCY_UNAVAILABLE, rate_basis_points=10_001),
			lambda: AlertObservation(AlertSignal.CAPACITY, days_to_warning_capacity=3651),
			lambda: AlertObservation(AlertSignal.BACKUP, integrity_ok=1),
		):
			with self.subTest(call=call):
				with self.assertRaises(OperationalPolicyError) as raised:
					call()
				self.assertIs(raised.exception.code, OperationalPolicyErrorCode.INVALID_OBSERVATION)

	def test_result_shapes_never_contain_recipient_or_domain_identifiers(self):
		for cls in (AlertObservation, AlertDecision):
			fields = {field.name for field in dataclasses.fields(cls)}
			self.assertTrue(
				fields.isdisjoint(
					{
						"email",
						"user",
						"customer",
						"repair_id",
						"file_id",
						"hold_id",
						"url",
						"hostname",
						"correlation_id",
					}
				)
			)


if __name__ == "__main__":
	unittest.main()
