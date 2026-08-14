# Copyright (c) 2026, Kuck and contributors
# For license information, please see license.txt
"""Testy DocType Naprawa.

Chronimy logikę, która ma realną wartość dla serwisu (skill watch-service-advisor):
- ślad akceptacji wyceny — naprawa nie rusza bez zgody klienta (ochrona przed sporami),
- automatyczne daty (akceptacji, wydania) — recepcja ich nie wpisuje ręcznie,
- pobranie danych kontaktowych klienta (fetch_from) — bez przepisywania,
- spójność procesu (Workflow) i konfiguracji powiadomień.
"""

from unittest.mock import patch

import frappe
from frappe.core.doctype.file.file import File
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from kuck_serwis import api, install, seed
from kuck_serwis.contact_update import ContactUpdateCode
from kuck_serwis.kuck_serwis.doctype.naprawa import naprawa as naprawa_controller

# Klient naprawy to ERPNext Customer. Nie pozwalamy frameworkowi auto-generować rekordów
# testowych Customera — ciągnie to ciężki łańcuch zależności ERPNext (Company, Loyalty
# Program, Payment Gateway), który wymagałby Setup Wizarda. Klientów w testach tworzymy
# jawnie (_make_klient), więc te zależności są zbędne.
IGNORE_TEST_RECORD_DEPENDENCIES = ["Customer"]


def _ensure_setup():
	"""Idempotentnie dokłada rolę/workflow/notyfikacje, jeśli na bazie testowej ich brak.

	Wywołujemy pojedyncze funkcje (nie setup_all), bo setup_all robi commit, co zepsułoby
	rollback izolujący testy. Te funkcje nie commitują.
	"""
	install.create_role()
	install.grant_external_access()
	if not frappe.db.exists("Workflow", "Serwis Naprawa"):
		install.create_workflow()
	# Powiadomienia odtwarzamy zawsze — to konfiguracja przebudowywana przy każdej migracji
	# (after_migrate -> setup_all), więc test ma widzieć aktualne warunki, a nie te zostawione
	# w bazie testowej z poprzedniej wersji kodu.
	install.create_notifications()


def _make_klient(**kwargs):
	# Klient naprawy to ERPNext Customer; dane kontaktowe trzyma w mobile_no/email_id.
	# customer_name nazywa rekord, więc dla izolacji testów domyślnie robimy go unikalnym.
	dane = {
		"doctype": "Customer",
		"customer_name": "Klient Testowy " + frappe.generate_hash(length=6),
		"customer_type": "Individual",
		"mobile_no": "+48500100200",
		"email_id": "jan.testowy@example.com",
	}
	dane.update(kwargs)
	return frappe.get_doc(dane).insert(ignore_permissions=True)


def _make_naprawa(klient=None, **kwargs):
	if klient is None:
		klient = _make_klient().name
	dane = {
		"doctype": "Naprawa",
		"klient": klient,
		"status": "Przyjęto",
		"rodzaj_naprawy": "Naprawa krótka",
		"model_zegarka": "Test 123",
		"sposob_dostarczenia": "Stacjonarnie",
		"sposob_odbioru": "Stacjonarnie",
	}
	dane.update(kwargs)
	return frappe.get_doc(dane)


def _make_private_photo(*, owner_doctype, owner_name, fieldname="zdjecie"):
	return frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"synthetic-{frappe.generate_hash(length=8)}.png",
			"is_private": 1,
			"content": b"synthetic-private-photo",
			"attached_to_doctype": owner_doctype,
			"attached_to_name": owner_name,
			"attached_to_field": fieldname,
		}
	).insert(ignore_permissions=True)


def _insert_legacy_public_photo(doc, file_url="/files/legacy-photo.png"):
	row = doc.append("zdjecia", {"zdjecie": file_url})
	row.db_insert()
	doc.reload()
	return row.name


def _primary_contact(customer_name):
	customer = frappe.get_doc("Customer", customer_name)
	return customer, frappe.get_doc("Contact", customer.customer_primary_contact)


def _set_primary_contact_values(customer_name, *, phone=None, email=None):
	customer, contact = _primary_contact(customer_name)
	if phone is not None:
		naprawa_controller._ustaw_glowny_telefon(contact, phone)
	if email is not None:
		naprawa_controller._ustaw_glowny_email(contact, email)
	contact.flags.ignore_mandatory = True
	contact.save(ignore_permissions=True)
	customer.db_set({"mobile_no": contact.mobile_no, "email_id": contact.email_id})
	return contact


