# Copyright (c) 2026, Kuck and contributors
# For license information, please see license.txt
"""Idempotentna konfiguracja modułu serwisu — uruchamiana po instalacji aplikacji.

Tworzy rolę Serwis, ustawia walutę PLN oraz buduje Workflow naprawy. Funkcje są
idempotentne, więc można je bezpiecznie wywołać ponownie (po migracji/aktualizacji).
"""

import frappe

REPAIR_STATE = "W naprawie"

WORKFLOW_STATES = [
	"Przyjęto",
	"Diagnoza",
	"Oczekuje na akceptację",
	REPAIR_STATE,
	"Oczekiwanie na część",
	"Gotowe do odbioru",
	"Wydano",
	"Anulowano",
]

# (od_stanu, akcja, do_stanu)
WORKFLOW_TRANSITIONS = [
	("Przyjęto", "Rozpocznij diagnozę", "Diagnoza"),
	("Przyjęto", "Przyjmij do naprawy", REPAIR_STATE),
	("Przyjęto", "Anuluj", "Anulowano"),
	("Diagnoza", "Wyślij wycenę", "Oczekuje na akceptację"),
	("Diagnoza", "Przyjmij do naprawy", REPAIR_STATE),
	("Diagnoza", "Anuluj", "Anulowano"),
	("Oczekuje na akceptację", "Akceptacja klienta", REPAIR_STATE),
	("Oczekuje na akceptację", "Klient rezygnuje", "Anulowano"),
	(REPAIR_STATE, "Czekaj na część", "Oczekiwanie na część"),
	(REPAIR_STATE, "Zakończ naprawę", "Gotowe do odbioru"),
	("Oczekiwanie na część", "Część dostępna", REPAIR_STATE),
	("Gotowe do odbioru", "Wróć do naprawy", REPAIR_STATE),
	("Gotowe do odbioru", "Wydaj zegarek", "Wydano"),
]

ROLE = "Serwis"

# Rola Serwis musi sięgać do danych ERPNext, których ERPNext domyślnie jej nie udostępnia.
# Klient naprawy to Customer, jego dane kontaktowe żyją w Contact, a adresy (wysyłka) w Address.
# read/write/create wystarcza recepcji — bez delete (nie kasujemy kartotek klientów). Doctypy
# słownikowe (Customer Group / Territory / Country) dostają samo read, by dało się je wskazać
# w polach Link przy zakładaniu klienta/adresu.
EXTERNAL_PERMISSIONS = {
	"Customer": ("read", "write", "create"),
	"Contact": ("read", "write", "create"),
	"Address": ("read", "write", "create"),
	"Customer Group": ("read",),
	"Territory": ("read",),
	"Country": ("read",),
}


def after_install():
	setup_all()
	seed_slowniki()


def setup_all():
	create_role()
	grant_external_access()
	set_currency_pln()
	set_language_pl()
	create_workflow()
	create_notifications()
	frappe.db.commit()


def seed_slowniki():
	"""Wstępnie wypełnia słowniki (kategorie/usterki/marki) danymi z arkuszy klienta.

	Wywoływane TYLKO przy instalacji (after_install), świadomie poza setup_all — setup_all biegnie
	też po każdej migracji, a seed ma być jednorazowy: warsztat zarządza słownikami sam, więc nie
	przywracamy skasowanych/zmienionych pozycji przy kolejnych `migrate`.
	"""
	from kuck_serwis import seed

	seed.seed_all()
	frappe.db.commit()


