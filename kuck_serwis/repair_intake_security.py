"""Request security for anonymous and authenticated repair intake writes."""

from __future__ import annotations

import secrets
from hashlib import sha256
from urllib.parse import urlsplit

import frappe

_ALLOWED_MIME_TYPES = frozenset({"application/json", "application/x-www-form-urlencoded"})
_COOKIE_NAME = "__Host-kuck_repair_intake"
_COOKIE_SECRET_BYTES = 32


def issue_csrf_token() -> str:
	if _current_user() != "Guest":
		return frappe.sessions.get_csrf_token()
	token = _guest_csrf_token(_guest_cookie_secret())
	data = getattr(getattr(frappe, "session", None), "data", None)
	if data is not None:
		data.csrf_token = token
	return token


def request_actor_scope() -> str:
	user = _current_user()
	if user == "Guest":
		raw = _guest_cookie_secret(create=False)
		return sha256(b"kuck.repair-intake.guest.v1\0" + raw.encode("ascii")).hexdigest()
	return sha256(b"kuck.repair-intake.user.v1\0" + user.encode("utf-8")).hexdigest()


def require_write_request() -> None:
	request = getattr(getattr(frappe, "local", None), "request", None)
	if request is None or getattr(request, "method", None) != "POST":
		_fail()
	if getattr(request, "mimetype", None) not in _ALLOWED_MIME_TYPES:
		_fail()
	content_length = getattr(request, "content_length", None)
	if type(content_length) is not int or not 1 <= content_length <= 24_000:
		_fail()
	origin = _origin(getattr(request, "headers", {}).get("Origin"))
	configured = _origin(getattr(getattr(frappe, "conf", None), "get", lambda _key: None)("host_name"))
	if origin is None or configured is None or origin != configured:
		_fail()
	if getattr(request, "headers", {}).get("Sec-Fetch-Site") not in (None, "same-origin"):
		_fail()
	provided = getattr(request, "headers", {}).get("X-Frappe-CSRF-Token")
	if _current_user() == "Guest":
		expected = _guest_csrf_token(_guest_cookie_secret(create=False))
	else:
		expected = getattr(getattr(frappe.session, "data", None), "csrf_token", None)
	if (
		type(provided) is not str
		or type(expected) is not str
		or not secrets.compare_digest(expected, provided)
	):
		_fail()


def _current_user() -> str:
	user = getattr(getattr(frappe, "session", None), "user", None)
	return user if type(user) is str and user else "Guest"


def _guest_cookie_secret(*, create: bool = True) -> str:
	request = getattr(getattr(frappe, "local", None), "request", None)
	cookies = getattr(request, "cookies", None)
	raw = cookies.get(_COOKIE_NAME) if hasattr(cookies, "get") else None
	if (
		type(raw) is str
		and 43 <= len(raw) <= 128
		and raw.isascii()
		and raw.replace("-", "").replace("_", "").isalnum()
	):
		return raw
	if not create:
		_fail()
	raw = secrets.token_urlsafe(_COOKIE_SECRET_BYTES)
	manager = getattr(getattr(frappe, "local", None), "cookie_manager", None)
	if manager is None or not hasattr(manager, "set_cookie"):
		_fail()
	manager.set_cookie(_COOKIE_NAME, raw, secure=True, httponly=True, samesite="Lax")
	return raw


def _guest_csrf_token(raw: str) -> str:
	return sha256(b"kuck.repair-intake.csrf.v1\0" + raw.encode("ascii")).hexdigest()


def _origin(value: object) -> tuple[str, str] | None:
	if type(value) is not str:
		return None
	parsed = urlsplit(value)
	if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in ("", "/"):
		return None
	if parsed.query or parsed.fragment or parsed.username or parsed.password:
		return None
	host = (parsed.hostname or "").casefold()
	if (
		parsed.scheme != "https"
		and host not in {"localhost", "127.0.0.1", "::1"}
		and not host.endswith(".localhost")
	):
		return None
	return parsed.scheme, parsed.netloc.casefold()


def _fail() -> None:
	raise frappe.PermissionError("REPAIR_INTAKE_REQUEST_REJECTED")
