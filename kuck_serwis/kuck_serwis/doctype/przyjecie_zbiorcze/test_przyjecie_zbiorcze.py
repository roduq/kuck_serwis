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

from unittest.mock import patch

import frappe
from frappe.core.doctype.file.file import File
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


def _make_private_photo(*, owner_name):
	return frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"synthetic-{frappe.generate_hash(length=8)}.png",
			"is_private": 1,
			"content": b"synthetic-private-photo",
			"attached_to_doctype": "Przyjecie Zbiorcze",
			"attached_to_name": owner_name,
			"attached_to_field": "zdjecie",
		}
	).insert(ignore_permissions=True)


def _insert_legacy_public_photo(doc, file_url="/files/legacy-photo.png"):
	row = doc.append("pozycje", {"model_zegarka": "Legacy", "zdjecie": file_url})
	row.db_insert()
	doc.reload()
	return row.name


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
		doc = _make_przyjecie(pozycje=[{"model_zegarka": "Zegarek A"}, {"model_zegarka": "Zegarek B"}])
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

	def test_nowe_publiczne_zdjecie_pozycji_jest_blokowane(self):
		doc = _make_przyjecie(pozycje=[{"model_zegarka": "Zegarek Z", "zdjecie": "/files/test.png"}])
		with self.assertRaisesRegex(frappe.ValidationError, "^PUBLIC_PHOTO_FORBIDDEN$"):
			doc.insert(ignore_permissions=True)

	def test_private_attachment_jest_kopiowany_do_naprawy_bez_odczytu_tresci_i_commita(self):
		doc = _make_przyjecie(pozycje=[{"model_zegarka": "Zegarek Z"}])
		doc.insert(ignore_permissions=True)
		photo = _make_private_photo(owner_name=doc.name)
		doc.pozycje[0].zdjecie = photo.file_url
		doc.save(ignore_permissions=True)

		with (
			patch.object(File, "get_content", side_effect=AssertionError("content read forbidden")),
			patch.object(frappe.db, "commit", side_effect=AssertionError("commit forbidden")),
		):
			created = doc.utworz_naprawy()
		self.assertEqual(len(created), 1)
		doc.reload()

		n = frappe.get_doc("Naprawa", doc.pozycje[0].naprawa)
		self.assertEqual(len(n.zdjecia), 1)
		self.assertEqual(n.zdjecia[0].zdjecie, photo.file_url)
		target_files = frappe.get_all(
			"File",
			filters={
				"file_url": photo.file_url,
				"attached_to_doctype": "Naprawa",
				"attached_to_name": n.name,
				"attached_to_field": "zdjecie",
			},
			fields=["name", "is_private"],
		)
		self.assertEqual(len(target_files), 1)
		self.assertEqual(target_files[0].is_private, 1)
		photo.reload()
		self.assertEqual(
			(photo.attached_to_doctype, photo.attached_to_name, photo.attached_to_field),
			("Przyjecie Zbiorcze", doc.name, "zdjecie"),
		)

	def test_caller_rollback_cofa_naprawe_i_attachment_copy(self):
		doc = _make_przyjecie(pozycje=[{"model_zegarka": "Rollback"}])
		doc.insert(ignore_permissions=True)
		photo = _make_private_photo(owner_name=doc.name)
		doc.pozycje[0].zdjecie = photo.file_url
		doc.save(ignore_permissions=True)
		frappe.db.savepoint("g0_50_caller_rollback")

		created = doc.utworz_naprawy()
		self.assertEqual(len(created), 1)
		self.assertTrue(frappe.db.exists("Naprawa", created[0]))
		self.assertTrue(
			frappe.db.exists(
				"File",
				{
					"file_url": photo.file_url,
					"attached_to_doctype": "Naprawa",
					"attached_to_name": created[0],
				},
			)
		)

		frappe.db.rollback(save_point="g0_50_caller_rollback")
		self.assertFalse(frappe.db.exists("Naprawa", created[0]))
		self.assertFalse(
			frappe.db.exists(
				"File",
				{
					"file_url": photo.file_url,
					"attached_to_doctype": "Naprawa",
					"attached_to_name": created[0],
				},
			)
		)

	def test_legacy_public_photo_nie_jest_transferowane_i_nie_tworzona_jest_naprawa(self):
		doc = _make_przyjecie(pozycje=[{"model_zegarka": "Current"}]).insert(ignore_permissions=True)
		row_name = _insert_legacy_public_photo(doc)
		doc.akceptacja_uwagi = "Unrelated change"
		doc.save(ignore_permissions=True)
		doc.reload()
		self.assertEqual(doc.pozycje[-1].name, row_name)
		self.assertEqual(doc.pozycje[-1].zdjecie, "/files/legacy-photo.png")
		before = frappe.db.count("Naprawa", {"klient": doc.klient})
		with self.assertRaisesRegex(frappe.ValidationError, "^PUBLIC_PHOTO_FORBIDDEN$"):
			doc.utworz_naprawy()
		self.assertEqual(frappe.db.count("Naprawa", {"klient": doc.klient}), before)

	def test_zmienione_legacy_public_photo_jest_blokowane(self):
		doc = _make_przyjecie(pozycje=[{"model_zegarka": "Current"}]).insert(ignore_permissions=True)
		_insert_legacy_public_photo(doc)
		doc.pozycje[-1].zdjecie = "/files/changed.png"
		with self.assertRaisesRegex(frappe.ValidationError, "^PUBLIC_PHOTO_FORBIDDEN$"):
			doc.save(ignore_permissions=True)


def _ensure_marka():
	nazwa = "Test Marka"
	if not frappe.db.exists("Marka Zegarka", nazwa):
		frappe.get_doc({"doctype": "Marka Zegarka", "nazwa": nazwa}).insert(ignore_permissions=True)
	return nazwa
