from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from kuck_serwis.public_contract import repair_remote_v1 as remote
from kuck_serwis.tests.test_public_contract_v1 import _make_customer, _make_intake, _make_repair


class TestRepairQuoteDecision(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.previous_user = frappe.session.user
		self.flag_present = remote.ROLLOUT_FLAG in frappe.conf
		self.previous_flag = frappe.conf.get(remote.ROLLOUT_FLAG)
		frappe.conf[remote.ROLLOUT_FLAG] = True
		frappe.set_user("Administrator")

	def tearDown(self):
		if self.flag_present:
			frappe.conf[remote.ROLLOUT_FLAG] = self.previous_flag
		else:
			frappe.conf.pop(remote.ROLLOUT_FLAG, None)
		frappe.set_user(self.previous_user)
		super().tearDown()

	def case(self, *, warranty=False, outcome="Nierozstrzygnięta"):
		customer = _make_customer()
		intake = _make_intake(customer=customer.name)
		from frappe.model.workflow import apply_workflow

		repair = _make_repair(customer, orientacyjna_wycena=300, source_intake=intake.name)
		apply_workflow(repair, "Rozpocznij diagnozę")
		apply_workflow(repair, "Wyślij wycenę")
		repair.wynik_gwarancji = outcome
		repair.save()
		intake.db_set(
			{
				"accepted_repair": repair.name,
				"status": "Przyjęte",
				"remote_state": "AWAITING_QUOTE_DECISION",
				"address_line1": "11 Listopada 8",
				"city": "Olsztyn",
				"postal_code": "10-104",
				"country": "PL",
				"warranty_claim": 1 if warranty else 0,
			}
		)
		intake.reload()
		return intake, repair

	def test_accept_uses_workflow_and_replay_does_not_duplicate(self):
		intake, repair = self.case()
		token = remote.issue_guest_access(intake).split("#", 1)[1]
		first = remote.submit_quote_decision(
			intake.public_id, "accept", str(intake.modified), "repair_accept_1234567890", token
		)
		second = remote.submit_quote_decision(
			intake.public_id, "accept", str(intake.modified), "repair_accept_1234567890", token
		)
		self.assertEqual(first, second)
		repair.reload()
		self.assertEqual(repair.status, "W naprawie")
		self.assertTrue(repair.klient_zaakceptowal)
		intake.reload()
		self.assertEqual(intake.remote_state, "IN_REPAIR")
		with self.assertRaises(remote.RemoteRepairError):
			remote.get_return_payment_snapshot(
				intake.public_id, "TPAY", internal_scope="kuck_shop.repair_commerce.v1"
			)
		from frappe.model.workflow import apply_workflow

		apply_workflow(repair, "Zakończ naprawę")
		intake.reload()
		self.assertEqual(intake.remote_state, "AWAITING_REPAIR_PAYMENT")
		snapshot = remote.get_return_payment_snapshot(
			intake.public_id, "TPAY", internal_scope="kuck_shop.repair_commerce.v1"
		)
		self.assertEqual(snapshot["amount_minor"], 32000)

	def test_service_cancellation_after_receipt_charges_only_return(self):
		customer = _make_customer()
		intake = _make_intake(customer=customer.name)
		repair = _make_repair(customer, orientacyjna_wycena=300, source_intake=intake.name)
		intake.db_set(
			{
				"accepted_repair": repair.name,
				"status": "Przyjęte",
				"remote_state": "RECEIVED",
				"address_line1": "11 Listopada 8",
				"city": "Olsztyn",
				"postal_code": "10-104",
				"country": "PL",
			}
		)
		from frappe.model.workflow import apply_workflow

		apply_workflow(repair, "Anuluj")
		intake.reload()
		self.assertEqual(intake.remote_state, "AWAITING_REPAIR_PAYMENT")
		snapshot = remote.get_return_payment_snapshot(
			intake.public_id, "TPAY", internal_scope="kuck_shop.repair_commerce.v1"
		)
		self.assertEqual(snapshot["amount_minor"], 2000)
		self.assertEqual(snapshot["repair_amount_minor"], 0)
		self.assertEqual(snapshot["outcome"], "rejected")

	def test_reject_cancels_and_offers_exact_cod_charge(self):
		intake, repair = self.case()
		token = remote.issue_guest_access(intake).split("#", 1)[1]
		remote.submit_quote_decision(
			intake.public_id, "reject", str(intake.modified), "repair_reject_1234567890", token
		)
		repair.reload()
		self.assertEqual(repair.status, "Anulowano")
		intake.reload()
		self.assertEqual(intake.remote_state, "AWAITING_REPAIR_PAYMENT")
		snapshot = remote.get_return_payment_snapshot(
			intake.public_id, "COD", internal_scope="kuck_shop.repair_commerce.v1"
		)
		self.assertEqual(snapshot["amount_minor"], 2500)
		self.assertEqual(snapshot["outcome"], "rejected")

	def test_positive_warranty_has_free_return_but_negative_is_charged(self):
		intake, repair = self.case(warranty=True, outcome="Uzasadniona")
		intake.last_quote_decision = "accept"
		intake.remote_state = "AWAITING_REPAIR_PAYMENT"
		intake.flags.repair_intake_transition = True
		intake.save()
		repair.db_set("status", "Gotowe do odbioru")
		snapshot = remote.get_return_payment_snapshot(
			intake.public_id, "FREE", internal_scope="kuck_shop.repair_commerce.v1"
		)
		self.assertEqual(snapshot["amount_minor"], 0)
		other, other_repair = self.case(warranty=True, outcome="Nieuzasadniona")
		other.last_quote_decision = "reject"
		other.remote_state = "AWAITING_REPAIR_PAYMENT"
		other.flags.repair_intake_transition = True
		other.save()
		other_repair.db_set("status", "Anulowano")
		snapshot = remote.get_return_payment_snapshot(
			other.public_id, "TPAY", internal_scope="kuck_shop.repair_commerce.v1"
		)
		self.assertEqual(snapshot["amount_minor"], 2000)

	def test_wrong_grant_cannot_change_decision(self):
		intake, repair = self.case()
		with self.assertRaises(remote.RemoteRepairError):
			remote.submit_quote_decision(
				intake.public_id, "accept", str(intake.modified), "repair_foreign_1234567890", "wrong-token"
			)
		repair.reload()
		self.assertEqual(repair.status, "Oczekuje na akceptację")
