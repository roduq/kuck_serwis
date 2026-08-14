"""Pure mapping from the active repair-audit probe result to typed evidence.

The returned evidence proves one point-in-time active write/read check only.  It
does not set or infer any of the eight audit-readiness gates.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from kuck_serwis.audit_readiness import ActiveProbeEvidence, AuditReadinessError

_PROBE_VERSION: Final = "repair-audit-active/v1"
_SUCCESS_CODE: Final = "ACTIVE_CANARY_OK"
_FAILURE_CODES: Final = frozenset(
	{
		"KEY_UNAVAILABLE",
		"SINK_ACK_INVALID",
		"SINK_UNAVAILABLE",
		"VERIFY_UNAVAILABLE",
		"VERIFY_COUNT_MISMATCH",
		"VERIFY_CONTENT_MISMATCH",
	}
)
_RESULT_KEYS: Final = frozenset({"ok", "checked_at", "probe_version", "codes"})


class ActiveProbeMappingErrorCode(StrEnum):
	INVALID_PROBE_RESULT = "INVALID_PROBE_RESULT"


class ActiveProbeMappingError(ValueError):
	"""Stable code-only failure that never echoes rejected probe data."""

	def __init__(self, code: ActiveProbeMappingErrorCode) -> None:
		self.code = code
		super().__init__(code.value)


def map_active_probe_result(result: object) -> ActiveProbeEvidence:
	"""Validate the exact active-probe v1 result and return point evidence."""
	if type(result) is not dict or len(result) != len(_RESULT_KEYS):
		_fail()
	if any(type(key) is not str or key not in _RESULT_KEYS for key in result):
		_fail()

	try:
		ok = result["ok"]
		checked_at = result["checked_at"]
		probe_version = result["probe_version"]
		codes = result["codes"]
	except (KeyError, TypeError):
		_fail()

	if type(ok) is not bool:
		_fail()
	if type(probe_version) is not str or probe_version != _PROBE_VERSION:
		_fail()
	if type(codes) is not list or len(codes) != 1 or type(codes[0]) is not str:
		_fail()

	code = codes[0]
	if ok:
		if code != _SUCCESS_CODE:
			_fail()
	elif code not in _FAILURE_CODES:
		_fail()

	try:
		return ActiveProbeEvidence(ok=ok, checked_at=checked_at)
	except AuditReadinessError:
		_fail()


def _fail() -> None:
	raise ActiveProbeMappingError(ActiveProbeMappingErrorCode.INVALID_PROBE_RESULT) from None
