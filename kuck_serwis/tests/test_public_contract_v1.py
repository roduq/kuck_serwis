from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from kuck_serwis.kuck_serwis.doctype.naprawa.naprawa import PUBLIC_ID_PATTERN
from kuck_serwis.patches import backfill_naprawa_public_id
from kuck_serwis.public_contract import v1


def _make_user(*, user_type="Website User", enabled=1):
	email = f"portal-{frappe.generate_hash(length=10).lower()}@example.test"
	return frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Portal Test",
			"enabled": enabled,
			"user_type": user_type,
			"send_welcome_email": 0,
		}
	).insert(ignore_permissions=True)


def _make_customer(*users):
	doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": "Portal Customer " + frappe.generate_hash(length=8),
			"customer_type": "Individual",
		}
	)
	for user in users:
		doc.append("portal_users", {"user": user.name})
	return doc.insert(ignore_permissions=True)


def _make_repair(customer, **values):
	data = {
		"doctype": "Naprawa",
		"klient": customer.name,
		"status": "Przyjęto",
		"rodzaj_naprawy": "Naprawa krótka",
		"model_zegarka": "Model publiczny",
		"numer_seryjny": "SECRET-SERIAL",
		"opis_naprawy": "Poufna notatka warsztatu",
		"sposob_dostarczenia": "Stacjonarnie",
		"sposob_odbioru": "Stacjonarnie",
	}
	data.update(values)
	return frappe.get_doc(data).insert(ignore_permissions=True)


