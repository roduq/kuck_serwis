"""Transactional repair notifications without marketing-consent dependencies."""

from __future__ import annotations

import frappe


def notify_repair_created(*, intake, repair) -> None:
	"""Queue the single creation notice after the operator created a repair."""

	repair.reload()
	if intake.requester_user:
		link = f"/konto/naprawy/{repair.public_id}"
	else:
		link = f"/serwis/zgloszenie/{intake.public_id}"
	watch = " ".join(value for value in (repair.marka, repair.model_zegarka) if value) or "Twojego zegarka"
	subject = "Nowa naprawa w serwisie Kuck"
	message = (
		"Dzień dobry,\n\n"
		f"Serwis przyjął zegarek i utworzył naprawę dla: {watch}.\n"
		"Możesz sprawdzić jej szczegóły i aktualny status po zalogowaniu lub przez swój link dostępu:\n"
		f"{frappe.utils.get_url(link)}\n\n"
		"Pozdrawiamy,\nSerwis Kuck"
	)
	try:
		frappe.sendmail(
			recipients=[intake.email],
			subject=subject,
			message=message,
			reference_doctype="Naprawa",
			reference_name=repair.name,
			delayed=True,
		)
	except Exception:
		frappe.logger("repair_intake", allow_site=True).error({"event": "repair_created_notification_failed"})
		return
	frappe.logger("repair_intake", allow_site=True).info({"event": "repair_created_notification_queued"})
