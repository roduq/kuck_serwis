# Copyright (c) 2026, Kuck and contributors
# For license information, please see license.txt
"""Testy DocType Przyjecie Zbiorcze.

Przyjęcie zbiorcze to TYLKO warstwa wejściowa: ma poprawnie rozłożyć jedną partię na osobne,
normalne naprawy. Chronimy więc to, co ma wartość dla serwisu (skill watch-service-advisor):
- każda pozycja staje się osobną Naprawą (niezależny obieg każdego zegarka),
- wspólne ustalenia i akceptacja klienta przenoszą się na każdą naprawę,
- ponowne uruchomienie nie duplikuje już utworzonych napraw (idempotencja),
- walidacja chroni przed pustą partią / pozycją bez identyfikacji zegarka.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from kuck_serwis import install

# Klient to ERPNext Customer — patrz uzasadnienie w test_naprawa.py.
IGNORE_TEST_RECORD_DEPENDENCIES = ["Customer"]


def _ensure_setup():
	install.create_role()
	install.grant_external_access()
	if not frappe.db.exists("Workflow", "Serwis Naprawa"):
		install.create_workflow()


def _make_klient(**kwargs):
	dane = {
		"doctype": "Customer",
		"customer_name": "Klient Testowy " + frappe.generate_hash(length=6),
		"customer_type": "Individual",
		"mobile_no": "+48500100200",
		"email_id": "jan.testowy@example.com",
	}
	dane.update(kwargs)
	return frappe.get_doc(dane).insert(ignore_permissions=True)


def _make_przyjecie(klient=None, pozycje=None, **kwargs):
	if klient is None:
		klient = _make_klient().name
	if pozycje is None:
		pozycje = [{"model_zegarka": "Test 123"}]
	dane = {
		"doctype": "Przyjecie Zbiorcze",
		"klient": klient,
		"rodzaj_naprawy": "Naprawa krótka",
		"sposob_dostarczenia": "Stacjonarnie",
		"sposob_odbioru": "Stacjonarnie",
		"pozycje": pozycje,
	}
	dane.update(kwargs)
	return frappe.get_doc(dane)


class TestPrzyjecieZbiorczeWalidacja(IntegrationTestCase):
	"""Walidacja partii — bez zapisu do bazy."""

	def test_pusta_partia_jest_odrzucana(self):
		doc = _make_przyjecie(pozycje=[])
		with self.assertRaises(frappe.ValidationError):
			doc.waliduj_pozycje()

	def test_pozycja_bez_identyfikacji_zegarka_jest_odrzucana(self):
		doc = _make_przyjecie(pozycje=[{"opis": "coś nie działa"}])
		with self.assertRaises(frappe.ValidationError):
			doc.waliduj_pozycje()

	def test_pozycja_z_samym_numerem_seryjnym_jest_ok(self):
		doc = _make_przyjecie(pozycje=[{"numer_seryjny": "SN-9999"}])
		doc.waliduj_pozycje()

	def test_data_akceptacji_ustawiana_automatycznie(self):
		doc = _make_przyjecie(klient_zaakceptowal=1)
		doc.data_akceptacji = None
		doc.set_akceptacja_date()
		self.assertEqual(str(doc.data_akceptacji), today())


class TestPrzyjecieZbiorczeTworzenie(IntegrationTestCase):
	"""Tworzenie napraw z partii (zapis do bazy)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_setup()

	def test_tworzy_osobna_naprawe_na_kazda_pozycje(self):
		marka = _ensure_marka()
		doc = _make_przyjecie(
			pozycje=[
				{"marka": marka, "model_zegarka": "Speedmaster", "numer_seryjny": "A1", "opis": "nie chodzi"},
				{"model_zegarka": "Submariner", "numer_seryjny": "B2", "opis": "wymiana baterii"},
			]
		)
		doc.insert(ignore_permissions=True)

		utworzone = doc.utworz_naprawy()
		doc.reload()

		self.assertEqual(len(utworzone), 2)
		self.assertEqual(doc.status, "Naprawy utworzone")
		# każda pozycja ma link do osobnej naprawy
		naprawy = [p.naprawa for p in doc.pozycje]
		self.assertTrue(all(naprawy))
		self.assertEqual(len(set(naprawy)), 2)

		# dane przepisane poprawnie na pierwszą naprawę
		n1 = frappe.get_doc("Naprawa", doc.pozycje[0].naprawa)
		self.assertEqual(n1.klient, doc.klient)
		self.assertEqual(n1.marka, marka)
		self.assertEqual(n1.model_zegarka, "Speedmaster")
		self.assertEqual(n1.numer_seryjny, "A1")
		self.assertEqual(n1.opis_naprawy, "nie chodzi")
		self.assertEqual(n1.rodzaj_naprawy, "Naprawa krótka")
		self.assertEqual(n1.status, "Przyjęto")

	def test_idempotencja_nie_duplikuje_napraw(self):
		doc = _make_przyjecie(
			pozycje=[{"model_zegarka": "Zegarek A"}, {"model_zegarka": "Zegarek B"}]
		)
		doc.insert(ignore_permissions=True)

		pierwsze = doc.utworz_naprawy()
		self.assertEqual(len(pierwsze), 2)

		# ponowne wywołanie bez zmian — nic nowego
		doc.reload()
		drugie = doc.utworz_naprawy()
		self.assertEqual(drugie, [])

		# dorzucenie kolejnego zegarka — tworzy tylko jego
		doc.append("pozycje", {"model_zegarka": "Zegarek C"})
		doc.save(ignore_permissions=True)
		trzecie = doc.utworz_naprawy()
		self.assertEqual(len(trzecie), 1)
		self.assertEqual(frappe.db.count("Naprawa", {"klient": doc.klient}), 3)

	def test_akceptacja_z_gory_przenosi_sie_na_naprawy(self):
		doc = _make_przyjecie(
			klient_zaakceptowal=1,
			akceptacja_uwagi="zgoda przy przyjęciu",
			pozycje=[{"model_zegarka": "Zegarek X"}],
		)
		doc.insert(ignore_permissions=True)
		doc.utworz_naprawy()
		doc.reload()

		n = frappe.get_doc("Naprawa", doc.pozycje[0].naprawa)
		self.assertTrue(n.klient_zaakceptowal)
		self.assertEqual(str(n.data_akceptacji), today())
		self.assertEqual(n.akceptacja_uwagi, "zgoda przy przyjęciu")

	def test_zdjecie_pozycji_trafia_do_naprawy(self):
		doc = _make_przyjecie(
			pozycje=[{"model_zegarka": "Zegarek Z", "zdjecie": "/files/test.png"}]
		)
		doc.insert(ignore_permissions=True)
		doc.utworz_naprawy()
		doc.reload()

		n = frappe.get_doc("Naprawa", doc.pozycje[0].naprawa)
		self.assertEqual(len(n.zdjecia), 1)
		self.assertEqual(n.zdjecia[0].zdjecie, "/files/test.png")


def _ensure_marka():
	nazwa = "Test Marka"
	if not frappe.db.exists("Marka Zegarka", nazwa):
		frappe.get_doc({"doctype": "Marka Zegarka", "nazwa": nazwa}).insert(ignore_permissions=True)
	return nazwa
