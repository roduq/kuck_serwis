"""Fail-closed, read-only repair contract consumed by ``kuck_shop``.

Direct operations apply the same per-site rollout and readiness gate as
capability negotiation, so importing this module cannot bypass a disabled
account capability.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
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
CURSOR_VERSION: Final = 1
CURSOR_TTL_SECONDS: Final = 300
CURSOR_MAX_CLOCK_SKEW_SECONDS: Final = 30
_CURSOR_MAX_LENGTH: Final = 1024
_CURSOR_SIGNATURE_BYTES: Final = 32
_CURSOR_KEY_CONTEXT: Final = b"kuck-serwis/public-contract/v1/cursor"
_AUDIT_AND_MONITORING_READY: Final = False
_CURSOR_FIELDS: Final = frozenset(
	{"cursor_version", "schema_revision", "issued_at", "last_creation", "last_public_id", "scope"}
)

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
	if not customers:
		if cursor is not None:
			_decode_cursor(cursor, customers)
		return {"items": [], "next_cursor": None}

	anchor = _decode_cursor(cursor, customers) if cursor is not None else None
	rows = _get_repair_page(customers, anchor, page_size + 1)
	has_more = len(rows) > page_size
	page = rows[:page_size]
	next_cursor = _encode_cursor(page[-1], customers) if has_more else None
	return {"items": [_project_repair(row) for row in page], "next_cursor": next_cursor}


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
	if not _AUDIT_AND_MONITORING_READY:
		return False
	if _cursor_signing_key() is None:
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


def _get_repair_page(customers: tuple[str, ...], anchor, limit: int):
	repair = frappe.qb.DocType("Naprawa")
	query = (
		frappe.qb.from_(repair)
		.select(*(repair[field] for field in _READ_FIELDS))
		.where(repair.klient.isin(customers))
		.orderby(repair.creation, order=frappe.qb.desc)
		.orderby(repair.public_id, order=frappe.qb.desc)
		.limit(limit)
	)
	if anchor is not None:
		last_creation, last_public_id = anchor
		query = query.where(
			(repair.creation < last_creation)
			| ((repair.creation == last_creation) & (repair.public_id < last_public_id))
		)
	return query.run(as_dict=True)


def _encode_cursor(last_row, customers: tuple[str, ...]) -> str:
	key = _require_cursor_signing_key()
	payload = {
		"cursor_version": CURSOR_VERSION,
		"schema_revision": SCHEMA_REVISION,
		"issued_at": _now_timestamp(),
		"last_creation": _normalise_creation(last_row.creation),
		"last_public_id": last_row.public_id,
		"scope": _scope_fingerprint(customers, key),
	}
	payload_bytes = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
	signature = hmac.new(key, payload_bytes, hashlib.sha256).digest()
	return f"{_base64url_encode(payload_bytes)}.{_base64url_encode(signature)}"


def _decode_cursor(cursor: object, customers: tuple[str, ...]):
	key = _require_cursor_signing_key()
	malformed = type(cursor) is not str or not cursor or len(cursor) > _CURSOR_MAX_LENGTH
	cursor_value = cursor if type(cursor) is str and len(cursor) <= _CURSOR_MAX_LENGTH else ""
	parts = cursor_value.split(".", 1)
	if len(parts) != 2 or "." in parts[1]:
		malformed = True
		parts = ["e30", ""]

	try:
		payload_bytes = _base64url_decode(parts[0])
	except (binascii.Error, UnicodeEncodeError, ValueError):
		payload_bytes = b"{}"
		malformed = True
	try:
		provided_signature = _base64url_decode(parts[1])
	except (binascii.Error, UnicodeEncodeError, ValueError):
		provided_signature = b""
		malformed = True
	if len(provided_signature) != _CURSOR_SIGNATURE_BYTES:
		provided_signature = bytes(_CURSOR_SIGNATURE_BYTES)
		malformed = True

	expected_signature = hmac.new(key, payload_bytes, hashlib.sha256).digest()
	signature_valid = hmac.compare_digest(provided_signature, expected_signature)
	if malformed or not signature_valid:
		_raise_invalid_cursor()

	try:
		payload = json.loads(payload_bytes)
		_validate_cursor_payload(payload, customers, key)
		last_creation = datetime.fromisoformat(payload["last_creation"])
	except (KeyError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
		_raise_invalid_cursor()
	return last_creation, payload["last_public_id"]


def _validate_cursor_payload(payload: object, customers: tuple[str, ...], key: bytes) -> None:
	if not isinstance(payload, dict) or set(payload) != _CURSOR_FIELDS:
		raise ValueError
	if type(payload["cursor_version"]) is not int or payload["cursor_version"] != CURSOR_VERSION:
		raise ValueError
	if type(payload["schema_revision"]) is not int or payload["schema_revision"] != SCHEMA_REVISION:
		raise ValueError
	if type(payload["issued_at"]) is not int:
		raise ValueError
	now = _now_timestamp()
	if payload["issued_at"] > now + CURSOR_MAX_CLOCK_SKEW_SECONDS:
		raise ValueError
	if now > payload["issued_at"] + CURSOR_TTL_SECONDS:
		raise ValueError
	if type(payload["last_public_id"]) is not str or not PUBLIC_ID_PATTERN.fullmatch(
		payload["last_public_id"]
	):
		raise ValueError
	if type(payload["last_creation"]) is not str:
		raise ValueError
	parsed_creation = datetime.fromisoformat(payload["last_creation"])
	if _normalise_creation(parsed_creation) != payload["last_creation"]:
		raise ValueError
	expected_scope = _scope_fingerprint(customers, key)
	provided_scope = payload["scope"] if type(payload["scope"]) is str else ""
	if not hmac.compare_digest(provided_scope, expected_scope):
		raise ValueError


def _cursor_signing_key() -> bytes | None:
	"""Derive a purpose-specific key from the in-memory, per-site Fernet key."""
	conf = getattr(frappe.local, "conf", None)
	encoded_key = conf.get("encryption_key") if conf else None
	if type(encoded_key) is not str:
		return None
	try:
		site_key = base64.b64decode(encoded_key.encode("ascii"), altchars=b"-_", validate=True)
	except (binascii.Error, UnicodeEncodeError, ValueError):
		return None
	if len(site_key) != 32:
		return None
	return hmac.new(site_key, _CURSOR_KEY_CONTEXT, hashlib.sha256).digest()


def _require_cursor_signing_key() -> bytes:
	key = _cursor_signing_key()
	if key is None:
		raise PublicContractError("DEPENDENCY_UNAVAILABLE", "Repair service is temporarily unavailable.")
	return key


def _scope_fingerprint(customers: tuple[str, ...], key: bytes) -> str:
	user = getattr(getattr(frappe, "session", None), "user", "") or ""
	scope = json.dumps([user, *sorted(customers)], ensure_ascii=True, separators=(",", ":")).encode()
	return hmac.new(key, b"scope\0" + scope, hashlib.sha256).hexdigest()


def _normalise_creation(value: datetime | str) -> str:
	parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
	return parsed.strftime("%Y-%m-%d %H:%M:%S.%f")


def _base64url_encode(value: bytes) -> str:
	return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
	if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
		raise ValueError
	padding = "=" * (-len(value) % 4)
	return base64.b64decode((value + padding).encode("ascii"), altchars=b"-_", validate=True)


def _now_timestamp() -> int:
	return int(time.time())


def _raise_invalid_cursor() -> None:
	raise PublicContractError("INVALID_CURSOR", "The pagination cursor is invalid.")


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
