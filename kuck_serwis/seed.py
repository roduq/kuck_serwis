# Copyright (c) 2026, Kuck and contributors
# For license information, please see license.txt
"""Seed słowników serwisu — kategorie napraw, usterki (rodzaje napraw) i marki zegarków.

Dane pochodzą z arkuszy klienta („50 Usterek Zegarków" + „Zestawienie Porównawcze" marek).
Funkcje są idempotentne (insert-if-not-exists), więc bezpiecznie wykonują się ponownie po
każdej migracji. Uzupełniają tylko brakujące rekordy — nie nadpisują ani nie kasują tego, co
warsztat sam dodał/zmienił w słownikach.
"""

import frappe

# Kategoria napraw -> lista usterek (rodzajów napraw) w tej kategorii.
# Kolejność zachowana jak w arkuszu klienta.
USTERKI_WG_KATEGORII = {
	"Zasilanie i elektronika (kwarcowe)": [
		"Rozładowana bateria/akumulator",
		"Wylanie baterii",
		"Zaśniedziałe styki baterii",
		"Uszkodzenie cewki",
		"Uszkodzenie rezonatora kwarcowego",
		"Zanieczyszczenie mechanizmu",
		"Szybkie rozładowywanie baterii",
		"Odwrócona polaryzacja",
		"Problem z ładowaniem Solar / Eco-Drive",
		"Awaria układu Kinetic",
	],
	"Mechanizm (zegarki mechaniczne i automatyczne)": [
		"Naprawa główna mechanizmu",
		"Zerwana sprężyna napędowa",
		"Sklejony włos sprężyny balansowej",
		"Złamana oś balansu",
		"Zużycie czopów i łożysk",
		"Wyschnięcie smarowania",
		"Uszkodzenie wahnika / rotora",
		"Awaria sprzęgła jednokierunkowego / rewersów",
		"Zablokowanie przekładni chodu",
		"Uszkodzenie wychwytu",
		"Uszkodzenie kół mechanizmu",
	],
	"Koperta, koronka i szczelność": [
		"Urwana lub skrzywiona koronka",
		"Złamany wałek naciągowy",
		"Zużycie uszczelek",
		"Zaparowane szkło od wewnątrz",
		"Zalanie mechanizmu wodą",
		"Pęknięte lub mocno porysowane szkło",
		"Wypadnięcie szkła z uszczelki",
		"Zablokowany pierścień obrotowy / bezel",
		"Uszkodzenie gwintu tubusu lub koronki",
		"Głębokie rysy i wgniecenia koperty",
	],
	"Tarcza i wskazówki": [
		"Poluzowanie lub odpadnięcie wskazówki",
		"Wskazówki ocierające o siebie",
		"Odpadnięcie indeksu/ramki lub cyfry/indeksu z tarczy",
		"Zerwanie mocowania tarczy",
		"Przebarwienie lub łuszczenie się tarczy",
		"Wykruszenie się masy luminescencyjnej",
		"Przesunięcie tarcz chronografu",
		"Zablokowanie tarczy datownika",
		"Zmatowienie tarczy",
	],
	"Bransolety, paski i ergonomia": [
		"Pęknięty lub wygięty teleskop",
		"Zużyty, przetarty lub pęknięty pasek",
		"Rozciągnięcie bransolety",
		"Uszkodzone zapięcie bransolety",
		"Wypadające piny / szpilki ogniw",
		"Parcenie i pękanie pasków silikonowych/kauczukowych",
		"Uszkodzenie teleskopu w klamerce paska",
		"Zerwana lub zgubiona szlufka w pasku",
		"Silne zabrudzenie bransolety",
		"Konieczność dopasowania rozmiaru",
		"Prucie bransolety mesh",
	],
}

