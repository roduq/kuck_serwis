# Copyright (c) 2026, Kuck and contributors
# For license information, please see license.txt
"""Testy raportu „Wydania i sprzedaz serwisu" — podgląd obrotu dla właściciela.

Logika agregacji (grupowanie, filtr dni tygodnia) to czysta funkcja na danych z bazy,
więc nadaje się do deterministycznego testu. Aby nie zależeć od pozostałych rekordów na
bazie, wszystkie rekordy testowe dostają tę samą, unikalną markę i każde zapytanie do
raportu filtrujemy po tej marce — to izoluje nasz zbiór bez kasowania cudzych danych.
"""

import frappe
from frappe.tests import IntegrationTestCase

from kuck_serwis.kuck_serwis.report.wydania_i_sprzedaz_serwisu.wydania_i_sprzedaz_serwisu import (
	execute,
)

MARKA = "ZZ Test Raport Marka"

# (data_wydania, kwota, rodzaj) — daty dobrane pod testy dni tygodnia:
# 2026-01-15 czw, 2026-01-20 wt, 2026-02-10 wt, 2026-03-05 czw
DANE = [
	("2026-01-15", 100, "Naprawa krótka"),
	("2026-01-20", 200, "Naprawa krótka"),
	("2026-02-10", 300, "Gwarancja"),
	("2026-03-05", 400, "Reklamacja"),
]


class TestRaportWydania(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Marka Zegarka", MARKA):
			frappe.get_doc({"doctype": "Marka Zegarka", "nazwa": MARKA}).insert(ignore_permissions=True)
		klient = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": "Raport Test " + frappe.generate_hash(length=6),
				"customer_type": "Individual",
				"mobile_no": "+48111000111",
			}
		).insert(ignore_permissions=True)
		cls.klient = klient.name

		for data_wydania, kwota, rodzaj in DANE:
			doc = frappe.get_doc(
				{
					"doctype": "Naprawa",
					"klient": cls.klient,
					"status": "Przyjęto",
					"rodzaj_naprawy": rodzaj,
					"marka": MARKA,
					"model_zegarka": "T",
					"sposob_dostarczenia": "Stacjonarnie",
					"sposob_odbioru": "Stacjonarnie",
				}
			).insert(ignore_permissions=True)
			# stan końcowy ustawiamy bezpośrednio — omijamy przejścia workflow w teście danych
			frappe.db.set_value(
				"Naprawa",
				doc.name,
				{"status": "Wydano", "data_wydania": data_wydania, "kwota_odbioru": kwota},
				update_modified=False,
			)

	def _run(self, **extra):
		filters = {
			"od_daty": "2026-01-01",
			"do_daty": "2026-12-31",
			"grupowanie": "Miesiąc",
			"marka": MARKA,  # izolacja zbioru testowego
		}
		filters.update(extra)
		_columns, data = execute(filters)
		return {r["okres"]: (r["liczba"], r["suma"]) for r in data}

	def test_grupowanie_miesiacami(self):
		wynik = self._run(grupowanie="Miesiąc")
		self.assertEqual(wynik.get("2026-01"), (2, 300))
		self.assertEqual(wynik.get("2026-02"), (1, 300))
		self.assertEqual(wynik.get("2026-03"), (1, 400))

	def test_grupowanie_kwartalami(self):
		wynik = self._run(grupowanie="Kwartał")
		self.assertEqual(wynik.get("2026-Q1"), (4, 1000))

	def test_grupowanie_rokiem(self):
		wynik = self._run(grupowanie="Rok")
		self.assertEqual(wynik.get("2026"), (4, 1000))

	def test_filtr_dni_tygodnia(self):
		# tylko wtorki: 2026-01-20 (200) i 2026-02-10 (300)
		wynik = self._run(grupowanie="Miesiąc", dni_tygodnia=["Wtorek"])
		self.assertEqual(wynik.get("2026-01"), (1, 200))
		self.assertEqual(wynik.get("2026-02"), (1, 300))
		self.assertNotIn("2026-03", wynik)

	def test_filtr_rodzaju_naprawy(self):
		wynik = self._run(grupowanie="Kwartał", rodzaj_naprawy="Naprawa krótka")
		self.assertEqual(wynik.get("2026-Q1"), (2, 300))

	def test_filtr_zakresu_dat(self):
		wynik = self._run(od_daty="2026-02-01", do_daty="2026-02-28")
		self.assertEqual(wynik, {"2026-02": (1, 300)})

	def test_marka_izoluje_zbior(self):
		# raport po naszej marce widzi dokładnie 4 wydania (niezależnie od reszty bazy)
		wynik = self._run(grupowanie="Rok")
		self.assertEqual(sum(liczba for liczba, _suma in wynik.values()), 4)


def _ensure_kategoria(nazwa):
	if not frappe.db.exists("Kategoria Napraw", nazwa):
		frappe.get_doc({"doctype": "Kategoria Napraw", "nazwa": nazwa}).insert(ignore_permissions=True)
	return nazwa


class TestRaportRozbicieKategorii(IntegrationTestCase):
	"""Rozbicie sprzedaży po głównej kategorii naprawy + filtr kategorii.

	Kluczowe: kwoty nie dublują się między kategoriami (jedna naprawa = jedna kategoria główna),
	więc suma kategorii = obrót okresu.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.marka = "ZZ Test Kat " + frappe.generate_hash(length=4)
		frappe.get_doc({"doctype": "Marka Zegarka", "nazwa": cls.marka}).insert(ignore_permissions=True)
		cls.kat_a = _ensure_kategoria("ZZ Kat A " + frappe.generate_hash(length=4))
		cls.kat_b = _ensure_kategoria("ZZ Kat B " + frappe.generate_hash(length=4))
		klient = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": "Raport Kat " + frappe.generate_hash(length=6),
				"customer_type": "Individual",
				"mobile_no": "+48111000222",
			}
		).insert(ignore_permissions=True)
		# kat A: 100 + 150, kat B: 200 — wszystko w kwietniu 2026
		for kwota, kategoria in [(100, cls.kat_a), (150, cls.kat_a), (200, cls.kat_b)]:
			doc = frappe.get_doc(
				{
					"doctype": "Naprawa",
					"klient": klient.name,
					"status": "Przyjęto",
					"rodzaj_naprawy": "Naprawa krótka",
					"marka": cls.marka,
					"model_zegarka": "T",
					"sposob_dostarczenia": "Stacjonarnie",
					"sposob_odbioru": "Stacjonarnie",
					"kategoria_glowna": kategoria,
				}
			).insert(ignore_permissions=True)
			frappe.db.set_value(
				"Naprawa",
				doc.name,
				{"status": "Wydano", "data_wydania": "2026-04-10", "kwota_odbioru": kwota},
				update_modified=False,
			)

	def _run(self, **extra):
		filters = {
			"od_daty": "2026-01-01",
			"do_daty": "2026-12-31",
			"grupowanie": "Rok",
			"marka": self.marka,
		}
		filters.update(extra)
		_columns, data = execute(filters)
		return {r["kategoria"]: (r["liczba"], r["suma"]) for r in data}

	def test_rozbicie_po_kategorii(self):
		wynik = self._run()
		self.assertEqual(wynik.get(self.kat_a), (2, 250))
		self.assertEqual(wynik.get(self.kat_b), (1, 200))

	def test_filtr_kategorii(self):
		wynik = self._run(kategoria=self.kat_a)
		self.assertEqual(wynik.get(self.kat_a), (2, 250))
		self.assertNotIn(self.kat_b, wynik)
