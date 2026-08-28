from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_test_client
from frappe.website.serve import get_response_without_exception_handling

from kuck_serwis import repair_intake
from kuck_serwis.repair_intake_contract import PRIVACY_PROOF_SHA256, PRIVACY_REVISION


def _digest(value: str) -> str:
	return hashlib.sha256(value.encode()).hexdigest()


def _make_customer():
	return frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": "Repair Intake Customer " + frappe.generate_hash(length=8),
			"customer_type": "Individual",
		}
	).insert(ignore_permissions=True)


def _make_intake(*, customer=None, suffix=None):
	suffix = suffix or frappe.generate_hash(length=12)
	doc = frappe.get_doc(
		{
			"doctype": repair_intake.DOCTYPE,
			"status": "Nowe",
			"source": "Guest",
			"submitted_at": frappe.utils.now_datetime(),
			"customer": customer,
			"full_name": "Jan Kowalski",
			"email": "jan@example.test",
			"phone": "+48 500 600 700",
			"brand_declared": "Testowa Marka",
			"model_declared": "Model Testowy",
			"serial_number": "SERIAL-TEST",
			"issue_description": "Zegarek zatrzymuje się podczas normalnego użytkowania.",
			"condition_description": "Rysa na zapięciu.",
			"warranty_claim": 0,
			"suggested_repair_type": "Naprawa długa",
			"delivery_method": "SALON",
			"return_method": "SALON",
			"privacy_revision": PRIVACY_REVISION,
			"privacy_proof_sha256": PRIVACY_PROOF_SHA256,
			"idempotency_binding": _digest("binding-" + suffix),
			"request_fingerprint": _digest("request-" + suffix),
		}
	)
	doc.flags.public_repair_intake = True
	return doc.insert(ignore_permissions=True)


class TestRepairIntakeFrappe(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.previous_user = frappe.session.user
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user(self.previous_user)
		super().tearDown()

	def test_doctype_has_no_guest_or_website_user_permission(self):
		meta = frappe.get_meta(repair_intake.DOCTYPE)
		self.assertEqual({row.role for row in meta.permissions}, {"Serwis", "System Manager"})
		frappe.set_user("Guest")
		self.assertFalse(frappe.has_permission(repair_intake.DOCTYPE, "read"))
		self.assertFalse(frappe.has_permission(repair_intake.DOCTYPE, "create"))

	def test_operator_accepts_once_only_after_customer_and_physical_receipt(self):
		customer = _make_customer()
		intake = _make_intake(customer=customer.name)
		result = repair_intake.accept_repair_intake(intake.name, str(intake.modified), True)
		repair = frappe.get_doc("Naprawa", result["repair"])
		self.assertEqual(repair.klient, customer.name)
		self.assertEqual(repair.model_zegarka, "Model Testowy")
		self.assertEqual(repair.status, "Przyjęto")
		accepted = frappe.get_doc(repair_intake.DOCTYPE, intake.name)
		self.assertEqual(accepted.status, "Przyjęte")
		self.assertEqual(accepted.accepted_repair, repair.name)
		replay = repair_intake.accept_repair_intake(intake.name, str(intake.modified), True)
		self.assertEqual(replay, result)
		self.assertEqual(frappe.db.count("Naprawa", {"name": repair.name}), 1)

	def test_accept_requires_customer_receipt_role_and_current_revision(self):
		intake = _make_intake()
		with self.assertRaises(frappe.ValidationError):
			repair_intake.accept_repair_intake(intake.name, str(intake.modified), False)
		with self.assertRaises(frappe.ValidationError):
			repair_intake.accept_repair_intake(intake.name, str(intake.modified), True)
		intake.customer = _make_customer().name
		intake.save()
		with self.assertRaises(frappe.ValidationError):
			repair_intake.accept_repair_intake(intake.name, "2020-01-01", True)
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			repair_intake.accept_repair_intake(intake.name, str(intake.modified), True)

	def test_submission_snapshot_and_controlled_state_are_immutable(self):
		intake = _make_intake()
		intake.email = "changed@example.test"
		with self.assertRaises(frappe.ValidationError):
			intake.save()
		intake.reload()
		intake.status = "Przyjęte"
		with self.assertRaises(frappe.ValidationError):
			intake.save()

	def test_guest_form_renders_without_disclosing_internal_identity(self):
		with patch("frappe.app.get_response", get_response_without_exception_handling):
			with ThreadPoolExecutor(max_workers=1) as executor:
				response = executor.submit(
					get_test_client().get,
					"/serwis/zglos-naprawe",
					base_url="http://kuck.localhost",
				).result(timeout=10)
		self.assertEqual(response.status_code, 200)
		html = response.get_data(as_text=True)
		self.assertEqual(html.count("<h1>"), 1)
		self.assertIn('id="repair-intake-form"', html)
		self.assertNotIn("RIN-", html)


if __name__ == "__main__":
	import unittest

	unittest.main()
