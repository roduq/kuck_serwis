# Copyright (c) 2026, Kuck and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today


class Naprawa(Document):
	def validate(self):
		self.set_akceptacja_date()
		self.guard_w_naprawie()
		self.set_data_wydania()

	def set_akceptacja_date(self):
		"""Akceptacja może nastąpić na każdym etapie (także z góry przy przyjęciu)."""
		if self.klient_zaakceptowal and not self.data_akceptacji:
			self.data_akceptacji = today()

	def guard_w_naprawie(self):
		"""Naprawa nie rusza bez zgody klienta — niezależnie od tego, czy była osobna wycena."""
		if self.status == "W naprawie" and not self.klient_zaakceptowal:
			frappe.throw(
				_("Nie można rozpocząć naprawy bez akceptacji klienta. Zaznacz „Klient zaakceptował naprawę”.")
			)

	def set_data_wydania(self):
		if self.status == "Wydano" and not self.data_wydania:
			self.data_wydania = today()
