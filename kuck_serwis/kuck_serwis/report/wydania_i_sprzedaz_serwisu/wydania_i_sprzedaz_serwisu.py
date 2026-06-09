# Copyright (c) 2026, Kuck and contributors
# For license information, please see license.txt

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, getdate

WEEKDAYS = ["Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek", "Sobota", "Niedziela"]


def execute(filters=None):
	filters = frappe._dict(filters or {})
	rows = _get_rows(filters)

	dni = filters.get("dni_tygodnia") or []
	grupowanie = filters.get("grupowanie") or "Miesiąc"

	brak = _("(brak kategorii)")
	agg = defaultdict(lambda: {"liczba": 0, "suma": 0.0})
	for r in rows:
		d = getdate(r.data_wydania)
		if dni and WEEKDAYS[d.weekday()] not in dni:
			continue
		# Rozbicie po okresie ORAZ głównej kategorii naprawy. Dzięki temu, że naprawa ma jedną
		# kategorię główną, kwoty nie dublują się — suma kategorii w okresie = obrót okresu.
		key = (_period_key(d, grupowanie), r.kategoria_glowna or brak)
		agg[key]["liczba"] += 1
		agg[key]["suma"] += flt(r.kwota_odbioru)

	data = [
		{"okres": okres, "kategoria": kategoria, "liczba": v["liczba"], "suma": v["suma"]}
		for (okres, kategoria), v in sorted(agg.items())
	]
	return _columns(), data


def _get_rows(filters):
	conditions = ["status = 'Wydano'", "data_wydania is not null"]
	values = {}
	if filters.get("od_daty"):
		conditions.append("data_wydania >= %(od_daty)s")
		values["od_daty"] = filters.od_daty
	if filters.get("do_daty"):
		conditions.append("data_wydania <= %(do_daty)s")
		values["do_daty"] = filters.do_daty
	if filters.get("rodzaj_naprawy"):
		conditions.append("rodzaj_naprawy = %(rodzaj_naprawy)s")
		values["rodzaj_naprawy"] = filters.rodzaj_naprawy
	if filters.get("marka"):
		conditions.append("marka = %(marka)s")
		values["marka"] = filters.marka
	if filters.get("kategoria"):
		conditions.append("kategoria_glowna = %(kategoria)s")
		values["kategoria"] = filters.kategoria

	where = " and ".join(conditions)
	return frappe.db.sql(
		f"select data_wydania, kwota_odbioru, kategoria_glowna from `tabNaprawa` where {where}",  # nosec
		values,
		as_dict=True,
	)


def _period_key(d, grupowanie):
	if grupowanie == "Dzień":
		return d.strftime("%Y-%m-%d")
	if grupowanie == "Tydzień":
		iso = d.isocalendar()
		return f"{iso[0]}-T{iso[1]:02d}"
	if grupowanie == "Kwartał":
		return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
	if grupowanie == "Rok":
		return f"{d.year}"
	return f"{d.year}-{d.month:02d}"  # Miesiąc (domyślnie)


def _columns():
	return [
		{"label": _("Okres"), "fieldname": "okres", "fieldtype": "Data", "width": 140},
		{
			"label": _("Kategoria napraw"),
			"fieldname": "kategoria",
			"fieldtype": "Data",
			"width": 200,
		},
		{"label": _("Liczba napraw"), "fieldname": "liczba", "fieldtype": "Int", "width": 130},
		{
			"label": _("Suma wydań (zł)"),
			"fieldname": "suma",
			"fieldtype": "Currency",
			"options": "PLN",
			"width": 160,
		},
	]