# Marki zegarków obsługiwane przez warsztat (zestawienie klienta).
MARKI = [
	"Aerowatch",
	"Alpina",
	"Armani Exchange",
	"Atlantic",
	"Aviator",
	"Ball",
	"Balticus",
	"Beco",
	"Bering",
	"Bonflair",
	"Braun",
	"Breitling",
	"Bulova",
	"Błonie",
	"Calvin Klein",
	"Caravelle",
	"Cartier",
	"Casio",
	"Casio Vintage",
	"Certina",
	"Cerutti",
	"Citizen",
	"Citrea",
	"Cluse",
	"Como Milano",
	"Continental",
	"D1 Milano",
	"David Daper",
	"Davosa",
	"Di-Modell",
	"Diesel",
	"Doxa",
	"Doxa Sub",
	"Edifice",
	"Emporio Armani",
	"Epos",
	"Esprit",
	"Exaequo",
	"Festina",
	"Fossil",
	"Frederique Constant",
	"G-Shock",
	"GC",
	"Gant",
	"Garett",
	"Garmin",
	"Glycine",
	"Grovana",
	"Gucci",
	"Guess",
	"Herbelin",
	"Hirsch",
	"Hornavan",
	"IWC",
	"Inventic",
	"Invicta",
	"Jacques Du Manoir",
	"Jacques Lemans",
	"Knock Nocky",
	"Koenig",
	"Kronaby",
	"Lacoste",
	"Leanschi",
	"Liu Jo",
	"Longines",
	"Lorus",
	"Lotus",
	"Luminox",
	"Mark Maddox",
	"Maurice Lacroix",
	"MeisterSinger",
	"Michael Kors",
	"Morellato",
	"Mudita",
	"Nodo",
	"Omega",
	"Orient",
	"Orient Star",
	"Oris",
	"Out of Order",
	"Paul Hewitt",
	"Police",
	"Polpora",
	"Pulsar",
	"Q&Q",
	"Rado",
	"Roamer",
	"Rotary",
	"SEVENFRIDAY",
	"Schaumburg",
	"Seiko",
	"Skagen",
	"Solar Aqua",
	"Spinnaker",
	"Squale",
	"Suunto",
	"TAG Heuer",
	"Ted Baker London",
	"Thom Olson",
	"Timberland",
	"Timex",
	"Tissot",
	"Tommy Hilfiger",
	"Traser",
	"Vector Smart",
	"Venezianico",
	"Viceroy",
	"Vostok Europe",
	"Wolf",
	"Xicorr",
	"adidas Originals",
	"Rolex",
]


def seed_all():
	"""Uzupełnia słowniki danymi z arkuszy klienta. Idempotentne."""
	seed_kategorie_i_usterki()
	seed_marki()


def seed_kategorie_i_usterki():
	"""Tworzy kategorie napraw, a następnie usterki przypisane do swoich kategorii.

	Kategoria musi istnieć przed usterką (wymagane pole Link). Istniejącej usterce bez
	kategorii dopisujemy ją — pole stało się wymagane, więc nie zostawiamy luk.
	"""
	for kategoria, usterki in USTERKI_WG_KATEGORII.items():
		if not frappe.db.exists("Kategoria Napraw", kategoria):
			frappe.get_doc(
				{"doctype": "Kategoria Napraw", "nazwa": kategoria}
			).insert(ignore_if_duplicate=True)

		for usterka in usterki:
			if not frappe.db.exists("Usterka", usterka):
				frappe.get_doc(
					{"doctype": "Usterka", "nazwa": usterka, "kategoria": kategoria}
				).insert(ignore_if_duplicate=True)
			elif not frappe.db.get_value("Usterka", usterka, "kategoria"):
				frappe.db.set_value("Usterka", usterka, "kategoria", kategoria)


def seed_marki():
	for marka in MARKI:
		if not frappe.db.exists("Marka Zegarka", marka):
			frappe.get_doc(
				{"doctype": "Marka Zegarka", "nazwa": marka}
			).insert(ignore_if_duplicate=True)
