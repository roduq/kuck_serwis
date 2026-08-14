"""Pure value-CAS planner for the legacy repair contact bridge.

The planner deliberately does not select a source of truth.  It only prevents a
submitted repair snapshot from silently replacing a different current value.
Contact values are never included in representations or errors.
"""

from dataclasses import dataclass, field
from enum import StrEnum

MAX_CONTACT_VALUE_LENGTH = 512


class ContactUpdateOutcome(StrEnum):
	NOOP = "NOOP"
	APPLY = "APPLY"
	CONFLICT = "CONFLICT"


class ContactUpdateCode(StrEnum):
	NEW_REPAIR = "NEW_REPAIR"
	CUSTOMER_CHANGED = "CUSTOMER_CHANGED"
	NO_CHANGES = "NO_CHANGES"
	ALREADY_APPLIED = "ALREADY_APPLIED"
	APPLY = "APPLY"
	CONTACT_REVISION_CONFLICT = "CONTACT_REVISION_CONFLICT"
	CONTACT_CLEAR_UNSUPPORTED = "CONTACT_CLEAR_UNSUPPORTED"
	CONTACT_TARGET_MISMATCH = "CONTACT_TARGET_MISMATCH"
	CONTACT_UPDATE_FORBIDDEN = "CONTACT_UPDATE_FORBIDDEN"
	INVALID_INPUT = "INVALID_INPUT"


class ContactUpdateError(ValueError):
	"""Code-only validation failure at the pure boundary."""

	def __init__(self, code: ContactUpdateCode) -> None:
		if type(code) is not ContactUpdateCode:
			raise TypeError("INVALID_ERROR_CODE")
		self.code = code
		super().__init__(code.value)

	def __repr__(self) -> str:
		return f"ContactUpdateError(code={self.code.value!r})"


@dataclass(frozen=True, slots=True)
class ContactSnapshot:
	phone: str = field(repr=False)
	email: str = field(repr=False)

	def __post_init__(self) -> None:
		_validate_value(self.phone)
		_validate_value(self.email)

	def __repr__(self) -> str:
		return "ContactSnapshot(<redacted>)"


@dataclass(frozen=True, slots=True)
class ContactUpdatePlan:
	outcome: ContactUpdateOutcome
	code: ContactUpdateCode
	update_phone: bool
	update_email: bool
	phone: str = field(repr=False)
	email: str = field(repr=False)

	def __post_init__(self) -> None:
		if type(self.outcome) is not ContactUpdateOutcome or type(self.code) is not ContactUpdateCode:
			raise ContactUpdateError(ContactUpdateCode.INVALID_INPUT)
		if type(self.update_phone) is not bool or type(self.update_email) is not bool:
			raise ContactUpdateError(ContactUpdateCode.INVALID_INPUT)
		_validate_value(self.phone)
		_validate_value(self.email)

		updates = self.update_phone or self.update_email
		if self.outcome is ContactUpdateOutcome.APPLY:
			if self.code is not ContactUpdateCode.APPLY or not updates:
				raise ContactUpdateError(ContactUpdateCode.INVALID_INPUT)
		elif updates:
			raise ContactUpdateError(ContactUpdateCode.INVALID_INPUT)
		elif self.outcome is ContactUpdateOutcome.CONFLICT and self.code not in {
			ContactUpdateCode.CONTACT_REVISION_CONFLICT,
			ContactUpdateCode.CONTACT_CLEAR_UNSUPPORTED,
		}:
			raise ContactUpdateError(ContactUpdateCode.INVALID_INPUT)
		elif self.outcome is ContactUpdateOutcome.NOOP and self.code not in {
			ContactUpdateCode.NEW_REPAIR,
			ContactUpdateCode.CUSTOMER_CHANGED,
			ContactUpdateCode.NO_CHANGES,
			ContactUpdateCode.ALREADY_APPLIED,
		}:
			raise ContactUpdateError(ContactUpdateCode.INVALID_INPUT)

	def __repr__(self) -> str:
		return (
			"ContactUpdatePlan("
			f"outcome={self.outcome.value!r}, code={self.code.value!r}, "
			f"update_phone={self.update_phone!r}, update_email={self.update_email!r})"
		)


