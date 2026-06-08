// Copyright (c) 2026, Kuck and contributors
// For license information, please see license.txt
//
// Z formularza naprawy recepcja jednym kliknięciem otwiera „Kartę klienta" —
// pełny pulpit wszystkich napraw tego klienta wraz z historią.

frappe.ui.form.on("Naprawa", {
	refresh(frm) {
		if (frm.doc.klient) {
			frm.add_custom_button(__("Karta klienta"), () => {
				frappe.route_options = { klient: frm.doc.klient };
				frappe.set_route("karta-klienta");
			});
		}
	},
});
