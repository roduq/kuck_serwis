"""Idempotent backfill of opaque public identifiers for repair intakes."""

import frappe

from kuck_serwis.kuck_serwis.doctype.kuck_repair_intake.kuck_repair_intake import (
	generate_unique_public_id,
)

BATCH_SIZE = 500
DOCTYPE = "Kuck Repair Intake"


def execute():
	total = frappe.db.count(DOCTYPE)
	missing_before = frappe.db.count(DOCTYPE, {"public_id": ["in", ["", None]]})
	assigned = 0
	last_name = ""

	while True:
		rows = frappe.get_all(
			DOCTYPE,
			filters=[
				["Kuck Repair Intake", "public_id", "in", ["", None]],
				["Kuck Repair Intake", "name", ">", last_name],
			],
			fields=["name"],
			order_by="name asc",
			limit=BATCH_SIZE,
		)
		if not rows:
			break
		for row in rows:
			frappe.db.set_value(
				DOCTYPE, row.name, "public_id", generate_unique_public_id(), update_modified=False
			)
			assigned += 1
		last_name = rows[-1].name

	report = {
		"total_count": total,
		"preserved_count": total - missing_before,
		"assigned_count": assigned,
		"missing_count": frappe.db.count(DOCTYPE, {"public_id": ["in", ["", None]]}),
	}
	frappe.logger("kuck_serwis.public_contract").info(
		"Repair intake public ID backfill completed", extra=report
	)
	return report