class TestPublicContractV1(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._previous_user = frappe.session.user
		self._flag_was_present = v1.ROLLOUT_FLAG in frappe.conf
		self._previous_flag = frappe.conf.get(v1.ROLLOUT_FLAG)
		frappe.conf.pop(v1.ROLLOUT_FLAG, None)

	def tearDown(self):
		frappe.set_user(self._previous_user)
		if self._flag_was_present:
			frappe.conf[v1.ROLLOUT_FLAG] = self._previous_flag
		else:
			frappe.conf.pop(v1.ROLLOUT_FLAG, None)
		super().tearDown()

	def test_capability_is_fail_closed_until_flag_and_full_readiness(self):
		self.assertEqual(
			v1.get_capabilities(),
			{"contract": "kuck-serwis/v1", "schema_revision": 1, "features": []},
		)
		frappe.conf[v1.ROLLOUT_FLAG] = True
		# Signed keyset cursors are deliberately not part of this slice.
		self.assertEqual(v1.get_capabilities()["features"], [])
		with self.assertRaises(v1.PublicContractError) as caught:
			v1.list_repairs_for_current_user(None, 20)
		self.assertEqual(caught.exception.code, "DEPENDENCY_UNAVAILABLE")
		with patch.object(v1, "_is_ready", return_value=True):
			self.assertEqual(v1.get_capabilities()["features"], ["account-read"])
		frappe.conf[v1.ROLLOUT_FLAG] = False
		with patch.object(v1, "_is_ready", return_value=True):
			self.assertEqual(v1.get_capabilities()["features"], [])

	def test_new_ids_are_random_formatted_and_immutable(self):
		customer = _make_customer()
		first = _make_repair(customer)
		second = _make_repair(customer)
		self.assertRegex(first.public_id, PUBLIC_ID_PATTERN)
		self.assertRegex(second.public_id, PUBLIC_ID_PATTERN)
		self.assertNotEqual(first.public_id, second.public_id)
		self.assertTrue(frappe.db.get_column_index("tabNaprawa", "public_id", unique=True))

		first.public_id = "rpr_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
		with self.assertRaises(frappe.ValidationError):
			first.save(ignore_permissions=True)

	def test_caller_cannot_supply_public_id_on_insert(self):
		customer = _make_customer()
		doc = frappe.get_doc(
			{
				"doctype": "Naprawa",
				"klient": customer.name,
				"status": "Przyjęto",
				"rodzaj_naprawy": "Naprawa krótka",
				"sposob_dostarczenia": "Stacjonarnie",
				"sposob_odbioru": "Stacjonarnie",
				"public_id": "rpr_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_account_read_is_scoped_by_portal_users_and_has_allowlisted_model(self):
		user_a = _make_user()
		user_b = _make_user()
		customer_a = _make_customer(user_a)
		customer_b = _make_customer(user_b)
		brand = "Test Brand " + frappe.generate_hash(length=8)
		frappe.get_doc({"doctype": "Marka Zegarka", "nazwa": brand}).insert(ignore_permissions=True)
		repair_a = _make_repair(
			customer_a,
			marka=brand,
			orientacyjna_wycena=123.4,
			klient_telefon="+48111222333",
			klient_email="private-a@example.test",
		)
		repair_b = _make_repair(customer_b)

		frappe.set_user(user_a.name)
		with patch.object(v1, "_account_read_enabled", return_value=True):
			result = v1.list_repairs_for_current_user(None, 50)
			item = v1.get_repair_for_current_user(repair_a.public_id)

		self.assertEqual(result, {"items": [item], "next_cursor": None})
		self.assertEqual(
			set(item),
			{
				"schema",
				"repair_id",
				"public_status",
				"status_label",
				"watch",
				"received_on",
				"estimated_completion_on",
				"quote",
				"actions",
			},
		)
		self.assertEqual(item["quote"], {"amount": "123.40", "currency": "PLN"})
		serialized = repr(item)
		for forbidden in (
			repair_a.name,
			repair_b.name,
			customer_a.name,
			"private-a@example.test",
			"+48111222333",
			"SECRET-SERIAL",
			"Poufna notatka warsztatu",
		):
			self.assertNotIn(forbidden, serialized)

	def test_foreign_missing_malformed_and_internal_name_are_same_not_found(self):
		user_a = _make_user()
		user_b = _make_user()
		customer_a = _make_customer(user_a)
		customer_b = _make_customer(user_b)
		_make_repair(customer_a)
		foreign = _make_repair(customer_b)
		frappe.set_user(user_a.name)

		errors = []
		with patch.object(v1, "_account_read_enabled", return_value=True):
			for repair_id in (
				foreign.public_id,
				"rpr_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
				"malformed",
				foreign.name,
			):
				with self.assertRaises(v1.PublicContractError) as caught:
					v1.get_repair_for_current_user(repair_id)
				errors.append((caught.exception.code, str(caught.exception)))
		self.assertEqual(errors, [("NOT_FOUND", "Repair was not found.")] * 4)

	def test_guest_disabled_and_system_user_are_rejected(self):
		disabled = _make_user(enabled=0)
		system_user = _make_user()
		customer = _make_customer(disabled, system_user)
		frappe.db.set_value("User", system_user.name, "user_type", "System User")
		_make_repair(customer)

		with patch.object(v1, "_account_read_enabled", return_value=True):
			for user in ("Guest", disabled.name, system_user.name):
				frappe.set_user(user)
				with self.assertRaises(v1.PublicContractError) as caught:
					v1.list_repairs_for_current_user(None, 20)
				self.assertEqual(caught.exception.code, "AUTH_REQUIRED")

	def test_page_size_and_unsigned_cursor_are_rejected(self):
		user = _make_user()
		_make_customer(user)
		frappe.set_user(user.name)
		with patch.object(v1, "_account_read_enabled", return_value=True):
			for invalid in (0, 51, 1.0, True):
				with self.assertRaises(v1.PublicContractError) as caught:
					v1.list_repairs_for_current_user(None, invalid)
				self.assertEqual(caught.exception.code, "VALIDATION_FAILED")
			with self.assertRaises(v1.PublicContractError) as caught:
				v1.list_repairs_for_current_user("unsigned", 20)
			self.assertEqual(caught.exception.code, "INVALID_CURSOR")

	def test_backfill_is_idempotent_and_report_contains_only_counters(self):
		customer = _make_customer()
		repair = _make_repair(customer)
		frappe.db.set_value("Naprawa", repair.name, "public_id", None, update_modified=False)

		first = backfill_naprawa_public_id.execute()
		assigned = frappe.db.get_value("Naprawa", repair.name, "public_id")
		second = backfill_naprawa_public_id.execute()

		self.assertRegex(assigned, PUBLIC_ID_PATTERN)
		self.assertEqual(frappe.db.get_value("Naprawa", repair.name, "public_id"), assigned)
		self.assertGreaterEqual(first["assigned_count"], 1)
		self.assertEqual(second["assigned_count"], 0)
		self.assertEqual(set(first), {"total_count", "preserved_count", "assigned_count", "missing_count"})
		self.assertNotIn(repair.name, repr(first))
		self.assertNotIn(customer.name, repr(first))
