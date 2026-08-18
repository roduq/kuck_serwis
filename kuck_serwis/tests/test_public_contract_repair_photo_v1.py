from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from kuck_serwis.kuck_serwis.doctype.kuck_repair_audit_event import (
	kuck_repair_audit_event as audit_store,
)
from kuck_serwis.public_contract import repair_photo_v1
from kuck_serwis.repair_photo_evidence_store import (
	RepairPhotoEvidenceStoreCode,
	RepairPhotoEvidenceStoreError,
)
from kuck_serwis.repair_photo_metadata import ScopedRepairPhotoEvidence


class _Sink:
	def __init__(self, acknowledge=True):
		self.acknowledge = acknowledge
		self.events = []

	def emit(self, event):
		self.events.append(dict(event))
		return self.acknowledge


class _State:
	enabled = 1
	user_type = "Website User"


def _evidence(repair_id, *positions):
	return tuple(
		ScopedRepairPhotoEvidence(
			repair_id=repair_id,
			position=position,
			is_private=True,
			exact_attachment=True,
			metadata_only=True,
		)
		for position in positions
	)


class TestRepairPhotoPublicContractV1(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.repair_id = "rpr_" + "B" * 32
		self.sink = _Sink()
		frappe.set_user("Guest")

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def _call(self, evidence=()):
		with (
			patch.object(repair_photo_v1, "_metadata_read_enabled", return_value=True),
			patch.object(repair_photo_v1.repair_contract, "_get_audit_sink", return_value=self.sink),
			patch.object(repair_photo_v1, "_audit_hmac_key", return_value=b"a" * 32),
			patch.object(repair_photo_v1.frappe.db, "get_value", return_value=_State()),
			patch.object(repair_photo_v1, "resolve_actor_scoped_repair_access", return_value=object()),
			patch.object(repair_photo_v1, "read_scoped_repair_photo_evidence", return_value=evidence),
		):
			frappe.local.session.user = "owner@example.test"
			return repair_photo_v1.get_repair_photo_metadata_for_current_user(self.repair_id)

	def test_capability_is_fail_closed_by_default(self):
		self.assertEqual(
			repair_photo_v1.get_capabilities(),
			{
				"contract": "kuck-serwis-repair-photo/v1",
				"schema_revision": 1,
				"features": [],
			},
		)

	def test_projects_only_sorted_positions_and_count(self):
		result = self._call(_evidence(self.repair_id, 9, 2))
		self.assertEqual(
			result,
			{
				"schema": "repair-photo-metadata/v1",
				"repair_id": self.repair_id,
				"photo_count": 2,
				"items": [
					{"position": 2, "state": "metadata_only"},
					{"position": 9, "state": "metadata_only"},
				],
			},
		)
		self.assertEqual(self.sink.events[0]["operation"], "photo_metadata_get")
		self.assertEqual(self.sink.events[0]["count"], 2)
		serialized = repr(result)
		for forbidden in ("/private/files", "File", "mime", "download", "description"):
			self.assertNotIn(forbidden, serialized)

	def test_guest_is_audited_then_denied_without_resolver(self):
		with (
			patch.object(repair_photo_v1, "_metadata_read_enabled", return_value=True),
			patch.object(repair_photo_v1.repair_contract, "_get_audit_sink", return_value=self.sink),
			patch.object(repair_photo_v1, "_audit_hmac_key", return_value=b"a" * 32),
			patch.object(repair_photo_v1, "resolve_actor_scoped_repair_access") as resolver,
		):
			with self.assertRaisesRegex(repair_photo_v1.PublicContractError, "Authentication"):
				repair_photo_v1.get_repair_photo_metadata_for_current_user(self.repair_id)
		resolver.assert_not_called()
		self.assertEqual(self.sink.events[0]["result_code"], "AUTH_REQUIRED")

	def test_missing_and_unsafe_evidence_share_public_not_found(self):
		for code in (
			RepairPhotoEvidenceStoreCode.SCOPED_REPAIR_NOT_FOUND,
			RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE,
		):
			self.sink.events.clear()
			with (
				patch.object(repair_photo_v1, "_metadata_read_enabled", return_value=True),
				patch.object(repair_photo_v1.repair_contract, "_get_audit_sink", return_value=self.sink),
				patch.object(repair_photo_v1, "_audit_hmac_key", return_value=b"a" * 32),
				patch.object(repair_photo_v1.frappe.db, "get_value", return_value=_State()),
				patch.object(
					repair_photo_v1,
					"resolve_actor_scoped_repair_access",
					side_effect=RepairPhotoEvidenceStoreError(code),
				),
			):
				frappe.local.session.user = "owner@example.test"
				with self.assertRaises(repair_photo_v1.PublicContractError) as failure:
					repair_photo_v1.get_repair_photo_metadata_for_current_user(self.repair_id)
			self.assertEqual(failure.exception.code, "NOT_FOUND")
			self.assertEqual(self.sink.events[0]["result_code"], "NOT_FOUND")

	def test_audit_failure_prevents_success_payload(self):
		self.sink.acknowledge = False
		with self.assertRaises(repair_photo_v1.PublicContractError) as failure:
			self._call(_evidence(self.repair_id, 1))
		self.assertEqual(failure.exception.code, "DEPENDENCY_UNAVAILABLE")

	def test_audit_allowlist_accepts_only_matching_photo_contract(self):
		event = {
			"event": "kuck_serwis.repair_photo.audit.v1",
			"contract": "kuck-serwis-repair-photo/v1",
			"schema_revision": 1,
			"correlation_id": "corr_" + "A" * 24,
			"operation": "photo_metadata_get",
			"outcome": "success",
			"actor_class": "website_user",
			"actor_hash": "a" * 64,
			"repair_handle_hash": "b" * 64,
			"result_code": "OK",
			"count": 1,
			"latency_ms": 1,
		}
		self.assertEqual(audit_store._validate_and_normalize_event(event)["operation"], "photo_metadata_get")
		with self.assertRaises(ValueError):
			audit_store._validate_and_normalize_event({**event, "contract": "kuck-serwis/v1"})