class TestNaprawaKontroler(IntegrationTestCase):
	"""Czyste testy metod validate() — bez udziału workflow (in-memory)."""

	def test_guard_blokuje_naprawe_bez_akceptacji(self):
		doc = _make_naprawa()
		doc.status = "W naprawie"
		doc.klient_zaakceptowal = 0
		with self.assertRaises(frappe.ValidationError):
			doc.guard_w_naprawie()

	def test_guard_przepuszcza_naprawe_z_akceptacja(self):
		doc = _make_naprawa()
		doc.status = "W naprawie"
		doc.klient_zaakceptowal = 1
		# nie powinno rzucić
		doc.guard_w_naprawie()

	def test_guard_nie_dotyczy_innych_statusow(self):
		doc = _make_naprawa()
		doc.status = "Diagnoza"
		doc.klient_zaakceptowal = 0
		# diagnoza bez akceptacji jest w porządku
		doc.guard_w_naprawie()

	def test_data_akceptacji_ustawiana_automatycznie(self):
		doc = _make_naprawa()
		doc.klient_zaakceptowal = 1
		doc.data_akceptacji = None
		doc.set_akceptacja_date()
		self.assertEqual(str(doc.data_akceptacji), today())

	def test_data_akceptacji_nie_nadpisuje_istniejacej(self):
		doc = _make_naprawa()
		doc.klient_zaakceptowal = 1
		doc.data_akceptacji = "2026-01-01"
		doc.set_akceptacja_date()
		self.assertEqual(str(doc.data_akceptacji), "2026-01-01")

	def test_data_wydania_ustawiana_przy_statusie_wydano(self):
		doc = _make_naprawa()
		doc.status = "Wydano"
		doc.data_wydania = None
		doc.set_data_wydania()
		self.assertEqual(str(doc.data_wydania), today())

	def test_data_wydania_nie_dotyczy_innych_statusow(self):
		doc = _make_naprawa()
		doc.status = "Gotowe do odbioru"
		doc.data_wydania = None
		doc.set_data_wydania()
		self.assertFalse(doc.data_wydania)


