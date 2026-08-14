# Copyright (c) 2026, Kuck and contributors
# For license information, please see license.txt
"""Przyjęcie zbiorcze — skrót przy wpisywaniu wielu zegarków jednego klienta naraz.

To TYLKO warstwa wejściowa. Po kliknięciu „Utwórz naprawy” każda pozycja staje się osobną,
normalną `Naprawa` z własnym numerem i niezależnym obiegiem (status, wycena, akceptacja,
wydanie, gwarancja). Sam rekord przyjęcia nie steruje naprawami po ich utworzeniu — służy już
tylko jako ślad „te zegarki przyszły razem” i podstawa wspólnego pokwitowania.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today

from kuck_serwis.repair_photo_policy import (
	PrivateFileEvidence,
	RepairPhotoPolicyCode,
	RepairPhotoPolicyError,
	RepairPhotoRow,
	require_transferable_private_photo,
	validate_repair_photo_references,
)

# Pola nagłówka przepisywane do każdej tworzonej naprawy (wspólne ustalenia partii).
# klient_telefon/klient_email celowo pominięte — Naprawa zaciąga je z klienta (fetch_from).
WSPOLNE_POLA = (
	"klient",
	"rodzaj_naprawy",
	"sposob_dostarczenia",
	"sposob_odbioru",
	"powiadom_sms",
	"powiadom_email",
	"data_stwierdzenia_usterki",
	"orientacyjny_termin_naprawy",
	"klient_zaakceptowal",
	"data_akceptacji",
	"akceptacja_uwagi",
)


class PrzyjecieZbiorcze(Document):
	def validate(self):
		self.set_akceptacja_date()
		self.waliduj_pozycje()
		self.validate_photo_privacy()

	def set_akceptacja_date(self):
		"""Akceptacja może nastąpić z góry przy przyjęciu (jak w Naprawie)."""
		if self.klient_zaakceptowal and not self.data_akceptacji:
			self.data_akceptacji = today()

	def waliduj_pozycje(self):
		if not self.pozycje:
			frappe.throw(_("Dodaj przynajmniej jeden zegarek do przyjęcia."))
		for i, poz in enumerate(self.pozycje, start=1):
			if not (poz.marka or poz.model_zegarka or poz.numer_seryjny):
				frappe.throw(_("Wiersz {0}: podaj markę, model lub numer seryjny zegarka.").format(i))

	def validate_photo_privacy(self):
		"""Admit only exact private Files or unchanged public legacy child rows."""
		photo_rows = tuple(row for row in self.pozycje if row.zdjecie)
		if not photo_rows:
			return
		current_rows = tuple(
			RepairPhotoRow(name=row.name, parent=row.parent, file_url=row.zdjecie) for row in photo_rows
		)
		stored_rows = () if self.is_new() else _stored_photo_rows(self.name)
		private_files = _private_file_evidence(tuple(row.zdjecie for row in photo_rows))
		try:
			validate_repair_photo_references(
				owner_doctype="Przyjecie Zbiorcze",
				owner_name=self.name,
				current_rows=current_rows,
				stored_rows=stored_rows,
				private_files=private_files,
			)
		except RepairPhotoPolicyError as error:
			frappe.throw(error.code.value)

	@frappe.whitelist()
	def utworz_naprawy(self):
		"""Tworzy po jednej Naprawie na każdą pozycję bez przypisanej naprawy (idempotentnie).

		Można wywołać ponownie po dorzuceniu kolejnych zegarków — pomija wiersze, które mają już
		ustawiony link `naprawa`. Zwraca listę nazw nowo utworzonych napraw.
		"""
		transfer_files = self._preflight_photo_transfers()
		utworzone = []
		for poz in self.pozycje:
			if poz.naprawa:
				continue
			naprawa = frappe.new_doc("Naprawa")
			for pole in WSPOLNE_POLA:
				naprawa.set(pole, self.get(pole))
			naprawa.marka = poz.marka
			naprawa.model_zegarka = poz.model_zegarka
			naprawa.numer_seryjny = poz.numer_seryjny
			naprawa.opis_naprawy = poz.opis
			naprawa.stan_przy_przyjeciu = poz.stan_przy_przyjeciu
			naprawa.insert()
			if poz.zdjecie:
				source_file = frappe.get_doc("File", transfer_files[poz.name])
				attachment = source_file.create_attachment_copy(
					"Naprawa",
					naprawa.name,
					"zdjecie",
					ignore_permissions=True,
				)
				if not attachment.is_private or attachment.file_url != poz.zdjecie:
					frappe.throw(RepairPhotoPolicyCode.PRIVATE_FILE_MISMATCH.value)
				naprawa.append("zdjecia", {"zdjecie": attachment.file_url})
				naprawa.save()
			poz.naprawa = naprawa.name
			utworzone.append(naprawa.name)

		if utworzone:
			self.status = "Naprawy utworzone"
			self.save()

		return utworzone

	def _preflight_photo_transfers(self) -> dict[str, str]:
		photo_rows = tuple(row for row in self.pozycje if row.zdjecie and not row.naprawa)
		if not photo_rows:
			return {}
		private_files = _private_file_evidence(tuple(row.zdjecie for row in photo_rows))
		result: dict[str, str] = {}
		try:
			for row in photo_rows:
				evidence = require_transferable_private_photo(
					owner_doctype="Przyjecie Zbiorcze",
					owner_name=self.name,
					file_url=row.zdjecie,
					private_files=private_files,
				)
				result[row.name] = evidence.name
		except RepairPhotoPolicyError as error:
			frappe.throw(error.code.value)
		return result


def _stored_photo_rows(parent: str) -> tuple[RepairPhotoRow, ...]:
	rows = frappe.get_all(
		"Przyjecie Zbiorcze Pozycja",
		filters={
			"parent": parent,
			"parenttype": "Przyjecie Zbiorcze",
			"parentfield": "pozycje",
			"zdjecie": ("is", "set"),
		},
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
