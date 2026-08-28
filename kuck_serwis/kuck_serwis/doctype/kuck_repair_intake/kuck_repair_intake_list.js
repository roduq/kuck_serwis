frappe.listview_settings["Kuck Repair Intake"] = {
	add_fields: ["status"],
	filters: [["status", "=", "Nowe"]],
	get_indicator(doc) {
		const colors = { Nowe: "orange", Przyjęte: "green", Odrzucone: "red" };
		return [__(doc.status), colors[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
