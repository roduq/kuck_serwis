from collections import Counter

import frappe


def execute():
	"""Uzupełnia kategorię główną istniejących napraw na podstawie ich usterek.

	Nowe pole `kategoria_glowna` służy rozbiciu sprzedaży po kategoriach w raporcie wydań.
	Dla rekordów sprzed wdrożenia wyliczamy dominującą kategorię z listy usterek (remis →
	pierwsza). Naprawy bez usterek zostają puste i trafią w raporcie do „(brak kategorii)".
	"""
	naprawy = frappe.get_all(
		"Naprawa",
		filters={"kategoria_glowna": ["in", ["", None]]},
		pluck="name",
	)
	for name in naprawy:
		usterki = frappe.get_all("Naprawa Usterka", filters={"parent": name}, pluck="usterka")
		kategorie = [frappe.db.get_value("Usterka", u, "kategoria") for u in usterki if u]
		kategorie = [k for k in kategorie if k]
		if kategorie:
			glowna = Counter(kategorie).most_common(1)[0][0]
			frappe.db.set_value("Naprawa", name, "kategoria_glowna", glowna, update_modified=False)
