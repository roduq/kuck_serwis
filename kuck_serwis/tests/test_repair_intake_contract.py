from __future__ import annotations

import unittest

from kuck_serwis.repair_intake_contract import (
	DeliveryMethod,
	RepairIntakeContractError,
	RepairIntakeErrorCode,
	validate_idempotency_key,
	validate_submission,
)


def payload(**changes):
	value = {
		"full_name": "Jan Kowalski",
		"email": "jan@example.com",
		"phone": "+48 500 600 700",
		"brand": "Longines",
		"model": "HydroConquest",
		"serial_number": "ABC-123",
		"purchase_date": "2024-01-02",
		"issue_description": "Zegarek zatrzymuje się po kilku godzinach noszenia.",
		"condition_description": "Drobne rysy na zapięciu.",
		"warranty": False,
		"delivery_method": "SALON",
		"return_method": "SALON",
		"declared_value": "",
		"privacy_accepted": True,
		"website": "",
	}
	value.update(changes)
	return value


class TestRepairIntakeContract(unittest.TestCase):
	def test_normalises_valid_salon_submission_without_value(self):
		result = validate_submission(payload(full_name="  Jan   Kowalski  ", email="JAN@EXAMPLE.COM"))
		self.assertEqual(result.full_name, "Jan Kowalski")
		self.assertEqual(result.email, "jan@example.com")
		self.assertEqual(result.delivery_method, DeliveryMethod.SALON)
		self.assertIsNone(result.declared_value)
		self.assertNotIn("jan@example.com", repr(result))

	def test_shipping_requires_value_and_accepts_exact_limit(self):
		with self.assertRaises(RepairIntakeContractError) as caught:
			validate_submission(payload(delivery_method="COURIER"))
		self.assertEqual(caught.exception.code, RepairIntakeErrorCode.VALUE_REQUIRED)
		result = validate_submission(payload(delivery_method="COURIER", declared_value="10 000,00"))
		self.assertEqual(str(result.declared_value), "10000.00")

	def test_value_above_limit_is_rejected(self):
		with self.assertRaises(RepairIntakeContractError) as caught:
			validate_submission(payload(return_method="COURIER", declared_value="10000.01"))
		self.assertEqual(caught.exception.code, RepairIntakeErrorCode.VALUE_ABOVE_LIMIT)

	def test_privacy_and_exact_schema_are_required(self):
		with self.assertRaises(RepairIntakeContractError) as caught:
			validate_submission(payload(privacy_accepted=False))
		self.assertEqual(caught.exception.code, RepairIntakeErrorCode.PRIVACY_REQUIRED)
		unknown = payload()
		unknown["customer"] = "CUST-001"
		with self.assertRaises(RepairIntakeContractError) as caught:
			validate_submission(unknown)
		self.assertEqual(caught.exception.code, RepairIntakeErrorCode.INVALID_SCHEMA)

	def test_dates_contact_and_text_are_bounded(self):
		for changes, code in (
			({"purchase_date": "2999-01-01"}, RepairIntakeErrorCode.INVALID_DATE),
			({"email": "not-an-email"}, RepairIntakeErrorCode.INVALID_EMAIL),
			({"phone": "123"}, RepairIntakeErrorCode.INVALID_TEXT),
			({"issue_description": "short"}, RepairIntakeErrorCode.INVALID_TEXT),
			({"model": "x" * 121}, RepairIntakeErrorCode.INVALID_TEXT),
		):
			with self.subTest(changes=changes):
				with self.assertRaises(RepairIntakeContractError) as caught:
					validate_submission(payload(**changes))
				self.assertEqual(caught.exception.code, code)

	def test_honeypot_is_preserved_only_as_boolean_evidence(self):
		result = validate_submission(payload(website="spam.example"))
		self.assertTrue(result.honeypot_triggered)
		self.assertNotIn("spam.example", result.canonical_json())

	def test_idempotency_key_is_strict(self):
		key = "repair_intake_abcdefghijklmnop"
		self.assertEqual(validate_idempotency_key(key), key)
		for invalid in (None, "short", "x" * 129, "invalid key with spaces"):
			with self.assertRaises(RepairIntakeContractError):
				validate_idempotency_key(invalid)


if __name__ == "__main__":
	unittest.main()