def plan_contact_update(
	*,
	is_new: bool,
	customer_changed: bool,
	baseline: ContactSnapshot,
	proposed: ContactSnapshot,
	current: ContactSnapshot,
) -> ContactUpdatePlan:
	"""Plan a field-level compare-and-set without exposing contact values."""

	if type(is_new) is not bool or type(customer_changed) is not bool:
		raise ContactUpdateError(ContactUpdateCode.INVALID_INPUT)
	baseline = _revalidate_snapshot(baseline)
	proposed = _revalidate_snapshot(proposed)
	current = _revalidate_snapshot(current)

	if is_new:
		return _no_update(ContactUpdateOutcome.NOOP, ContactUpdateCode.NEW_REPAIR, proposed)
	if customer_changed:
		return _no_update(ContactUpdateOutcome.NOOP, ContactUpdateCode.CUSTOMER_CHANGED, proposed)

	phone_changed = proposed.phone != baseline.phone
	email_changed = proposed.email != baseline.email
	if not phone_changed and not email_changed:
		return _no_update(ContactUpdateOutcome.NOOP, ContactUpdateCode.NO_CHANGES, proposed)
	if (phone_changed and not proposed.phone) or (email_changed and not proposed.email):
		return _no_update(
			ContactUpdateOutcome.CONFLICT,
			ContactUpdateCode.CONTACT_CLEAR_UNSUPPORTED,
			proposed,
		)

	phone_apply, phone_conflict = _field_decision(
		changed=phone_changed,
		baseline=baseline.phone,
		proposed=proposed.phone,
		current=current.phone,
	)
	email_apply, email_conflict = _field_decision(
		changed=email_changed,
		baseline=baseline.email,
		proposed=proposed.email,
		current=current.email,
	)
	if phone_conflict or email_conflict:
		return _no_update(
			ContactUpdateOutcome.CONFLICT,
			ContactUpdateCode.CONTACT_REVISION_CONFLICT,
			proposed,
		)
	if not phone_apply and not email_apply:
		return _no_update(ContactUpdateOutcome.NOOP, ContactUpdateCode.ALREADY_APPLIED, proposed)
	return ContactUpdatePlan(
		outcome=ContactUpdateOutcome.APPLY,
		code=ContactUpdateCode.APPLY,
		update_phone=phone_apply,
		update_email=email_apply,
		phone=proposed.phone,
		email=proposed.email,
	)


def _field_decision(*, changed: bool, baseline: str, proposed: str, current: str) -> tuple[bool, bool]:
	if not changed or current == proposed:
		return False, False
	if current == baseline:
		return True, False
	return False, True


def _no_update(
	outcome: ContactUpdateOutcome,
	code: ContactUpdateCode,
	proposed: ContactSnapshot,
) -> ContactUpdatePlan:
	return ContactUpdatePlan(
		outcome=outcome,
		code=code,
		update_phone=False,
		update_email=False,
		phone=proposed.phone,
		email=proposed.email,
	)


def _validate_value(value: object) -> None:
	if type(value) is not str or len(value) > MAX_CONTACT_VALUE_LENGTH:
		raise ContactUpdateError(ContactUpdateCode.INVALID_INPUT)


def _revalidate_snapshot(value: object) -> ContactSnapshot:
	if type(value) is not ContactSnapshot:
		raise ContactUpdateError(ContactUpdateCode.INVALID_INPUT)
	try:
		return ContactSnapshot(phone=value.phone, email=value.email)
	except (AttributeError, TypeError, ContactUpdateError):
		raise ContactUpdateError(ContactUpdateCode.INVALID_INPUT) from None
