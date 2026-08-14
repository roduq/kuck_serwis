"""Pure readiness planning for the durable repair-audit capability.

The planner consumes already collected evidence.  It does not probe services,
read configuration, use a clock, or enable the public capability.  A future
runtime adapter must collect and persist evidence before calling this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

MAX_PROBE_AGE_SECONDS: Final = 86_400
_CANONICAL_UTC_RE: Final = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class AuditReadinessErrorCode(StrEnum):
	INVALID_EVIDENCE = "INVALID_EVIDENCE"
	INVALID_PROBE = "INVALID_PROBE"
	INVALID_CHECKED_AT = "INVALID_CHECKED_AT"
	INVALID_MAX_AGE = "INVALID_MAX_AGE"
	INVALID_RESULT = "INVALID_RESULT"


class AuditReadinessError(ValueError):
	"""Stable code-only error which never echoes rejected evidence."""

	def __init__(self, code: AuditReadinessErrorCode) -> None:
		self.code = code
		super().__init__(code.value)


class ReadinessCode(StrEnum):
	READY = "READY"
	ACTIVE_PROBE_MISSING = "ACTIVE_PROBE_MISSING"
	ACTIVE_PROBE_FAILED = "ACTIVE_PROBE_FAILED"
	ACTIVE_PROBE_STALE = "ACTIVE_PROBE_STALE"
	ACTIVE_PROBE_FUTURE = "ACTIVE_PROBE_FUTURE"
	SINK_NOT_READY = "SINK_NOT_READY"
	SCHEMA_NOT_READY = "SCHEMA_NOT_READY"
	RETENTION_NOT_SIGNED_OFF = "RETENTION_NOT_SIGNED_OFF"
	LEGAL_HOLD_NOT_SIGNED_OFF = "LEGAL_HOLD_NOT_SIGNED_OFF"
	ALERTING_OWNER_NOT_READY = "ALERTING_OWNER_NOT_READY"
	ALERT_THRESHOLD_NOT_READY = "ALERT_THRESHOLD_NOT_READY"
	ROLLBACK_NOT_READY = "ROLLBACK_NOT_READY"
	RUNBOOK_NOT_READY = "RUNBOOK_NOT_READY"


@dataclass(frozen=True, slots=True)
class ActiveProbeEvidence:
	"""Sanitized outcome of one already executed active probe."""

	ok: bool
	checked_at: str = field(repr=False)

	def __post_init__(self) -> None:
		if type(self.ok) is not bool:
			_fail(AuditReadinessErrorCode.INVALID_PROBE)
		_parse_canonical_utc(self.checked_at, AuditReadinessErrorCode.INVALID_PROBE)


@dataclass(frozen=True, slots=True)
class AuditReadinessEvidence:
	"""Closed evidence set; every default is deliberately fail-closed."""

	active_probe: ActiveProbeEvidence | None = None
	sink_ready: bool = False
	schema_ready: bool = False
	retention_signed_off: bool = False
	legal_hold_signed_off: bool = False
	alerting_owner_ready: bool = False
	alert_threshold_ready: bool = False
	rollback_ready: bool = False
	runbook_ready: bool = False

	def __post_init__(self) -> None:
		if self.active_probe is not None:
			object.__setattr__(self, "active_probe", _rebuild_probe(self.active_probe))
		for fieldname in _BOOLEAN_EVIDENCE_FIELDS:
			if type(getattr(self, fieldname)) is not bool:
				_fail(AuditReadinessErrorCode.INVALID_EVIDENCE)


@dataclass(frozen=True, slots=True)
class AuditReadinessPlan:
	"""Code-only decision; it cannot mutate or enable runtime configuration."""

	capability_ready: bool
	codes: tuple[ReadinessCode, ...]

	def __post_init__(self) -> None:
		if type(self.capability_ready) is not bool or type(self.codes) is not tuple:
			_fail(AuditReadinessErrorCode.INVALID_RESULT)
		if not self.codes or len(self.codes) > len(ReadinessCode):
			_fail(AuditReadinessErrorCode.INVALID_RESULT)
		if any(type(code) is not ReadinessCode for code in self.codes):
			_fail(AuditReadinessErrorCode.INVALID_RESULT)
		if self.capability_ready:
			if self.codes != (ReadinessCode.READY,):
				_fail(AuditReadinessErrorCode.INVALID_RESULT)
			return
		if ReadinessCode.READY in self.codes:
			_fail(AuditReadinessErrorCode.INVALID_RESULT)
		canonical = tuple(
			code for code in ReadinessCode if code is not ReadinessCode.READY and code in self.codes
		)
		if self.codes != canonical:
			_fail(AuditReadinessErrorCode.INVALID_RESULT)


_BOOLEAN_EVIDENCE_FIELDS: Final = (
	"sink_ready",
	"schema_ready",
	"retention_signed_off",
	"legal_hold_signed_off",
	"alerting_owner_ready",
	"alert_threshold_ready",
	"rollback_ready",
	"runbook_ready",
)


def plan_audit_readiness(
	evidence: AuditReadinessEvidence,
	*,
	checked_at: str,
	max_probe_age_seconds: int,
) -> AuditReadinessPlan:
	"""Return a deterministic readiness decision at an explicit UTC instant."""
	validated = _rebuild_evidence(evidence)
	assessed_at = _parse_canonical_utc(checked_at, AuditReadinessErrorCode.INVALID_CHECKED_AT)
	if type(max_probe_age_seconds) is not int or not 1 <= max_probe_age_seconds <= MAX_PROBE_AGE_SECONDS:
		_fail(AuditReadinessErrorCode.INVALID_MAX_AGE)

	failures: set[ReadinessCode] = set()
	probe = validated.active_probe
	if probe is None:
		failures.add(ReadinessCode.ACTIVE_PROBE_MISSING)
	else:
		if not probe.ok:
			failures.add(ReadinessCode.ACTIVE_PROBE_FAILED)
		probe_checked_at = _parse_canonical_utc(probe.checked_at, AuditReadinessErrorCode.INVALID_PROBE)
		age_seconds = int((assessed_at - probe_checked_at).total_seconds())
		if age_seconds < 0:
			failures.add(ReadinessCode.ACTIVE_PROBE_FUTURE)
		elif age_seconds > max_probe_age_seconds:
			failures.add(ReadinessCode.ACTIVE_PROBE_STALE)

	_gate(validated.sink_ready, ReadinessCode.SINK_NOT_READY, failures)
	_gate(validated.schema_ready, ReadinessCode.SCHEMA_NOT_READY, failures)
	_gate(validated.retention_signed_off, ReadinessCode.RETENTION_NOT_SIGNED_OFF, failures)
	_gate(validated.legal_hold_signed_off, ReadinessCode.LEGAL_HOLD_NOT_SIGNED_OFF, failures)
	_gate(validated.alerting_owner_ready, ReadinessCode.ALERTING_OWNER_NOT_READY, failures)
	_gate(validated.alert_threshold_ready, ReadinessCode.ALERT_THRESHOLD_NOT_READY, failures)
	_gate(validated.rollback_ready, ReadinessCode.ROLLBACK_NOT_READY, failures)
	_gate(validated.runbook_ready, ReadinessCode.RUNBOOK_NOT_READY, failures)

	if not failures:
		return AuditReadinessPlan(True, (ReadinessCode.READY,))
	codes = tuple(code for code in ReadinessCode if code is not ReadinessCode.READY and code in failures)
	return AuditReadinessPlan(False, codes)


def _gate(ready: bool, code: ReadinessCode, failures: set[ReadinessCode]) -> None:
	if not ready:
		failures.add(code)


def _rebuild_probe(value: object) -> ActiveProbeEvidence:
	if type(value) is not ActiveProbeEvidence:
		_fail(AuditReadinessErrorCode.INVALID_PROBE)
	try:
		return ActiveProbeEvidence(ok=value.ok, checked_at=value.checked_at)
	except AuditReadinessError:
		raise
	except (AttributeError, TypeError, ValueError):
		_fail(AuditReadinessErrorCode.INVALID_PROBE)


def _rebuild_evidence(value: object) -> AuditReadinessEvidence:
	if type(value) is not AuditReadinessEvidence:
		_fail(AuditReadinessErrorCode.INVALID_EVIDENCE)
	try:
		return AuditReadinessEvidence(
			active_probe=value.active_probe,
			sink_ready=value.sink_ready,
			schema_ready=value.schema_ready,
			retention_signed_off=value.retention_signed_off,
			legal_hold_signed_off=value.legal_hold_signed_off,
			alerting_owner_ready=value.alerting_owner_ready,
			alert_threshold_ready=value.alert_threshold_ready,
			rollback_ready=value.rollback_ready,
			runbook_ready=value.runbook_ready,
		)
	except AuditReadinessError:
		raise
	except (AttributeError, TypeError, ValueError):
		_fail(AuditReadinessErrorCode.INVALID_EVIDENCE)


def _parse_canonical_utc(value: object, code: AuditReadinessErrorCode) -> datetime:
	if type(value) is not str or _CANONICAL_UTC_RE.fullmatch(value) is None:
		_fail(code)
	try:
		parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
	except ValueError:
		_fail(code)
	if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
		_fail(code)
	return parsed


def _fail(code: AuditReadinessErrorCode) -> None:
	raise AuditReadinessError(code)