def create_role():
	if not frappe.db.exists("Role", ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(
			ignore_if_duplicate=True
		)


def grant_external_access():
	"""Nadaje roli Serwis dostęp do doctypów ERPNext potrzebnych recepcji (Customer i powiązane).

	ERPNext domyślnie nie daje tej roli żadnego dostępu, więc dokładamy Custom DocPerm dla
	każdego wpisu z EXTERNAL_PERMISSIONS. Idempotentne — add_permission nie duplikuje
	istniejącego wpisu, a update_permission_property tylko ustawia flagi.
	"""
	from frappe.permissions import add_permission, update_permission_property

	for doctype, ptypes in EXTERNAL_PERMISSIONS.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		add_permission(doctype, ROLE, 0)
		for ptype in ptypes:
			update_permission_property(doctype, ROLE, 0, ptype, 1)


def set_currency_pln():
	if frappe.db.exists("Currency", "PLN"):
		frappe.db.set_value("Currency", "PLN", "enabled", 1)
	if not frappe.db.get_single_value("System Settings", "currency"):
		frappe.db.set_single_value("System Settings", "currency", "PLN")


def set_language_pl():
	"""Serwis nie zna angielskiego — cały Desk ma być po polsku.

	Frappe ma komplet tłumaczeń `pl`; ustawienie domyślnego języka systemu sprawia,
	że standardowe komunikaty (przyciski, walidacje, listy) ładują się po polsku.
	"""
	frappe.db.set_single_value("System Settings", "language", "pl")


def _ensure_workflow_masters():
	"""Workflow State i Workflow Action Master to słowniki — muszą istnieć zanim podepniemy je w Workflow."""
	for state in WORKFLOW_STATES:
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc(
				{"doctype": "Workflow State", "workflow_state_name": state}
			).insert(ignore_if_duplicate=True)
	actions = {action for _from, action, _to in WORKFLOW_TRANSITIONS}
	for action in actions:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc(
				{"doctype": "Workflow Action Master", "workflow_action_name": action}
			).insert(ignore_if_duplicate=True)


def create_workflow():
	create_role()
	_ensure_workflow_masters()
	workflow_name = "Serwis Naprawa"

	doc = (
		frappe.get_doc("Workflow", workflow_name)
		if frappe.db.exists("Workflow", workflow_name)
		else frappe.new_doc("Workflow")
	)
	doc.workflow_name = workflow_name
	doc.document_type = "Naprawa"
	doc.workflow_state_field = "status"
	doc.is_active = 1
	doc.send_email_alert = 0
	doc.override_status = 0

	doc.set("states", [])
	for state in WORKFLOW_STATES:
		doc.append(
			"states",
			{"state": state, "doc_status": "0", "allow_edit": ROLE},
		)

	doc.set("transitions", [])
	for from_state, action, to_state in WORKFLOW_TRANSITIONS:
		doc.append(
			"transitions",
			{
				"state": from_state,
				"action": action,
				"next_state": to_state,
				"allowed": ROLE,
				"allow_self_approval": 1,
			},
		)

	doc.save(ignore_permissions=True)


# (nazwa, temat, kanał, status wyzwalający, pole z odbiorcą, treść Jinja)
NOTIFICATIONS = [
	(
		"Naprawa - gotowa do odbioru (E-mail)",
		"Twój zegarek jest gotowy do odbioru — {{ doc.name }}",
		"Email",
		"Gotowe do odbioru",
		"klient_email",
		"Dzień dobry,\n\n"
		"Naprawa **{{ doc.name }}** została zakończona — zegarek "
		"{{ doc.marka or '' }} {{ doc.model_zegarka or '' }} jest gotowy do odbioru.\n"
		"{% if doc.kwota_odbioru %}Kwota do zapłaty: {{ doc.kwota_odbioru }} zł.\n{% endif %}"
		"\nZapraszamy,\nSerwis Kuck",
	),
	(
		"Naprawa - gotowa do odbioru (SMS)",
		"Zegarek gotowy do odbioru — {{ doc.name }}",
		"SMS",
		"Gotowe do odbioru",
		"klient_telefon",
		"Serwis Kuck: naprawa {{ doc.name }} gotowa do odbioru. Zapraszamy.",
	),
	(
		"Naprawa - wycena do akceptacji (E-mail)",
		"Wycena naprawy {{ doc.name }} — prosimy o akceptację",
		"Email",
		"Oczekuje na akceptację",
		"klient_email",
		"Dzień dobry,\n\n"
		"Dla naprawy {{ doc.name }} przygotowaliśmy orientacyjną wycenę: "
		"{{ doc.orientacyjna_wycena }} zł.\n"
		"Prosimy o kontakt i akceptację, abyśmy mogli rozpocząć naprawę.\n\n"
		"Pozdrawiamy,\nSerwis Kuck",
	),
	(
		"Naprawa - wycena do akceptacji (SMS)",
		"Wycena naprawy {{ doc.name }}",
		"SMS",
		"Oczekuje na akceptację",
		"klient_telefon",
		"Serwis Kuck: wycena naprawy {{ doc.name }}: {{ doc.orientacyjna_wycena }} zl. Prosimy o akceptacje.",
	),
]


def create_notifications():
	for name, subject, channel, status_value, receiver_field, message in NOTIFICATIONS:
		condition = f'doc.status == "{status_value}" and not doc.nie_powiadamiaj_klienta'
		if frappe.db.exists("Notification", name):
			doc = frappe.get_doc("Notification", name)
		else:
			doc = frappe.new_doc("Notification")
			doc.name = name

		doc.subject = subject
		doc.channel = channel
		doc.document_type = "Naprawa"
		doc.event = "Value Change"
		doc.value_changed = "status"
		doc.condition = condition
		doc.message = message
		doc.enabled = 1
		doc.send_system_notification = 0

		doc.set("recipients", [{"receiver_by_document_field": receiver_field}])
		doc.save(ignore_permissions=True)
