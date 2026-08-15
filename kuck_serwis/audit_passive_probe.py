"""Pure planning for one point-in-time passive repair-audit observation.

The caller must collect every observation from trusted runtime boundaries.  This
module only validates and classifies those already collected booleans.  It does
not read configuration, inspect a database, own a clock, persist freshness, set
readiness gates, or enable a capability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

_CANONICAL_UTC_RE: Final = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class PassiveProbeCode(StrEnum):
	PASSIVE_NOT_RUN = "PASSIVE_NOT_RUN"
	PASSIVE_OK = "PASSIVE_OK"
	PASSIVE_CONNECTION_UNAVAILABLE = "PASSIVE_CONNECTION_UNAVAILABLE"
	PASSIVE_SCHEMA_MISMATCH = "PASSIVE_SCHEMA_MISMATCH"
	PASSIVE_PUBLIC_ID_INVALID = "PASSIVE_PUBLIC_ID_INVALID"
	PASSIVE_STATUS_INVALID = "PASSIVE_STATUS_INVALID"
	PASSIVE_RETENTION_INVALID = "PASSIVE_RETENTION_INVALID"
	PASSIVE_HOLD_UNAVAILABLE = "PASSIVE_HOLD_UNAVAILABLE"
	PASSIVE_METRICS_UNAVAILABLE = "PASSIVE_METRICS_UNAVAILABLE"
	PASSIVE_PURGE_STALE = "PASSIVE_PURGE_STALE"
	PASSIVE_INTERNAL_ERROR = "PASSIVE_INTERNAL_ERROR"


class PassiveProbeErrorCode(StrEnum):
	INVALID_OBSERVATIONS = "INVALID_OBSERVATIONS"
	INVALID_CHECKED_AT = "INVALID_CHECKED_AT"
	INVALID_RESULT = "INVALID_RESULT"


class PassiveProbeError(ValueError):
	"""Stable code-only failure without rejected observation data."""

	def __init__(self, code: PassiveProbeErrorCode) -> None:
		if type(code) is not PassiveProbeErrorCode:
			raise TypeError("INVALID_ERROR_CODE")
		self.code = code
		super().__init__(code.value)

	def __repr__(self) -> str:
		return f"PassiveProbeError(code={self.code.value!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PassiveProbeObservations:
	"""Exact facts supplied by a future trusted passive-monitor adapter."""

	connection_available: bool = field(repr=False)
	schema_matches: bool = field(repr=False)
	public_ids_valid: bool = field(repr=False)
	statuses_valid: bool = field(repr=False)
	retention_valid: bool = field(repr=False)
	hold_available: bool = field(repr=False)
	metrics_available: bool = field(repr=False)
	purge_fresh: bool = field(repr=False)

	def __post_init__(self) -> None:
		for field_name in _OBSERVATION_FIELDS:
			if type(getattr(self, field_name)) is not bool:
				_fail(PassiveProbeErrorCode.INVALID_OBSERVATIONS)

	def __repr__(self) -> str:
		return "PassiveProbeObservations(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class PassiveProbeAssessment:
	"""Point evidence only; it cannot mutate or compose audit readiness."""

	ok: bool
	checked_at: str = field(repr=False)
	code: PassiveProbeCode

	def __post_init__(self) -> None:
		if type(self.ok) is not bool:
			_fail(PassiveProbeErrorCode.INVALID_RESULT)
		_parse_canonical_utc(self.checked_at, PassiveProbeErrorCode.INVALID_RESULT)
		if type(self.code) is not PassiveProbeCode:
			_fail(PassiveProbeErrorCode.INVALID_RESULT)
		if self.ok:
			if self.code is not PassiveProbeCode.PASSIVE_OK:
				_fail(PassiveProbeErrorCode.INVALID_RESULT)
		elif self.code in {PassiveProbeCode.PASSIVE_OK, PassiveProbeCode.PASSIVE_NOT_RUN}:
			_fail(PassiveProbeErrorCode.INVALID_RESULT)

	def __repr__(self) -> str:
		return f"PassiveProbeAssessment(ok={self.ok!r}, code={self.code.value!r}, <redacted>)"


_OBSERVATION_FIELDS: Final = (
	"connection_available",
	"schema_matches",
	"public_ids_valid",
	"statuses_valid",
	"retention_valid",
	"hold_available",
	"metrics_available",
	"purge_fresh",
)

_FAILURE_PRECEDENCE: Final = (
	("connection_available", PassiveProbeCode.PASSIVE_CONNECTION_UNAVAILABLE),
	("schema_matches", PassiveProbeCode.PASSIVE_SCHEMA_MISMATCH),
	("public_ids_valid", PassiveProbeCode.PASSIVE_PUBLIC_ID_INVALID),
	("statuses_valid", PassiveProbeCode.PASSIVE_STATUS_INVALID),
	("retention_valid", PassiveProbeCode.PASSIVE_RETENTION_INVALID),
	("hold_available", PassiveProbeCode.PASSIVE_HOLD_UNAVAILABLE),
	("metrics_available", PassiveProbeCode.PASSIVE_METRICS_UNAVAILABLE),
	("purge_fresh", PassiveProbeCode.PASSIVE_PURGE_STALE),
)


def plan_passive_probe(
	observations: PassiveProbeObservations,
	*,
	checked_at: str,
) -> PassiveProbeAssessment:
	"""Return the first failure in fixed precedence, or point-in-time success."""

	validated = _rebuild_observations(observations)
	canonical_checked_at = _parse_canonical_utc(checked_at, PassiveProbeErrorCode.INVALID_CHECKED_AT)
	for field_name, code in _FAILURE_PRECEDENCE:
		if not getattr(validated, field_name):
			return PassiveProbeAssessment(False, canonical_checked_at, code)
	return PassiveProbeAssessment(True, canonical_checked_at, PassiveProbeCode.PASSIVE_OK)


def _rebuild_observations(value: object) -> PassiveProbeObservations:
	if type(value) is not PassiveProbeObservations:
		_fail(PassiveProbeErrorCode.INVALID_OBSERVATIONS)
	try:
		return PassiveProbeObservations(
			connection_available=value.connection_available,
			schema_matches=value.schema_matches,
			public_ids_valid=value.public_ids_valid,
			statuses_valid=value.statuses_valid,
			retention_valid=value.retention_valid,
			hold_available=value.hold_available,
			metrics_available=value.metrics_available,
			purge_fresh=value.purge_fresh,
		)
	except PassiveProbeError:
		raise
	except (AttributeError, TypeError, ValueError):
		_fail(PassiveProbeErrorCode.INVALID_OBSERVATIONS)


def _parse_canonical_utc(value: object, code: PassiveProbeErrorCode) -> str:
	if type(value) is not str or _CANONICAL_UTC_RE.fullmatch(value) is None:
		_fail(code)
	try:
		parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
	except ValueError:
		_fail(code)
	if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
		_fail(code)
	return value


def _fail(code: PassiveProbeErrorCode) -> None:
	raise PassiveProbeError(code) from None