class TestNaprawaIntegracja(IntegrationTestCase):
	"""Testy z zapisem do bazy: fetch_from oraz przejścia Workflow."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_setup()

	def test_fetch_from_pobiera_dane_klienta(self):
		klient = _make_klient(
			customer_name="Anna Kowalska " + frappe.generate_hash(length=6),
			mobile_no="+48600700800",
			email_id="anna.kowalska@example.com",
		)
		doc = _make_naprawa(klient=klient.name)
		doc.insert(ignore_permissions=True)
		self.assertEqual(doc.klient_nazwa, klient.customer_name)
		self.assertEqual(doc.klient_telefon, "+48600700800")
		self.assertEqual(doc.klient_email, "anna.kowalska@example.com")

	def test_new_and_customer_changed_save_do_not_access_contact_target(self):
		with patch.object(
			naprawa_controller,
			"_lock_contact_target",
			side_effect=AssertionError("CONTACT_TARGET_ACCESSED"),
		) as target:
			doc = _make_naprawa().insert(ignore_permissions=True)
			target.assert_not_called()
			doc.klient = _make_klient().name
			doc.save(ignore_permissions=True)
		target.assert_not_called()

	def test_unrelated_save_does_not_read_or_write_contact_target(self):
		doc = _make_naprawa().insert(ignore_permissions=True)
		baseline = (doc.klient_telefon, doc.klient_email)
		_set_primary_contact_values(
			doc.klient,
			phone="+48500100201",
			email="synthetic-current@example.test",
		)
		doc.model_zegarka = "Unrelated synthetic change"
		with patch.object(
			naprawa_controller,
			"_lock_contact_target",
			side_effect=AssertionError("CONTACT_TARGET_ACCESSED"),
		) as target:
			doc.save(ignore_permissions=True)
		target.assert_not_called()
		customer, contact = _primary_contact(doc.klient)
		self.assertEqual(
			(contact.mobile_no, contact.email_id), ("+48500100201", "synthetic-current@example.test")
		)
		self.assertEqual(
			(customer.mobile_no, customer.email_id), ("+48500100201", "synthetic-current@example.test")
		)
		self.assertEqual(
			frappe.db.get_value("Naprawa", doc.name, ["klient_telefon", "klient_email"]),
			baseline,
		)

	def test_phone_apply_preserves_concurrent_email_and_child_rows(self):
		doc = _make_naprawa().insert(ignore_permissions=True)
		customer, contact = _primary_contact(doc.klient)
		contact.append("phone_nos", {"phone": "+48500100202"})
		contact.append("email_ids", {"email_id": "synthetic-secondary@example.test"})
		contact.save(ignore_permissions=True)
		_set_primary_contact_values(doc.klient, email="synthetic-concurrent@example.test")

		doc.klient_telefon = "+48500100203"
		doc.save(ignore_permissions=True)
		customer, contact = _primary_contact(doc.klient)
		self.assertEqual(contact.mobile_no, "+48500100203")
		self.assertEqual(contact.email_id, "synthetic-concurrent@example.test")
		self.assertEqual((customer.mobile_no, customer.email_id), (contact.mobile_no, contact.email_id))
		self.assertIn("+48500100202", {row.phone for row in contact.phone_nos})
		self.assertIn("synthetic-secondary@example.test", {row.email_id for row in contact.email_ids})

	def test_email_apply_preserves_concurrent_phone(self):
		doc = _make_naprawa().insert(ignore_permissions=True)
		_set_primary_contact_values(doc.klient, phone="+48500100204")
		doc.klient_email = "synthetic-proposed@example.test"
		doc.save(ignore_permissions=True)
		customer, contact = _primary_contact(doc.klient)
		self.assertEqual(contact.mobile_no, "+48500100204")
		self.assertEqual(contact.email_id, "synthetic-proposed@example.test")
		self.assertEqual((customer.mobile_no, customer.email_id), (contact.mobile_no, contact.email_id))

	def test_customer_fallback_preserves_unrelated_current_field(self):
		doc = _make_naprawa().insert(ignore_permissions=True)
		frappe.db.set_value("Customer", doc.klient, "customer_primary_contact", None)
		frappe.db.set_value("Customer", doc.klient, "email_id", "synthetic-current@example.test")
		doc.klient_telefon = "+48500100203"
		doc.save(ignore_permissions=True)
		self.assertEqual(
			frappe.db.get_value("Customer", doc.klient, ["mobile_no", "email_id"]),
			("+48500100203", "synthetic-current@example.test"),
		)

	def test_same_field_change_conflicts_before_naprawa_write(self):
		doc = _make_naprawa().insert(ignore_permissions=True)
		baseline = doc.klient_telefon
		_set_primary_contact_values(doc.klient, phone="+48500100204")
		doc.klient_telefon = "+48500100203"
		with self.assertRaisesRegex(
			frappe.ValidationError,
			f"^{ContactUpdateCode.CONTACT_REVISION_CONFLICT.value}$",
		):
			doc.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Naprawa", doc.name, "klient_telefon"), baseline)
		self.assertEqual(_primary_contact(doc.klient)[1].mobile_no, "+48500100204")

	def test_same_email_change_conflicts_without_partial_write(self):
		doc = _make_naprawa().insert(ignore_permissions=True)
		baseline = doc.klient_email
		_set_primary_contact_values(doc.klient, email="synthetic-concurrent@example.test")
		doc.klient_email = "synthetic-proposed@example.test"
		with self.assertRaisesRegex(
			frappe.ValidationError,
			f"^{ContactUpdateCode.CONTACT_REVISION_CONFLICT.value}$",
		):
			doc.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Naprawa", doc.name, "klient_email"), baseline)
		self.assertEqual(
			_primary_contact(doc.klient)[1].email_id,
			"synthetic-concurrent@example.test",
		)

	def test_already_applied_value_is_idempotent(self):
		doc = _make_naprawa().insert(ignore_permissions=True)
		_set_primary_contact_values(doc.klient, phone="+48500100205")
		doc.klient_telefon = "+48500100205"
		doc.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Naprawa", doc.name, "klient_telefon"), "+48500100205")
		self.assertEqual(_primary_contact(doc.klient)[1].mobile_no, "+48500100205")

	def test_contact_clear_is_rejected_before_target_read(self):
		doc = _make_naprawa().insert(ignore_permissions=True)
		previous = frappe.get_doc("Naprawa", doc.name)
		doc._doc_before_save = previous
		doc.klient_telefon = ""
		with patch.object(
			naprawa_controller,
			"_lock_contact_target",
			side_effect=AssertionError("CONTACT_TARGET_ACCESSED"),
		) as target:
			with self.assertRaisesRegex(
				frappe.ValidationError,
				f"^{ContactUpdateCode.CONTACT_CLEAR_UNSUPPORTED.value}$",
			):
				doc.prepare_contact_update()
		target.assert_not_called()

	def test_swapped_primary_contact_is_rejected(self):
		doc = _make_naprawa().insert(ignore_permissions=True)
		other = _make_klient()
		other_contact = _primary_contact(other.name)[1]
		frappe.db.set_value("Customer", doc.klient, "customer_primary_contact", other_contact.name)
		doc.klient_telefon = "+48500100203"
		with self.assertRaisesRegex(
			frappe.ValidationError,
			f"^{ContactUpdateCode.CONTACT_TARGET_MISMATCH.value}$",
		):
			doc.save(ignore_permissions=True)
		self.assertNotEqual(other_contact.reload().mobile_no, "+48500100203")

	def test_parent_ignore_permissions_does_not_bypass_contact_permission(self):
		doc = _make_naprawa().insert(ignore_permissions=True)
		doc.klient_telefon = "+48500100203"
		real_has_permission = frappe.has_permission

		def permission(doctype, *args, **kwargs):
			if doctype == "Contact":
				return False
			return real_has_permission(doctype, *args, **kwargs)

		with patch.object(naprawa_controller.frappe, "has_permission", side_effect=permission):
			with self.assertRaisesRegex(
				frappe.ValidationError,
				f"^{ContactUpdateCode.CONTACT_UPDATE_FORBIDDEN.value}$",
			):
				doc.save(ignore_permissions=True)
		self.assertNotEqual(_primary_contact(doc.klient)[1].mobile_no, "+48500100203")

	def test_target_reads_use_for_update(self):
		doc = _make_naprawa().insert(ignore_permissions=True)
		calls = []
		real_get_doc = frappe.get_doc

		def get_doc(*args, **kwargs):
			if args and args[0] in {"Customer", "Contact"}:
				calls.append((args[0], kwargs.get("for_update")))
			return real_get_doc(*args, **kwargs)

		with patch.object(naprawa_controller.frappe, "get_doc", side_effect=get_doc):
			naprawa_controller._lock_contact_target(doc.klient)
		self.assertEqual(calls, [("Customer", True), ("Contact", True)])

	def test_success_obeys_caller_transaction_and_rollback(self):
		doc = _make_naprawa().insert(ignore_permissions=True)
		baseline = doc.klient_telefon
		frappe.db.savepoint("g0_55_caller_rollback")
		with patch.object(frappe.db, "commit", side_effect=AssertionError("COMMIT_CALLED")) as commit:
			doc.klient_telefon = "+48500100206"
			doc.save(ignore_permissions=True)
		commit.assert_not_called()
		self.assertEqual(_primary_contact(doc.klient)[1].mobile_no, "+48500100206")
		frappe.db.rollback(save_point="g0_55_caller_rollback")
		self.assertEqual(_primary_contact(doc.klient)[1].mobile_no, baseline)
		self.assertEqual(frappe.db.get_value("Naprawa", doc.name, "klient_telefon"), baseline)

	def test_insert_ustawia_date_akceptacji(self):
		doc = _make_naprawa(klient_zaakceptowal=1)
		doc.insert(ignore_permissions=True)
		self.assertEqual(str(doc.data_akceptacji), today())

	def test_nowa_publiczna_referencja_zdjecia_jest_blokowana(self):
		doc = _make_naprawa(zdjecia=[{"zdjecie": "/files/new-photo.png"}])
		with self.assertRaisesRegex(frappe.ValidationError, "^PUBLIC_PHOTO_FORBIDDEN$"):
			doc.insert(ignore_permissions=True)

	def test_private_file_musi_byc_exact_attachment_naprawy(self):
		doc = _make_naprawa().insert(ignore_permissions=True)
		photo = _make_private_photo(owner_doctype="Naprawa", owner_name=doc.name)
		self.assertIsInstance(photo, File)
		self.assertEqual(
			(photo.attached_to_doctype, photo.attached_to_name, photo.attached_to_field),
			("Naprawa", doc.name, "zdjecie"),
		)
		doc.append("zdjecia", {"zdjecie": photo.file_url})
		doc.save(ignore_permissions=True)
		doc.reload()
		self.assertEqual(doc.zdjecia[0].zdjecie, photo.file_url)

	def test_private_url_without_exact_attachment_is_rejected(self):
		doc = _make_naprawa().insert(ignore_permissions=True)
		photo = _make_private_photo(owner_doctype="Naprawa", owner_name="SER-WRONG")
		doc.append("zdjecia", {"zdjecie": photo.file_url})
		with self.assertRaisesRegex(frappe.ValidationError, "^PRIVATE_FILE_REQUIRED$"):
			doc.save(ignore_permissions=True)

	def test_unchanged_legacy_public_child_is_grandfathered(self):
		doc = _make_naprawa().insert(ignore_permissions=True)
		row_name = _insert_legacy_public_photo(doc)
		doc.model_zegarka = "Unrelated change"
		doc.save(ignore_permissions=True)
		doc.reload()
		self.assertEqual(doc.zdjecia[0].name, row_name)
		self.assertEqual(doc.zdjecia[0].zdjecie, "/files/legacy-photo.png")

	def test_changed_or_readded_legacy_public_child_is_rejected(self):
		doc = _make_naprawa().insert(ignore_permissions=True)
		_insert_legacy_public_photo(doc)
		doc.zdjecia[0].zdjecie = "/files/changed.png"
		with self.assertRaisesRegex(frappe.ValidationError, "^PUBLIC_PHOTO_FORBIDDEN$"):
			doc.save(ignore_permissions=True)
		doc.reload()
		doc.remove(doc.zdjecia[0])
		doc.append("zdjecia", {"zdjecie": "/files/legacy-photo.png"})
		with self.assertRaisesRegex(frappe.ValidationError, "^PUBLIC_PHOTO_FORBIDDEN$"):
			doc.save(ignore_permissions=True)

	def test_workflow_blokuje_wejscie_w_naprawe_bez_akceptacji(self):
		doc = _make_naprawa(klient_zaakceptowal=0)
		doc.insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			apply_workflow(doc, "Przyjmij do naprawy")
		# status nie powinien się zmienić w bazie
		self.assertEqual(frappe.db.get_value("Naprawa", doc.name, "status"), "Przyjęto")

	def test_workflow_wejscie_w_naprawe_z_akceptacja(self):
		doc = _make_naprawa(klient_zaakceptowal=1)
		doc.insert(ignore_permissions=True)
		apply_workflow(doc, "Przyjmij do naprawy")
		self.assertEqual(doc.status, "W naprawie")

	def test_workflow_pelna_sciezka_do_wydania(self):
		# Powiadomienia domyślnie wyłączone (powiadom_sms/email = 0) -> w teście nie wyzwalamy
		# realnych SMS/e-mail. kwota_odbioru wymagana przy wydaniu naprawy płatnej.
		doc = _make_naprawa(klient_zaakceptowal=1, kwota_odbioru=250)
		doc.insert(ignore_permissions=True)
		apply_workflow(doc, "Przyjmij do naprawy")
		apply_workflow(doc, "Zakończ naprawę")
		apply_workflow(doc, "Wydaj zegarek")
		self.assertEqual(doc.status, "Wydano")
		self.assertEqual(str(doc.data_wydania), today())

	def test_wydanie_wymaga_kwoty_dla_naprawy_platnej(self):
		"""Naprawa płatna nie może zostać wydana bez kwoty — chroni rzetelność obrotu."""
		doc = _make_naprawa(klient_zaakceptowal=1, rodzaj_naprawy="Naprawa krótka")
		doc.insert(ignore_permissions=True)
		apply_workflow(doc, "Przyjmij do naprawy")
		apply_workflow(doc, "Zakończ naprawę")
		with self.assertRaises(frappe.ValidationError):
			apply_workflow(doc, "Wydaj zegarek")
		self.assertEqual(frappe.db.get_value("Naprawa", doc.name, "status"), "Gotowe do odbioru")

	def test_wydanie_podpowiada_kwote_z_wyceny(self):
		"""Pusta kwota odbioru przy wydaniu uzupełnia się orientacyjną wyceną."""
		doc = _make_naprawa(klient_zaakceptowal=1, orientacyjna_wycena=180)
		doc.insert(ignore_permissions=True)
		apply_workflow(doc, "Przyjmij do naprawy")
		apply_workflow(doc, "Zakończ naprawę")
		apply_workflow(doc, "Wydaj zegarek")
		self.assertEqual(doc.status, "Wydano")
		self.assertEqual(doc.kwota_odbioru, 180)

	def test_wydanie_gwarancji_bez_kwoty_dozwolone(self):
		"""Gwarancja bywa bezpłatna — 0 zł nie blokuje wydania."""
		doc = _make_naprawa(klient_zaakceptowal=1, rodzaj_naprawy="Gwarancja")
		doc.insert(ignore_permissions=True)
		apply_workflow(doc, "Przyjmij do naprawy")
		apply_workflow(doc, "Zakończ naprawę")
		apply_workflow(doc, "Wydaj zegarek")
		self.assertEqual(doc.status, "Wydano")


class TestKartaKlienta(IntegrationTestCase):
	"""Dane dla strony „Karta klienta" — naprawy jednego klienta i podsumowanie.

	To jest kontrakt API zasilającego pulpit klienta: serwis musi widzieć komplet
	napraw danego klienta (i tylko jego) oraz poprawne liczniki/obrót.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_setup()

	@staticmethod
	def _naprawa_w_stanie(klient, status, **pola):
		# Workflow blokuje wstawienie naprawy od razu w docelowym stanie, a tu chodzi tylko
		# o dane do statystyk — ustawiamy status wprost w bazie (z pominięciem workflow).
		doc = _make_naprawa(klient=klient)
		doc.insert(ignore_permissions=True)
		frappe.db.set_value("Naprawa", doc.name, {"status": status, **pola})
		return doc

	def test_dane_klienta_zwraca_tylko_jego_naprawy_i_statystyki(self):
		klient = _make_klient()
		obcy = _make_klient()
		# Naprawy badanego klienta w różnych stanach.
		self._naprawa_w_stanie(klient.name, "Przyjęto")
		self._naprawa_w_stanie(klient.name, "Gotowe do odbioru")
		self._naprawa_w_stanie(klient.name, "Wydano", kwota_odbioru=300)
		self._naprawa_w_stanie(klient.name, "Anulowano")
		# Naprawa innego klienta nie może trafić do wyniku.
		self._naprawa_w_stanie(obcy.name, "Wydano", kwota_odbioru=999)

		dane = api.dane_klienta(klient.name)

		self.assertEqual(dane["klient"]["name"], klient.name)
		self.assertEqual(len(dane["naprawy"]), 4)
		stat = dane["statystyki"]
		self.assertEqual(stat["liczba"], 4)
		self.assertEqual(stat["w_toku"], 2)  # Przyjęto + Gotowe do odbioru (nie Wydano/Anulowano)
		self.assertEqual(stat["gotowe"], 1)  # Gotowe do odbioru
		self.assertEqual(stat["obrot"], 300)  # tylko z naprawy „Wydano" tego klienta

	def test_dane_klienta_bez_argumentu_zwraca_pusto(self):
		self.assertEqual(api.dane_klienta(""), {})


