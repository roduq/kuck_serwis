"""Versioned remote-intake access, approval and provider snapshot boundary."""

from __future__ import annotations

import hashlib
import re
import secrets

import frappe
from frappe.utils import add_to_date, get_url, now_datetime

DOCTYPE = "Kuck Repair Intake"
CONTRACT_NAME = "kuck-serwis-remote/v1"
SCHEMA_REVISION = 1
ROLLOUT_FLAG = "enable_kuck_serwis_remote"
PUBLIC_ID_RE = re.compile(r"^rqi_[A-Za-z0-9_-]{32}$")
GRANT_DAYS = 30
REMOTE_LABELS = {
	"AWAITING_SERVICE_REVIEW": "Oczekuje na akceptację serwisu",
	"AWAITING_INBOUND_PAYMENT": "Oczekuje na opłacenie przesyłki do serwisu",
	"INBOUND_PAYMENT_PENDING": "Płatność za przesyłkę do serwisu w toku",
	"INBOUND_LABEL_READY": "Etykieta do serwisu jest gotowa",
	"INBOUND_IN_TRANSIT": "Zegarek jest w drodze do serwisu",
	"AWAITING_PHYSICAL_RECEIPT": "Oczekuje na przekazanie zegarka",
	"RECEIVED": "Przyjęte do serwisu",
	"AWAITING_QUOTE_DECISION": "Wycena oczekuje na Twoją decyzję",
	"AWAITING_REPAIR_PAYMENT": "Oczekuje na płatność naprawy i zwrotu",
	"RETURN_LABEL_READY": "Przesyłka zwrotna jest przygotowana",
	"RETURN_IN_TRANSIT": "Zegarek jest w drodze do Ciebie",
	"READY_FOR_PICKUP": "Zegarek jest gotowy do odbioru w salonie",
	"COMPLETED": "Zakończono",
	"CANCELLED": "Anulowano",
	"REVIEW_REQUIRED": "Wymaga kontaktu z serwisem",
}
ACTIONS_BY_STATE = {
	"AWAITING_INBOUND_PAYMENT": ["pay_inbound"],
	"INBOUND_LABEL_READY": ["download_inbound_label"],
	"AWAITING_QUOTE_DECISION": ["accept_quote", "reject_quote"],
	"AWAITING_REPAIR_PAYMENT": ["pay_repair_return", "choose_return_cod"],
	"RETURN_LABEL_READY": ["download_return_label"],
}


class RemoteRepairError(RuntimeError):
	def __init__(self, code: str):
		self.code = code
		super().__init__(code)


def get_capabilities() -> dict[str, object]:
	features = []
	if frappe.conf.get(ROLLOUT_FLAG) is True and "public_id" in frappe.db.get_table_columns(DOCTYPE):
		features = ["account-intake-detail", "guest-intake-read", "approved-shipment-read"]
	return {"contract": CONTRACT_NAME, "schema_revision": SCHEMA_REVISION, "features": features}


def issue_guest_access(intake) -> str:
	"""Rotate one guest grant and return only its fragment-based access URL."""

	if intake.source != "Guest":
		return "/konto/naprawy"
	raw_token = secrets.token_urlsafe(32)
	grant_hash = _grant_hash(raw_token)
	intake.db_set(
		{
			"access_grant_hash": grant_hash,
			"access_grant_expires_at": add_to_date(now_datetime(), days=GRANT_DAYS),
			"access_grant_revoked": 0,
		},
		update_modified=False,
	)
	return f"/serwis/zgloszenie/{intake.public_id}#{raw_token}"


def notify_intake_created(intake, *, access_url: str) -> None:
	message = (
		"Dzień dobry,\n\n"
		"Otrzymaliśmy Twoje zgłoszenie naprawy zegarka. Serwis najpierw je zweryfikuje. "
		"Nie wysyłaj zegarka przed otrzymaniem instrukcji.\n"
		f"Podgląd zgłoszenia: {get_url(access_url)}\n\n"
		"Pozdrawiamy,\nSerwis Kuck"
	)
	_send(intake.email, "Przyjęliśmy zgłoszenie naprawy", message, intake=intake)


