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

	// Table MultiSelect wywołuje zdarzenie pola na formularzu rodzica.
	usterki(frm) {
		ustaw_kategorie_glowna(frm);
	},
});

// Lustro logiki serwerowej (Naprawa.ustaw_kategorie_glowna): kategoria główna =
// dominująca kategoria wśród wybranych usterek. Tu robimy to na żywo w formularzu,
// żeby recepcja widziała podpowiedź od razu po wskazaniu usterek, a nie dopiero po
// zapisie. Uzupełniamy automatycznie, ale nie nadpisujemy ręcznej korekty — pole
// aktualizujemy tylko, gdy jest puste albo trzyma poprzednią auto-wartość.
async function ustaw_kategorie_glowna(frm) {
	const usterki = (frm.doc.usterki || []).map((u) => u.usterka).filter(Boolean);
	if (!usterki.length) return;

	const kategorie = await Promise.all(
		usterki.map((u) =>
			frappe.db.get_value("Usterka", u, "kategoria").then((r) => (r.message || {}).kategoria),
		),
	);

	const licznik = {};
	let dominujaca = null;
	let max = 0;
	for (const k of kategorie.filter(Boolean)) {
		licznik[k] = (licznik[k] || 0) + 1;
		if (licznik[k] > max) {
			max = licznik[k];
			dominujaca = k;
		}
	}
	if (!dominujaca) return;

	if (!frm.doc.kategoria_glowna || frm.doc.kategoria_glowna === frm.__kategoria_auto) {
		frm.__kategoria_auto = dominujaca;
		frm.set_value("kategoria_glowna", dominujaca);
	}
}
