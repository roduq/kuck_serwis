from __future__ import annotations

import frappe

from kuck_serwis.repair_intake import requester_prefill
from kuck_serwis.repair_intake_contract import PRIVACY_URL
from kuck_serwis.repair_intake_security import issue_csrf_token

sitemap = 1

CSP = "; ".join(
	(
		"default-src 'self'",
		"base-uri 'self'",
		"object-src 'none'",
		"frame-ancestors 'none'",
		"form-action 'self'",
		"script-src 'self' 'unsafe-inline'",
		"style-src 'self' 'unsafe-inline'",
		"img-src 'self' data: blob:",
		"font-src 'self' data:",
		"connect-src 'self'",
		"media-src 'self'",
		"worker-src 'self' blob:",
		"manifest-src 'self'",
		"upgrade-insecure-requests",
	)
)


def get_context(context):
	frappe.local.response_headers.set("Content-Security-Policy", CSP)
	context.no_cache = 1
	context.no_breadcrumbs = True
	context.title = "Zgłoś naprawę zegarka"
	context.description = "Przekaż serwisowi Kuck dane zegarka i opis usterki."
	context.csrf_token = issue_csrf_token()
	context.privacy_url = PRIVACY_URL
	context.prefill = requester_prefill()
	return context
