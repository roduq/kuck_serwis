# Copyright (c) 2026, Kuck and contributors
# For license information, please see license.txt

import re
import secrets
from collections import Counter

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, today

from kuck_serwis.contact_update import (
	ContactSnapshot,
	ContactUpdateCode,
	ContactUpdateOutcome,
	ContactUpdatePlan,
	plan_contact_update,
)
from kuck_serwis.repair_photo_policy import (
	PrivateFileEvidence,
	RepairPhotoPolicyCode,
	RepairPhotoPolicyError,
	RepairPhotoRow,
	validate_repair_photo_references,
)

# Typy napraw, które bywają bezpłatne — przy wydaniu nie wymuszamy na nich kwoty > 0.
RODZAJE_BEZPLATNE = ("Gwarancja", "Reklamacja")
PUBLIC_ID_PATTERN = re.compile(r"^rpr_[A-Za-z0-9_-]{32}$")
PUBLIC_ID_GENERATION_ATTEMPTS = 5


class Naprawa(Document):
	def before_insert(self):
		if self.public_id:
			frappe.throw(_("Publiczny identyfikator jest zarządzany przez system."))
		self.public_id = generate_unique_public_id()

	def validate(self):
		self.validate_public_id_immutable()
		self.validate_photo_privacy()
		self.ustaw_kategorie_glowna()
		self.set_akceptacja_date()
		self.guard_w_naprawie()
		self.uzupelnij_kwote_odbioru()
		self.set_data_wydania()
		self.prepare_contact_update()

	def validate_photo_privacy(self):
		"""Stop new public references while preserving exact legacy child rows."""
		if not self.zdjecia:
			return
		current_rows = tuple(
			RepairPhotoRow(name=row.name, parent=row.parent, file_url=row.zdjecie) for row in self.zdjecia
		)
		stored_rows = () if self.is_new() else _stored_photo_rows(self.name)
		private_files = _private_file_evidence(tuple(row.zdjecie for row in self.zdjecia))
		try:
			validate_repair_photo_references(
				owner_doctype="Naprawa",
				owner_name=self.name,
				current_rows=current_rows,
				stored_rows=stored_rows,
				private_files=private_files,
			)
		except RepairPhotoPolicyError as error:
			frappe.throw(error.code.value)

	def validate_public_id_immutable(self):
		"""Nie pozwala formularzom, importom ani kodowi API zmieniać publicznego ID."""
		if self.is_new():
			return
		stored_public_id = frappe.db.get_value("Naprawa", self.name, "public_id")
		if stored_public_id != self.public_id:
			frappe.throw(_("Publiczny identyfikator jest niezmienny."))

	def ustaw_kategorie_glowna(self):
		"""Główna kategoria naprawy = dominująca kategoria wśród wybranych usterek.

		Daje rozłączny podział sprzedaży po kategoriach w raporcie wydań (jedna naprawa = jedna
		kategoria, więc kwota nie dubluje się między kategoriami). Uzupełniamy tylko gdy pole jest
		puste — świadomej korekty recepcji nie nadpisujemy.
		"""
		if self.kategoria_glowna:
			return
		kategorie = [
			frappe.db.get_value("Usterka", u.usterka, "kategoria") for u in (self.usterki or []) if u.usterka
		]
		kategorie = [k for k in kategorie if k]
		if kategorie:
			self.kategoria_glowna = Counter(kategorie).most_common(1)[0][0]

	def uzupelnij_kwote_odbioru(self):
		"""Przy wydaniu kwota odbioru jest obowiązkowa — chroni rzetelność obrotu w raportach.

		Jeśli pole jest puste, podpowiadamy orientacyjną wycenę (recepcja może poprawić jednym
		ruchem). Naprawy gwarancyjne/reklamacyjne bywają bezpłatne, a pole Currency we Frappe i
		tak nie odróżnia „0 wpisane" od „puste" — dlatego blokujemy brak kwoty tylko dla typów
		płatnych, a darmowe wydania (0 zł) przepuszczamy.
		"""
		if self.status != "Wydano":
			return
		if not flt(self.kwota_odbioru) and self.orientacyjna_wycena:
			self.kwota_odbioru = self.orientacyjna_wycena
		if not flt(self.kwota_odbioru) and self.rodzaj_naprawy not in RODZAJE_BEZPLATNE:
			frappe.throw(_("Podaj kwotę odbioru przed wydaniem zegarka."))

	def on_update(self):
		self.apply_prepared_contact_update()

	def prepare_contact_update(self):
		"""Prepare a field-level CAS before the Naprawa row is written.

		An unrelated save intentionally leaves the stored Naprawa contact snapshot
		unchanged.  It is an inert editing baseline, not a source-of-truth refresh;
		this can cause a safe conflict on a later explicit edit rather than overwrite
		a newer Customer/Contact value.
		"""
		previous = self.get_doc_before_save()
		baseline = _contact_snapshot(previous)
		proposed = _contact_snapshot(self)
		preflight = plan_contact_update(
			is_new=previous is None,
			customer_changed=previous is not None and previous.klient != self.klient,
			baseline=baseline,
			proposed=proposed,
			current=baseline,
		)
		self.flags.contact_update_plan = preflight
		self.flags.contact_update_customer = None
		self.flags.contact_update_contact = None
		_raise_contact_conflict(preflight)
		if preflight.outcome is not ContactUpdateOutcome.APPLY:
			return

		customer, contact = _lock_contact_target(self.klient)
		current = _contact_snapshot(contact or customer)
		plan = plan_contact_update(
			is_new=False,
			customer_changed=False,
			baseline=baseline,
			proposed=proposed,
			current=current,
		)
		_raise_contact_conflict(plan)
		self.flags.contact_update_plan = plan
		self.flags.contact_update_customer = customer
		self.flags.contact_update_contact = contact

	def apply_prepared_contact_update(self):
		plan = self.flags.get("contact_update_plan")
		if type(plan) is not ContactUpdatePlan or plan.outcome is not ContactUpdateOutcome.APPLY:
			return
		customer = self.flags.get("contact_update_customer")
		contact = self.flags.get("contact_update_contact")
		if contact is not None:
			if plan.update_phone:
				_ustaw_glowny_telefon(contact, plan.phone)
			if plan.update_email:
				_ustaw_glowny_email(contact, plan.email)
			contact.flags.ignore_mandatory = True
			contact.save()
			customer.db_set({"mobile_no": contact.mobile_no, "email_id": contact.email_id})
			return
		changes = {}
		if plan.update_phone:
			changes["mobile_no"] = plan.phone
		if plan.update_email:
			changes["email_id"] = plan.email
		customer.db_set(changes)

	def set_akceptacja_date(self):
		"""Akceptacja może nastąpić na każdym etapie (także z góry przy przyjęciu)."""
		if self.klient_zaakceptowal and not self.data_akceptacji:
			self.data_akceptacji = today()

	def guard_w_naprawie(self):
		"""Naprawa nie rusza bez zgody klienta — niezależnie od tego, czy była osobna wycena."""
		if self.status == "W naprawie" and not self.klient_zaakceptowal:
			frappe.throw(
				_(
					"Nie można rozpocząć naprawy bez akceptacji klienta. Zaznacz „Klient zaakceptował naprawę”."
				)
			)

	def set_data_wydania(self):
		if self.status == "Wydano" and not self.data_wydania:
			self.data_wydania = today()


