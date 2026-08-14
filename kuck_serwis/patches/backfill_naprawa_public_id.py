"""Idempotent, PII-free backfill of opaque repair identifiers."""

import frappe

from kuck_serwis.kuck_serwis.doctype.naprawa.naprawa import generate_unique_public_id

BATCH_SIZE = 500


def execute():
	total = frappe.db.count("Naprawa")
	missing_before = frappe.db.count("Naprawa", {"public_id": ["in", ["", None]]})
	assigned = 0
	last_name = ""

	while True:
		rows = frappe.get_all(
			"Naprawa",
			filters=[["Naprawa", "public_id", "in", ["", None]], ["Naprawa", "name", ">", last_name]],
			fields=["name"],
			order_by="name asc",
			limit=BATCH_SIZE,
		)
		if not rows:
			break
		for row in rows:
			frappe.db.set_value(
				"Naprawa", row.name, "public_id", generate_unique_public_id(), update_modified=False
			)
			assigned += 1
		last_name = rows[-1].name

	report = {
		"total_count": total,
		"preserved_count": total - missing_before,
		"assigned_count": assigned,
		"missing_count": frappe.db.count("Naprawa", {"public_id": ["in", ["", None]]}),
	}
	frappe.logger("kuck_serwis.public_contract").info("Naprawa public ID backfill completed", extra=report)
	return report
