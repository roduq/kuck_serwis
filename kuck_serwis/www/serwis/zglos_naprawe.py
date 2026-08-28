from __future__ import annotations

import frappe

from kuck_serwis.repair_intake import requester_prefill
from kuck_serwis.repair_intake_security import issue_csrf_token

sitemap = 1


def get_context(context):
	context.no_cache = 1
	context.no_breadcrumbs = True
	context.title = "Zgłoś naprawę zegarka"
	context.description = "Przekaż serwisowi Kuck dane zegarka i opis usterki."
	context.csrf_token = issue_csrf_token()
	context.prefill = requester_prefill()
	return context
