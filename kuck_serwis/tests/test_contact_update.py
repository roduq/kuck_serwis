import unittest
from dataclasses import FrozenInstanceError

from kuck_serwis.contact_update import (
	ContactSnapshot,
	ContactUpdateCode,
	ContactUpdateError,
	ContactUpdateOutcome,
	ContactUpdatePlan,
	plan_contact_update,
)

BASELINE = ContactSnapshot(phone="phone-baseline", email="email-baseline@example.test")


def _plan(*, proposed=BASELINE, current=BASELINE, is_new=False, customer_changed=False):
	return plan_contact_update(
		is_new=is_new,
		customer_changed=customer_changed,
		baseline=BASELINE,
		proposed=proposed,
		current=current,
	)


class TestContactUpdatePlanner(unittest.TestCase):
	def test_new_repair_is_noop(self):
		plan = _plan(
			is_new=True,
			proposed=ContactSnapshot(phone="phone-new", email=BASELINE.email),
		)
		self.assertEqual((plan.outcome, plan.code), (ContactUpdateOutcome.NOOP, ContactUpdateCode.NEW_REPAIR))

	def test_customer_change_is_noop(self):
		plan = _plan(
			customer_changed=True,
			proposed=ContactSnapshot(phone="phone-new", email=BASELINE.email),
		)
		self.assertEqual(
			(plan.outcome, plan.code),
			(ContactUpdateOutcome.NOOP, ContactUpdateCode.CUSTOMER_CHANGED),
		)

	def test_unrelated_save_is_noop_even_when_current_changed(self):
		plan = _plan(current=ContactSnapshot(phone="phone-current", email="email-current@example.test"))
		self.assertEqual((plan.outcome, plan.code), (ContactUpdateOutcome.NOOP, ContactUpdateCode.NO_CHANGES))

	def test_phone_update_applies_when_current_matches_baseline(self):
		plan = _plan(proposed=ContactSnapshot(phone="phone-new", email=BASELINE.email))
		self.assertEqual((plan.outcome, plan.code), (ContactUpdateOutcome.APPLY, ContactUpdateCode.APPLY))
		self.assertTrue(plan.update_phone)
		self.assertFalse(plan.update_email)

	def test_email_update_applies_when_current_matches_baseline(self):
		plan = _plan(proposed=ContactSnapshot(phone=BASELINE.phone, email="email-new@example.test"))
		self.assertEqual(plan.outcome, ContactUpdateOutcome.APPLY)
		self.assertFalse(plan.update_phone)
		self.assertTrue(plan.update_email)

	def test_both_fields_apply_atomically(self):
		plan = _plan(proposed=ContactSnapshot(phone="phone-new", email="email-new@example.test"))
		self.assertEqual(plan.outcome, ContactUpdateOutcome.APPLY)
		self.assertTrue(plan.update_phone)
		self.assertTrue(plan.update_email)

	def test_same_field_concurrent_change_conflicts(self):
		plan = _plan(
			proposed=ContactSnapshot(phone="phone-proposed", email=BASELINE.email),
			current=ContactSnapshot(phone="phone-concurrent", email=BASELINE.email),
		)
		self.assertEqual(
			(plan.outcome, plan.code),
			(ContactUpdateOutcome.CONFLICT, ContactUpdateCode.CONTACT_REVISION_CONFLICT),
		)

	def test_unrelated_current_email_is_preserved_during_phone_update(self):
		plan = _plan(
			proposed=ContactSnapshot(phone="phone-proposed", email=BASELINE.email),
			current=ContactSnapshot(phone=BASELINE.phone, email="email-concurrent@example.test"),
		)
		self.assertEqual(plan.outcome, ContactUpdateOutcome.APPLY)
		self.assertTrue(plan.update_phone)
		self.assertFalse(plan.update_email)

	def test_unrelated_current_phone_is_preserved_during_email_update(self):
		plan = _plan(
			proposed=ContactSnapshot(phone=BASELINE.phone, email="email-proposed@example.test"),
			current=ContactSnapshot(phone="phone-concurrent", email=BASELINE.email),
		)
		self.assertEqual(plan.outcome, ContactUpdateOutcome.APPLY)
		self.assertFalse(plan.update_phone)
		self.assertTrue(plan.update_email)

	def test_exact_already_applied_value_is_idempotent_noop(self):
		proposed = ContactSnapshot(phone="phone-proposed", email="email-proposed@example.test")
		plan = _plan(proposed=proposed, current=proposed)
		self.assertEqual(
			(plan.outcome, plan.code),
			(ContactUpdateOutcome.NOOP, ContactUpdateCode.ALREADY_APPLIED),
		)

	def test_mixed_already_applied_and_safe_field_applies_only_safe_field(self):
		proposed = ContactSnapshot(phone="phone-proposed", email="email-proposed@example.test")
		current = ContactSnapshot(phone="phone-proposed", email=BASELINE.email)
		plan = _plan(proposed=proposed, current=current)
		self.assertEqual(plan.outcome, ContactUpdateOutcome.APPLY)
		self.assertFalse(plan.update_phone)
		self.assertTrue(plan.update_email)

	def test_any_conflict_prevents_partial_apply(self):
		plan = _plan(
			proposed=ContactSnapshot(phone="phone-proposed", email="email-proposed@example.test"),
			current=ContactSnapshot(phone="phone-concurrent", email=BASELINE.email),
		)
		self.assertEqual(plan.outcome, ContactUpdateOutcome.CONFLICT)
		self.assertFalse(plan.update_phone)
		self.assertFalse(plan.update_email)

	def test_clear_is_unsupported_and_fail_closed(self):
		for proposed in (
			ContactSnapshot(phone="", email=BASELINE.email),
			ContactSnapshot(phone=BASELINE.phone, email=""),
		):
			with self.subTest(proposed=proposed):
				plan = _plan(proposed=proposed)
				self.assertEqual(
					(plan.outcome, plan.code),
					(ContactUpdateOutcome.CONFLICT, ContactUpdateCode.CONTACT_CLEAR_UNSUPPORTED),
				)

	def test_exact_bool_and_dto_types_are_required(self):
		with self.assertRaises(ContactUpdateError) as raised:
			plan_contact_update(
				is_new=1,  # type: ignore[arg-type]
				customer_changed=False,
				baseline=BASELINE,
				proposed=BASELINE,
				current=BASELINE,
			)
		self.assertIs(raised.exception.code, ContactUpdateCode.INVALID_INPUT)
		with self.assertRaises(ContactUpdateError):
			ContactSnapshot(phone=None, email="")  # type: ignore[arg-type]

	def test_missing_snapshot_attributes_fail_with_code_only_error(self):
		forged = object.__new__(ContactSnapshot)
		with self.assertRaises(ContactUpdateError) as raised:
			_plan(current=forged)
		self.assertEqual(str(raised.exception), ContactUpdateCode.INVALID_INPUT.value)

	def test_forged_snapshot_values_are_revalidated(self):
		forged = object.__new__(ContactSnapshot)
		object.__setattr__(forged, "phone", 1)
		object.__setattr__(forged, "email", "marker-private-email@example.test")
		with self.assertRaises(ContactUpdateError) as raised:
			_plan(proposed=forged)
		self.assertEqual(str(raised.exception), ContactUpdateCode.INVALID_INPUT.value)
		self.assertNotIn("marker-private", repr(raised.exception))

	def test_dtos_are_frozen_and_forged_plan_is_rejected(self):
		with self.assertRaises(FrozenInstanceError):
			BASELINE.phone = "changed"  # type: ignore[misc]
		with self.assertRaises(ContactUpdateError):
			ContactUpdatePlan(
				outcome=ContactUpdateOutcome.NOOP,
				code=ContactUpdateCode.NO_CHANGES,
				update_phone=True,
				update_email=False,
				phone="marker-phone",
				email="marker-email@example.test",
			)

	def test_values_are_redacted_from_repr_and_errors(self):
		phone = "marker-private-phone"
		email = "marker-private-email@example.test"
		snapshot = ContactSnapshot(phone=phone, email=email)
		plan = _plan(proposed=snapshot, current=BASELINE)
		for rendered in (repr(snapshot), repr(plan)):
			self.assertNotIn(phone, rendered)
			self.assertNotIn(email, rendered)
		with self.assertRaises(ContactUpdateError) as raised:
			ContactSnapshot(phone=phone * 600, email=email)
		self.assertEqual(str(raised.exception), ContactUpdateCode.INVALID_INPUT.value)
		self.assertNotIn(phone, repr(raised.exception))


if __name__ == "__main__":
	unittest.main()
