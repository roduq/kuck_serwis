// Copyright (c) 2026, Kuck and contributors
// For license information, please see license.txt
//
// Przyjęcie zbiorcze: jednym kliknięciem zakłada osobne naprawy dla wszystkich wpisanych
// zegarków, a po utworzeniu daje skrót do wspólnego pokwitowania i Karty klienta.

frappe.ui.form.on("Przyjecie Zbiorcze", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		if (frm.doc.status !== "Naprawy utworzone") {
			frm.add_custom_button(__("Utwórz naprawy"), () => {
				frm.call("utworz_naprawy").then((r) => {
					const ile = (r.message || []).length;
					if (ile) {
						frappe.show_alert(
							{ message: __("Utworzono napraw: {0}", [ile]), indicator: "green" },
							5
						);
					} else {
						frappe.msgprint(__("Brak nowych zegarków do utworzenia napraw."));
					}
					frm.reload_doc();
				});
			}).addClass("btn-primary");
		} else {
			frm.add_custom_button(__("Drukuj pokwitowanie"), () => {
				frappe.set_route("print", frm.doctype, frm.docname);
			});
		}

		if (frm.doc.klient) {
			frm.add_custom_button(__("Karta klienta"), () => {
				frappe.route_options = { klient: frm.doc.klient };
				frappe.set_route("karta-klienta");
			});
		}
	},
});
