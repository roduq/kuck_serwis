"""Append-only durable storage for sanitized public-contract audit events."""

from __future__ import annotations

import re
from typing import Final

import frappe
from frappe import _
from frappe.database import get_db
from frappe.model.document import Document
from frappe.utils import now_datetime

DOCTYPE: Final = "Kuck Repair Audit Event"
_TABLE: Final = "tabKuck Repair Audit Event"
_SYSTEM_ACTOR: Final = "__audit_sink__"
_EVENT_ID_PATTERN: Final = re.compile(r"^evt_[A-Za-z0-9_-]{24}$")
_CORRELATION_ID_PATTERN: Final = re.compile(r"^corr_[A-Za-z0-9_-]{24}$")
_HASH_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_EVENT_FIELDS: Final = frozenset(
	{
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
_OPERATIONS: Final = frozenset({"list", "get"})
_OUTCOMES: Final = frozenset({"success", "deny", "error"})
_ACTOR_CLASSES: Final = frozenset({"guest", "website_user", "system_user", "disabled_user", "unknown"})
_RESULT_CODES: Final = frozenset(
	{
		"OK",
		"AUTH_REQUIRED",
		"NOT_FOUND",
		"INVALID_CURSOR",
		"VALIDATION_FAILED",
		"DEPENDENCY_UNAVAILABLE",
		"INTERNAL_ERROR",
	}
)
_PERSISTED_EVENT_FIELDS: Final = (
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
)


class KuckRepairAuditEvent(Document):
	"""Framework guard for the table; the durable sink inserts through an isolated connection."""

	def validate(self):
		if not self.is_new():
			frappe.throw(_("Audit events are append-only."))

	def on_trash(self):
		frappe.throw(_("Audit events are append-only."))

	def before_rename(self, old_name, new_name, merge=False):
		frappe.throw(_("Audit events are append-only."))


class AuditEventConflictError(ValueError):
	"""A correlation ID was replayed with a different sanitized event."""


class DurableRepairAuditSink:
	"""Persist one sanitized event and ACK only after an isolated database commit.

	The public contract is read-only. An independent connection is deliberate: the
	audit record survives a later rollback of the request, while this sink never
	commits unrelated work present on ``frappe.db``. Callers must not extend the
	public-contract boundary with business mutations.
	"""

	def emit(self, event: dict[str, object]) -> bool:
		row = _validate_and_normalize_event(event)
		database = _new_isolated_database()
		try:
			try:
				_insert_event(database, row)
				database.commit()
			except Exception as error:
				database.rollback()
				if not database.is_unique_key_violation(error) or not _matches_existing_event(database, row):
					if database.is_unique_key_violation(error):
						raise AuditEventConflictError("Conflicting audit event replay.") from None
					raise
			return True
		finally:
			database.close()


def _validate_and_normalize_event(event: dict[str, object]) -> dict[str, object]:
	if type(event) is not dict or set(event) != _EVENT_FIELDS:
		raise ValueError("Audit event does not match the v1 allowlist.")
	if event["event"] != "kuck_serwis.public_contract.audit.v1":
		raise ValueError("Unsupported audit event.")
	if event["contract"] != "kuck-serwis/v1":
		raise ValueError("Unsupported audit contract.")
	if type(event["schema_revision"]) is not int or event["schema_revision"] != 1:
		raise ValueError("Unsupported audit schema revision.")
	correlation_id = event["correlation_id"]
	if type(correlation_id) is not str or not _CORRELATION_ID_PATTERN.fullmatch(correlation_id):
		raise ValueError("Invalid audit correlation ID.")
	if event["operation"] not in _OPERATIONS:
		raise ValueError("Invalid audit operation.")
	if event["outcome"] not in _OUTCOMES:
		raise ValueError("Invalid audit outcome.")
	if event["actor_class"] not in _ACTOR_CLASSES:
		raise ValueError("Invalid audit actor class.")
	if type(event["actor_hash"]) is not str or not _HASH_PATTERN.fullmatch(event["actor_hash"]):
		raise ValueError("Invalid audit actor hash.")
	repair_hash = event["repair_handle_hash"]
	if repair_hash is not None and (type(repair_hash) is not str or not _HASH_PATTERN.fullmatch(repair_hash)):
		raise ValueError("Invalid audit repair handle hash.")
	if event["result_code"] not in _RESULT_CODES:
		raise ValueError("Invalid audit result code.")
	for fieldname in ("count", "latency_ms"):
		value = event[fieldname]
		if type(value) is not int or not 0 <= value <= 2_147_483_647:
			raise ValueError(f"Invalid audit {fieldname}.")
	if event["operation"] == "list" and repair_hash is not None:
		raise ValueError("List events cannot contain a repair handle hash.")
	if event["operation"] == "get" and repair_hash is None:
		raise ValueError("Get events require a repair handle hash.")
	if (event["result_code"] == "OK") != (event["outcome"] == "success"):
		raise ValueError("Audit result and outcome do not match.")
	if event["outcome"] != "success" and event["count"] != 0:
		raise ValueError("Non-success audit events cannot report results.")

	event_id = f"evt_{correlation_id.removeprefix('corr_')}"
	if not _EVENT_ID_PATTERN.fullmatch(event_id):
		raise ValueError("Invalid audit event ID.")
	return {
		"event_id": event_id,
		**event,
	}


def _new_isolated_database():
	conf = frappe.local.conf
	return get_db(
		socket=conf.db_socket,
		host=conf.db_host,
		port=conf.db_port,
		user=conf.db_user or conf.db_name,
		password=conf.db_password,
		cur_db_name=conf.db_name,
	)


def _insert_event(database, row: dict[str, object]) -> None:
	timestamp = now_datetime()
	values = {
		"name": row["event_id"],
		"creation": timestamp,
		"modified": timestamp,
		"modified_by": _SYSTEM_ACTOR,
		"owner": _SYSTEM_ACTOR,
		"docstatus": 0,
		"idx": 0,
		**row,
	}
	columns = tuple(values)
	column_list = ", ".join(f"`{column}`" for column in columns)
	quoted_column_list = ", ".join(f'"{column}"' for column in columns)
	placeholders = ", ".join(f"%({column})s" for column in columns)
	database.multisql(
		{
			"mariadb": f"INSERT INTO `{_TABLE}` ({column_list}) VALUES ({placeholders})",
			"postgres": f'INSERT INTO "{_TABLE}" ({quoted_column_list}) VALUES ({placeholders})',
			"sqlite": f'INSERT INTO "{_TABLE}" ({quoted_column_list}) VALUES ({placeholders})',
		},
		values,
	)


def _matches_existing_event(database, row: dict[str, object]) -> bool:
	column_list = ", ".join(f"`{column}`" for column in _PERSISTED_EVENT_FIELDS)
	quoted_column_list = ", ".join(f'"{column}"' for column in _PERSISTED_EVENT_FIELDS)
	rows = database.multisql(
		{
			"mariadb": f"SELECT {column_list} FROM `{_TABLE}` WHERE `correlation_id` = %s",
			"postgres": (f'SELECT {quoted_column_list} FROM "{_TABLE}" WHERE "correlation_id" = %s'),
			"sqlite": f'SELECT {quoted_column_list} FROM "{_TABLE}" WHERE "correlation_id" = %s',
		},
		(row["correlation_id"],),
		as_dict=True,
	)
	if len(rows) != 1:
		return False
	return all(rows[0][fieldname] == row[fieldname] for fieldname in _PERSISTED_EVENT_FIELDS)
