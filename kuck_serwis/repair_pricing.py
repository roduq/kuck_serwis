"""Authoritative PLN minor-unit pricing for remote repair outcomes."""

from __future__ import annotations

from enum import StrEnum

RETURN_PREPAID_MINOR = 2_000
RETURN_COD_MINOR = 2_500


class RepairOutcome(StrEnum):
	PAID = "paid"
	REJECTED = "rejected"
	WARRANTY_APPROVED = "warranty_approved"
	WARRANTY_REJECTED = "warranty_rejected"
	MANUFACTURER_FORWARDED = "manufacturer_forwarded"


def return_charge_minor(outcome: RepairOutcome, settlement: str) -> int:
	if outcome in {RepairOutcome.WARRANTY_APPROVED, RepairOutcome.MANUFACTURER_FORWARDED}:
		return 0
	if settlement == "COD" and outcome in {RepairOutcome.REJECTED, RepairOutcome.WARRANTY_REJECTED}:
		return RETURN_COD_MINOR
	if settlement == "TPAY":
		return RETURN_PREPAID_MINOR
	raise ValueError("INVALID_RETURN_SETTLEMENT")


def repair_payment_minor(*, repair_amount_minor: int, outcome: RepairOutcome) -> int:
	if type(repair_amount_minor) is not int or repair_amount_minor < 0:
		raise ValueError("INVALID_REPAIR_AMOUNT")
	if outcome is RepairOutcome.PAID:
		return repair_amount_minor + RETURN_PREPAID_MINOR
	if outcome in {RepairOutcome.WARRANTY_APPROVED, RepairOutcome.MANUFACTURER_FORWARDED}:
		return 0
	raise ValueError("REPAIR_PAYMENT_NOT_REQUIRED")
