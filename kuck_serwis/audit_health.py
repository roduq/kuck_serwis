"""Dark active health probe for the durable repair-audit sink.

This module deliberately has no whitelisted endpoint or scheduler hook.  The
probe proves that the existing sink acknowledged a committed, sanitized canary
and that the complete row is visible through a subsequently opened connection.
It does not participate in capability readiness.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

from kuck_serwis.kuck_serwis.doctype.kuck_repair_audit_event import (
	kuck_repair_audit_event as audit_store,
)
from kuck_serwis.public_contract import v1

PROBE_VERSION: Final = "repair-audit-active/v1"
_SUCCESS_CODE: Final = "ACTIVE_CANARY_OK"
_CONTROL_CODES: Final = frozenset(
	{
		_SUCCESS_CODE,
		"KEY_UNAVAILABLE",
		"SINK_ACK_INVALID",
		"SINK_UNAVAILABLE",
		"VERIFY_UNAVAILABLE",
		"VERIFY_COUNT_MISMATCH",
		"VERIFY_CONTENT_MISMATCH",
	}
)


def run_active_repair_audit_probe() -> dict[str, object]:
	"""Write and verify one PII-free canary, returning only a sanitized result."""
	checked_at = _utc_timestamp()
	started_at = time.perf_counter_ns()
	try:
		audit_key = v1._audit_hmac_key()
	except Exception:
		return _result(False, checked_at, "KEY_UNAVAILABLE")
	if audit_key is None:
		return _result(False, checked_at, "KEY_UNAVAILABLE")

	event = {
		"event": v1._AUDIT_EVENT_NAME,
		"contract": v1.CONTRACT_NAME,
		"schema_revision": v1.SCHEMA_REVISION,
		"correlation_id": f"corr_{secrets.token_urlsafe(18)}",
		"operation": "list",
		"outcome": "success",
		"actor_class": "unknown",
		"actor_hash": v1._audit_hash(audit_key, "actor", "health-probe"),
		"repair_handle_hash": None,
		"result_code": "OK",
		"count": 0,
		"latency_ms": max(0, (time.perf_counter_ns() - started_at) // 1_000_000),
	}

	try:
		acknowledged = audit_store.DurableRepairAuditSink().emit(event)
	except Exception:
		return _result(False, checked_at, "SINK_UNAVAILABLE")
	if acknowledged is not True:
		return _result(False, checked_at, "SINK_ACK_INVALID")

	try:
		rows = _read_canary(event["correlation_id"])
	except Exception:
		return _result(False, checked_at, "VERIFY_UNAVAILABLE")
	if len(rows) != 1:
		return _result(False, checked_at, "VERIFY_COUNT_MISMATCH")
	if not _row_matches_event(rows[0], event):
		return _result(False, checked_at, "VERIFY_CONTENT_MISMATCH")
	return _result(True, checked_at, _SUCCESS_CODE)


def _read_canary(correlation_id: object):
	"""Read the canary using a connection created after the sink has returned."""
	database = audit_store._new_isolated_database()
	try:
		column_list = ", ".join(f"`{column}`" for column in audit_store._PERSISTED_EVENT_FIELDS)
		quoted_column_list = ", ".join(f'"{column}"' for column in audit_store._PERSISTED_EVENT_FIELDS)
		return database.multisql(
			{
				"mariadb": (
					f"SELECT {column_list} FROM `tabKuck Repair Audit Event` WHERE `correlation_id` = %s"
				),
				"postgres": (
					f'SELECT {quoted_column_list} FROM "tabKuck Repair Audit Event" '
					'WHERE "correlation_id" = %s'
				),
				"sqlite": (
					f'SELECT {quoted_column_list} FROM "tabKuck Repair Audit Event" '
					'WHERE "correlation_id" = %s'
				),
			},
			(correlation_id,),
			as_dict=True,
		)
	finally:
		database.close()


def _result(ok: bool, checked_at: str, code: str) -> dict[str, object]:
	if code not in _CONTROL_CODES:
		code = "VERIFY_UNAVAILABLE"
	return {
		"ok": ok,
		"checked_at": checked_at,
		"probe_version": PROBE_VERSION,
		"codes": [code],
	}


def _row_matches_event(row: object, event: dict[str, object]) -> bool:
	return isinstance(row, Mapping) and all(
		row.get(fieldname) == event[fieldname] for fieldname in audit_store._PERSISTED_EVENT_FIELDS
	)


def _utc_timestamp() -> str:
	return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
