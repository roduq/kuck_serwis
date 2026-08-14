# Copyright (c) 2026, Kuck and contributors
# For license information, please see license.txt
"""Rozszerzenia standardowych doctypów ERPNext na potrzeby serwisu."""

from frappe import _


def customer_dashboard(data):
	"""Dodaje idempotentnie „Naprawa" do grupy „Serwis" pulpitu Customer.

	Dzięki temu z kartoteki klienta widać licznik jego napraw i jednym kliknięciem
	przechodzi się do listy. Pole łączące Naprawę z Customerem to `klient`
	(niestandardowe — ERPNext domyślnie zakłada pole `customer`).
	"""
	if data is None:
		data = {}
	data.setdefault("non_standard_fieldnames", {})["Naprawa"] = "klient"

	transactions = data.setdefault("transactions", [])
	service_label = _("Serwis")
	service_group = None
	service_items = []
	composed_transactions = []
	for group in transactions:
		if group.get("label") != service_label:
			composed_transactions.append(group)
			continue

		service_items.extend(group.get("items") or [])
		if service_group is None:
			service_group = group
			composed_transactions.append(service_group)
		else:
			# A duplicated registration must not duplicate the group. Preserve any
			# non-conflicting metadata contributed by the additional group.
			for key, value in group.items():
				if key not in {"label", "items"}:
					service_group.setdefault(key, value)

	if service_group is None:
		service_group = {"label": service_label, "items": []}
		composed_transactions.insert(0, service_group)

	service_group["items"] = [item for item in service_items if item != "Naprawa"]
	service_group["items"].append("Naprawa")
	transactions[:] = composed_transactions
	return data