@frappe.whitelist(methods=["POST"])
def approve_remote_intake(intake_name: str, expected_modified: str) -> dict[str, str]:
	"""Allow payment only after an operator verifies customer, type and address."""

	from kuck_serwis.repair_intake import _lock, _require_revision, _require_service_role

	_require_service_role()
	_lock(intake_name)
	intake = frappe.get_doc(DOCTYPE, intake_name)
	_require_revision(intake, expected_modified)
	if intake.status != "Nowe" or intake.remote_state != "AWAITING_SERVICE_REVIEW":
		frappe.throw("REPAIR_INTAKE_INVALID_REMOTE_TRANSITION", frappe.ValidationError)
	if not intake.customer or not frappe.db.exists("Customer", intake.customer):
		frappe.throw("REPAIR_INTAKE_CUSTOMER_REQUIRED", frappe.ValidationError)
	if intake.delivery_method == "COURIER" and not _address_complete(intake):
		frappe.throw("REPAIR_INTAKE_ADDRESS_REQUIRED", frappe.ValidationError)
	intake.remote_state = (
		"AWAITING_INBOUND_PAYMENT" if intake.delivery_method == "COURIER" else "AWAITING_PHYSICAL_RECEIPT"
	)
	intake.service_approved_by = frappe.session.user
	intake.service_approved_at = now_datetime()
	intake.flags.repair_intake_transition = True
	intake.save()
	access_url = _existing_access_url(intake)
	message = (
		"Dzień dobry,\n\n"
		"Serwis zatwierdził Twoje zgłoszenie do dalszej obsługi. "
		"W panelu zgłoszenia znajdziesz instrukcję płatności i przekazania zegarka.\n"
		f"{get_url(access_url)}\n\nNie wysyłaj zegarka przed otrzymaniem etykiety lub instrukcji."
	)
	_send(intake.email, "Zgłoszenie zatwierdzone przez serwis", message, intake=intake)
	return {"remote_state": intake.remote_state}


def get_intake_for_current_user(public_id: str) -> dict[str, object]:
	_require_enabled()
	if not _valid_public_id(public_id):
		raise RemoteRepairError("NOT_FOUND")
	user = frappe.session.user
	customers = _authorized_customers(user)
	rows = frappe.get_all(
		DOCTYPE,
		filters={"public_id": public_id, "requester_user": user},
		fields=["name"],
		limit=1,
	)
	if not rows and customers:
		rows = frappe.get_all(
			DOCTYPE, filters={"public_id": public_id, "customer": ["in", customers]}, fields=["name"], limit=1
		)
	if not rows:
		raise RemoteRepairError("NOT_FOUND")
	return _public_case(frappe.get_doc(DOCTYPE, rows[0].name))


def get_intake_by_token(public_id: str, raw_token: str) -> dict[str, object]:
	if type(raw_token) is not str or not 40 <= len(raw_token) <= 128:
		raise RemoteRepairError("NOT_FOUND")
	return get_intake_by_grant_hash(public_id, grant_hash(raw_token))


def get_intake_by_grant_hash(public_id: str, hashed_grant: str) -> dict[str, object]:
	_require_enabled()
	if not _valid_public_id(public_id) or type(hashed_grant) is not str or len(hashed_grant) != 64:
		raise RemoteRepairError("NOT_FOUND")
	rows = frappe.get_all(
		DOCTYPE,
		filters={
			"public_id": public_id,
			"access_grant_hash": hashed_grant,
			"access_grant_revoked": 0,
			"access_grant_expires_at": [">", now_datetime()],
		},
		fields=["name"],
		limit=1,
	)
	if not rows:
		raise RemoteRepairError("NOT_FOUND")
	return _public_case(frappe.get_doc(DOCTYPE, rows[0].name))


def grant_hash(raw_token: str) -> str:
	return _grant_hash(raw_token)


def get_approved_shipment_snapshot(public_id: str, *, internal_scope: str) -> dict[str, object]:
	"""Return PII only to the in-process shop orchestrator after service approval."""

	_require_enabled()
	if internal_scope != "kuck_shop.repair_commerce.v1":
		raise RemoteRepairError("NOT_PERMITTED")
	if not _valid_public_id(public_id):
		raise RemoteRepairError("NOT_FOUND")
	rows = frappe.get_all(DOCTYPE, filters={"public_id": public_id}, fields=["name"], limit=1)
	if not rows:
		raise RemoteRepairError("NOT_FOUND")
	intake = frappe.get_doc(DOCTYPE, rows[0].name)
	if intake.remote_state not in {"AWAITING_INBOUND_PAYMENT", "INBOUND_PAYMENT_PENDING"}:
		raise RemoteRepairError("INVALID_TRANSITION")
	if intake.delivery_method != "COURIER" or not _address_complete(intake):
		raise RemoteRepairError("VALIDATION_FAILED")
	return {
		"schema": "repair-inbound-shipment/v1",
		"intake_id": intake.public_id,
		"revision": str(intake.modified),
		"declared_value_minor": round(float(intake.declared_value) * 100),
		"sender": {
			"name": intake.full_name,
			"email": intake.email,
			"phone": intake.phone,
			"address_line1": intake.address_line1,
			"address_line2": intake.address_line2,
			"city": intake.city,
			"postal_code": intake.postal_code,
			"country": intake.country,
		},
		"receiver": {
			"name": "Kuck",
			"address_line1": "ul. 11 Listopada 8",
			"address_line2": "",
			"city": "Olsztyn",
			"postal_code": "10-104",
			"country": "PL",
		},
	}


