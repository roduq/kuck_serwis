"""Fail-closed, read-only repair contract consumed by ``kuck_shop``.

The account capability intentionally remains dark until signed keyset cursors
are implemented. Direct operations apply the same rollout gate, so importing
this module cannot bypass the disabled capability.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Final

import frappe

CONTRACT_NAME: Final = "kuck-serwis/v1"
SCHEMA_REVISION: Final = 1
ACCOUNT_READ: Final = "account-read"
ROLLOUT_FLAG: Final = "enable_kuck_serwis_account_read"
PUBLIC_ID_PATTERN: Final = re.compile(r"^rpr_[A-Za-z0-9_-]{32}$")
_LOOKUP_SENTINEL: Final = "rpr_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

# An unsigned or non-expiring cursor would weaken the ADR. Keep rollout dark
# until a separate server-side signing/key-rotation mechanism is delivered.
_SIGNED_KEYSET_PAGINATION_READY: Final = False

STATUS_MAP: Final = {
	"Przyjęto": ("received", "Przyjęto"),
	"Diagnoza": ("diagnosis", "Diagnoza"),
	"Oczekuje na akceptację": ("awaiting_customer", "Oczekuje na akceptację"),
	"W naprawie": ("in_repair", "W naprawie"),
	"Oczekiwanie na część": ("awaiting_part", "Oczekiwanie na część"),
	"Gotowe do odbioru": ("ready_for_collection", "Gotowe do odbioru"),
	"Wydano": ("completed", "Zakończono"),
	"Anulowano": ("cancelled", "Anulowano"),
}

_READ_FIELDS: Final = (
	"public_id",
	"status",
	"marka",
	"model_zegarka",
	"creation",
	"orientacyjny_termin_naprawy",
	"orientacyjna_wycena",
)


class PublicContractError(RuntimeError):
	"""Stable error without internal identifiers, queries, or tracebacks."""

	def __init__(self, code: str, public_message: str) -> None:
		self.code = code
		self.public_message = public_message
		super().__init__(public_message)


def get_capabilities() -> dict[str, object]:
	features = [ACCOUNT_READ] if _account_read_enabled() else []
	return {"contract": CONTRACT_NAME, "schema_revision": SCHEMA_REVISION, "features": features}


def list_repairs_for_current_user(cursor=None, page_size=20) -> dict[str, object]:
	_require_account_read()
	customers = _authorized_customers_for_current_user()
	_validate_page_size(page_size)
	if cursor is not None:
		raise PublicContractError("INVALID_CURSOR", "The pagination cursor is invalid.")
	if not customers:
		return {"items": [], "next_cursor": None}

	rows = frappe.get_all(
		"Naprawa",
		filters={"klient": ["in", customers]},
		fields=list(_READ_FIELDS),
		order_by="creation desc, public_id desc",
		limit=page_size + 1,
	)
	if len(rows) > page_size:
		# Never truncate silently or substitute offset pagination.
		raise PublicContractError("DEPENDENCY_UNAVAILABLE", "Repair service is temporarily unavailable.")
	return {"items": [_project_repair(row) for row in rows], "next_cursor": None}


def get_repair_for_current_user(repair_id) -> dict[str, object]:
	_require_account_read()
	customers = _authorized_customers_for_current_user()
	lookup_id = (
		repair_id if type(repair_id) is str and PUBLIC_ID_PATTERN.fullmatch(repair_id) else _LOOKUP_SENTINEL
	)
	rows = []
	if customers:
		rows = frappe.get_all(
			"Naprawa",
			filters={"public_id": lookup_id, "klient": ["in", customers]},
			fields=list(_READ_FIELDS),
			limit=1,
		)
	if not rows:
		raise PublicContractError("NOT_FOUND", "Repair was not found.")
	return _project_repair(rows[0])


def _account_read_enabled() -> bool:
	if frappe.conf.get(ROLLOUT_FLAG) is not True:
		return False
	try:
		return _is_ready()
	except Exception:
		return False


def _is_ready() -> bool:
	if not _SIGNED_KEYSET_PAGINATION_READY:
		return False
	if "public_id" not in frappe.db.get_table_columns("Naprawa"):
		return False
	get_column_index = getattr(frappe.db, "get_column_index", None)
	if not callable(get_column_index) or not get_column_index("tabNaprawa", "public_id", unique=True):
		return False
	if frappe.db.count("Naprawa", {"public_id": ["in", ["", None]]}):
		return False
	return not frappe.db.count("Naprawa", {"status": ["not in", list(STATUS_MAP)]})


def _require_account_read() -> None:
	if not _account_read_enabled():
		raise PublicContractError("DEPENDENCY_UNAVAILABLE", "Repair service is temporarily unavailable.")


def _authorized_customers_for_current_user() -> tuple[str, ...]:
	user = getattr(getattr(frappe, "session", None), "user", None)
	if not user or user == "Guest":
		raise PublicContractError("AUTH_REQUIRED", "Authentication is required.")
	user_state = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
	if not user_state or not user_state.enabled or user_state.user_type != "Website User":
		raise PublicContractError("AUTH_REQUIRED", "Authentication is required.")
	customers = frappe.get_all(
		"Portal User",
		filters={
			"parenttype": "Customer",
			"parentfield": "portal_users",
			"user": user,
		},
		pluck="parent",
	)
	return tuple(sorted(set(customers)))


def _validate_page_size(page_size: object) -> None:
	if type(page_size) is not int or not 1 <= page_size <= 50:
		raise PublicContractError("VALIDATION_FAILED", "Page size must be an integer between 1 and 50.")


def _project_repair(row) -> dict[str, object]:
	try:
		public_status, status_label = STATUS_MAP[row.status]
	except KeyError:
		raise PublicContractError("DEPENDENCY_UNAVAILABLE", "Repair service is temporarily unavailable.")
	return {
		"schema": "repair-portal/v1",
		"repair_id": row.public_id,
		"public_status": public_status,
		"status_label": status_label,
		"watch": {"brand": row.marka or None, "model": row.model_zegarka or None},
		"received_on": _date_string(row.creation),
		"estimated_completion_on": _date_string(row.orientacyjny_termin_naprawy),
		"quote": {"amount": _money_string(row.orientacyjna_wycena), "currency": "PLN"},
		"actions": [],
	}


def _date_string(value: date | datetime | str | None) -> str | None:
	if not value:
		return None
	if isinstance(value, (date, datetime)):
		return value.isoformat()[:10]
	return str(value)[:10]


def _money_string(value: object) -> str | None:
	if value in (None, ""):
		return None
	return format(Decimal(str(value)), ".2f")
