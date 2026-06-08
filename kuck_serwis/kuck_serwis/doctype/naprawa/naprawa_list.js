frappe.listview_settings["Naprawa"] = {
	add_fields: ["status", "priorytet"],
	get_indicator: function (doc) {
		const colors = {
			"Przyjęto": "orange",
			"Diagnoza": "yellow",
			"Oczekuje na akceptację": "purple",
			"W naprawie": "blue",
			"Oczekiwanie na część": "gray",
			"Gotowe do odbioru": "green",
			"Wydano": "light-gray",
			"Anulowano": "red",
		};
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
};
