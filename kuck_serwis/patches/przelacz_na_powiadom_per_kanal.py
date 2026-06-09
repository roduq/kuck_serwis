import frappe

DOCTYPES = ("Naprawa", "Przyjecie Zbiorcze")
STARE_POLE = "nie_powiadamiaj_klienta"


def execute():
	"""Zamiana jednego negatywnego pola (nie_powiadamiaj_klienta) na dwa pozytywne
	(powiadom_sms / powiadom_email).

	Decyzją klienta nowe przełączniki są domyślnie WYŁĄCZONE, więc istniejące rekordy nie
	wymagają przepisywania wartości: nowe kolumny startują z 0 (= nie powiadamiamy), co jest
	spójne także z tymi, którzy mieli nie_powiadamiaj_klienta = 1. Po synchronizacji doctype
	stara kolumna zostaje osierocona — usuwamy ją, by nie myliła i nie ciążyła schematowi.
	"""
	for doctype in DOCTYPES:
		if STARE_POLE in frappe.db.get_table_columns(doctype):
			frappe.db.sql_ddl(f"alter table `tab{doctype}` drop column `{STARE_POLE}`")