def mark_inbound_payment_pending(public_id: str, expected_revision: str, *, internal_scope: str) -> None:
	_internal_transition(
		public_id,
		internal_scope=internal_scope,
		expected_revision=expected_revision,
		allowed_from={"AWAITING_INBOUND_PAYMENT", "INBOUND_PAYMENT_PENDING"},
		to_state="INBOUND_PAYMENT_PENDING",
	)


def mark_inbound_label_ready(public_id: str, *, internal_scope: str) -> None:
	_internal_transition(
		public_id,
		internal_scope=internal_scope,
		expected_revision=None,
		allowed_from={"INBOUND_PAYMENT_PENDING", "INBOUND_LABEL_READY"},
		to_state="INBOUND_LABEL_READY",
	)


def _internal_transition(public_id, *, internal_scope, expected_revision, allowed_from, to_state):
	if internal_scope != "kuck_shop.repair_commerce.v1":
		raise RemoteRepairError("NOT_PERMITTED")
	if not _valid_public_id(public_id):
		raise RemoteRepairError("NOT_FOUND")
	rows = frappe.db.sql(
		"SELECT name FROM `tabKuck Repair Intake` WHERE public_id = %s FOR UPDATE", public_id
	)
	if not rows:
		raise RemoteRepairError("NOT_FOUND")
	intake = frappe.get_doc(DOCTYPE, rows[0][0])
	if expected_revision is not None and str(intake.modified) != expected_revision:
		raise RemoteRepairError("REVISION_CONFLICT")
	if intake.remote_state not in allowed_from:
		raise RemoteRepairError("INVALID_TRANSITION")
	if intake.remote_state == to_state:
		return
	intake.remote_state = to_state
	intake.flags.repair_intake_transition = True
	intake.save()


def set_remote_state(intake, remote_state: str) -> None:
	if remote_state not in REMOTE_LABELS:
		raise RemoteRepairError("VALIDATION_FAILED")
	intake.remote_state = remote_state
	intake.flags.repair_intake_transition = True


def get_accounting_snapshot(public_id, *, internal_scope):
	_require_enabled()
	if internal_scope != "kuck_shop.repair_commerce.v1":
		raise RemoteRepairError("NOT_PERMITTED")
	intake = _locked_intake(public_id)
	if not intake.customer:
		raise RemoteRepairError("VALIDATION_FAILED")
	return {"customer": intake.customer, "email": intake.email}


def submit_quote_decision(public_id, decision, expected_revision, request_id, access_token=None):
	"""Apply an owned quote decision through the existing Naprawa workflow."""

	from frappe.model.workflow import apply_workflow

	from kuck_serwis.repair_intake_contract import validate_idempotency_key

	_require_enabled()
	if decision not in {"accept", "reject"}:
		raise RemoteRepairError("VALIDATION_FAILED")
	validate_idempotency_key(request_id)
	if access_token:
		get_intake_by_token(public_id, access_token)
	else:
		get_intake_for_current_user(public_id)
	intake = _locked_intake(public_id)
	if intake.last_quote_request_id:
		if intake.last_quote_request_id == request_id and intake.last_quote_decision == decision:
			return {"decision": decision, "remote_state": intake.remote_state}
		raise RemoteRepairError("IDEMPOTENCY_CONFLICT")
	if str(intake.modified) != expected_revision or intake.remote_state != "AWAITING_QUOTE_DECISION":
		raise RemoteRepairError("REVISION_CONFLICT")
	if not intake.accepted_repair:
		raise RemoteRepairError("INVALID_TRANSITION")
	repair = frappe.get_doc("Naprawa", intake.accepted_repair)
	if repair.status != "Oczekuje na akceptację":
		raise RemoteRepairError("INVALID_TRANSITION")
	if decision == "accept":
		repair.klient_zaakceptowal = 1
		repair.akceptacja_uwagi = "Akceptacja wyceny w portalu klienta"
		repair.save()
		apply_workflow(repair, "Akceptacja klienta")
	else:
		apply_workflow(repair, "Klient rezygnuje")
	intake.last_quote_request_id = request_id
	intake.last_quote_decision = decision
	intake.remote_state = "READY_FOR_PICKUP" if intake.return_method == "SALON" else "AWAITING_REPAIR_PAYMENT"
	intake.flags.repair_intake_transition = True
	intake.save()
	return {"decision": decision, "remote_state": intake.remote_state}


