"""Pure operational retention and alert policy for Kuck.

The module is a closed, deterministic contract.  It does not read a clock,
configuration, users, roles, a database or a metrics backend.  It cannot send
mail, delete evidence, enable a scheduler or activate a public capability.
Future adapters must collect trusted, PII-free observations before calling the
planners below.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

POLICY_ID: Final = "kuck-operational-evidence/v1"
POLICY_VERSION: Final = 1
POLICY_OWNER: Final = "KUCK ZEGARKI BIŻUTERIA SP.J."
MODERATOR_ROLE: Final = "Kuck Store Moderator"
POLICY_REVISION_SHA256: Final = "d5f14c0bdc2a55ff0ea42319c7ccd3b218ed5c0d787a17e2c3cc287ee31591d1"

MAX_COUNT: Final = 10**12
MAX_AGE_SECONDS: Final = 10 * 365 * 24 * 60 * 60
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")


class OperationalPolicyErrorCode(StrEnum):
	INVALID_POLICY = "INVALID_POLICY"
	POLICY_REVISION_MISMATCH = "POLICY_REVISION_MISMATCH"
	INVALID_APPROVALS = "INVALID_APPROVALS"
	INVALID_RECIPIENT_EVIDENCE = "INVALID_RECIPIENT_EVIDENCE"
	INVALID_HOLD_EVIDENCE = "INVALID_HOLD_EVIDENCE"
	INVALID_BACKUP_EVIDENCE = "INVALID_BACKUP_EVIDENCE"
	INVALID_OBSERVATION = "INVALID_OBSERVATION"
	INVALID_RESULT = "INVALID_RESULT"


class OperationalPolicyError(ValueError):
	"""Stable code-only error which never echoes configuration or evidence."""

	def __init__(self, code: OperationalPolicyErrorCode) -> None:
		if type(code) is not OperationalPolicyErrorCode:
			raise TypeError("INVALID_ERROR_CODE")
		self.code = code
		super().__init__(code.value)

	def __repr__(self) -> str:
		return f"OperationalPolicyError(code={self.code.value!r})"


class EvidenceType(StrEnum):
	REPAIR_AUDIT_EVENT = "REPAIR_AUDIT_EVENT"
	READINESS_ALERT_EVIDENCE = "READINESS_ALERT_EVIDENCE"
	OPERATIONAL_METRICS = "OPERATIONAL_METRICS"
	REPAIR_PHOTO = "REPAIR_PHOTO"
	SEO_SNAPSHOT = "SEO_SNAPSHOT"


class RetentionAnchor(StrEnum):
	CREATED_AT = "CREATED_AT"
	REPAIR_TERMINAL_AT = "REPAIR_TERMINAL_AT"


class BackupClass(StrEnum):
	DAILY = "DAILY"
	WEEKLY = "WEEKLY"
	MONTHLY = "MONTHLY"


class AlertSignal(StrEnum):
	AUDIT_SINK = "AUDIT_SINK"
	ACTIVE_PROBE = "ACTIVE_PROBE"
	PASSIVE_PROBE = "PASSIVE_PROBE"
	COLLECTOR = "COLLECTOR"
	METRICS_EXPORTER = "METRICS_EXPORTER"
	PUBLIC_ID_INVALID = "PUBLIC_ID_INVALID"
	STATUS_INVALID = "STATUS_INVALID"
	DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
	PUBLIC_CONTRACT_LATENCY_10M = "PUBLIC_CONTRACT_LATENCY_10M"
	PUBLIC_CONTRACT_LATENCY_5M = "PUBLIC_CONTRACT_LATENCY_5M"
	AUDIT_PURGE = "AUDIT_PURGE"
	PHOTO_RETENTION_DRY_RUN = "PHOTO_RETENTION_DRY_RUN"
	PHOTO_RETENTION_APPLY = "PHOTO_RETENTION_APPLY"
	LEGAL_HOLD_CONTROL = "LEGAL_HOLD_CONTROL"
	BACKUP = "BACKUP"
	RESTORE_DRILL = "RESTORE_DRILL"
	CAPACITY = "CAPACITY"
	ALERT_ROUTING = "ALERT_ROUTING"


class AlertSeverity(StrEnum):
	NONE = "NONE"
	WARNING = "WARNING"
	CRITICAL = "CRITICAL"


class AlertTarget(StrEnum):
	PRIMARY = "PRIMARY"
	ESCALATION = "ESCALATION"
	BUSINESS_OWNER = "BUSINESS_OWNER"
	ALL = "ALL"


_WARNING_ESCALATION_SPEC: Final = (
	(0, AlertTarget.PRIMARY),
	(4 * 60 * 60, AlertTarget.ESCALATION),
	(24 * 60 * 60, AlertTarget.ALL),
)
_CRITICAL_ESCALATION_SPEC: Final = (
	(0, AlertTarget.PRIMARY),
	(15 * 60, AlertTarget.ESCALATION),
	(60 * 60, AlertTarget.BUSINESS_OWNER),
	(4 * 60 * 60, AlertTarget.ALL),
)


class PolicyReadinessCode(StrEnum):
	READY = "READY"
	BUSINESS_APPROVAL_MISSING = "BUSINESS_APPROVAL_MISSING"
	LEGAL_APPROVAL_MISSING = "LEGAL_APPROVAL_MISSING"
	OPERATIONS_APPROVAL_MISSING = "OPERATIONS_APPROVAL_MISSING"
	MODERATOR_ROLE_NOT_READY = "MODERATOR_ROLE_NOT_READY"
	PRIMARY_RECIPIENT_NOT_READY = "PRIMARY_RECIPIENT_NOT_READY"
	ESCALATION_RECIPIENT_NOT_READY = "ESCALATION_RECIPIENT_NOT_READY"
	RECIPIENTS_NOT_DISTINCT = "RECIPIENTS_NOT_DISTINCT"
	RECIPIENT_USER_STATE_NOT_READY = "RECIPIENT_USER_STATE_NOT_READY"
	DELIVERY_TEST_NOT_READY = "DELIVERY_TEST_NOT_READY"
	HOLD_REGISTRY_NOT_READY = "HOLD_REGISTRY_NOT_READY"
	HOLD_APPROVER_NOT_READY = "HOLD_APPROVER_NOT_READY"
	HOLD_SEGREGATION_NOT_READY = "HOLD_SEGREGATION_NOT_READY"
	HOLD_REVIEW_NOT_READY = "HOLD_REVIEW_NOT_READY"
	BACKUP_OFF_HOST_NOT_READY = "BACKUP_OFF_HOST_NOT_READY"
	BACKUP_TARGETS_NOT_READY = "BACKUP_TARGETS_NOT_READY"
	BACKUP_FRESHNESS_NOT_READY = "BACKUP_FRESHNESS_NOT_READY"
	RESTORE_DRILL_NOT_READY = "RESTORE_DRILL_NOT_READY"


class AlertCode(StrEnum):
	OK = "OK"
	AUDIT_SINK_FAILURE = "AUDIT_SINK_FAILURE"
	ACTIVE_PROBE_WARNING = "ACTIVE_PROBE_WARNING"
	ACTIVE_PROBE_CRITICAL = "ACTIVE_PROBE_CRITICAL"
	PASSIVE_PROBE_WARNING = "PASSIVE_PROBE_WARNING"
	PASSIVE_PROBE_CRITICAL = "PASSIVE_PROBE_CRITICAL"
	COLLECTOR_WARNING = "COLLECTOR_WARNING"
	COLLECTOR_CRITICAL = "COLLECTOR_CRITICAL"
	METRICS_EXPORTER_STALE = "METRICS_EXPORTER_STALE"
	PUBLIC_ID_INVALID = "PUBLIC_ID_INVALID"
	STATUS_INVALID = "STATUS_INVALID"
	DEPENDENCY_UNAVAILABLE_WARNING = "DEPENDENCY_UNAVAILABLE_WARNING"
	DEPENDENCY_UNAVAILABLE_CRITICAL = "DEPENDENCY_UNAVAILABLE_CRITICAL"
	PUBLIC_CONTRACT_LATENCY_WARNING = "PUBLIC_CONTRACT_LATENCY_WARNING"
	PUBLIC_CONTRACT_LATENCY_CRITICAL = "PUBLIC_CONTRACT_LATENCY_CRITICAL"
	AUDIT_PURGE_WARNING = "AUDIT_PURGE_WARNING"
	AUDIT_PURGE_CRITICAL = "AUDIT_PURGE_CRITICAL"
	PHOTO_DRY_RUN_WARNING = "PHOTO_DRY_RUN_WARNING"
	PHOTO_DRY_RUN_CRITICAL = "PHOTO_DRY_RUN_CRITICAL"
	PHOTO_APPLY_WARNING = "PHOTO_APPLY_WARNING"
	PHOTO_APPLY_CRITICAL = "PHOTO_APPLY_CRITICAL"
	LEGAL_HOLD_CONTROL_FAILURE = "LEGAL_HOLD_CONTROL_FAILURE"
	BACKUP_WARNING = "BACKUP_WARNING"
	BACKUP_CRITICAL = "BACKUP_CRITICAL"
	RESTORE_DRILL_WARNING = "RESTORE_DRILL_WARNING"
	RESTORE_DRILL_CRITICAL = "RESTORE_DRILL_CRITICAL"
	CAPACITY_WARNING = "CAPACITY_WARNING"
	CAPACITY_CRITICAL = "CAPACITY_CRITICAL"
	ALERT_ROUTING_FAILURE = "ALERT_ROUTING_FAILURE"


@dataclass(frozen=True, slots=True)
class RetentionRule:
	evidence_type: EvidenceType
	retention_days: int
	anchor: RetentionAnchor
	legal_hold_overrides: bool = True

	def __post_init__(self) -> None:
		if (
			type(self.evidence_type) is not EvidenceType
			or type(self.retention_days) is not int
			or not 1 <= self.retention_days <= 3650
			or type(self.anchor) is not RetentionAnchor
			or self.legal_hold_overrides is not True
		):
			_fail(OperationalPolicyErrorCode.INVALID_POLICY)


@dataclass(frozen=True, slots=True)
class BackupRotationRule:
	backup_class: BackupClass
	retention_days: int

	def __post_init__(self) -> None:
		if (
			type(self.backup_class) is not BackupClass
			or type(self.retention_days) is not int
			or not 1 <= self.retention_days <= 3650
		):
			_fail(OperationalPolicyErrorCode.INVALID_POLICY)


@dataclass(frozen=True, slots=True)
class AlertThresholds:
	active_probe_fresh_seconds: int
	passive_probe_fresh_seconds: int
	collector_warning_seconds: int
	collector_critical_seconds: int
	metrics_critical_seconds: int
	dependency_warning_count: int
	dependency_warning_basis_points: int
	dependency_critical_count: int
	dependency_critical_basis_points: int
	latency_warning_ms: int
	latency_critical_ms: int
	audit_purge_warning_seconds: int
	audit_purge_critical_seconds: int
	audit_cutoff_warning_seconds: int
	audit_cutoff_critical_seconds: int
	photo_dry_run_warning_seconds: int
	photo_dry_run_critical_seconds: int
	photo_apply_warning_seconds: int
	photo_apply_critical_seconds: int
	photo_oldest_critical_seconds: int
	backup_warning_seconds: int
	backup_critical_seconds: int
	restore_drill_warning_days: int
	restore_drill_critical_days: int
	capacity_warning_basis_points: int
	capacity_critical_basis_points: int
	capacity_forecast_days: int

	def __post_init__(self) -> None:
		for value in (
			self.__dict__.values()
			if hasattr(self, "__dict__")
			else (getattr(self, name) for name in self.__slots__)
		):
			if type(value) is not int or value < 0:
				_fail(OperationalPolicyErrorCode.INVALID_POLICY)


@dataclass(frozen=True, slots=True, repr=False)
class OperationalPolicyV1:
	policy_id: str
	version: int
	owner: str = field(repr=False)
	policy_revision_sha256: str = field(repr=False)
	retention_rules: tuple[RetentionRule, ...]
	backup_rotation: tuple[BackupRotationRule, ...]
	restore_clone_days: int
	backup_rpo_hours: int
	backup_rto_hours: int
	restore_drill_days: int
	legal_hold_review_days: int
	recipient_role: str
	minimum_distinct_recipients: int
	delivery_test_max_age_days: int
	alert_thresholds: AlertThresholds

	def __post_init__(self) -> None:
		if (
			type(self.policy_id) is not str
			or type(self.version) is not int
			or type(self.owner) is not str
			or not _valid_sha256(self.policy_revision_sha256)
			or type(self.retention_rules) is not tuple
			or type(self.backup_rotation) is not tuple
			or type(self.restore_clone_days) is not int
			or type(self.backup_rpo_hours) is not int
			or type(self.backup_rto_hours) is not int
			or type(self.restore_drill_days) is not int
			or type(self.legal_hold_review_days) is not int
			or type(self.recipient_role) is not str
			or type(self.minimum_distinct_recipients) is not int
			or type(self.delivery_test_max_age_days) is not int
			or type(self.alert_thresholds) is not AlertThresholds
		):
			_fail(OperationalPolicyErrorCode.INVALID_POLICY)
		if any(type(rule) is not RetentionRule for rule in self.retention_rules):
			_fail(OperationalPolicyErrorCode.INVALID_POLICY)
		if any(type(rule) is not BackupRotationRule for rule in self.backup_rotation):
			_fail(OperationalPolicyErrorCode.INVALID_POLICY)

	def __repr__(self) -> str:
		return f"OperationalPolicyV1(policy_id={self.policy_id!r}, version={self.version!r}, <redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class PolicyApprovalEvidence:
	policy_revision_sha256: str = field(repr=False)
	business_approved: bool
	legal_approved: bool
	operations_approved: bool

	def __post_init__(self) -> None:
		if not _valid_sha256(self.policy_revision_sha256) or any(
			type(value) is not bool
			for value in (self.business_approved, self.legal_approved, self.operations_approved)
		):
			_fail(OperationalPolicyErrorCode.INVALID_APPROVALS)

	def __repr__(self) -> str:
		return "PolicyApprovalEvidence(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class RecipientRouteEvidence:
	policy_revision_sha256: str = field(repr=False)
	moderator_role_configured: bool
	primary_user_count: int
	escalation_user_count: int
	distinct_user_count: int
	all_users_enabled: bool
	all_emails_valid: bool
	delivery_test_age_days: int

	def __post_init__(self) -> None:
		if not _valid_sha256(self.policy_revision_sha256):
			_fail(OperationalPolicyErrorCode.INVALID_RECIPIENT_EVIDENCE)
		for value in (self.moderator_role_configured, self.all_users_enabled, self.all_emails_valid):
			if type(value) is not bool:
				_fail(OperationalPolicyErrorCode.INVALID_RECIPIENT_EVIDENCE)
		for value in (
			self.primary_user_count,
			self.escalation_user_count,
			self.distinct_user_count,
			self.delivery_test_age_days,
		):
			if type(value) is not int or not 0 <= value <= 10_000:
				_fail(OperationalPolicyErrorCode.INVALID_RECIPIENT_EVIDENCE)

	def __repr__(self) -> str:
		return "RecipientRouteEvidence(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LegalHoldControlEvidence:
	policy_revision_sha256: str = field(repr=False)
	registry_readable: bool
	approver_configured: bool
	purge_operator_separate: bool
	review_interval_days: int
	overdue_hold_count: int
	unknown_hold_count: int

	def __post_init__(self) -> None:
		if not _valid_sha256(self.policy_revision_sha256):
			_fail(OperationalPolicyErrorCode.INVALID_HOLD_EVIDENCE)
		for value in (self.registry_readable, self.approver_configured, self.purge_operator_separate):
			if type(value) is not bool:
				_fail(OperationalPolicyErrorCode.INVALID_HOLD_EVIDENCE)
		for value in (self.review_interval_days, self.overdue_hold_count, self.unknown_hold_count):
			if type(value) is not int or not 0 <= value <= MAX_COUNT:
				_fail(OperationalPolicyErrorCode.INVALID_HOLD_EVIDENCE)

	def __repr__(self) -> str:
		return "LegalHoldControlEvidence(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class BackupControlEvidence:
	policy_revision_sha256: str = field(repr=False)
	off_host_copy_ready: bool
	rpo_hours: int
	rto_hours: int
	restore_drill_interval_days: int
	latest_verified_backup_age_seconds: int
	latest_restore_drill_age_days: int

	def __post_init__(self) -> None:
		if not _valid_sha256(self.policy_revision_sha256) or type(self.off_host_copy_ready) is not bool:
			_fail(OperationalPolicyErrorCode.INVALID_BACKUP_EVIDENCE)
		for value in (
			self.rpo_hours,
			self.rto_hours,
			self.restore_drill_interval_days,
			self.latest_verified_backup_age_seconds,
			self.latest_restore_drill_age_days,
		):
			if type(value) is not int or not 0 <= value <= MAX_AGE_SECONDS:
				_fail(OperationalPolicyErrorCode.INVALID_BACKUP_EVIDENCE)

	def __repr__(self) -> str:
		return "BackupControlEvidence(<redacted>)"


@dataclass(frozen=True, slots=True)
class OperationalPolicyReadinessPlan:
	policy_ready: bool
	codes: tuple[PolicyReadinessCode, ...]
	purge_authorized: bool = False
	delivery_authorized: bool = False
	activation_authorized: bool = False

	def __post_init__(self) -> None:
		if (
			type(self.policy_ready) is not bool
			or type(self.codes) is not tuple
			or not self.codes
			or any(type(code) is not PolicyReadinessCode for code in self.codes)
			or self.purge_authorized is not False
			or self.delivery_authorized is not False
			or self.activation_authorized is not False
		):
			_fail(OperationalPolicyErrorCode.INVALID_RESULT)
		if self.policy_ready:
			if self.codes != (PolicyReadinessCode.READY,):
				_fail(OperationalPolicyErrorCode.INVALID_RESULT)
		elif PolicyReadinessCode.READY in self.codes:
			_fail(OperationalPolicyErrorCode.INVALID_RESULT)
		canonical = tuple(code for code in PolicyReadinessCode if code in self.codes)
		if self.codes != canonical:
			_fail(OperationalPolicyErrorCode.INVALID_RESULT)


@dataclass(frozen=True, slots=True, repr=False)
class AlertObservation:
	signal: AlertSignal
	count: int = 0
	secondary_count: int = 0
	age_seconds: int = 0
	secondary_age_seconds: int = 0
	rate_basis_points: int = 0
	latency_ms: int = 0
	current_basis_points: int = 0
	days_to_warning_capacity: int = 0
	integrity_ok: bool = True

	def __post_init__(self) -> None:
		if type(self.signal) is not AlertSignal or type(self.integrity_ok) is not bool:
			_fail(OperationalPolicyErrorCode.INVALID_OBSERVATION)
		for value in (self.count, self.secondary_count):
			if type(value) is not int or not 0 <= value <= MAX_COUNT:
				_fail(OperationalPolicyErrorCode.INVALID_OBSERVATION)
		for value in (self.age_seconds, self.secondary_age_seconds, self.latency_ms):
			if type(value) is not int or not 0 <= value <= MAX_AGE_SECONDS:
				_fail(OperationalPolicyErrorCode.INVALID_OBSERVATION)
		for value in (self.rate_basis_points, self.current_basis_points):
			if type(value) is not int or not 0 <= value <= 10_000:
				_fail(OperationalPolicyErrorCode.INVALID_OBSERVATION)
		if type(self.days_to_warning_capacity) is not int or not 0 <= self.days_to_warning_capacity <= 3650:
			_fail(OperationalPolicyErrorCode.INVALID_OBSERVATION)

	def __repr__(self) -> str:
		return f"AlertObservation(signal={self.signal.value!r}, <redacted>)"


@dataclass(frozen=True, slots=True)
class EscalationStep:
	after_seconds: int
	target: AlertTarget

	def __post_init__(self) -> None:
		if (
			type(self.after_seconds) is not int
			or not 0 <= self.after_seconds <= 86_400
			or type(self.target) is not AlertTarget
		):
			_fail(OperationalPolicyErrorCode.INVALID_RESULT)


@dataclass(frozen=True, slots=True)
class AlertDecision:
	severity: AlertSeverity
	code: AlertCode
	readiness_evidence_ok: bool
	escalation: tuple[EscalationStep, ...]
	purge_authorized: bool = False
	delivery_authorized: bool = False
	activation_authorized: bool = False

	def __post_init__(self) -> None:
		if (
			type(self.severity) is not AlertSeverity
			or type(self.code) is not AlertCode
			or type(self.readiness_evidence_ok) is not bool
			or type(self.escalation) is not tuple
			or any(type(step) is not EscalationStep for step in self.escalation)
			or self.purge_authorized is not False
			or self.delivery_authorized is not False
			or self.activation_authorized is not False
		):
			_fail(OperationalPolicyErrorCode.INVALID_RESULT)
		if self.severity is AlertSeverity.NONE:
			if self.code is not AlertCode.OK or not self.readiness_evidence_ok or self.escalation:
				_fail(OperationalPolicyErrorCode.INVALID_RESULT)
		elif self.code is AlertCode.OK or self.readiness_evidence_ok or not self.escalation:
			_fail(OperationalPolicyErrorCode.INVALID_RESULT)


def build_operational_policy_v1() -> OperationalPolicyV1:
	"""Return the exact approved policy manifest and verify its canonical digest."""

	policy = OperationalPolicyV1(
		policy_id=POLICY_ID,
		version=POLICY_VERSION,
		owner=POLICY_OWNER,
		policy_revision_sha256=POLICY_REVISION_SHA256,
		retention_rules=(
			RetentionRule(EvidenceType.REPAIR_AUDIT_EVENT, 180, RetentionAnchor.CREATED_AT),
			RetentionRule(EvidenceType.READINESS_ALERT_EVIDENCE, 180, RetentionAnchor.CREATED_AT),
			RetentionRule(EvidenceType.OPERATIONAL_METRICS, 90, RetentionAnchor.CREATED_AT),
			RetentionRule(EvidenceType.REPAIR_PHOTO, 1095, RetentionAnchor.REPAIR_TERMINAL_AT),
			RetentionRule(EvidenceType.SEO_SNAPSHOT, 90, RetentionAnchor.CREATED_AT),
		),
		backup_rotation=(
			BackupRotationRule(BackupClass.DAILY, 14),
			BackupRotationRule(BackupClass.WEEKLY, 56),
			BackupRotationRule(BackupClass.MONTHLY, 180),
		),
		restore_clone_days=7,
		backup_rpo_hours=24,
		backup_rto_hours=8,
		restore_drill_days=90,
		legal_hold_review_days=90,
		recipient_role=MODERATOR_ROLE,
		minimum_distinct_recipients=2,
		delivery_test_max_age_days=30,
		alert_thresholds=_thresholds(),
	)
	if _policy_digest(policy) != POLICY_REVISION_SHA256:
		_fail(OperationalPolicyErrorCode.POLICY_REVISION_MISMATCH)
	return policy


def plan_operational_policy_readiness_v1(
	policy: OperationalPolicyV1,
	*,
	approvals: PolicyApprovalEvidence,
	recipients: RecipientRouteEvidence,
	holds: LegalHoldControlEvidence,
	backups: BackupControlEvidence,
) -> OperationalPolicyReadinessPlan:
	"""Evaluate policy evidence without authorizing any runtime operation."""

	validated_policy = _validated_policy(policy)
	validated_approvals = _rebuild_approvals(approvals)
	validated_recipients = _rebuild_recipients(recipients)
	validated_holds = _rebuild_holds(holds)
	validated_backups = _rebuild_backups(backups)
	for revision in (
		validated_approvals.policy_revision_sha256,
		validated_recipients.policy_revision_sha256,
		validated_holds.policy_revision_sha256,
		validated_backups.policy_revision_sha256,
	):
		if revision != validated_policy.policy_revision_sha256:
			_fail(OperationalPolicyErrorCode.POLICY_REVISION_MISMATCH)

	failures: set[PolicyReadinessCode] = set()
	_gate(validated_approvals.business_approved, PolicyReadinessCode.BUSINESS_APPROVAL_MISSING, failures)
	_gate(validated_approvals.legal_approved, PolicyReadinessCode.LEGAL_APPROVAL_MISSING, failures)
	_gate(
		validated_approvals.operations_approved,
		PolicyReadinessCode.OPERATIONS_APPROVAL_MISSING,
		failures,
	)
	_gate(
		validated_recipients.moderator_role_configured,
		PolicyReadinessCode.MODERATOR_ROLE_NOT_READY,
		failures,
	)
	_gate(
		validated_recipients.primary_user_count >= 1,
		PolicyReadinessCode.PRIMARY_RECIPIENT_NOT_READY,
		failures,
	)
	_gate(
		validated_recipients.escalation_user_count >= 1,
		PolicyReadinessCode.ESCALATION_RECIPIENT_NOT_READY,
		failures,
	)
	_gate(
		validated_recipients.distinct_user_count >= validated_policy.minimum_distinct_recipients,
		PolicyReadinessCode.RECIPIENTS_NOT_DISTINCT,
		failures,
	)
	_gate(
		validated_recipients.all_users_enabled and validated_recipients.all_emails_valid,
		PolicyReadinessCode.RECIPIENT_USER_STATE_NOT_READY,
		failures,
	)
	_gate(
		validated_recipients.delivery_test_age_days <= validated_policy.delivery_test_max_age_days,
		PolicyReadinessCode.DELIVERY_TEST_NOT_READY,
		failures,
	)
	_gate(validated_holds.registry_readable, PolicyReadinessCode.HOLD_REGISTRY_NOT_READY, failures)
	_gate(validated_holds.approver_configured, PolicyReadinessCode.HOLD_APPROVER_NOT_READY, failures)
	_gate(
		validated_holds.purge_operator_separate,
		PolicyReadinessCode.HOLD_SEGREGATION_NOT_READY,
		failures,
	)
	_gate(
		validated_holds.review_interval_days == validated_policy.legal_hold_review_days
		and validated_holds.overdue_hold_count == 0
		and validated_holds.unknown_hold_count == 0,
		PolicyReadinessCode.HOLD_REVIEW_NOT_READY,
		failures,
	)
	_gate(validated_backups.off_host_copy_ready, PolicyReadinessCode.BACKUP_OFF_HOST_NOT_READY, failures)
	_gate(
		validated_backups.rpo_hours == validated_policy.backup_rpo_hours
		and validated_backups.rto_hours == validated_policy.backup_rto_hours
		and validated_backups.restore_drill_interval_days == validated_policy.restore_drill_days,
		PolicyReadinessCode.BACKUP_TARGETS_NOT_READY,
		failures,
	)
	_gate(
		validated_backups.latest_verified_backup_age_seconds
		<= validated_policy.alert_thresholds.backup_warning_seconds,
		PolicyReadinessCode.BACKUP_FRESHNESS_NOT_READY,
		failures,
	)
	_gate(
		validated_backups.latest_restore_drill_age_days <= validated_policy.restore_drill_days,
		PolicyReadinessCode.RESTORE_DRILL_NOT_READY,
		failures,
	)
	if not failures:
		return OperationalPolicyReadinessPlan(True, (PolicyReadinessCode.READY,))
	return OperationalPolicyReadinessPlan(
		False,
		tuple(
			code for code in PolicyReadinessCode if code is not PolicyReadinessCode.READY and code in failures
		),
	)


def retention_rule_for_v1(
	policy: OperationalPolicyV1,
	evidence_type: EvidenceType,
) -> RetentionRule:
	"""Return one exact rule; unknown or forged evidence classes fail closed."""

	validated_policy = _validated_policy(policy)
	if type(evidence_type) is not EvidenceType:
		_fail(OperationalPolicyErrorCode.INVALID_POLICY)
	matches = tuple(rule for rule in validated_policy.retention_rules if rule.evidence_type is evidence_type)
	if len(matches) != 1:
		_fail(OperationalPolicyErrorCode.INVALID_POLICY)
	return matches[0]


def evaluate_alert_observation_v1(
	policy: OperationalPolicyV1,
	observation: AlertObservation,
) -> AlertDecision:
	"""Classify one trusted code-only observation using exact v1 thresholds."""

	validated_policy = _validated_policy(policy)
	value = _rebuild_observation(observation)
	thresholds = validated_policy.alert_thresholds
	severity, code = _classify(value, thresholds)
	if severity is AlertSeverity.NONE:
		return AlertDecision(severity, code, True, ())
	return AlertDecision(severity, code, False, _escalation(severity))


def _classify(value: AlertObservation, thresholds: AlertThresholds) -> tuple[AlertSeverity, AlertCode]:
	if value.signal is AlertSignal.AUDIT_SINK:
		return _critical_if(value.count >= 1, AlertCode.AUDIT_SINK_FAILURE)
	if value.signal is AlertSignal.ACTIVE_PROBE:
		if value.count >= 2 or value.age_seconds > thresholds.active_probe_fresh_seconds:
			return AlertSeverity.CRITICAL, AlertCode.ACTIVE_PROBE_CRITICAL
		return _warning_if(value.count >= 1, AlertCode.ACTIVE_PROBE_WARNING)
	if value.signal is AlertSignal.PASSIVE_PROBE:
		if value.count >= 2 or value.age_seconds > thresholds.passive_probe_fresh_seconds:
			return AlertSeverity.CRITICAL, AlertCode.PASSIVE_PROBE_CRITICAL
		return _warning_if(value.count >= 1, AlertCode.PASSIVE_PROBE_WARNING)
	if value.signal is AlertSignal.COLLECTOR:
		return _age_levels(
			value.age_seconds,
			thresholds.collector_warning_seconds,
			thresholds.collector_critical_seconds,
			AlertCode.COLLECTOR_WARNING,
			AlertCode.COLLECTOR_CRITICAL,
		)
	if value.signal is AlertSignal.METRICS_EXPORTER:
		return _critical_if(
			value.age_seconds > thresholds.metrics_critical_seconds,
			AlertCode.METRICS_EXPORTER_STALE,
		)
	if value.signal is AlertSignal.PUBLIC_ID_INVALID:
		return _critical_if(value.count > 0, AlertCode.PUBLIC_ID_INVALID)
	if value.signal is AlertSignal.STATUS_INVALID:
		return _critical_if(value.count > 0, AlertCode.STATUS_INVALID)
	if value.signal is AlertSignal.DEPENDENCY_UNAVAILABLE:
		if (
			value.count >= thresholds.dependency_critical_count
			or value.rate_basis_points > thresholds.dependency_critical_basis_points
		):
			return AlertSeverity.CRITICAL, AlertCode.DEPENDENCY_UNAVAILABLE_CRITICAL
		if (
			value.count >= thresholds.dependency_warning_count
			and value.rate_basis_points > thresholds.dependency_warning_basis_points
		):
			return AlertSeverity.WARNING, AlertCode.DEPENDENCY_UNAVAILABLE_WARNING
		return AlertSeverity.NONE, AlertCode.OK
	if value.signal is AlertSignal.PUBLIC_CONTRACT_LATENCY_10M:
		return _warning_if(
			value.latency_ms > thresholds.latency_warning_ms,
			AlertCode.PUBLIC_CONTRACT_LATENCY_WARNING,
		)
	if value.signal is AlertSignal.PUBLIC_CONTRACT_LATENCY_5M:
		return _critical_if(
			value.latency_ms > thresholds.latency_critical_ms,
			AlertCode.PUBLIC_CONTRACT_LATENCY_CRITICAL,
		)
	if value.signal is AlertSignal.AUDIT_PURGE:
		if (
			value.age_seconds > thresholds.audit_purge_critical_seconds
			or value.secondary_age_seconds > thresholds.audit_cutoff_critical_seconds
		):
			return AlertSeverity.CRITICAL, AlertCode.AUDIT_PURGE_CRITICAL
		if (
			value.age_seconds > thresholds.audit_purge_warning_seconds
			or value.secondary_age_seconds > thresholds.audit_cutoff_warning_seconds
		):
			return AlertSeverity.WARNING, AlertCode.AUDIT_PURGE_WARNING
		return AlertSeverity.NONE, AlertCode.OK
	if value.signal is AlertSignal.PHOTO_RETENTION_DRY_RUN:
		return _age_levels(
			value.age_seconds,
			thresholds.photo_dry_run_warning_seconds,
			thresholds.photo_dry_run_critical_seconds,
			AlertCode.PHOTO_DRY_RUN_WARNING,
			AlertCode.PHOTO_DRY_RUN_CRITICAL,
		)
	if value.signal is AlertSignal.PHOTO_RETENTION_APPLY:
		if (
			value.age_seconds > thresholds.photo_apply_critical_seconds
			or value.secondary_age_seconds > thresholds.photo_oldest_critical_seconds
		):
			return AlertSeverity.CRITICAL, AlertCode.PHOTO_APPLY_CRITICAL
		return _warning_if(
			value.age_seconds > thresholds.photo_apply_warning_seconds,
			AlertCode.PHOTO_APPLY_WARNING,
		)
	if value.signal is AlertSignal.LEGAL_HOLD_CONTROL:
		return _critical_if(
			value.count > 0 or value.secondary_count > 0 or not value.integrity_ok,
			AlertCode.LEGAL_HOLD_CONTROL_FAILURE,
		)
	if value.signal is AlertSignal.BACKUP:
		if not value.integrity_ok or value.age_seconds > thresholds.backup_critical_seconds:
			return AlertSeverity.CRITICAL, AlertCode.BACKUP_CRITICAL
		return _warning_if(
			value.age_seconds > thresholds.backup_warning_seconds,
			AlertCode.BACKUP_WARNING,
		)
	if value.signal is AlertSignal.RESTORE_DRILL:
		if value.age_seconds > thresholds.restore_drill_critical_days * 86_400:
			return AlertSeverity.CRITICAL, AlertCode.RESTORE_DRILL_CRITICAL
		return _warning_if(
			value.age_seconds > thresholds.restore_drill_warning_days * 86_400,
			AlertCode.RESTORE_DRILL_WARNING,
		)
	if value.signal is AlertSignal.CAPACITY:
		if value.current_basis_points >= thresholds.capacity_critical_basis_points:
			return AlertSeverity.CRITICAL, AlertCode.CAPACITY_CRITICAL
		if (
			value.current_basis_points >= thresholds.capacity_warning_basis_points
			or 1 <= value.days_to_warning_capacity <= thresholds.capacity_forecast_days
		):
			return AlertSeverity.WARNING, AlertCode.CAPACITY_WARNING
		return AlertSeverity.NONE, AlertCode.OK
	if value.signal is AlertSignal.ALERT_ROUTING:
		return _critical_if(not value.integrity_ok or value.count > 0, AlertCode.ALERT_ROUTING_FAILURE)
	_fail(OperationalPolicyErrorCode.INVALID_OBSERVATION)


def _critical_if(condition: bool, code: AlertCode) -> tuple[AlertSeverity, AlertCode]:
	return (AlertSeverity.CRITICAL, code) if condition else (AlertSeverity.NONE, AlertCode.OK)


def _warning_if(condition: bool, code: AlertCode) -> tuple[AlertSeverity, AlertCode]:
	return (AlertSeverity.WARNING, code) if condition else (AlertSeverity.NONE, AlertCode.OK)


def _age_levels(
	age: int,
	warning: int,
	critical: int,
	warning_code: AlertCode,
	critical_code: AlertCode,
) -> tuple[AlertSeverity, AlertCode]:
	if age > critical:
		return AlertSeverity.CRITICAL, critical_code
	if age > warning:
		return AlertSeverity.WARNING, warning_code
	return AlertSeverity.NONE, AlertCode.OK


def _escalation(severity: AlertSeverity) -> tuple[EscalationStep, ...]:
	if severity is AlertSeverity.CRITICAL:
		return tuple(
			EscalationStep(after_seconds, target) for after_seconds, target in _CRITICAL_ESCALATION_SPEC
		)
	if severity is AlertSeverity.WARNING:
		return tuple(
			EscalationStep(after_seconds, target) for after_seconds, target in _WARNING_ESCALATION_SPEC
		)
	_fail(OperationalPolicyErrorCode.INVALID_RESULT)


def _thresholds() -> AlertThresholds:
	return AlertThresholds(
		active_probe_fresh_seconds=600,
		passive_probe_fresh_seconds=120,
		collector_warning_seconds=90,
		collector_critical_seconds=120,
		metrics_critical_seconds=120,
		dependency_warning_count=5,
		dependency_warning_basis_points=100,
		dependency_critical_count=20,
		dependency_critical_basis_points=500,
		latency_warning_ms=500,
		latency_critical_ms=1000,
		audit_purge_warning_seconds=26 * 60 * 60,
		audit_purge_critical_seconds=48 * 60 * 60,
		audit_cutoff_warning_seconds=24 * 60 * 60,
		audit_cutoff_critical_seconds=72 * 60 * 60,
		photo_dry_run_warning_seconds=26 * 60 * 60,
		photo_dry_run_critical_seconds=48 * 60 * 60,
		photo_apply_warning_seconds=8 * 24 * 60 * 60,
		photo_apply_critical_seconds=15 * 24 * 60 * 60,
		photo_oldest_critical_seconds=14 * 24 * 60 * 60,
		backup_warning_seconds=26 * 60 * 60,
		backup_critical_seconds=48 * 60 * 60,
		restore_drill_warning_days=90,
		restore_drill_critical_days=100,
		capacity_warning_basis_points=8000,
		capacity_critical_basis_points=9000,
		capacity_forecast_days=30,
	)


def _policy_digest(policy: OperationalPolicyV1) -> str:
	payload = {
		"alert_thresholds": {
			name: getattr(policy.alert_thresholds, name) for name in policy.alert_thresholds.__slots__
		},
		"backup_rotation": [
			{"backup_class": rule.backup_class.value, "retention_days": rule.retention_days}
			for rule in policy.backup_rotation
		],
		"backup_rpo_hours": policy.backup_rpo_hours,
		"backup_rto_hours": policy.backup_rto_hours,
		"legal_hold_review_days": policy.legal_hold_review_days,
		"minimum_distinct_recipients": policy.minimum_distinct_recipients,
		"delivery_test_max_age_days": policy.delivery_test_max_age_days,
		"owner": policy.owner,
		"policy_id": policy.policy_id,
		"recipient_role": policy.recipient_role,
		"restore_clone_days": policy.restore_clone_days,
		"restore_drill_days": policy.restore_drill_days,
		"warning_escalation": [
			{"after_seconds": after_seconds, "target": target.value}
			for after_seconds, target in _WARNING_ESCALATION_SPEC
		],
		"critical_escalation": [
			{"after_seconds": after_seconds, "target": target.value}
			for after_seconds, target in _CRITICAL_ESCALATION_SPEC
		],
		"retention_rules": [
			{
				"anchor": rule.anchor.value,
				"evidence_type": rule.evidence_type.value,
				"legal_hold_overrides": rule.legal_hold_overrides,
				"retention_days": rule.retention_days,
			}
			for rule in policy.retention_rules
		],
		"version": policy.version,
	}
	canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
	return hashlib.sha256(canonical).hexdigest()


def _validated_policy(value: object) -> OperationalPolicyV1:
	if type(value) is not OperationalPolicyV1:
		_fail(OperationalPolicyErrorCode.INVALID_POLICY)
	try:
		rebuilt = OperationalPolicyV1(
			policy_id=value.policy_id,
			version=value.version,
			owner=value.owner,
			policy_revision_sha256=value.policy_revision_sha256,
			retention_rules=tuple(
				RetentionRule(rule.evidence_type, rule.retention_days, rule.anchor, rule.legal_hold_overrides)
				for rule in value.retention_rules
			),
			backup_rotation=tuple(
				BackupRotationRule(rule.backup_class, rule.retention_days) for rule in value.backup_rotation
			),
			restore_clone_days=value.restore_clone_days,
			backup_rpo_hours=value.backup_rpo_hours,
			backup_rto_hours=value.backup_rto_hours,
			restore_drill_days=value.restore_drill_days,
			legal_hold_review_days=value.legal_hold_review_days,
			recipient_role=value.recipient_role,
			minimum_distinct_recipients=value.minimum_distinct_recipients,
			delivery_test_max_age_days=value.delivery_test_max_age_days,
			alert_thresholds=AlertThresholds(
				**{name: getattr(value.alert_thresholds, name) for name in value.alert_thresholds.__slots__}
			),
		)
	except OperationalPolicyError:
		raise
	except (AttributeError, TypeError, ValueError):
		_fail(OperationalPolicyErrorCode.INVALID_POLICY)
	expected = _unchecked_policy()
	if rebuilt != expected or _policy_digest(rebuilt) != POLICY_REVISION_SHA256:
		_fail(OperationalPolicyErrorCode.POLICY_REVISION_MISMATCH)
	return rebuilt


def _unchecked_policy() -> OperationalPolicyV1:
	"""Build the manifest without recursively validating its digest."""

	return OperationalPolicyV1(
		policy_id=POLICY_ID,
		version=POLICY_VERSION,
		owner=POLICY_OWNER,
		policy_revision_sha256=POLICY_REVISION_SHA256,
		retention_rules=(
			RetentionRule(EvidenceType.REPAIR_AUDIT_EVENT, 180, RetentionAnchor.CREATED_AT),
			RetentionRule(EvidenceType.READINESS_ALERT_EVIDENCE, 180, RetentionAnchor.CREATED_AT),
			RetentionRule(EvidenceType.OPERATIONAL_METRICS, 90, RetentionAnchor.CREATED_AT),
			RetentionRule(EvidenceType.REPAIR_PHOTO, 1095, RetentionAnchor.REPAIR_TERMINAL_AT),
			RetentionRule(EvidenceType.SEO_SNAPSHOT, 90, RetentionAnchor.CREATED_AT),
		),
		backup_rotation=(
			BackupRotationRule(BackupClass.DAILY, 14),
			BackupRotationRule(BackupClass.WEEKLY, 56),
			BackupRotationRule(BackupClass.MONTHLY, 180),
		),
		restore_clone_days=7,
		backup_rpo_hours=24,
		backup_rto_hours=8,
		restore_drill_days=90,
		legal_hold_review_days=90,
		recipient_role=MODERATOR_ROLE,
		minimum_distinct_recipients=2,
		delivery_test_max_age_days=30,
		alert_thresholds=_thresholds(),
	)


def _rebuild_approvals(value: object) -> PolicyApprovalEvidence:
	if type(value) is not PolicyApprovalEvidence:
		_fail(OperationalPolicyErrorCode.INVALID_APPROVALS)
	try:
		return PolicyApprovalEvidence(
			value.policy_revision_sha256,
			value.business_approved,
			value.legal_approved,
			value.operations_approved,
		)
	except OperationalPolicyError:
		raise
	except (AttributeError, TypeError, ValueError):
		_fail(OperationalPolicyErrorCode.INVALID_APPROVALS)


def _rebuild_recipients(value: object) -> RecipientRouteEvidence:
	if type(value) is not RecipientRouteEvidence:
		_fail(OperationalPolicyErrorCode.INVALID_RECIPIENT_EVIDENCE)
	try:
		return RecipientRouteEvidence(*(getattr(value, name) for name in value.__slots__))
	except OperationalPolicyError:
		raise
	except (AttributeError, TypeError, ValueError):
		_fail(OperationalPolicyErrorCode.INVALID_RECIPIENT_EVIDENCE)


def _rebuild_holds(value: object) -> LegalHoldControlEvidence:
	if type(value) is not LegalHoldControlEvidence:
		_fail(OperationalPolicyErrorCode.INVALID_HOLD_EVIDENCE)
	try:
		return LegalHoldControlEvidence(*(getattr(value, name) for name in value.__slots__))
	except OperationalPolicyError:
		raise
	except (AttributeError, TypeError, ValueError):
		_fail(OperationalPolicyErrorCode.INVALID_HOLD_EVIDENCE)


def _rebuild_backups(value: object) -> BackupControlEvidence:
	if type(value) is not BackupControlEvidence:
		_fail(OperationalPolicyErrorCode.INVALID_BACKUP_EVIDENCE)
	try:
		return BackupControlEvidence(*(getattr(value, name) for name in value.__slots__))
	except OperationalPolicyError:
		raise
	except (AttributeError, TypeError, ValueError):
		_fail(OperationalPolicyErrorCode.INVALID_BACKUP_EVIDENCE)


def _rebuild_observation(value: object) -> AlertObservation:
	if type(value) is not AlertObservation:
		_fail(OperationalPolicyErrorCode.INVALID_OBSERVATION)
	try:
		return AlertObservation(*(getattr(value, name) for name in value.__slots__))
	except OperationalPolicyError:
		raise
	except (AttributeError, TypeError, ValueError):
		_fail(OperationalPolicyErrorCode.INVALID_OBSERVATION)


def _gate(condition: bool, code: PolicyReadinessCode, failures: set[PolicyReadinessCode]) -> None:
	if not condition:
		failures.add(code)


def _valid_sha256(value: object) -> bool:
	return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _fail(code: OperationalPolicyErrorCode) -> None:
	raise OperationalPolicyError(code) from None