def _contact_snapshot(document) -> ContactSnapshot:
	if document is None:
		return ContactSnapshot(phone="", email="")
	return ContactSnapshot(
		phone=(getattr(document, "klient_telefon", None) or getattr(document, "mobile_no", None) or ""),
		email=(getattr(document, "klient_email", None) or getattr(document, "email_id", None) or ""),
	)


def _raise_contact_conflict(plan: ContactUpdatePlan) -> None:
	if plan.outcome is ContactUpdateOutcome.CONFLICT:
		frappe.throw(plan.code.value)


def _require_contact_write_permission(document) -> None:
	if not frappe.has_permission(document.doctype, ptype="write", doc=document):
		frappe.throw(ContactUpdateCode.CONTACT_UPDATE_FORBIDDEN.value)


def _lock_contact_target(customer_name: str):
	customer = frappe.get_doc("Customer", customer_name, for_update=True)
	_require_contact_write_permission(customer)
	contact = None
	if customer.customer_primary_contact:
		contact = frappe.get_doc("Contact", customer.customer_primary_contact, for_update=True)
		_validate_contact_owner(contact, customer.name)
		_require_contact_write_permission(contact)
	return customer, contact


def _validate_contact_owner(contact, customer_name: str) -> None:
	customer_links = tuple(
		link.link_name for link in (contact.links or ()) if link.link_doctype == "Customer"
	)
	if customer_links != (customer_name,):
		frappe.throw(ContactUpdateCode.CONTACT_TARGET_MISMATCH.value)