def get_return_payment_snapshot(public_id, settlement, *, internal_scope):
	_require_enabled()
	if internal_scope != "kuck_shop.repair_commerce.v1":
		raise RemoteRepairError("NOT_PERMITTED")
	intake = _locked_intake(public_id)
	if intake.remote_state != "AWAITING_REPAIR_PAYMENT" or not _address_complete(intake):
		raise RemoteRepairError("INVALID_TRANSITION")
	outcome = _return_outcome(intake)
	if settlement == "FREE" and outcome not in {"warranty_approved", "manufacturer_forwarded"}:
		raise RemoteRepairError("INVALID_TRANSITION")
	amount = 0 if settlement == "FREE" else (2500 if settlement == "COD" else 2000)
	if settlement == "COD" and outcome not in {"rejected", "warranty_rejected"}:
		raise RemoteRepairError("INVALID_TRANSITION")
	repair_amount = 0
	if outcome == "paid" and settlement != "FREE":
		repair_amount = round(
			float(frappe.db.get_value("Naprawa", intake.accepted_repair, "orientacyjna_wycena") or 0) * 100
		)
	return {
		"schema": "repair-return-shipment/v1",
		"intake_id": intake.public_id,
		"revision": str(intake.modified),
		"declared_value_minor": round(float(intake.declared_value) * 100),
		"repair_amount_minor": repair_amount,
		"amount_minor": amount + repair_amount,
		"settlement": settlement,
		"outcome": outcome,
		"sender": {
			"name": intake.full_name,
			"email": intake.email,
			"phone": intake.phone,
			"address_line1": intake.address_line1,
			"address_line2": intake.address_line2,
			"city": intake.city,
			"postal_code": intake.postal_code,
			"country": intake.country,
		},
		"receiver": {
			"name": "Kuck",
			"address_line1": "ul. 11 Listopada 8",
			"address_line2": "",
			"city": "Olsztyn",
			"postal_code": "10-104",
			"country": "PL",
		},
	}


def mark_return_label_ready(public_id, *, internal_scope):
	_internal_transition(
		public_id,
		internal_scope=internal_scope,
		expected_revision=None,
		allowed_from={"AWAITING_REPAIR_PAYMENT", "RETURN_LABEL_READY"},
		to_state="RETURN_LABEL_READY",
	)


def mark_inbound_in_transit(public_id, *, internal_scope):
	_internal_transition(
		public_id,
		internal_scope=internal_scope,
		expected_revision=None,
		allowed_from={"INBOUND_LABEL_READY", "INBOUND_IN_TRANSIT"},
		to_state="INBOUND_IN_TRANSIT",
	)


def mark_inbound_delivered(public_id, *, internal_scope):
	_internal_transition(
		public_id,
		internal_scope=internal_scope,
		expected_revision=None,
		allowed_from={"INBOUND_LABEL_READY", "INBOUND_IN_TRANSIT", "AWAITING_PHYSICAL_RECEIPT"},
		to_state="AWAITING_PHYSICAL_RECEIPT",
	)


def mark_return_in_transit(public_id, *, internal_scope):
	_internal_transition(
		public_id,
		internal_scope=internal_scope,
		expected_revision=None,
		allowed_from={"RETURN_LABEL_READY", "RETURN_IN_TRANSIT"},
		to_state="RETURN_IN_TRANSIT",
	)


def mark_return_delivered(public_id, *, internal_scope):
	if internal_scope != "kuck_shop.repair_commerce.v1":
		raise RemoteRepairError("NOT_PERMITTED")
	intake = _locked_intake(public_id)
	if intake.remote_state not in {"RETURN_LABEL_READY", "RETURN_IN_TRANSIT", "COMPLETED"}:
		raise RemoteRepairError("INVALID_TRANSITION")
	if intake.accepted_repair:
		repair = frappe.get_doc("Naprawa", intake.accepted_repair)
		if repair.status == "Gotowe do odbioru":
			from frappe.model.workflow import apply_workflow

			apply_workflow(repair, "Wydaj zegarek")
	intake.remote_state = "COMPLETED"
	intake.flags.repair_intake_transition = True
	intake.save()


