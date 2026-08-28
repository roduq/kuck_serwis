frappe.ui.form.on("Kuck Repair Intake", {
	refresh(frm) {
		if (frm.doc.accepted_repair) {
			frm.add_custom_button(__("Otwórz naprawę"), () =>
				frappe.set_route("Form", "Naprawa", frm.doc.accepted_repair)
			);
		}
		if (frm.is_new() || ["Przyjęte", "Odrzucone"].includes(frm.doc.status)) return;
		frm.add_custom_button(
			__("Utwórz naprawę po przyjęciu"),
			() => {
				if (!frm.doc.customer) {
					frappe.msgprint(
						__(
							"Najpierw wybierz zweryfikowanego klienta (Customer) i zapisz zgłoszenie."
						)
					);
					return;
				}
				frappe.prompt(
					[
						{
							fieldname: "physical_receipt_confirmed",
							fieldtype: "Check",
							label: __("Potwierdzam fizyczne przyjęcie zegarka"),
							reqd: 1,
						},
					],
					(values) =>
						frappe.call({
							method: "kuck_serwis.repair_intake.accept_repair_intake",
							args: {
								intake_name: frm.doc.name,
								expected_modified: frm.doc.modified,
								physical_receipt_confirmed: values.physical_receipt_confirmed,
							},
							freeze: true,
							callback: (response) => {
								if (response.message?.repair) {
									frappe.set_route("Form", "Naprawa", response.message.repair);
								}
							},
						}),
					__("Przyjęcie zegarka"),
					__("Utwórz naprawę")
				);
			},
			__("Obsługa zgłoszenia")
		);
		frm.add_custom_button(
			__("Odrzuć zgłoszenie"),
			() => {
				frappe.prompt(
					[
						{
							fieldname: "reason",
							fieldtype: "Small Text",
							label: __("Powód"),
							reqd: 1,
						},
					],
					(values) =>
						frappe.call({
							method: "kuck_serwis.repair_intake.reject_repair_intake",
							args: {
								intake_name: frm.doc.name,
								expected_modified: frm.doc.modified,
								reason: values.reason,
							},
							callback: () => frm.reload_doc(),
						}),
					__("Odrzucenie zgłoszenia"),
					__("Odrzuć")
				);
			},
			__("Obsługa zgłoszenia")
		);
	},
});
