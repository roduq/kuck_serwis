"""Read-only, code-only preflight for existing repair-audit database facts.

This module deliberately does not build a passive-probe observation or
assessment.  It only reports independently provable database facts.  Every
authorization/readiness flag is permanently false.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol

from kuck_serwis.operational_policy_v1 import POLICY_REVISION_SHA256

_AUDIT_TABLE: Final = "tabKuck Repair Audit Event"
_REPAIR_TABLE: Final = "tabNaprawa"
_QUERY_BUDGET_ROWS: Final = 10_000
_ALLOWED_STATUSES: Final = (
	"Przyjęto",
	"Diagnoza",
	"Oczekuje na akceptację",
	"W naprawie",
	"Oczekiwanie na część",
	"Gotowe do odbioru",
	"Wydano",
	"Anulowano",
)

_COLUMNS_SQL: Final = (
	"SELECT column_name, data_type, is_nullable FROM information_schema.columns "
	"WHERE table_schema = DATABASE() AND table_name = %s ORDER BY ordinal_position"
)
_INDEXES_SQL: Final = (
	"SELECT index_name, non_unique, seq_in_index, column_name FROM information_schema.statistics "
	"WHERE table_schema = DATABASE() AND table_name = %s ORDER BY index_name, seq_in_index"
)
_PERMISSIONS_SQL: Final = "SELECT 1 FROM `tabDocPerm` WHERE parent = %s LIMIT 1"
_PUBLIC_ID_EXPLAIN_SQL: Final = (
	"EXPLAIN SELECT 1 FROM `tabNaprawa` WHERE `public_id` IS NULL OR `public_id` = '' LIMIT 1"
)
_PUBLIC_ID_SQL: Final = "SELECT 1 FROM `tabNaprawa` WHERE `public_id` IS NULL OR `public_id` = '' LIMIT 1"
_STATUS_EXPLAIN_SQL: Final = (
	"EXPLAIN SELECT 1 FROM `tabNaprawa` WHERE `status` NOT IN (%s, %s, %s, %s, %s, %s, %s, %s) LIMIT 1"
)
_STATUS_SQL: Final = (
	"SELECT 1 FROM `tabNaprawa` WHERE `status` NOT IN (%s, %s, %s, %s, %s, %s, %s, %s) LIMIT 1"
)
_ALLOWED_SQL: Final = frozenset(
	{
		_COLUMNS_SQL,
		_INDEXES_SQL,
		_PERMISSIONS_SQL,
		_PUBLIC_ID_EXPLAIN_SQL,
		_PUBLIC_ID_SQL,
		_STATUS_EXPLAIN_SQL,
		_STATUS_SQL,
	}
)
_ALLOWED_QUERY_VALUES: Final = {
	_COLUMNS_SQL: frozenset({(_AUDIT_TABLE,), (_REPAIR_TABLE,)}),
	_INDEXES_SQL: frozenset({(_AUDIT_TABLE,), (_REPAIR_TABLE,)}),
	_PERMISSIONS_SQL: frozenset({("Kuck Repair Audit Event",)}),
	_PUBLIC_ID_EXPLAIN_SQL: frozenset({()}),
	_PUBLIC_ID_SQL: frozenset({()}),
	_STATUS_EXPLAIN_SQL: frozenset({_ALLOWED_STATUSES}),
	_STATUS_SQL: frozenset({_ALLOWED_STATUSES}),
}
_AUDIT_FIELDS: Final = frozenset(
	{
		"name",
		"creation",
		"event_id",
		"event",
		"contract",
		"schema_revision",
		"correlation_id",
		"operation",
		"outcome",
		"actor_class",
		"actor_hash",
		"repair_handle_hash",
		"result_code",
		"count",
		"latency_ms",
	}
)


class ExistingDbPreflightCode(StrEnum):
	DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
	DATABASE_DIALECT_NOT_PROVEN = "DATABASE_DIALECT_NOT_PROVEN"
	AUDIT_TABLE_NOT_PROVEN = "AUDIT_TABLE_NOT_PROVEN"
	AUDIT_FIELDS_NOT_PROVEN = "AUDIT_FIELDS_NOT_PROVEN"
	AUDIT_UNIQUE_KEYS_NOT_PROVEN = "AUDIT_UNIQUE_KEYS_NOT_PROVEN"
	AUDIT_PERMISSIONS_NOT_PROVEN = "AUDIT_PERMISSIONS_NOT_PROVEN"
	AUDIT_PURGE_INDEX_NOT_PROVEN = "AUDIT_PURGE_INDEX_NOT_PROVEN"
	PUBLIC_ID_FIELD_NOT_PROVEN = "PUBLIC_ID_FIELD_NOT_PROVEN"
	PUBLIC_ID_UNIQUE_KEY_NOT_PROVEN = "PUBLIC_ID_UNIQUE_KEY_NOT_PROVEN"
	PUBLIC_ID_DATA_INVALID = "PUBLIC_ID_DATA_INVALID"
	PUBLIC_ID_DATA_NOT_PROVEN = "PUBLIC_ID_DATA_NOT_PROVEN"
	STATUS_DATA_INVALID = "STATUS_DATA_INVALID"
	STATUS_DATA_NOT_PROVEN = "STATUS_DATA_NOT_PROVEN"
	EXISTING_DB_PARTIAL_EVIDENCE = "EXISTING_DB_PARTIAL_EVIDENCE"


_CODE_ORDER: Final = tuple(ExistingDbPreflightCode)


@dataclass(frozen=True, slots=True, repr=False)
class ExistingDbPreflightResult:
	"""Granular partial evidence; never an operational assessment."""

	codes: tuple[ExistingDbPreflightCode, ...]
	policy_revision_sha256: str = field(repr=False)
	assessment_authorized: bool = False
	purge_authorized: bool = False
	delivery_authorized: bool = False
	activation_authorized: bool = False
	capability_ready: bool = False
	readiness_evidence_ok: bool = False

	def __post_init__(self) -> None:
		exclusive_codes = {
			ExistingDbPreflightCode.DATABASE_UNAVAILABLE,
			ExistingDbPreflightCode.DATABASE_DIALECT_NOT_PROVEN,
			ExistingDbPreflightCode.EXISTING_DB_PARTIAL_EVIDENCE,
		}
		if (
			type(self.codes) is not tuple
			or not self.codes
			or any(type(code) is not ExistingDbPreflightCode for code in self.codes)
			or len(set(self.codes)) != len(self.codes)
			or self.codes != tuple(code for code in _CODE_ORDER if code in self.codes)
			or (any(code in exclusive_codes for code in self.codes) and len(self.codes) != 1)
			or type(self.policy_revision_sha256) is not str
			or self.policy_revision_sha256 != POLICY_REVISION_SHA256
			or self.assessment_authorized is not False
			or self.purge_authorized is not False
			or self.delivery_authorized is not False
			or self.activation_authorized is not False
			or self.capability_ready is not False
			or self.readiness_evidence_ok is not False
		):
			raise ValueError("INVALID_EXISTING_DB_PREFLIGHT_RESULT")

	def __repr__(self) -> str:
		return f"ExistingDbPreflightResult(codes={tuple(code.value for code in self.codes)!r}, <redacted>)"


class _ReadOnlyDatabase(Protocol):
	db_type: str

	def sql(self, query: str, values: tuple[object, ...] = (), *, as_dict: bool = False): ...

	def close(self) -> None: ...


def collect_existing_db_preflight_v1() -> ExistingDbPreflightResult:
	"""Collect bounded facts through the existing isolated audit connection."""

	try:
		database = _new_isolated_database()
	except Exception:
		return _result({ExistingDbPreflightCode.DATABASE_UNAVAILABLE})
	try:
		result = _collect_existing_db_preflight_v1(database)
	except Exception:
		result = _result({ExistingDbPreflightCode.DATABASE_UNAVAILABLE})
	try:
		database.close()
	except Exception:
		return _result({ExistingDbPreflightCode.DATABASE_UNAVAILABLE})
	return result


def _collect_existing_db_preflight_v1(database: object) -> ExistingDbPreflightResult:
	if not _is_database_port(database):
		return _result({ExistingDbPreflightCode.DATABASE_UNAVAILABLE})
	if database.db_type != "mariadb":
		return _result({ExistingDbPreflightCode.DATABASE_DIALECT_NOT_PROVEN})

	codes: set[ExistingDbPreflightCode] = set()
	audit_columns = _columns(database, _AUDIT_TABLE)
	if not audit_columns:
		codes.add(ExistingDbPreflightCode.AUDIT_TABLE_NOT_PROVEN)
	elif not _AUDIT_FIELDS.issubset(audit_columns):
		codes.add(ExistingDbPreflightCode.AUDIT_FIELDS_NOT_PROVEN)
	audit_indexes = _indexes(database, _AUDIT_TABLE)
	if not _has_unique_single(audit_indexes, "event_id") or not _has_unique_single(
		audit_indexes, "correlation_id"
	):
		codes.add(ExistingDbPreflightCode.AUDIT_UNIQUE_KEYS_NOT_PROVEN)
	if _select(database, _PERMISSIONS_SQL, ("Kuck Repair Audit Event",)):
		codes.add(ExistingDbPreflightCode.AUDIT_PERMISSIONS_NOT_PROVEN)
	if not _has_index_prefix(audit_indexes, ("creation", "name")):
		codes.add(ExistingDbPreflightCode.AUDIT_PURGE_INDEX_NOT_PROVEN)

	repair_columns = _columns(database, _REPAIR_TABLE)
	if "public_id" not in repair_columns:
		codes.add(ExistingDbPreflightCode.PUBLIC_ID_FIELD_NOT_PROVEN)
	repair_indexes = _indexes(database, _REPAIR_TABLE)
	public_id_unique = _has_unique_single(repair_indexes, "public_id")
	if not public_id_unique:
		codes.add(ExistingDbPreflightCode.PUBLIC_ID_UNIQUE_KEY_NOT_PROVEN)
	if "public_id" not in repair_columns or not public_id_unique:
		codes.add(ExistingDbPreflightCode.PUBLIC_ID_DATA_NOT_PROVEN)
	else:
		plan = _select(database, _PUBLIC_ID_EXPLAIN_SQL)
		if not _bounded_plan(plan):
			codes.add(ExistingDbPreflightCode.PUBLIC_ID_DATA_NOT_PROVEN)
		elif _select(database, _PUBLIC_ID_SQL):
			codes.add(ExistingDbPreflightCode.PUBLIC_ID_DATA_INVALID)

	status_plan = _select(database, _STATUS_EXPLAIN_SQL, _ALLOWED_STATUSES)
	if not _bounded_plan(status_plan):
		codes.add(ExistingDbPreflightCode.STATUS_DATA_NOT_PROVEN)
	elif _select(database, _STATUS_SQL, _ALLOWED_STATUSES):
		codes.add(ExistingDbPreflightCode.STATUS_DATA_INVALID)

	if not codes:
		codes.add(ExistingDbPreflightCode.EXISTING_DB_PARTIAL_EVIDENCE)
	return _result(codes)


def _is_database_port(value: object) -> bool:
	return (
		type(getattr(value, "db_type", None)) is str
		and callable(getattr(value, "sql", None))
		and callable(getattr(value, "close", None))
	)


def _select(database: _ReadOnlyDatabase, query: str, values: tuple[object, ...] = ()) -> tuple[dict, ...]:
	if (
		query not in _ALLOWED_SQL
		or type(values) is not tuple
		or values not in _ALLOWED_QUERY_VALUES.get(query, frozenset())
	):
		raise ValueError("QUERY_NOT_ALLOWLISTED")
	rows = database.sql(query, values, as_dict=True)
	if type(rows) not in {list, tuple} or len(rows) > _QUERY_BUDGET_ROWS:
		raise ValueError("INVALID_QUERY_RESULT")
	if any(not isinstance(row, dict) for row in rows):
		raise ValueError("INVALID_QUERY_RESULT")
	return tuple(dict(row) for row in rows)


def _columns(database: _ReadOnlyDatabase, table: str) -> frozenset[str]:
	rows = _select(database, _COLUMNS_SQL, (table,))
	return frozenset(row["column_name"] for row in rows if type(row.get("column_name")) is str)


def _indexes(database: _ReadOnlyDatabase, table: str) -> tuple[tuple[str, bool, int, str], ...]:
	result = []
	for row in _select(database, _INDEXES_SQL, (table,)):
		name = row.get("index_name")
		non_unique = row.get("non_unique")
		position = row.get("seq_in_index")
		column = row.get("column_name")
		if type(name) is str and type(non_unique) is int and type(position) is int and type(column) is str:
			result.append((name, bool(non_unique), position, column))
	return tuple(result)


def _has_unique_single(indexes: tuple[tuple[str, bool, int, str], ...], column: str) -> bool:
	by_name: dict[str, list[tuple[bool, int, str]]] = {}
	for name, non_unique, position, indexed_column in indexes:
		by_name.setdefault(name, []).append((non_unique, position, indexed_column))
	return any(parts == [(False, 1, column)] for parts in by_name.values())


def _has_index_prefix(indexes: tuple[tuple[str, bool, int, str], ...], columns: tuple[str, ...]) -> bool:
	by_name: dict[str, list[tuple[int, str]]] = {}
	for name, _non_unique, position, column in indexes:
		by_name.setdefault(name, []).append((position, column))
	return any(
		tuple(column for _position, column in sorted(parts))[: len(columns)] == columns
		for parts in by_name.values()
	)


def _bounded_plan(rows: tuple[dict, ...]) -> bool:
	if len(rows) != 1:
		return False
	row = rows[0]
	access_type = row.get("type")
	key = row.get("key")
	estimated_rows = row.get("rows")
	return (
		type(access_type) is str
		and access_type.upper() not in {"ALL", "INDEX"}
		and type(key) is str
		and bool(key)
		and type(estimated_rows) is int
		and 0 <= estimated_rows <= _QUERY_BUDGET_ROWS
	)


def _result(codes: set[ExistingDbPreflightCode]) -> ExistingDbPreflightResult:
	return ExistingDbPreflightResult(
		codes=tuple(code for code in _CODE_ORDER if code in codes),
		policy_revision_sha256=POLICY_REVISION_SHA256,
	)


def _new_isolated_database():
	# Deferred so pure policy/preflight tests do not require a Frappe runtime.
	from kuck_serwis.kuck_serwis.doctype.kuck_repair_audit_event import kuck_repair_audit_event

	return kuck_repair_audit_event._new_isolated_database()
