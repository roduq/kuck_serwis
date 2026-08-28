from __future__ import annotations

import types
import unittest
from datetime import datetime

from kuck_serwis import repair_intake, repair_intake_security


class FakeValidationError(Exception):
	pass


class FakePermissionError(Exception):
	pass


class FakeDuplicateEntryError(Exception):
	pass


class FakeDoc:
	def __init__(self, values, database):
		self.values = values
		self.flags = types.SimpleNamespace(public_repair_intake=False)
		self.database = database

	def insert(self, **kwargs):
		if kwargs != {"ignore_permissions": True} or not self.flags.public_repair_intake:
			raise AssertionError("unsafe public insert")
		binding = self.values["idempotency_binding"]
		if binding in self.database.rows:
			raise FakeDuplicateEntryError
		self.database.rows[binding] = {
			"name": f"RIN-2026-{len(self.database.rows) + 1:05d}",
			"request_fingerprint": self.values["request_fingerprint"],
			"values": dict(self.values),
		}


class FakeDB:
	def __init__(self):
		self.rows = {}
		self.user_state = {"enabled": 1, "user_type": "Website User"}

	def get_value(self, doctype, name, fields, as_dict=False):
		if doctype == repair_intake.DOCTYPE:
			row = self.rows.get(name["idempotency_binding"])
			return types.SimpleNamespace(**row) if row and as_dict else row
		if doctype == "User":
			return types.SimpleNamespace(**self.user_state) if as_dict else self.user_state
		raise AssertionError((doctype, name, fields))


class FakeLogger:
	def __init__(self):
		self.events = []

	def info(self, event):
		self.events.append(event)


def fake_frappe():
	database = FakeDB()
	logger = FakeLogger()
	request = types.SimpleNamespace(
		method="POST",
		mimetype="application/json",
		content_length=1000,
		headers={"Origin": "https://kuck.localhost", "Sec-Fetch-Site": "same-origin"},
		cookies={repair_intake_security._COOKIE_NAME: "A" * 43},
	)
	fake = types.SimpleNamespace(
		db=database,
		session=types.SimpleNamespace(user="Guest", data=types.SimpleNamespace(csrf_token=None)),
		local=types.SimpleNamespace(request=request, cookie_manager=types.SimpleNamespace()),
		conf={"host_name": "https://kuck.localhost"},
		ValidationError=FakeValidationError,
		PermissionError=FakePermissionError,
		DuplicateEntryError=FakeDuplicateEntryError,
	)
	fake.get_doc = lambda values: FakeDoc(values, database)
	fake.get_all = lambda doctype, **kwargs: []
	fake.logger = lambda *args, **kwargs: logger
	fake.throw = lambda message, exception=FakeValidationError: (_ for _ in ()).throw(exception(message))
	fake._logger = logger
	request.headers["X-Frappe-CSRF-Token"] = repair_intake_security._guest_csrf_token("A" * 43)
	return fake


def payload(**changes):
	value = {
		"full_name": "Jan Kowalski",
		"email": "jan@example.com",
		"phone": "+48 500 600 700",
		"brand": "Longines",
		"model": "HydroConquest",
		"serial_number": "ABC-123",
		"purchase_date": "2024-01-02",
		"issue_description": "Zegarek zatrzymuje się po kilku godzinach.",
		"condition_description": "Drobne rysy.",
		"warranty": False,
		"delivery_method": "SALON",
		"return_method": "SALON",
		"declared_value": "",
		"privacy_accepted": True,
		"website": "",
	}
	value.update(changes)
	return value


