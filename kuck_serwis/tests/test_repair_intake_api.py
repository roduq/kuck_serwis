from __future__ import annotations

import types
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import frappe as real_frappe
from frappe import rate_limiter as real_rate_limiter

from kuck_serwis import repair_intake, repair_intake_security


class FakeValidationError(Exception):
	pass


class FakePermissionError(Exception):
	pass


class FakeDuplicateEntryError(Exception):
	pass


class FakeFiles(dict):
	def getlist(self, key):
		return self.get(key, [])


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

	def reload(self):
		return self


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


class FakeRateLimitCache:
	def __init__(self):
		self.values = {}

	def make_key(self, key):
		return key.encode()

	def get(self, key):
		return self.values.get(key)

	def setex(self, key, seconds, value):
		self.values[key] = value

	def incrby(self, key, amount):
		self.values[key] = self.values.get(key, 0) + amount
		return self.values[key]


def raise_frappe_exception(message, exception):
	raise exception(message)


def fake_frappe():
	database = FakeDB()
	logger = FakeLogger()
	request = types.SimpleNamespace(
		method="POST",
		mimetype="application/json",
		content_length=1000,
		headers={"Origin": "https://kuck.localhost", "Sec-Fetch-Site": "same-origin"},
		cookies={repair_intake_security._COOKIE_NAME: "A" * 43},
		files=FakeFiles(),
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
		self.frappe.local.request.headers["Origin"] = "https://attacker.example"
		with mock.patch.object(repair_intake, "_submit_repair_intake_rate_limited") as limited:
			with self.assertRaises(FakePermissionError):
				self.submit(payload(), key)
			limited.assert_not_called()
		self.assertFalse(self.frappe.db.rows)

	def test_origin_uses_effective_ports_and_localhost_must_match_configured_port(self):
		key = "repair_abcdefghijklmnopqrstuvwxyz"
		cases = (
			("https://erpnext.kuck.pl", "https://erpnext.kuck.pl:443", True),
			("https://erpnext.kuck.pl:443", "https://erpnext.kuck.pl", True),
			("https://erpnext.kuck.pl", "https://erpnext.kuck.pl:444", False),
			("http://localhost:8000", "http://localhost:8000", True),
			("http://localhost:8000", "http://localhost:8001", False),
			("http://127.0.0.1:8000", "http://127.0.0.1:8001", False),
			("http://[::1]:8000", "http://[::1]:8000", True),
			("https://erpnext.kuck.pl", "https://[invalid", False),
		)
		for configured, origin, accepted in cases:
			with self.subTest(configured=configured, origin=origin):
				self.frappe.conf["host_name"] = configured
				self.frappe.local.request.headers["Origin"] = origin
				self.frappe.db.rows.clear()
				if accepted:
					self.assertEqual(self.submit(payload(), key), {"accepted": True})
				else:
					with self.assertRaises(FakePermissionError):
						self.submit(payload(), key)

	def test_missing_or_invalid_content_length_fails_closed(self):
		key = "repair_abcdefghijklmnopqrstuvwxyz"
		for invalid_length in (None, "1000", 0, 24_001):
			with self.subTest(content_length=invalid_length):
				self.frappe.local.request.content_length = invalid_length
				with self.assertRaises(FakePermissionError):
					self.submit(payload(), key)
		self.assertFalse(self.frappe.db.rows)

	def test_multipart_without_photos_and_strict_file_field_are_supported(self):
		key = "repair_abcdefghijklmnopqrstuvwxyz"
		self.frappe.local.request.mimetype = "multipart/form-data"
		self.assertEqual(self.submit(payload(), key), {"accepted": True})
		self.frappe.db.rows.clear()
		self.frappe.local.request.files = FakeFiles({"avatar": []})
		with self.assertRaises(FakeValidationError):
			self.submit(payload(), key)
		self.frappe.local.request.files = FakeFiles()
		self.frappe.local.request.content_length = 16 * 1024 * 1024 + 1
		with self.assertRaises(FakePermissionError):
			self.submit(payload(), key)

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


class TestRepairIntakeRateLimitIntegration(unittest.TestCase):
	def setUp(self):
		self.sites = TemporaryDirectory()
		Path(self.sites.name, "apps.txt").write_text("frappe\nerpnext\nkuck_serwis\n", encoding="utf-8")
		site_path = Path(self.sites.name, "test.localhost")
		site_path.mkdir()
		Path(site_path, "site_config.json").write_text("{}", encoding="utf-8")
		real_frappe.init(site="test.localhost", sites_path=self.sites.name)
		self.cache = FakeRateLimitCache()
		self.cache_patch = mock.patch.object(real_frappe, "cache", self.cache)
		self.cache_patch.start()
		self.translation_patch = mock.patch.object(real_rate_limiter, "_", side_effect=lambda value: value)
		self.translation_patch.start()
		self.throw_patch = mock.patch.object(real_frappe, "throw", side_effect=raise_frappe_exception)
		self.throw_patch.start()
		real_frappe.local.request = types.SimpleNamespace(method="POST")
		real_frappe.local.request_ip = "198.51.100.7"
		real_frappe.local.form_dict = real_frappe._dict(cmd="kuck_serwis.repair_intake.submit_repair_intake")

	def tearDown(self):
		self.throw_patch.stop()
		self.translation_patch.stop()
		self.cache_patch.stop()
		real_frappe.destroy()
		self.sites.cleanup()

	def test_post_validation_limit_is_ip_only_and_ninth_write_is_rejected(self):
		with mock.patch.object(repair_intake, "_submit_repair_intake", return_value={"accepted": True}):
			for _ in range(8):
				self.assertEqual(repair_intake._submit_repair_intake_rate_limited(), {"accepted": True})
			with self.assertRaises(real_frappe.RateLimitExceededError):
				repair_intake._submit_repair_intake_rate_limited()
			real_frappe.local.request_ip = "198.51.100.8"
			self.assertEqual(repair_intake._submit_repair_intake_rate_limited(), {"accepted": True})

		keys = tuple(self.cache.values)
		self.assertEqual(len(keys), 2)
		self.assertTrue(all(key.endswith(b":3600") for key in keys))

	def test_invalid_origin_consumes_only_outer_short_window(self):
		real_frappe.local.request = types.SimpleNamespace(
			method="POST",
			mimetype="application/json",
			content_length=100,
			headers={"Origin": "http://localhost:8000"},
			cookies={},
		)
		real_frappe.local.session = types.SimpleNamespace(
			user="Guest", data=types.SimpleNamespace(csrf_token=None)
		)
		real_frappe.conf.host_name = "https://erpnext.kuck.pl"
		with self.assertRaises(real_frappe.PermissionError):
			repair_intake.submit_repair_intake(payload(), "repair_abcdefghijklmnopqrstuvwxyz")

		keys = tuple(self.cache.values)
		self.assertEqual(len(keys), 1)
		self.assertTrue(keys[0].endswith(b":600"))


if __name__ == "__main__":
	unittest.main()
