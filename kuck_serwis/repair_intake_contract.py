"""Pure validation contract for a public repair intake submission."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
from typing import Final

MAX_DECLARED_VALUE_PLN: Final = Decimal("10000.00")
PRIVACY_REVISION: Final = "2026-08-25-v1"
PRIVACY_PROOF_SHA256: Final = sha256(b"kuck.pl/polityka-prywatnosci/2026-08-25-v1").hexdigest()
_EMAIL_RE: Final = re.compile(r"^[^\s@]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,63}$")
_PHONE_RE: Final = re.compile(r"^[+0-9][+0-9 ()-]{5,31}$")
_IDEMPOTENCY_RE: Final = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
_EXPECTED_FIELDS: Final = frozenset(
	{
		"full_name",
		"email",
		"phone",
		"brand",
		"model",
		"serial_number",
		"purchase_date",
		"issue_description",
		"condition_description",
		"warranty",
		"delivery_method",
		"return_method",
		"declared_value",
		"privacy_accepted",
		"website",
	}
)


class RepairIntakeErrorCode(StrEnum):
	INVALID_SCHEMA = "INVALID_SCHEMA"
	INVALID_TEXT = "INVALID_TEXT"
	INVALID_EMAIL = "INVALID_EMAIL"
	INVALID_PHONE = "INVALID_PHONE"
	INVALID_DATE = "INVALID_DATE"
	INVALID_CHOICE = "INVALID_CHOICE"
	INVALID_VALUE = "INVALID_VALUE"
	VALUE_REQUIRED = "VALUE_REQUIRED"
	VALUE_ABOVE_LIMIT = "VALUE_ABOVE_LIMIT"
	PRIVACY_REQUIRED = "PRIVACY_REQUIRED"
	INVALID_IDEMPOTENCY_KEY = "INVALID_IDEMPOTENCY_KEY"


class RepairIntakeContractError(ValueError):
	def __init__(self, code: RepairIntakeErrorCode, field_name: str) -> None:
		self.code = code
		self.field_name = field_name
		super().__init__(code.value)


class DeliveryMethod(StrEnum):
	SALON = "SALON"
	COURIER = "COURIER"


@dataclass(frozen=True, slots=True)
class RepairIntakeSubmission:
	full_name: str = field(repr=False)
	email: str = field(repr=False)
	phone: str = field(repr=False)
	brand: str
	model: str
	serial_number: str = field(repr=False)
	purchase_date: str | None
	issue_description: str = field(repr=False)
	condition_description: str = field(repr=False)
	warranty: bool
	delivery_method: DeliveryMethod
	return_method: DeliveryMethod
	declared_value: Decimal | None
	privacy_accepted: bool = field(repr=False)
	honeypot_triggered: bool = field(repr=False)

	def canonical_json(self) -> str:
		data = asdict(self)
		data["delivery_method"] = self.delivery_method.value
		data["return_method"] = self.return_method.value
		data["declared_value"] = (
			format(self.declared_value, ".2f") if self.declared_value is not None else None
		)
		data.pop("honeypot_triggered")
		return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def validate_submission(payload: object) -> RepairIntakeSubmission:
	if type(payload) is not dict or set(payload) != _EXPECTED_FIELDS:
		_fail(RepairIntakeErrorCode.INVALID_SCHEMA, "payload")
	full_name = _single_line(payload["full_name"], "full_name", 2, 120)
	email = _email(payload["email"])
	phone = _phone(payload["phone"])
	brand = _single_line(payload["brand"], "brand", 1, 80)
	model = _single_line(payload["model"], "model", 1, 120)
	serial_number = _optional_single_line(payload["serial_number"], "serial_number", 120)
	purchase_date = _optional_date(payload["purchase_date"], "purchase_date")
	issue = _multiline(payload["issue_description"], "issue_description", 10, 2000)
	condition = _optional_multiline(payload["condition_description"], "condition_description", 1000)
	warranty = _boolean(payload["warranty"], "warranty")
	delivery = _choice(payload["delivery_method"], "delivery_method")
	return_method = _choice(payload["return_method"], "return_method")
	declared_value = _declared_value(payload["declared_value"])
	if (
		delivery is DeliveryMethod.COURIER or return_method is DeliveryMethod.COURIER
	) and declared_value is None:
		_fail(RepairIntakeErrorCode.VALUE_REQUIRED, "declared_value")
	privacy = _boolean(payload["privacy_accepted"], "privacy_accepted")
	if not privacy:
		_fail(RepairIntakeErrorCode.PRIVACY_REQUIRED, "privacy_accepted")
	honeypot = _optional_single_line(payload["website"], "website", 200)
	return RepairIntakeSubmission(
		full_name=full_name,
		email=email,
		phone=phone,
		brand=brand,
		model=model,
		serial_number=serial_number,
		purchase_date=purchase_date,
		issue_description=issue,
		condition_description=condition,
		warranty=warranty,
		delivery_method=delivery,
		return_method=return_method,
		declared_value=declared_value,
		privacy_accepted=privacy,
		honeypot_triggered=bool(honeypot),
	)


def validate_idempotency_key(value: object) -> str:
	if type(value) is not str or _IDEMPOTENCY_RE.fullmatch(value) is None:
		_fail(RepairIntakeErrorCode.INVALID_IDEMPOTENCY_KEY, "idempotency_key")
	return value


def _single_line(value: object, field_name: str, minimum: int, maximum: int) -> str:
	if type(value) is not str:
		_fail(RepairIntakeErrorCode.INVALID_TEXT, field_name)
	value = " ".join(_normalise(value).split())
	if not minimum <= len(value) <= maximum or _has_controls(value):
		_fail(RepairIntakeErrorCode.INVALID_TEXT, field_name)
	return value


def _optional_single_line(value: object, field_name: str, maximum: int) -> str:
	if value in (None, ""):
		return ""
	return _single_line(value, field_name, 1, maximum)


def _multiline(value: object, field_name: str, minimum: int, maximum: int) -> str:
	if type(value) is not str:
		_fail(RepairIntakeErrorCode.INVALID_TEXT, field_name)
	value = _normalise(value).replace("\r\n", "\n").replace("\r", "\n").strip()
	if not minimum <= len(value) <= maximum or _has_controls(value, allow_newline=True):
		_fail(RepairIntakeErrorCode.INVALID_TEXT, field_name)
	return value


def _optional_multiline(value: object, field_name: str, maximum: int) -> str:
	if value in (None, ""):
		return ""
	return _multiline(value, field_name, 1, maximum)


def _email(value: object) -> str:
	value = _single_line(value, "email", 3, 254).lower()
	if _EMAIL_RE.fullmatch(value) is None or ".." in value:
		_fail(RepairIntakeErrorCode.INVALID_EMAIL, "email")
	return value


def _phone(value: object) -> str:
	value = _single_line(value, "phone", 6, 32)
	if _PHONE_RE.fullmatch(value) is None or sum(character.isdigit() for character in value) < 6:
		_fail(RepairIntakeErrorCode.INVALID_PHONE, "phone")
	return value


def _optional_date(value: object, field_name: str) -> str | None:
	if value in (None, ""):
		return None
	if type(value) is not str or len(value) != 10:
		_fail(RepairIntakeErrorCode.INVALID_DATE, field_name)
	try:
		parsed = date.fromisoformat(value)
	except ValueError:
		_fail(RepairIntakeErrorCode.INVALID_DATE, field_name)
	if parsed > date.today():
		_fail(RepairIntakeErrorCode.INVALID_DATE, field_name)
	return parsed.isoformat()


def _boolean(value: object, field_name: str) -> bool:
	if value in (True, 1, "1", "true", "on"):
		return True
	if value in (False, 0, "0", "false", "off", ""):
		return False
	_fail(RepairIntakeErrorCode.INVALID_CHOICE, field_name)


def _choice(value: object, field_name: str) -> DeliveryMethod:
	try:
		return DeliveryMethod(value)
	except (TypeError, ValueError):
		_fail(RepairIntakeErrorCode.INVALID_CHOICE, field_name)


def _declared_value(value: object) -> Decimal | None:
	if value in (None, ""):
		return None
	if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
		_fail(RepairIntakeErrorCode.INVALID_VALUE, "declared_value")
	try:
		parsed = Decimal(str(value).replace(" ", "").replace(",", ".")).quantize(Decimal("0.01"))
	except (InvalidOperation, ValueError):
		_fail(RepairIntakeErrorCode.INVALID_VALUE, "declared_value")
	if not parsed.is_finite() or parsed <= 0:
		_fail(RepairIntakeErrorCode.INVALID_VALUE, "declared_value")
	if parsed > MAX_DECLARED_VALUE_PLN:
		_fail(RepairIntakeErrorCode.VALUE_ABOVE_LIMIT, "declared_value")
	return parsed


def _normalise(value: str) -> str:
	return unicodedata.normalize("NFC", value)


def _has_controls(value: str, *, allow_newline: bool = False) -> bool:
	return any(
		unicodedata.category(character) == "Cc" and not (allow_newline and character == "\n")
		for character in value
	)


def _fail(code: RepairIntakeErrorCode, field_name: str):
	raise RepairIntakeContractError(code, field_name) from None
