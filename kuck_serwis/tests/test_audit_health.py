from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from kuck_serwis import audit_health
from kuck_serwis.kuck_serwis.doctype.kuck_repair_audit_event import (
	kuck_repair_audit_event as audit_store,
)
from kuck_serwis.public_contract import v1


class _FakeDatabase:
	def __init__(self, rows=None, error=None):
		self.rows = [] if rows is None else rows
		self.error = error
		self.closed = False
		self.calls = []

	def multisql(self, statements, values, as_dict=False):
		self.calls.append((statements, values, as_dict))
		if self.error is not None:
			raise self.error
		return self.rows

	def close(self):
		self.closed = True


def _captured_event_sink(acknowledgement=True, error=None):
	events = []

	def emit(event):
		events.append(dict(event))
		if error is not None:
			raise error
		return acknowledgement

	return SimpleNamespace(emit=emit), events


class TestActiveRepairAuditProbe(IntegrationTestCase):
	def _run_with(self, sink, database):
		with (
			patch.object(v1, "_audit_hmac_key", return_value=bytes([91]) * 32),
			patch.object(audit_store, "DurableRepairAuditSink", return_value=sink),
			patch.object(audit_store, "_new_isolated_database", return_value=database),
			patch.object(audit_health, "_utc_timestamp", return_value="2026-08-14T12:00:00Z"),
			patch.object(audit_health.secrets, "token_urlsafe", return_value="A" * 24),
		):
			return audit_health.run_active_repair_audit_probe()

	def test_success_requires_literal_true_and_an_identical_single_row(self):
		sink, events = _captured_event_sink()
		event_row = {
			"event": v1._AUDIT_EVENT_NAME,
			"contract": v1.CONTRACT_NAME,
			"schema_revision": v1.SCHEMA_REVISION,
			"correlation_id": f"corr_{'A' * 24}",
			"operation": "list",
			"outcome": "success",
			"actor_class": "unknown",
			"actor_hash": v1._audit_hash(bytes([91]) * 32, "actor", "health-probe"),
			"repair_handle_hash": None,
			"result_code": "OK",
			"count": 0,
			"latency_ms": 0,
		}
		database = _FakeDatabase([event_row])

		with patch.object(audit_health.time, "perf_counter_ns", side_effect=[1_000_000, 1_000_000]):
			result = self._run_with(sink, database)

		self.assertEqual(
			result,
			{
				"ok": True,
				"checked_at": "2026-08-14T12:00:00Z",
				"probe_version": "repair-audit-active/v1",
				"codes": ["ACTIVE_CANARY_OK"],
			},
		)
		self.assertEqual(events, [event_row])
		self.assertTrue(database.closed)
		self.assertEqual(database.calls[0][1], (f"corr_{'A' * 24}",))
		self.assertTrue(database.calls[0][2])

	def test_false_ack_and_non_boolean_one_are_fail_closed_without_read(self):
		for acknowledgement in (False, None, 1):
			with self.subTest(acknowledgement=acknowledgement):
				sink, _events = _captured_event_sink(acknowledgement)
				database = _FakeDatabase()
				result = self._run_with(sink, database)
				self.assertFalse(result["ok"])
				self.assertEqual(result["codes"], ["SINK_ACK_INVALID"])
				self.assertEqual(database.calls, [])
				self.assertFalse(database.closed)

	def test_sink_exception_is_sanitized_and_fail_closed(self):
		sink, _events = _captured_event_sink(error=RuntimeError("secret database detail"))
		result = self._run_with(sink, _FakeDatabase())
		self.assertEqual(result["codes"], ["SINK_UNAVAILABLE"])
		self.assertNotIn("secret", repr(result))

	def test_zero_or_two_rows_are_fail_closed(self):
		for rows in ([], [{}, {}]):
			with self.subTest(count=len(rows)):
				sink, _events = _captured_event_sink()
				result = self._run_with(sink, _FakeDatabase(rows))
				self.assertEqual(result["codes"], ["VERIFY_COUNT_MISMATCH"])

	def test_mismatched_row_and_read_exception_are_fail_closed(self):
		sink, _events = _captured_event_sink()
		result = self._run_with(sink, _FakeDatabase([{"event": "wrong"}]))
		self.assertEqual(result["codes"], ["VERIFY_CONTENT_MISMATCH"])

		sink, _events = _captured_event_sink()
		result = self._run_with(sink, _FakeDatabase(error=RuntimeError("private SQL")))
		self.assertEqual(result["codes"], ["VERIFY_UNAVAILABLE"])
		self.assertNotIn("private SQL", repr(result))

	def test_missing_hmac_key_is_fail_closed_before_sink(self):
		with (
			patch.object(v1, "_audit_hmac_key", return_value=None),
			patch.object(audit_store, "DurableRepairAuditSink") as sink,
		):
			result = audit_health.run_active_repair_audit_probe()
		self.assertEqual(result["codes"], ["KEY_UNAVAILABLE"])
		sink.assert_not_called()

	def test_probe_never_commits_the_caller_database(self):
		sink, _events = _captured_event_sink(False)
		with patch.object(frappe.db, "commit") as caller_commit:
			result = self._run_with(sink, _FakeDatabase())
		self.assertFalse(result["ok"])
		caller_commit.assert_not_called()


class TestRealActiveRepairAuditProbe(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.correlation_suffix = frappe.generate_hash(length=24)
		self.correlation_id = f"corr_{self.correlation_suffix}"

	def tearDown(self):
		frappe.db.rollback()
		database = audit_store._new_isolated_database()
		try:
			database.multisql(
				{
					"mariadb": ("DELETE FROM `tabKuck Repair Audit Event` WHERE correlation_id = %s"),
					"postgres": ('DELETE FROM "tabKuck Repair Audit Event" WHERE "correlation_id" = %s'),
					"sqlite": ('DELETE FROM "tabKuck Repair Audit Event" WHERE "correlation_id" = %s'),
				},
				(self.correlation_id,),
			)
			database.commit()
		finally:
			database.close()
		super().tearDown()

	def test_real_sink_commit_is_visible_to_fresh_verification_connection(self):
		with patch.object(audit_health.secrets, "token_urlsafe", return_value=self.correlation_suffix):
			result = audit_health.run_active_repair_audit_probe()

		self.assertTrue(result["ok"])
		self.assertEqual(result["codes"], ["ACTIVE_CANARY_OK"])
		self.assertEqual(set(result), {"ok", "checked_at", "probe_version", "codes"})
		self.assertNotIn(self.correlation_id, repr(result))
		database = audit_store._new_isolated_database()
		try:
			rows = database.multisql(
				{
					"mariadb": (
						"SELECT correlation_id FROM `tabKuck Repair Audit Event` WHERE correlation_id = %s"
					),
					"postgres": (
						'SELECT "correlation_id" FROM "tabKuck Repair Audit Event" '
						'WHERE "correlation_id" = %s'
					),
					"sqlite": (
						'SELECT "correlation_id" FROM "tabKuck Repair Audit Event" '
						'WHERE "correlation_id" = %s'
					),
				},
				(self.correlation_id,),
			)
		finally:
			database.close()
		self.assertEqual(rows, ((self.correlation_id,),))
