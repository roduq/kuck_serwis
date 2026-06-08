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

# Pola nagłówka przepisywane do każdej tworzonej naprawy (wspólne ustalenia partii).
# klient_telefon/klient_email celowo pominięte — Naprawa zaciąga je z klienta (fetch_from).
WSPOLNE_POLA = (
	"klient",
	"rodzaj_naprawy",
	"sposob_dostarczenia",
	"sposob_odbioru",
	"nie_powiadamiaj_klienta",
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

	def set_akceptacja_date(self):
		"""Akceptacja może nastąpić z góry przy przyjęciu (jak w Naprawie)."""
		if self.klient_zaakceptowal and not self.data_akceptacji:
			self.data_akceptacji = today()

	def waliduj_pozycje(self):
		if not self.pozycje:
			frappe.throw(_("Dodaj przynajmniej jeden zegarek do przyjęcia."))
		for i, poz in enumerate(self.pozycje, start=1):
			if not (poz.marka or poz.model_zegarka or poz.numer_seryjny):
				frappe.throw(
					_("Wiersz {0}: podaj markę, model lub numer seryjny zegarka.").format(i)
				)

	@frappe.whitelist()
	def utworz_naprawy(self):
		"""Tworzy po jednej Naprawie na każdą pozycję bez przypisanej naprawy (idempotentnie).

		Można wywołać ponownie po dorzuceniu kolejnych zegarków — pomija wiersze, które mają już
		ustawiony link `naprawa`. Zwraca listę nazw nowo utworzonych napraw.
		"""
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
			if poz.zdjecie:
				naprawa.append("zdjecia", {"zdjecie": poz.zdjecie})
			naprawa.insert()
			poz.naprawa = naprawa.name
			utworzone.append(naprawa.name)

		if utworzone:
			self.status = "Naprawy utworzone"
			self.save()

		return utworzone
