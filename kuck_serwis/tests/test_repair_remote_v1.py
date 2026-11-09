from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from kuck_serwis.public_contract import repair_remote_v1 as remote
from kuck_serwis.tests.test_public_contract_v1 import _make_customer, _make_intake, _make_user


class TestRepairRemoteV1(IntegrationTestCase):
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

	def test_guest_grant_is_hashed_scoped_and_revocable(self):
		intake = _make_intake()
		url = remote.issue_guest_access(intake)
		token = url.split("#", 1)[1]
		self.assertNotIn(token, repr(frappe.get_doc(remote.DOCTYPE, intake.name)))
		case = remote.get_intake_by_token(intake.public_id, token)
		self.assertEqual(case["intake_id"], intake.public_id)
		self.assertEqual(case["remote_state"], "AWAITING_SERVICE_REVIEW")
		self.assertEqual(case["actions"], [])
		intake.db_set("access_grant_revoked", 1)
		with self.assertRaises(remote.RemoteRepairError) as caught:
			remote.get_intake_by_token(intake.public_id, token)
		self.assertEqual(caught.exception.code, "NOT_FOUND")

	def test_operator_approval_unlocks_payment_snapshot_once(self):
		customer = _make_customer()
		intake = _make_intake(customer=customer.name)
		intake.db_set(
			{
				"address_line1": "11 Listopada 8",
				"city": "Olsztyn",
				"postal_code": "10-104",
				"country": "PL",
			}
		)
		intake.reload()
		with patch("kuck_serwis.public_contract.repair_remote_v1._send"):
			result = remote.approve_remote_intake(intake.name, str(intake.modified))
		self.assertEqual(result["remote_state"], "AWAITING_INBOUND_PAYMENT")
		snapshot = remote.get_approved_shipment_snapshot(
			intake.public_id, internal_scope="kuck_shop.repair_commerce.v1"
		)
		self.assertEqual(snapshot["declared_value_minor"], 100000)
		self.assertEqual(snapshot["receiver"]["postal_code"], "10-104")
		with self.assertRaises(remote.RemoteRepairError):
			remote.get_approved_shipment_snapshot(intake.public_id, internal_scope="forged")
		with self.assertRaises(frappe.ValidationError):
			remote.approve_remote_intake(intake.name, str(intake.modified))

	def test_account_scope_does_not_expose_foreign_case(self):
		user_a = _make_user()
		user_b = _make_user()
		customer_a = _make_customer(user_a)
		own = _make_intake(requester_user=user_a.name)
		_make_intake(requester_user=user_b.name)
		_make_intake(customer=customer_a.name)
		frappe.set_user(user_a.name)
		case = remote.get_intake_for_current_user(own.public_id)
		self.assertEqual(case["intake_id"], own.public_id)
		with self.assertRaises(remote.RemoteRepairError) as caught:
			remote.get_intake_for_current_user("rqi_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB")
		self.assertEqual(caught.exception.code, "NOT_FOUND")


class TestRepairPricing(IntegrationTestCase):
	def test_all_return_prices_follow_outcome_and_settlement(self):
		from kuck_serwis.repair_pricing import RepairOutcome, repair_payment_minor, return_charge_minor

		self.assertEqual(return_charge_minor(RepairOutcome.WARRANTY_APPROVED, "TPAY"), 0)
		self.assertEqual(return_charge_minor(RepairOutcome.MANUFACTURER_FORWARDED, "TPAY"), 0)
		self.assertEqual(return_charge_minor(RepairOutcome.REJECTED, "TPAY"), 2000)
		self.assertEqual(return_charge_minor(RepairOutcome.REJECTED, "COD"), 2500)
		self.assertEqual(return_charge_minor(RepairOutcome.WARRANTY_REJECTED, "TPAY"), 2000)
		self.assertEqual(return_charge_minor(RepairOutcome.WARRANTY_REJECTED, "COD"), 2500)
		self.assertEqual(repair_payment_minor(repair_amount_minor=30000, outcome=RepairOutcome.PAID), 32000)
		with self.assertRaises(ValueError):
			return_charge_minor(RepairOutcome.PAID, "COD")
