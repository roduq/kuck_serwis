frappe.listview_settings["Przyjecie Zbiorcze"] = {
	add_fields: ["status"],
	get_indicator: function (doc) {
		const colors = {
			"Robocze": "orange",
			"Naprawy utworzone": "green",
		};
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
};
