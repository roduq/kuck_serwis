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


def after_install():
	setup_all()


def setup_all():
	create_role()
	set_currency_pln()
	create_workflow()
	frappe.db.commit()


def create_role():
	if not frappe.db.exists("Role", ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(
			ignore_if_duplicate=True
		)


def set_currency_pln():
	if frappe.db.exists("Currency", "PLN"):
		frappe.db.set_value("Currency", "PLN", "enabled", 1)
	if not frappe.db.get_single_value("System Settings", "currency"):
		frappe.db.set_single_value("System Settings", "currency", "PLN")


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