def _ustaw_glowny_telefon(contact, telefon):
	"""Czyni `telefon` jedynym głównym numerem kontaktu (dodaje wiersz, jeśli go nie ma)."""
	if not any(p.phone == telefon for p in contact.phone_nos):
		contact.append("phone_nos", {"phone": telefon})
	for p in contact.phone_nos:
		glowny = 1 if p.phone == telefon else 0
		p.is_primary_phone = glowny
		p.is_primary_mobile_no = glowny


def _ustaw_glowny_email(contact, email):
	"""Czyni `email` jedynym głównym adresem kontaktu (dodaje wiersz, jeśli go nie ma)."""
	if not any(e.email_id == email for e in contact.email_ids):
		contact.append("email_ids", {"email_id": email})
	for e in contact.email_ids:
		e.is_primary = 1 if e.email_id == email else 0


def generate_unique_public_id():
	"""Generuje nieprzewidywalny identyfikator; constraint bazy chroni przed wyścigiem."""
	for _attempt in range(PUBLIC_ID_GENERATION_ATTEMPTS):
		candidate = f"rpr_{secrets.token_urlsafe(24)}"
		if PUBLIC_ID_PATTERN.fullmatch(candidate) and not frappe.db.exists(
			"Naprawa", {"public_id": candidate}
		):
			return candidate
	frappe.throw(_("Nie udało się nadać publicznego identyfikatora naprawy."))


def _stored_photo_rows(parent: str) -> tuple[RepairPhotoRow, ...]:
	rows = frappe.get_all(
		"Naprawa Zdjecie",
		filters={"parent": parent, "parenttype": "Naprawa", "parentfield": "zdjecia"},
		fields=["name", "parent", "zdjecie"],
		order_by="idx asc",
	)
	return tuple(RepairPhotoRow(name=row.name, parent=row.parent, file_url=row.zdjecie) for row in rows)


def _private_file_evidence(file_urls: tuple[str, ...]) -> tuple[PrivateFileEvidence, ...]:
	private_urls = tuple(url for url in file_urls if type(url) is str and url.startswith("/private/files/"))
	if not private_urls:
		return ()
	rows = frappe.get_all(
		"File",
		filters={"file_url": ("in", private_urls)},
		fields=[
			"name",
			"file_url",
			"is_private",
			"attached_to_doctype",
			"attached_to_name",
			"attached_to_field",
		],
		order_by="name asc",
	)
	return tuple(
		PrivateFileEvidence(
			name=row.name,
			file_url=row.file_url,
			is_private=_exact_file_private_flag(row.is_private),
			attached_to_doctype=row.attached_to_doctype,
			attached_to_name=row.attached_to_name,
			attached_to_field=row.attached_to_field,
		)
		for row in rows
	)


def _exact_file_private_flag(value: object) -> bool:
	if type(value) is bool:
		return value
	if type(value) is int and value in (0, 1):
		return bool(value)
	raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_INPUT)
