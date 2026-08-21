"""Fail-closed metadata-only repair-photo contract for ``kuck_shop``.

This module deliberately has no whitelisted method and never returns a File
identity, URL, path, MIME type, description, or content bytes.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from typing import Final

import frappe

from kuck_serwis.public_contract import v1 as repair_contract
from kuck_serwis.repair_photo_evidence_store import (
	RepairPhotoEvidenceStoreCode,
	RepairPhotoEvidenceStoreError,
	read_scoped_repair_photo_evidence,
	resolve_actor_scoped_repair_access,
)
from kuck_serwis.repair_photo_metadata import (
	RepairPhotoMetadataError,
	plan_repair_photo_metadata,
)

CONTRACT_NAME: Final = "kuck-serwis-repair-photo/v1"
SCHEMA_REVISION: Final = 1
METADATA_SCHEMA: Final = "repair-photo-metadata/v1"
ACCOUNT_PHOTO_METADATA_READ: Final = "account-photo-metadata-read"
ROLLOUT_FLAG: Final = "enable_kuck_serwis_account_photo_metadata_read"
PUBLIC_ID_PATTERN: Final = re.compile(r"^rpr_[A-Za-z0-9_-]{32}$")
_LOOKUP_SENTINEL: Final = "rpr_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
_AUDIT_EVENT_NAME: Final = "kuck_serwis.repair_photo.audit.v1"
_AUDIT_KEY_CONTEXT: Final = b"kuck-serwis/repair-photo/v1/audit"
_METADATA_POLICY_READY: Final = True


PublicContractError = repair_contract.PublicContractError


def get_capabilities() -> dict[str, object]:
	features = [ACCOUNT_PHOTO_METADATA_READ] if _metadata_read_enabled() else []
	return {"contract": CONTRACT_NAME, "schema_revision": SCHEMA_REVISION, "features": features}


def get_repair_photo_metadata_for_current_user(repair_id) -> dict[str, object]:
	"""Return count and canonical source positions after durable audit ACK."""

	started_at = time.perf_counter_ns()
	correlation_id = f"corr_{secrets.token_urlsafe(18)}"
	sink = repair_contract._get_audit_sink()
	audit_key = _audit_hmac_key()
	if sink is None or audit_key is None:
		raise PublicContractError("DEPENDENCY_UNAVAILABLE", "Repair service is temporarily unavailable.")

	result = None
	pending_error = None
	trusted_code = "OK"
	try:
		result = _get_repair_photo_metadata_for_current_user(repair_id)
	except PublicContractError as error:
		pending_error = error
		trusted_code = (
			error.code if error.code in {"AUTH_REQUIRED", "NOT_FOUND"} else "DEPENDENCY_UNAVAILABLE"
		)
	except RepairPhotoEvidenceStoreError as error:
		if error.code in {
			RepairPhotoEvidenceStoreCode.SCOPED_REPAIR_NOT_FOUND,
			RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE,
		}:
			pending_error = PublicContractError("NOT_FOUND", "Repair was not found.")
			trusted_code = "NOT_FOUND"
		else:
			pending_error = PublicContractError(
				"DEPENDENCY_UNAVAILABLE", "Repair service is temporarily unavailable."
			)
			trusted_code = "DEPENDENCY_UNAVAILABLE"
	except RepairPhotoMetadataError:
		pending_error = PublicContractError(
			"DEPENDENCY_UNAVAILABLE", "Repair service is temporarily unavailable."
		)
		trusted_code = "INTERNAL_ERROR"
	except Exception:
		pending_error = PublicContractError(
			"DEPENDENCY_UNAVAILABLE", "Repair service is temporarily unavailable."
		)
		trusted_code = "INTERNAL_ERROR"

	event = {
		"event": _AUDIT_EVENT_NAME,
		"contract": CONTRACT_NAME,
		"schema_revision": SCHEMA_REVISION,
		"correlation_id": correlation_id,
		"operation": "photo_metadata_get",
		"outcome": "success"
		if trusted_code == "OK"
		else ("deny" if trusted_code in {"AUTH_REQUIRED", "NOT_FOUND"} else "error"),
		"actor_class": repair_contract._actor_class(),
		"actor_hash": _audit_hash(audit_key, "actor", repair_contract._session_user()),
		"repair_handle_hash": _audit_hash(audit_key, "repair", repair_id if type(repair_id) is str else ""),
		"result_code": trusted_code,
		"count": result["photo_count"] if trusted_code == "OK" else 0,
		"latency_ms": max(0, (time.perf_counter_ns() - started_at) // 1_000_000),
	}
	repair_contract._emit_audit_event(sink, event)
	if pending_error is not None:
		raise pending_error
	return result


def _get_repair_photo_metadata_for_current_user(repair_id) -> dict[str, object]:
	_require_metadata_read()
	actor = repair_contract._session_user()
	if not actor or actor == "Guest":
		raise PublicContractError("AUTH_REQUIRED", "Authentication is required.")
	state = frappe.db.get_value("User", actor, ["enabled", "user_type"], as_dict=True)
	if not state or state.enabled != 1 or state.user_type != "Website User":
		raise PublicContractError("AUTH_REQUIRED", "Authentication is required.")
	lookup_id = (
		repair_id if type(repair_id) is str and PUBLIC_ID_PATTERN.fullmatch(repair_id) else _LOOKUP_SENTINEL
	)
	access = resolve_actor_scoped_repair_access(repair_id=lookup_id, actor_identity=actor)
	evidence = read_scoped_repair_photo_evidence(access)
	items = plan_repair_photo_metadata(actor_scope_confirmed=True, repair_id=lookup_id, evidence=evidence)
	return {
		"schema": METADATA_SCHEMA,
		"repair_id": lookup_id,
		"photo_count": len(items),
		"items": [{"position": item.position, "state": item.state.value.lower()} for item in items],
	}


def _metadata_read_enabled() -> bool:
	if frappe.conf.get(ROLLOUT_FLAG) is not True:
		return False
	try:
		return (
			_METADATA_POLICY_READY
			and repair_contract.ACCOUNT_READ in repair_contract.get_capabilities()["features"]
			and repair_contract._get_audit_sink() is not None
			and _audit_hmac_key() is not None
		)
	except Exception:
		return False


def _require_metadata_read() -> None:
	if not _metadata_read_enabled():
		raise PublicContractError("DEPENDENCY_UNAVAILABLE", "Repair service is temporarily unavailable.")


def _audit_hmac_key() -> bytes | None:
	site_key = repair_contract._site_key()
	if site_key is None:
		return None
	return hmac.new(site_key, _AUDIT_KEY_CONTEXT, hashlib.sha256).digest()


def _audit_hash(key: bytes, domain: str, value: str) -> str:
	return hmac.new(key, f"{domain}\0{value}".encode(), hashlib.sha256).hexdigest()
