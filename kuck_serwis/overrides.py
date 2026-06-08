# Copyright (c) 2026, Kuck and contributors
# For license information, please see license.txt
"""Rozszerzenia standardowych doctypów ERPNext na potrzeby serwisu."""

from frappe import _


def customer_dashboard(data):
	"""Dokłada grupę „Serwis" → „Naprawa" na pulpicie powiązań formularza Customer.

	Dzięki temu z kartoteki klienta widać licznik jego napraw i jednym kliknięciem
	przechodzi się do listy. Pole łączące Naprawę z Customerem to `klient`
	(niestandardowe — ERPNext domyślnie zakłada pole `customer`).
	"""
	data = data or {}
	data.setdefault("non_standard_fieldnames", {})["Naprawa"] = "klient"
	data.setdefault("transactions", []).insert(
		0, {"label": _("Serwis"), "items": ["Naprawa"]}
	)
	return data