def _locked_intake(public_id):
	if not _valid_public_id(public_id):
		raise RemoteRepairError("NOT_FOUND")
	rows = frappe.db.sql(
		"SELECT name FROM `tabKuck Repair Intake` WHERE public_id = %s FOR UPDATE", public_id
	)
	if not rows:
		raise RemoteRepairError("NOT_FOUND")
	return frappe.get_doc(DOCTYPE, rows[0][0])


def _public_case(intake) -> dict[str, object]:
	return {
		"schema": "repair-intake-detail/v1",
		"intake_id": intake.public_id,
		"status": intake.status,
		"remote_state": intake.remote_state,
		"status_label": REMOTE_LABELS.get(intake.remote_state, "Wymaga kontaktu z serwisem"),
		"watch": {"brand": intake.brand_declared or None, "model": intake.model_declared or None},
		"submitted_on": intake.submitted_at.isoformat()[:10],
		"delivery_method": intake.delivery_method,
		"return_method": intake.return_method,
		"warranty_claim": bool(intake.warranty_claim),
		"actions": _public_actions(intake),
		"revision": str(intake.modified),
		"accepted_repair_id": _accepted_public_id(intake),
	}


def _public_actions(intake) -> list[str]:
	actions = list(ACTIONS_BY_STATE.get(intake.remote_state, []))
	if intake.remote_state == "AWAITING_REPAIR_PAYMENT":
		outcome = _return_outcome(intake)
		if intake.return_method == "SALON" or outcome in {"warranty_approved", "manufacturer_forwarded"}:
			return ["create_free_return"]
		if outcome in {"rejected", "warranty_rejected"}:
			return ["pay_repair_return", "choose_return_cod"]
		return ["pay_repair_return"]
	return actions


def _return_outcome(intake) -> str:
	if not intake.accepted_repair:
		return "paid"
	outcome = frappe.db.get_value("Naprawa", intake.accepted_repair, "wynik_gwarancji")
	return {
		"Uzasadniona": "warranty_approved",
		"Przekazana producentowi": "manufacturer_forwarded",
		"Nieuzasadniona": "warranty_rejected",
	}.get(outcome, "rejected" if intake.last_quote_decision == "reject" else "paid")


def _accepted_public_id(intake) -> str | None:
	if not intake.accepted_repair:
		return None
	return frappe.db.get_value("Naprawa", intake.accepted_repair, "public_id")


def _existing_access_url(intake) -> str:
	return f"/serwis/zgloszenie/{intake.public_id}" if intake.source == "Guest" else "/konto/naprawy"


def _authorized_customers(user: str) -> tuple[str, ...]:
	if not user or user == "Guest":
		raise RemoteRepairError("AUTH_REQUIRED")
	state = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
	if not state or not state.enabled or state.user_type != "Website User":
		raise RemoteRepairError("AUTH_REQUIRED")
	return tuple(
		sorted(
			set(
				frappe.get_all(
					"Portal User",
					filters={"parenttype": "Customer", "parentfield": "portal_users", "user": user},
					pluck="parent",
				)
			)
		)
	)


def _address_complete(intake) -> bool:
	return bool(
		intake.address_line1
		and intake.city
		and re.fullmatch(r"[0-9]{2}-[0-9]{3}", intake.postal_code or "")
		and intake.country == "PL"
	)


def _grant_hash(raw_token: str) -> str:
	return hashlib.sha256(b"kuck.repair-intake.access-grant.v1\0" + raw_token.encode("ascii")).hexdigest()


def _valid_public_id(value: object) -> bool:
	return type(value) is str and PUBLIC_ID_RE.fullmatch(value) is not None


def _require_enabled() -> None:
	if frappe.conf.get(ROLLOUT_FLAG) is not True:
		raise RemoteRepairError("DEPENDENCY_UNAVAILABLE")


def _send(recipient: str, subject: str, message: str, *, intake) -> None:
	try:
		frappe.sendmail(
			recipients=[recipient],
			subject=subject,
			message=message,
			reference_doctype=DOCTYPE,
			reference_name=intake.name,
			delayed=True,
		)
	except Exception:
		frappe.logger("repair_intake", allow_site=True).error(
			{"event": "repair_transactional_notification_failed"}
		)
