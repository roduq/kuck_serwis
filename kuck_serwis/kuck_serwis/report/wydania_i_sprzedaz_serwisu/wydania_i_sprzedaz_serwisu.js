// Copyright (c) 2026, Kuck and contributors
// For license information, please see license.txt

frappe.query_reports["Wydania i sprzedaz serwisu"] = {
	filters: [
		{
			fieldname: "od_daty",
			label: __("Od daty"),
			fieldtype: "Date",
			default: frappe.datetime.year_start(),
			reqd: 1,
		},
		{
			fieldname: "do_daty",
			label: __("Do daty"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "grupowanie",
			label: __("Grupowanie"),
			fieldtype: "Select",
			options: ["Dzień", "Tydzień", "Miesiąc", "Kwartał", "Rok"].join("\n"),
			default: "Miesiąc",
		},
		{
			fieldname: "rodzaj_naprawy",
			label: __("Rodzaj naprawy"),
			fieldtype: "Select",
			options: ["", "Gwarancja", "Naprawa długa", "Naprawa krótka", "Reklamacja"].join("\n"),
		},
		{
			fieldname: "marka",
			label: __("Marka"),
			fieldtype: "Link",
			options: "Marka Zegarka",
		},
		{
			fieldname: "kategoria",
			label: __("Kategoria napraw"),
			fieldtype: "Link",
			options: "Kategoria Napraw",
		},
		{
			fieldname: "dni_tygodnia",
			label: __("Dni tygodnia"),
			fieldtype: "MultiSelectList",
			get_data: function () {
				return [
					"Poniedziałek",
					"Wtorek",
					"Środa",
					"Czwartek",
					"Piątek",
					"Sobota",
					"Niedziela",
				].map((d) => ({ value: d, description: "" }));
			},
		},
	],
};
