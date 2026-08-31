from __future__ import annotations

import hashlib
import re

import frappe
from frappe.model.document import Document
from frappe.utils import flt

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_STATUSES = frozenset({"Nowe", "Przyjęte", "Odrzucone"})
_SOURCES = frozenset({"Guest", "Portal User"})
_METHODS = frozenset({"SALON", "COURIER"})


class KuckRepairIntake(Document):
	def validate(self):
		if self.status not in _STATUSES or self.source not in _SOURCES:
			frappe.throw("REPAIR_INTAKE_INVALID_STATE")
		if self.delivery_method not in _METHODS or self.return_method not in _METHODS:
			frappe.throw("REPAIR_INTAKE_INVALID_DELIVERY")
		if _DIGEST.fullmatch(self.idempotency_binding or "") is None:
			frappe.throw("REPAIR_INTAKE_INVALID_BINDING")
		if _DIGEST.fullmatch(self.request_fingerprint or "") is None:
			frappe.throw("REPAIR_INTAKE_INVALID_FINGERPRINT")
		if _DIGEST.fullmatch(self.privacy_proof_sha256 or "") is None:
			frappe.throw("REPAIR_INTAKE_INVALID_PRIVACY_PROOF")
		self._validate_photos()
		value = flt(self.declared_value)
		if value < 0 or value > 10000:
			frappe.throw("REPAIR_INTAKE_VALUE_OUT_OF_RANGE")
		if (self.delivery_method == "COURIER" or self.return_method == "COURIER") and value <= 0:
			frappe.throw("REPAIR_INTAKE_VALUE_REQUIRED")
		previous = self.get_doc_before_save()
		if previous:
			for fieldname in (
				"source",
				"requester_user",
				"full_name",
				"email",
				"phone",
				"brand_declared",
				"model_declared",
				"serial_number",
				"purchase_date",
				"issue_description",
				"condition_description",
				"warranty_claim",
				"delivery_method",
				"return_method",
				"declared_value",
				"privacy_revision",
				"privacy_proof_sha256",
				"idempotency_binding",
				"request_fingerprint",
				"submitted_at",
				"photo_count",
				"photo_manifest_sha256",
			):
				current_value = self.get(fieldname)
				previous_value = previous.get(fieldname)
				if fieldname == "declared_value":
					current_value = flt(current_value)
					previous_value = flt(previous_value)
				if current_value != previous_value and not (
					fieldname in {"photo_count", "photo_manifest_sha256"}
					and self.flags.get("repair_intake_photo_initialization")
				):
					frappe.throw("REPAIR_INTAKE_SUBMISSION_IMMUTABLE")
			if self.as_dict().get("photos") != previous.as_dict().get("photos") and not self.flags.get(
				"repair_intake_photo_initialization"
			):
				frappe.throw("REPAIR_INTAKE_PHOTOS_IMMUTABLE")
			if (
				self.status != previous.status
				or self.accepted_repair != previous.accepted_repair
				or self.reviewed_by != previous.reviewed_by
				or self.reviewed_at != previous.reviewed_at
			) and not self.flags.get("repair_intake_transition"):
				frappe.throw("REPAIR_INTAKE_CONTROLLED_TRANSITION_REQUIRED")
		if self.status == "Przyjęte" and not self.accepted_repair:
			frappe.throw("REPAIR_INTAKE_REPAIR_REQUIRED")
		if self.accepted_repair and self.status != "Przyjęte":
			frappe.throw("REPAIR_INTAKE_INVALID_REPAIR_LINK")

	def _validate_photos(self):
		rows = tuple(self.get("photos") or ())
		if len(rows) > 3 or int(self.photo_count or 0) != len(rows):
			frappe.throw("REPAIR_INTAKE_PHOTO_COUNT_INVALID")
		if rows and _DIGEST.fullmatch(self.photo_manifest_sha256 or "") is None:
			frappe.throw("REPAIR_INTAKE_PHOTO_MANIFEST_INVALID")
		if not rows and self.photo_manifest_sha256:
			frappe.throw("REPAIR_INTAKE_PHOTO_MANIFEST_INVALID")
		seen = set()
		for row in rows:
			if (
				row.photo in seen
				or not (row.photo or "").startswith("/private/files/")
				or _DIGEST.fullmatch(row.content_sha256 or "") is None
				or row.normalizer_version != "1"
				or row.scan_status != "NOT_SCANNED"
			):
				frappe.throw("REPAIR_INTAKE_PHOTO_INVALID")
			seen.add(row.photo)
			if not self.is_new():
				matches = frappe.get_all(
					"File",
					filters={
						"file_url": row.photo,
						"is_private": 1,
						"attached_to_doctype": self.doctype,
						"attached_to_name": self.name,
						"attached_to_field": "photos",
					},
					pluck="name",
					limit=2,
				)
				if len(matches) != 1:
					frappe.throw("REPAIR_INTAKE_PHOTO_ATTACHMENT_INVALID")
		if rows:
			manifest = hashlib.sha256(
				("kuck.repair-intake.photos.v1\0" + "\0".join(row.content_sha256 for row in rows)).encode()
			).hexdigest()
			if self.photo_manifest_sha256 != manifest:
				frappe.throw("REPAIR_INTAKE_PHOTO_MANIFEST_INVALID")