class TestKuckSerwisSetup(IntegrationTestCase):
	"""Weryfikacja, że konfiguracja modułu (rola, workflow, powiadomienia) jest spójna."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_setup()

	def test_rola_serwis_istnieje(self):
		self.assertTrue(frappe.db.exists("Role", "Serwis"))

	def test_rola_serwis_ma_dostep_do_erpnext(self):
		"""Rola Serwis musi mieć dostęp do Customer i powiązanych danych ERPNext —
		inaczej recepcja nie założy/nie edytuje klienta, kontaktu ani adresu.
		Sprawdzamy Custom DocPerm nadane przez grant_external_access."""
		for doctype, ptypes in install.EXTERNAL_PERMISSIONS.items():
			perm = frappe.db.get_value(
				"Custom DocPerm",
				{"parent": doctype, "role": install.ROLE, "permlevel": 0},
				["read", "write", "create"],
				as_dict=True,
			)
			self.assertIsNotNone(perm, f"brak Custom DocPerm dla roli Serwis na {doctype}")
			for ptype in ptypes:
				self.assertTrue(perm.get(ptype), f"rola Serwis nie ma '{ptype}' na {doctype}")

	def test_workflow_aktywny_z_kompletem_stanow(self):
		wf = frappe.get_doc("Workflow", "Serwis Naprawa")
		self.assertTrue(wf.is_active)
		self.assertEqual(wf.document_type, "Naprawa")
		self.assertEqual(wf.workflow_state_field, "status")
		stany = {s.state for s in wf.states}
		self.assertEqual(stany, set(install.WORKFLOW_STATES))

	def test_workflow_ma_bezposrednie_przejscie_przyjeto_w_naprawe(self):
		wf = frappe.get_doc("Workflow", "Serwis Naprawa")
		przejscia = {(t.state, t.next_state) for t in wf.transitions}
		# klient godzi się od razu przy przyjęciu — bez kroku wyceny
		self.assertIn(("Przyjęto", "W naprawie"), przejscia)

	def test_powiadomienia_warunkowane_zgoda_klienta(self):
		# Każdy kanał ma własny przełącznik zgody (logika pozytywna, domyślnie wyłączona).
		for name, pole_zgody in (
			("Naprawa - gotowa do odbioru (E-mail)", "powiadom_email"),
			("Naprawa - gotowa do odbioru (SMS)", "powiadom_sms"),
			("Naprawa - wycena do akceptacji (E-mail)", "powiadom_email"),
			("Naprawa - wycena do akceptacji (SMS)", "powiadom_sms"),
		):
			self.assertTrue(frappe.db.exists("Notification", name), f"brak powiadomienia: {name}")
			notif = frappe.get_doc("Notification", name)
			self.assertTrue(notif.enabled)
			self.assertEqual(notif.event, "Value Change")
			self.assertEqual(notif.value_changed, "status")
			# powiadomienie respektuje przełącznik właściwego kanału
			self.assertIn(pole_zgody, notif.condition)


class TestSeedSlownikow(IntegrationTestCase):
	"""Seed słowników z arkuszy klienta — kategorie, usterki (z kategorią) i marki.

	seed_all() jest idempotentny i nie commituje, więc rollback testu sprząta po nim.
	"""

	def test_seed_tworzy_kategorie_usterki_i_marki(self):
		seed.seed_all()

		# wszystkie kategorie z arkusza istnieją
		for kategoria in seed.USTERKI_WG_KATEGORII:
			self.assertTrue(
				frappe.db.exists("Kategoria Napraw", kategoria),
				f"brak kategorii: {kategoria}",
			)

		# każda usterka istnieje i jest przypięta do właściwej kategorii
		for kategoria, usterki in seed.USTERKI_WG_KATEGORII.items():
			for usterka in usterki:
				self.assertEqual(
					frappe.db.get_value("Usterka", usterka, "kategoria"),
					kategoria,
					f"usterka {usterka!r} nie wskazuje na {kategoria!r}",
				)

		# marki z zestawienia klienta
		for marka in seed.MARKI:
			self.assertTrue(frappe.db.exists("Marka Zegarka", marka), f"brak marki: {marka}")

	def test_seed_jest_idempotentny(self):
		seed.seed_all()
		przed = (
			frappe.db.count("Kategoria Napraw"),
			frappe.db.count("Usterka"),
			frappe.db.count("Marka Zegarka"),
		)
		seed.seed_all()
		po = (
			frappe.db.count("Kategoria Napraw"),
			frappe.db.count("Usterka"),
			frappe.db.count("Marka Zegarka"),
		)
		self.assertEqual(przed, po)

	def test_kategoria_jest_wymagana_na_usterce(self):
		# pole kategoria stało się wymagane — usterka bez niej nie może powstać
		doc = frappe.get_doc(
			{"doctype": "Usterka", "nazwa": "Usterka bez kategorii " + frappe.generate_hash(length=6)}
		)
		with self.assertRaises(frappe.MandatoryError):
			doc.insert(ignore_permissions=True)
