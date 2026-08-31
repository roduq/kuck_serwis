"""Frappe boundary for public repair intake and controlled Desk promotion."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

import frappe
from frappe.rate_limiter import rate_limit
from frappe.utils import get_datetime, now_datetime

from kuck_serwis.repair_intake_contract import (
	PRIVACY_PROOF_SHA256,
	PRIVACY_REVISION,
	RepairIntakeContractError,
	validate_idempotency_key,
	validate_submission,
)
from kuck_serwis.repair_intake_photo import (
	RepairIntakePhotoError,
	bind_normalized_photo_to_repair,
	media_fingerprint,
	normalize_uploaded_photos,
)
from kuck_serwis.repair_intake_security import request_actor_scope, require_write_request

DOCTYPE = "Kuck Repair Intake"
_SUCCESS = {"accepted": True}
_REPAIR_TYPES = frozenset({"Naprawa krótka", "Naprawa długa", "Gwarancja", "Reklamacja"})


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=8, seconds=60 * 60)
def submit_repair_intake(payload: object = None, idempotency_key: object = None) -> dict[str, bool]:
	"""Create one private intake without creating or disclosing Customer/Repair."""
	require_write_request()
	try:
		raw = _payload(payload)
		submission = validate_submission(raw)
		key = validate_idempotency_key(idempotency_key)
	except (
		RepairIntakeContractError,
		json.JSONDecodeError,
		UnicodeDecodeError,
		TypeError,
	):
		frappe.throw("REPAIR_INTAKE_VALIDATION_FAILED", frappe.ValidationError)
	if submission.honeypot_triggered:
		return dict(_SUCCESS)
	try:
		photos = normalize_uploaded_photos(_request_photo_files())
	except RepairIntakePhotoError:
		frappe.throw("REPAIR_INTAKE_VALIDATION_FAILED", frappe.ValidationError)

	scope = request_actor_scope()
	binding = _digest("idempotency", scope, _digest("key", key))
	fingerprint = _digest("request", submission.canonical_json(), media_fingerprint(photos))
	legacy_fingerprint = _digest("request", submission.canonical_json()) if not photos else None
	existing = _existing(binding)
	if existing:
		return _replay(existing, fingerprint, legacy_fingerprint)

	user = _current_user()
	customer = _single_authorized_customer(user)
	source = "Guest" if user == "Guest" else "Portal User"
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"status": "Nowe",
			"source": source,
			"requester_user": None if user == "Guest" else user,
			"customer": customer,
			"full_name": submission.full_name,
			"email": submission.email,
			"phone": submission.phone,
			"brand_declared": submission.brand,
			"model_declared": submission.model,
			"serial_number": submission.serial_number,
			"purchase_date": submission.purchase_date,
			"issue_description": submission.issue_description,
			"condition_description": submission.condition_description,
			"warranty_claim": submission.warranty,
			"suggested_repair_type": "Gwarancja" if submission.warranty else "Naprawa długa",
			"delivery_method": submission.delivery_method.value,
			"return_method": submission.return_method.value,
			"declared_value": submission.declared_value,
			"privacy_revision": PRIVACY_REVISION,
			"privacy_proof_sha256": PRIVACY_PROOF_SHA256,
			"idempotency_binding": binding,
			"request_fingerprint": fingerprint,
			"submitted_at": now_datetime(),
		}
	)
	doc.flags.public_repair_intake = True
	try:
		doc.insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		existing = _existing(binding)
		if existing:
			return _replay(existing, fingerprint, legacy_fingerprint)
		raise
	# Never catch failures below: Frappe rolls the request transaction back, and
	# every File insert registers File.on_rollback to remove its private blob.
	for photo in photos:
		attachment = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"repair-intake-{frappe.generate_hash(length=16)}.jpg",
				"is_private": 1,
				"content": photo.body,
				"attached_to_doctype": DOCTYPE,
				"attached_to_name": doc.name,
				"attached_to_field": "photos",
			}
		).insert(ignore_permissions=True)
		doc.append(
			"photos",
			{
				"photo": attachment.file_url,
				"content_sha256": photo.sha256,
				"width": photo.width,
				"height": photo.height,
				"normalizer_version": "1",
				"scan_status": "NOT_SCANNED",
			},
		)
	if photos:
		doc.photo_count = len(photos)
		doc.photo_manifest_sha256 = media_fingerprint(photos)
		doc.flags.repair_intake_photo_initialization = True
		doc.save(ignore_permissions=True)
	frappe.logger("repair_intake", allow_site=True).info(
		{"event": "repair_intake_created", "source": source, "account_linked": bool(customer)}
	)
	return dict(_SUCCESS)


@frappe.whitelist(methods=["POST"])
def accept_repair_intake(
	intake_name: str,
	expected_modified: str,
	physical_receipt_confirmed: object,
) -> dict[str, str]:
	"""Create one Naprawa only after a service operator confirms physical receipt."""
	_require_service_role()
	if physical_receipt_confirmed not in (True, 1, "1", "true", "on"):
		frappe.throw("PHYSICAL_RECEIPT_REQUIRED", frappe.ValidationError)
	_lock(intake_name)
	doc = frappe.get_doc(DOCTYPE, intake_name)
	if doc.status == "Przyjęte" and doc.accepted_repair:
		return {"repair": doc.accepted_repair}
	_require_revision(doc, expected_modified)
	if doc.status == "Odrzucone":
		frappe.throw("REPAIR_INTAKE_REJECTED", frappe.ValidationError)
	if not doc.customer or not frappe.db.exists("Customer", doc.customer):
		frappe.throw("REPAIR_INTAKE_CUSTOMER_REQUIRED", frappe.ValidationError)
	if doc.suggested_repair_type not in _REPAIR_TYPES:
		frappe.throw("REPAIR_INTAKE_TYPE_INVALID", frappe.ValidationError)
	brand = doc.brand_declared if frappe.db.exists("Marka Zegarka", doc.brand_declared) else None
	repair = frappe.get_doc(
		{
			"doctype": "Naprawa",
			"klient": doc.customer,
			"klient_telefon": doc.phone,
			"klient_email": doc.email,
			"rodzaj_naprawy": doc.suggested_repair_type,
			"marka": brand,
			"model_zegarka": doc.model_declared,
			"numer_seryjny": doc.serial_number,
			"data_zakupu": doc.purchase_date,
			"opis_naprawy": doc.issue_description,
			"stan_przy_przyjeciu": doc.condition_description,
			"sposob_dostarczenia": _legacy_delivery(doc.delivery_method),
			"sposob_odbioru": _legacy_delivery(doc.return_method),
			"powiadom_sms": 0,
			"powiadom_email": 0,
		}
	)
	transfer_files = tuple(_exact_intake_photo_file(doc.name, row) for row in doc.photos or ())
	repair.insert()
	for position, (row, source_file) in enumerate(zip(doc.photos or (), transfer_files, strict=True), 1):
		content = source_file.get_content()
		if type(content) is not bytes or hashlib.sha256(content).hexdigest() != row.content_sha256:
			frappe.throw("REPAIR_INTAKE_PHOTO_CONTENT_INVALID", frappe.ValidationError)
		binding = _digest("photo-copy", repair.name, str(position), row.content_sha256)
		bound_content = bind_normalized_photo_to_repair(content, binding)
		attachment = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"repair-{frappe.generate_hash(length=16)}.jpg",
				"is_private": 1,
				"content": bound_content,
				"attached_to_doctype": "Naprawa",
				"attached_to_name": repair.name,
				"attached_to_field": "zdjecie",
			}
		).insert(ignore_permissions=True)
		if not attachment.is_private or attachment.file_url == row.photo:
			frappe.throw("REPAIR_INTAKE_PHOTO_TRANSFER_FAILED", frappe.ValidationError)
		repair.append(
			"zdjecia",
			{
				"zdjecie": attachment.file_url,
				"opis": "Zdjęcie ze zgłoszenia online",
				"source_intake": doc.name,
				"source_intake_position": position,
				"source_content_sha256": row.content_sha256,
			},
		)
	if doc.photos:
		repair.save()
	doc.status = "Przyjęte"
	doc.accepted_repair = repair.name
	doc.reviewed_by = frappe.session.user
	doc.reviewed_at = now_datetime()
	doc.flags.repair_intake_transition = True
	doc.save()
	frappe.logger("repair_intake", allow_site=True).info({"event": "repair_intake_accepted"})
	return {"repair": repair.name}


@frappe.whitelist(methods=["POST"])
def reject_repair_intake(intake_name: str, expected_modified: str, reason: object) -> dict[str, bool]:
	_require_service_role()
	if type(reason) is not str or not 3 <= len(reason.strip()) <= 500:
		frappe.throw("REPAIR_INTAKE_REJECTION_REASON_REQUIRED", frappe.ValidationError)
	_lock(intake_name)
	doc = frappe.get_doc(DOCTYPE, intake_name)
	if doc.status == "Odrzucone":
		return {"rejected": True}
	_require_revision(doc, expected_modified)
	if doc.status == "Przyjęte":
		frappe.throw("REPAIR_INTAKE_ALREADY_ACCEPTED", frappe.ValidationError)
	doc.status = "Odrzucone"
	doc.review_notes = reason.strip()
	doc.reviewed_by = frappe.session.user
	doc.reviewed_at = now_datetime()
	doc.flags.repair_intake_transition = True
	doc.save()
	frappe.logger("repair_intake", allow_site=True).info({"event": "repair_intake_rejected"})
	return {"rejected": True}


def requester_prefill() -> dict[str, object]:
	user = _current_user()
	if user == "Guest":
		return {"full_name": "", "email": "", "phone": "", "linked_to_account": False}
	customer = _single_authorized_customer(user)
	if customer:
		row = frappe.db.get_value(
			"Customer", customer, ["customer_name", "email_id", "mobile_no"], as_dict=True
		)
		if row:
			return {
				"full_name": row.customer_name or "",
				"email": row.email_id or user,
				"phone": row.mobile_no or "",
				"linked_to_account": True,
			}
	row = frappe.db.get_value("User", user, ["full_name", "email"], as_dict=True)
	return {
		"full_name": (row.full_name if row else "") or "",
		"email": (row.email if row else user) or "",
		"phone": "",
		"linked_to_account": False,
	}


def _payload(value: object) -> dict:
	if type(value) is dict:
		return value
	if type(value) is str and len(value.encode("utf-8")) <= 20_000:
		decoded = json.loads(value)
		if type(decoded) is dict:
			return decoded
	raise TypeError


def _request_photo_files() -> tuple[object, ...]:
	request = getattr(getattr(frappe, "local", None), "request", None)
	files = getattr(request, "files", None)
	if files is None:
		return ()
	keys = tuple(files.keys())
	if any(key != "photos" for key in keys):
		raise RepairIntakePhotoError("REPAIR_INTAKE_PHOTO_INVALID")
	return tuple(files.getlist("photos")) if "photos" in keys else ()


def _exact_intake_photo_file(intake_name: str, row):
	matches = frappe.get_all(
		"File",
		filters={
			"file_url": row.photo,
			"is_private": 1,
			"attached_to_doctype": DOCTYPE,
			"attached_to_name": intake_name,
			"attached_to_field": "photos",
		},
		pluck="name",
		limit=2,
	)
	if len(matches) != 1:
		frappe.throw("REPAIR_INTAKE_PHOTO_ATTACHMENT_INVALID", frappe.ValidationError)
	return frappe.get_doc("File", matches[0])


def _current_user() -> str:
	user = getattr(getattr(frappe, "session", None), "user", None)
	return user if type(user) is str and user else "Guest"


def _single_authorized_customer(user: str) -> str | None:
	if user == "Guest":
		return None
	state = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
	if not state or not state.enabled or state.user_type != "Website User":
		return None
	customers = tuple(
		sorted(
			set(
				frappe.get_all(
					"Portal User",
					filters={
						"parenttype": "Customer",
						"parentfield": "portal_users",
						"user": user,
					},
					pluck="parent",
				)
			)
		)
	)
	return customers[0] if len(customers) == 1 else None


def _existing(binding: str):
	return frappe.db.get_value(
		DOCTYPE,
		{"idempotency_binding": binding},
		["name", "request_fingerprint"],
		as_dict=True,
	)


def _replay(existing, fingerprint: str, legacy_fingerprint: str | None = None) -> dict[str, bool]:
	if existing.request_fingerprint not in {fingerprint, legacy_fingerprint}:
		frappe.throw("REPAIR_INTAKE_IDEMPOTENCY_CONFLICT", frappe.ValidationError)
	return dict(_SUCCESS)


def _digest(domain: str, *values: str) -> str:
	content = b"\0".join(value.encode("utf-8") for value in values)
	return hashlib.sha256(f"kuck.repair-intake.{domain}.v1\0".encode() + content).hexdigest()


def _require_service_role() -> None:
	if not {"Serwis", "System Manager"}.intersection(frappe.get_roles()):
		frappe.throw("REPAIR_INTAKE_NOT_PERMITTED", frappe.PermissionError)


def _lock(name: str) -> None:
	if type(name) is not str or not name.startswith("RIN-") or len(name) > 64:
		frappe.throw("REPAIR_INTAKE_NOT_FOUND", frappe.DoesNotExistError)
	row = frappe.db.sql(f"SELECT name FROM `tab{DOCTYPE}` WHERE name = %s FOR UPDATE", name)
	if not row:
		frappe.throw("REPAIR_INTAKE_NOT_FOUND", frappe.DoesNotExistError)


def _require_revision(doc, expected_modified: str) -> None:
	try:
		expected = get_datetime(expected_modified)
	except (TypeError, ValueError):
		frappe.throw("REPAIR_INTAKE_STATE_CONFLICT", frappe.ValidationError)
	actual = get_datetime(doc.modified)
	if not isinstance(expected, datetime) or actual != expected:
		frappe.throw("REPAIR_INTAKE_STATE_CONFLICT", frappe.ValidationError)


def _legacy_delivery(value: str) -> str:
	return "Wysyłkowo" if value == "COURIER" else "Stacjonarnie"