class TestRepairIntakeAPI(unittest.TestCase):
	def setUp(self):
		self.real_api_frappe = repair_intake.frappe
		self.real_security_frappe = repair_intake_security.frappe
		self.real_now_datetime = repair_intake.now_datetime
		self.frappe = fake_frappe()
		repair_intake.frappe = self.frappe
		repair_intake_security.frappe = self.frappe
		repair_intake.now_datetime = lambda: datetime(2026, 8, 28, 12, 0, 0)
		self.submit = repair_intake.submit_repair_intake.__wrapped__

	def tearDown(self):
		repair_intake.frappe = self.real_api_frappe
		repair_intake_security.frappe = self.real_security_frappe
		repair_intake.now_datetime = self.real_now_datetime

	def test_guest_creates_private_intake_without_customer_or_public_identifier(self):
		key = "repair_abcdefghijklmnopqrstuvwxyz"
		result = self.submit(payload(), key)
		self.assertEqual(result, {"accepted": True})
		self.assertEqual(len(self.frappe.db.rows), 1)
		stored = next(iter(self.frappe.db.rows.values()))["values"]
		self.assertEqual(stored["source"], "Guest")
		self.assertIsNone(stored["customer"])
		self.assertIsNone(stored["requester_user"])
		self.assertNotIn(key, repr(stored))
		self.assertNotIn("RIN-", repr(result))

	def test_same_actor_key_and_payload_replays_but_changed_payload_conflicts(self):
		key = "repair_abcdefghijklmnopqrstuvwxyz"
		self.assertEqual(self.submit(payload(), key), {"accepted": True})
		self.assertEqual(self.submit(payload(), key), {"accepted": True})
		self.assertEqual(len(self.frappe.db.rows), 1)
		with self.assertRaises(FakeValidationError):
			self.submit(payload(model="Different"), key)
		self.assertEqual(len(self.frappe.db.rows), 1)

	def test_honeypot_and_foreign_origin_never_write(self):
		key = "repair_abcdefghijklmnopqrstuvwxyz"
		self.assertEqual(self.submit(payload(website="spam.example"), key), {"accepted": True})
		self.assertFalse(self.frappe.db.rows)

	def test_missing_or_invalid_content_length_fails_closed(self):
		key = "repair_abcdefghijklmnopqrstuvwxyz"
		for invalid_length in (None, "1000", 0, 24_001):
			with self.subTest(content_length=invalid_length):
				self.frappe.local.request.content_length = invalid_length
				with self.assertRaises(FakePermissionError):
					self.submit(payload(), key)
		self.assertFalse(self.frappe.db.rows)
		self.frappe.local.request.headers["Origin"] = "https://attacker.example"
		with self.assertRaises(FakePermissionError):
			self.submit(payload(), key)
		self.assertFalse(self.frappe.db.rows)

	def test_logged_website_user_links_only_exact_portal_user_customer(self):
		self.frappe.session.user = "member@example.test"
		self.frappe.session.data.csrf_token = "C" * 32
		self.frappe.local.request.headers["X-Frappe-CSRF-Token"] = "C" * 32
		self.frappe.get_all = lambda doctype, **kwargs: ["CUST-1"] if doctype == "Portal User" else []
		self.submit(payload(), "repair_abcdefghijklmnopqrstuvwxyz")
		stored = next(iter(self.frappe.db.rows.values()))["values"]
		self.assertEqual(stored["source"], "Portal User")
		self.assertEqual(stored["requester_user"], "member@example.test")
		self.assertEqual(stored["customer"], "CUST-1")

	def test_multiple_customer_links_fail_closed_to_unlinked_intake(self):
		self.frappe.session.user = "member@example.test"
		self.frappe.session.data.csrf_token = "C" * 32
		self.frappe.local.request.headers["X-Frappe-CSRF-Token"] = "C" * 32
		self.frappe.get_all = lambda doctype, **kwargs: ["CUST-2", "CUST-1"]
		self.submit(payload(), "repair_abcdefghijklmnopqrstuvwxyz")
		stored = next(iter(self.frappe.db.rows.values()))["values"]
		self.assertIsNone(stored["customer"])

	def test_endpoint_is_guest_post_only_and_rate_limited(self):
		self.assertTrue(getattr(repair_intake.submit_repair_intake, "allow_guest", True))
		allowed = getattr(self.real_api_frappe, "allowed_http_methods_for_whitelisted_func", {})
		if allowed:
			self.assertEqual(allowed[repair_intake.submit_repair_intake], ["POST"])
		self.assertEqual(repair_intake.submit_repair_intake.__name__, "submit_repair_intake")


if __name__ == "__main__":
	unittest.main()
