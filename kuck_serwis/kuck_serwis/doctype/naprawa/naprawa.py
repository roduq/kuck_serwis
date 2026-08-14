# Copyright (c) 2026, Kuck and contributors
# For license information, please see license.txt

import re
import secrets
from collections import Counter

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, today

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
		self.ustaw_kategorie_glowna()
		self.set_akceptacja_date()
		self.guard_w_naprawie()
		self.uzupelnij_kwote_odbioru()
		self.set_data_wydania()

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
		self.zapisz_kontakt_u_klienta()

	def zapisz_kontakt_u_klienta(self):
		"""Telefon/e-mail to dane KLIENTA (Customer), nie naprawy.

		Pola na naprawie są tylko wygodnym oknem do danych klienta: przy wyborze
		klienta zaciągają się automatycznie (fetch_from), a edycja tutaj zapisuje się
		z powrotem przy kliencie. W ERPNext dane kontaktowe żyją w Contact (Customer
		pokazuje je tylko jako podsumowanie), więc — jeśli klient ma główny kontakt —
		aktualizujemy ten kontakt; jeśli nie, zapisujemy wprost na Customerze.
		"""
		if not self.klient or not (self.klient_telefon or self.klient_email):
			return

		customer = frappe.get_doc("Customer", self.klient)
		tel_zmieniony = self.klient_telefon and self.klient_telefon != (customer.mobile_no or "")
		mail_zmieniony = self.klient_email and self.klient_email != (customer.email_id or "")
		if not (tel_zmieniony or mail_zmieniony):
			return

		if customer.customer_primary_contact:
			self._zapisz_w_kontakcie(customer)
		else:
			zmiany = {}
			if tel_zmieniony:
				zmiany["mobile_no"] = self.klient_telefon
			if mail_zmieniony:
				zmiany["email_id"] = self.klient_email
			frappe.db.set_value("Customer", self.klient, zmiany)

	def _zapisz_w_kontakcie(self, customer):
		"""Ustawia telefon/e-mail jako główne w kontakcie klienta i odświeża podsumowanie na Customerze."""
		contact = frappe.get_doc("Contact", customer.customer_primary_contact)
		if self.klient_telefon:
			_ustaw_glowny_telefon(contact, self.klient_telefon)
		if self.klient_email:
			_ustaw_glowny_email(contact, self.klient_email)
		contact.flags.ignore_mandatory = True
		contact.save(ignore_permissions=True)
		# Customer.mobile_no/email_id to fetch_from głównego kontaktu — odświeżamy go zapisem.
		frappe.db.set_value(
			"Customer",
			customer.name,
			{"mobile_no": contact.mobile_no, "email_id": contact.email_id},
		)

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
