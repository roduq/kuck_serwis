// Copyright (c) 2026, Kuck and contributors
// For license information, please see license.txt
//
// Z kartoteki klienta otwieramy „Kartę klienta" — pulpit jego napraw z historią.

frappe.ui.form.on("Customer", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		frm.add_custom_button(__("Karta klienta"), () => {
			frappe.route_options = { klient: frm.doc.name };
			frappe.set_route("karta-klienta");
		});
	},
});
