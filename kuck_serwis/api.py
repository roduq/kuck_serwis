# Copyright (c) 2026, Kuck and contributors
# For license information, please see license.txt
"""Dane na potrzeby Desku — strona „Karta klienta".

Jeden klient = jeden ERPNext Customer. Strona pokazuje recepcji komplet napraw
danego klienta wraz z ich statusem i kluczowymi datami (historia współpracy),
plus skrótowe podsumowanie (ile napraw, ile w toku, gotowe, łączny obrót).
"""

import frappe
from frappe import _

# Statusy, w których naprawa jest „zamknięta" — nie liczą się jako praca w toku.
STATUSY_ZAMKNIETE = ("Wydano", "Anulowano")

# Pola napraw pokazywane na karcie klienta (historia napraw).
POLA_NAPRAWY = (
	"name",
	"status",
	"rodzaj_naprawy",
	"marka",
	"model_zegarka",
	"numer_seryjny",
	"orientacyjna_wycena",
	"kwota_odbioru",
	"data_akceptacji",
	"data_wydania",
	"creation",
)


@frappe.whitelist()
def dane_klienta(klient: str) -> dict:
	"""Zwraca dane klienta, jego naprawy i podsumowanie dla strony „Karta klienta".

	Respektuje uprawnienia użytkownika: wymaga odczytu Customer, a listę napraw
	pobiera przez `get_list` (filtruje po uprawnieniach roli do Naprawy).
	"""
	if not klient:
		return {}

	frappe.has_permission("Customer", doc=klient, throw=True)

	customer = frappe.db.get_value(
		"Customer",
		klient,
		["name", "customer_name", "mobile_no", "email_id", "customer_group", "territory"],
		as_dict=True,
	)
	if not customer:
		frappe.throw(_("Nie znaleziono klienta {0}.").format(frappe.bold(klient)))

	naprawy = frappe.get_list(
		"Naprawa",
		filters={"klient": klient},
		fields=list(POLA_NAPRAWY),
		order_by="creation desc",
		limit_page_length=0,
	)

	return {
		"klient": customer,
		"naprawy": naprawy,
		"statystyki": _statystyki(naprawy),
	}


def _statystyki(naprawy: list) -> dict:
	"""Liczy skrót: ile napraw, ile w toku, ile gotowych do odbioru, łączny obrót (z wydanych)."""
	return {
		"liczba": len(naprawy),
		"w_toku": sum(1 for n in naprawy if n.status not in STATUSY_ZAMKNIETE),
		"gotowe": sum(1 for n in naprawy if n.status == "Gotowe do odbioru"),
		"obrot": sum((n.kwota_odbioru or 0) for n in naprawy if n.status == "Wydano"),
	}
