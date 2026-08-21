import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from kuck_serwis.kuck_serwis.doctype.kuck_repair_audit_event import (
	kuck_repair_audit_event as audit_store,
)
from kuck_serwis.kuck_serwis.doctype.naprawa.naprawa import PUBLIC_ID_PATTERN
from kuck_serwis.patches import backfill_naprawa_public_id
from kuck_serwis.public_contract import v1

TEST_CURSOR_KEY = bytes([66]) * 32
TEST_AUDIT_KEY = bytes([67]) * 32


class _CapturingAuditSink:
	def __init__(self, *, acknowledge=True):
		self.acknowledge = acknowledge
		self.events = []

	def emit(self, event):
		self.events.append(dict(event))
		return self.acknowledge


def _make_audit_event(correlation_id=None):
	return {
		"event": "kuck_serwis.public_contract.audit.v1",
		"contract": "kuck-serwis/v1",
		"schema_revision": 1,
		"correlation_id": correlation_id or f"corr_{frappe.generate_hash(length=24)}",
		"operation": "list",
		"outcome": "success",
		"actor_class": "website_user",
		"actor_hash": "a" * 64,
		"repair_handle_hash": None,
		"result_code": "OK",
		"count": 2,
		"latency_ms": 7,
	}


def _make_user(*, user_type="Website User", enabled=1):
	email = f"portal-{frappe.generate_hash(length=10).lower()}@example.test"
	return frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Portal Test",
			"enabled": enabled,
			"user_type": user_type,
			"send_welcome_email": 0,
		}
	).insert(ignore_permissions=True)


def _make_customer(*users):
	doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": "Portal Customer " + frappe.generate_hash(length=8),
			"customer_type": "Individual",
		}
	)
	for user in users:
		doc.append("portal_users", {"user": user.name})
	return doc.insert(ignore_permissions=True)


def _make_repair(customer, **values):
	data = {
		"doctype": "Naprawa",
		"klient": customer.name,
		"status": "Przyjęto",
		"rodzaj_naprawy": "Naprawa krótka",
		"model_zegarka": "Model publiczny",
		"numer_seryjny": "SECRET-SERIAL",
		"opis_naprawy": "Poufna notatka warsztatu",
		"sposob_dostarczenia": "Stacjonarnie",
		"sposob_odbioru": "Stacjonarnie",
	}
	data.update(values)
	return frappe.get_doc(data).insert(ignore_permissions=True)


class TestDurableRepairAuditSink(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.correlation_ids = []

	def tearDown(self):
		# These rows are committed on an isolated connection by design, so the
		# normal test transaction rollback cannot remove them.
		frappe.db.rollback()
		if self.correlation_ids:
			database = audit_store._new_isolated_database()
			try:
				for correlation_id in self.correlation_ids:
					database.multisql(
						{
							"mariadb": ("DELETE FROM `tabKuck Repair Audit Event` WHERE correlation_id = %s"),
							"postgres": (
								'DELETE FROM "tabKuck Repair Audit Event" WHERE "correlation_id" = %s'
							),
							"sqlite": (
								'DELETE FROM "tabKuck Repair Audit Event" WHERE "correlation_id" = %s'
							),
						},
						(correlation_id,),
					)
				database.commit()
			finally:
				database.close()
		super().tearDown()

	def _event(self):
		event = _make_audit_event()
		self.correlation_ids.append(event["correlation_id"])
		return event

	def _stored_rows(self, correlation_id):
		database = audit_store._new_isolated_database()
		try:
			return database.multisql(
				{
					"mariadb": (
						"SELECT event_id, event, contract, schema_revision, correlation_id, "
						"operation, outcome, actor_class, actor_hash, repair_handle_hash, "
						"result_code, count, latency_ms, owner, modified_by "
						"FROM `tabKuck Repair Audit Event` WHERE correlation_id = %s"
					),
					"postgres": (
						'SELECT "event_id", "event", "contract", "schema_revision", '
						'"correlation_id", "operation", "outcome", "actor_class", '
						'"actor_hash", "repair_handle_hash", "result_code", "count", '
						'"latency_ms", "owner", "modified_by" '
						'FROM "tabKuck Repair Audit Event" WHERE "correlation_id" = %s'
					),
					"sqlite": (
						'SELECT "event_id", "event", "contract", "schema_revision", '
						'"correlation_id", "operation", "outcome", "actor_class", '
						'"actor_hash", "repair_handle_hash", "result_code", "count", '
						'"latency_ms", "owner", "modified_by" '
						'FROM "tabKuck Repair Audit Event" WHERE "correlation_id" = %s'
					),
				},
				(correlation_id,),
				as_dict=True,
			)
		finally:
			database.close()

	def test_real_sink_commits_only_allowlisted_sanitized_event(self):
		event = self._event()
		self.assertIs(audit_store.DurableRepairAuditSink().emit(event), True)

		self.assertEqual(frappe.get_meta(audit_store.DOCTYPE).permissions, [])
		self.assertTrue(frappe.db.get_column_index("tabKuck Repair Audit Event", "event_id", unique=True))
		self.assertTrue(
			frappe.db.get_column_index("tabKuck Repair Audit Event", "correlation_id", unique=True)
		)
		rows = self._stored_rows(event["correlation_id"])
		self.assertEqual(len(rows), 1)
		row = rows[0]
		self.assertEqual(row.event_id, event["correlation_id"].replace("corr_", "evt_", 1))
		for fieldname, value in event.items():
			self.assertEqual(row[fieldname], value)
		self.assertEqual(row.owner, audit_store._SYSTEM_ACTOR)
		self.assertEqual(row.modified_by, audit_store._SYSTEM_ACTOR)
		serialized = repr(row)
		for forbidden in ("@", "rpr_", "NAP-", "raw_cursor", "Customer"):
			self.assertNotIn(forbidden, serialized)

	def test_public_contract_returns_only_after_real_sink_commit(self):
		correlation_suffix = frappe.generate_hash(length=24)
		correlation_id = f"corr_{correlation_suffix}"
		self.correlation_ids.append(correlation_id)
		expected = {"items": [], "next_cursor": None}
		with (
			patch.object(v1.secrets, "token_urlsafe", return_value=correlation_suffix),
			patch.object(v1, "_list_repairs_for_current_user", return_value=expected),
		):
			result = v1.list_repairs_for_current_user(None, 20)

		self.assertEqual(result, expected)
		rows = self._stored_rows(correlation_id)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].result_code, "OK")
		self.assertEqual(rows[0].count, 0)

	def test_duplicate_is_idempotent_but_conflicting_replay_is_rejected(self):
		event = self._event()
		sink = audit_store.DurableRepairAuditSink()
		self.assertIs(sink.emit(event), True)
		self.assertIs(sink.emit(dict(event)), True)
		self.assertEqual(len(self._stored_rows(event["correlation_id"])), 1)

		conflicting = {**event, "latency_ms": event["latency_ms"] + 1}
		with self.assertRaises(audit_store.AuditEventConflictError):
			sink.emit(conflicting)
		self.assertEqual(self._stored_rows(event["correlation_id"])[0].latency_ms, 7)

	def test_invalid_or_extended_event_is_rejected_without_storage(self):
		event = self._event()
		event["raw_cursor"] = "private@example.test"
		with self.assertRaises(ValueError):
			audit_store.DurableRepairAuditSink().emit(event)
		self.assertEqual(self._stored_rows(event["correlation_id"]), [])

	def test_framework_update_and_delete_are_refused(self):
		event = self._event()
		audit_store.DurableRepairAuditSink().emit(event)
		frappe.db.rollback()
		document = frappe.get_doc(audit_store.DOCTYPE, event["correlation_id"].replace("corr_", "evt_", 1))
		document.latency_ms += 1
		with self.assertRaises(frappe.ValidationError):
			document.save(ignore_permissions=True)
		frappe.db.rollback()
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc(audit_store.DOCTYPE, document.name, ignore_permissions=True)
		frappe.db.rollback()
		self.assertEqual(len(self._stored_rows(event["correlation_id"])), 1)

	def test_storage_failure_is_fail_closed_at_public_contract_boundary(self):
		event = self._event()
		with (
			patch.object(audit_store, "_insert_event", side_effect=RuntimeError("database unavailable")),
			patch.object(v1, "_log_audit_sink_failure") as diagnostic,
			self.assertRaises(v1.PublicContractError) as caught,
		):
			v1._emit_audit_event(audit_store.DurableRepairAuditSink(), event)
		self.assertEqual(caught.exception.code, "DEPENDENCY_UNAVAILABLE")
		diagnostic.assert_called_once()
		self.assertEqual(self._stored_rows(event["correlation_id"]), [])


class TestPublicContractV1(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._previous_user = frappe.session.user
		self._flag_was_present = v1.ROLLOUT_FLAG in frappe.conf
		self._previous_flag = frappe.conf.get(v1.ROLLOUT_FLAG)
		frappe.conf.pop(v1.ROLLOUT_FLAG, None)
		self.audit_sink = _CapturingAuditSink()
		self._audit_sink_patch = patch.object(v1, "_get_audit_sink", return_value=self.audit_sink)
		self._audit_key_patch = patch.object(v1, "_audit_hmac_key", return_value=TEST_AUDIT_KEY)
		self._audit_sink_patch.start()
		self._audit_key_patch.start()

	def tearDown(self):
		self._audit_key_patch.stop()
		self._audit_sink_patch.stop()
		frappe.set_user(self._previous_user)
		if self._flag_was_present:
			frappe.conf[v1.ROLLOUT_FLAG] = self._previous_flag
		else:
			frappe.conf.pop(v1.ROLLOUT_FLAG, None)
		super().tearDown()

	def test_capability_is_fail_closed_until_flag_and_full_readiness(self):
		self.assertEqual(
			v1.get_capabilities(),
			{"contract": "kuck-serwis/v1", "schema_revision": 1, "features": []},
		)
		frappe.conf[v1.ROLLOUT_FLAG] = True
		with (
			patch.object(v1, "_cursor_signing_key", return_value=TEST_CURSOR_KEY),
			patch.object(v1.frappe.db, "get_table_columns", return_value=["public_id"]),
			patch.object(v1.frappe.db, "get_column_index", return_value=True),
			patch.object(v1.frappe.db, "count", return_value=0),
		):
			self.assertEqual(v1.get_capabilities()["features"], [v1.ACCOUNT_READ])
		frappe.conf[v1.ROLLOUT_FLAG] = False
		with patch.object(v1, "_is_ready", return_value=True):
			self.assertEqual(v1.get_capabilities()["features"], [])
		frappe.conf[v1.ROLLOUT_FLAG] = True
		with patch.object(v1, "_is_ready", return_value=False):
			self.assertEqual(v1.get_capabilities()["features"], [])

	def test_new_ids_are_random_formatted_and_immutable(self):
		customer = _make_customer()
		first = _make_repair(customer)
		second = _make_repair(customer)
		self.assertRegex(first.public_id, PUBLIC_ID_PATTERN)
		self.assertRegex(second.public_id, PUBLIC_ID_PATTERN)
		self.assertNotEqual(first.public_id, second.public_id)
		self.assertTrue(frappe.db.get_column_index("tabNaprawa", "public_id", unique=True))

		first.public_id = "rpr_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
		with self.assertRaises(frappe.ValidationError):
			first.save(ignore_permissions=True)

	def test_caller_cannot_supply_public_id_on_insert(self):
		customer = _make_customer()
		doc = frappe.get_doc(
			{
				"doctype": "Naprawa",
				"klient": customer.name,
				"status": "Przyjęto",
				"rodzaj_naprawy": "Naprawa krótka",
				"sposob_dostarczenia": "Stacjonarnie",
				"sposob_odbioru": "Stacjonarnie",
				"public_id": "rpr_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_account_read_is_scoped_by_portal_users_and_has_allowlisted_model(self):
		user_a = _make_user()
		user_b = _make_user()
		customer_a = _make_customer(user_a)
		customer_b = _make_customer(user_b)
		brand = "Test Brand " + frappe.generate_hash(length=8)
		frappe.get_doc({"doctype": "Marka Zegarka", "nazwa": brand}).insert(ignore_permissions=True)
		repair_a = _make_repair(
			customer_a,
			marka=brand,
			orientacyjna_wycena=123.4,
			klient_telefon="+48111222333",
			klient_email="private-a@example.test",
		)
		repair_b = _make_repair(customer_b)

		frappe.set_user(user_a.name)
		with patch.object(v1, "_account_read_enabled", return_value=True):
			result = v1.list_repairs_for_current_user(None, 50)
			item = v1.get_repair_for_current_user(repair_a.public_id)

		self.assertEqual(result, {"items": [item], "next_cursor": None})
		self.assertEqual(
			set(item),
			{
				"schema",
				"repair_id",
				"public_status",
				"status_label",
				"watch",
				"received_on",
				"estimated_completion_on",
				"quote",
				"actions",
			},
		)
		self.assertEqual(item["quote"], {"amount": "123.40", "currency": "PLN"})
		serialized = repr(item)
		for forbidden in (
			repair_a.name,
			repair_b.name,
			customer_a.name,
			"private-a@example.test",
			"+48111222333",
			"SECRET-SERIAL",
			"Poufna notatka warsztatu",
		):
			self.assertNotIn(forbidden, serialized)

	def test_foreign_missing_malformed_and_internal_name_are_same_not_found(self):
		user_a = _make_user()
		user_b = _make_user()
		customer_a = _make_customer(user_a)
		customer_b = _make_customer(user_b)
		_make_repair(customer_a)
		foreign = _make_repair(customer_b)
		frappe.set_user(user_a.name)

		errors = []
		with patch.object(v1, "_account_read_enabled", return_value=True):
			for repair_id in (
				foreign.public_id,
				"rpr_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
				"malformed",
				foreign.name,
			):
				with self.assertRaises(v1.PublicContractError) as caught:
					v1.get_repair_for_current_user(repair_id)
				errors.append((caught.exception.code, str(caught.exception)))
		self.assertEqual(errors, [("NOT_FOUND", "Repair was not found.")] * 4)

	def test_guest_disabled_and_system_user_are_rejected(self):
		disabled = _make_user(enabled=0)
		system_user = _make_user()
		customer = _make_customer(disabled, system_user)
		frappe.db.set_value("User", system_user.name, "user_type", "System User")
		_make_repair(customer)

		with patch.object(v1, "_account_read_enabled", return_value=True):
			for user in ("Guest", disabled.name, system_user.name):
				frappe.set_user(user)
				with self.assertRaises(v1.PublicContractError) as caught:
					v1.list_repairs_for_current_user(None, 20)
				self.assertEqual(caught.exception.code, "AUTH_REQUIRED")

	def test_page_size_and_unsigned_cursor_are_rejected(self):
		user = _make_user()
		_make_customer(user)
		frappe.set_user(user.name)
		with (
			patch.object(v1, "_account_read_enabled", return_value=True),
			patch.object(v1, "_cursor_signing_key", return_value=TEST_CURSOR_KEY),
		):
			for invalid in (0, 51, 1.0, True):
				with self.assertRaises(v1.PublicContractError) as caught:
					v1.list_repairs_for_current_user(None, invalid)
				self.assertEqual(caught.exception.code, "VALIDATION_FAILED")
			with self.assertRaises(v1.PublicContractError) as caught:
				v1.list_repairs_for_current_user("unsigned", 20)
			self.assertEqual(caught.exception.code, "INVALID_CURSOR")

	def test_signed_keyset_pagination_has_no_duplicates_or_omissions(self):
		user = _make_user()
		customer = _make_customer(user)
		repairs = [_make_repair(customer, model_zegarka=f"Page {index}") for index in range(5)]
		creation_values = (
			"2026-08-14 12:00:05.000000",
			"2026-08-14 12:00:04.000000",
			"2026-08-14 12:00:04.000000",
			"2026-08-14 12:00:03.000000",
			"2026-08-14 12:00:02.000000",
		)
		for repair, creation in zip(repairs, creation_values, strict=True):
			frappe.db.set_value("Naprawa", repair.name, "creation", creation, update_modified=False)
		expected = frappe.get_all(
			"Naprawa",
			filters={"name": ["in", [repair.name for repair in repairs]]},
			pluck="public_id",
			order_by="creation desc, public_id desc",
		)

		frappe.set_user(user.name)
		seen = []
		cursor = None
		pages = 0
		with (
			patch.object(v1, "_account_read_enabled", return_value=True),
			patch.object(v1, "_cursor_signing_key", return_value=TEST_CURSOR_KEY),
			patch.object(v1, "_now_timestamp", return_value=10_000),
		):
			while True:
				result = v1.list_repairs_for_current_user(cursor, 2)
				pages += 1
				seen.extend(item["repair_id"] for item in result["items"])
				cursor = result["next_cursor"]
				if cursor is None:
					break
				self.assertRegex(cursor, r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
				decoded_payload = json.loads(v1._base64url_decode(cursor.split(".", 1)[0]))
				self.assertEqual(set(decoded_payload), v1._CURSOR_FIELDS)
				self.assertNotIn(user.name, repr(decoded_payload))
				self.assertNotIn(customer.name, repr(decoded_payload))

		self.assertEqual(pages, 3)
		self.assertEqual(seen, expected)
		self.assertEqual(len(seen), len(set(seen)))

	def test_cursor_rejects_tamper_expiry_cross_user_scope_change_and_schema(self):
		user_a = _make_user()
		user_b = _make_user()
		customer_a = _make_customer(user_a)
		customer_b = _make_customer(user_b)
		_make_repair(customer_a, model_zegarka="First")
		_make_repair(customer_a, model_zegarka="Second")
		_make_repair(customer_b, model_zegarka="Foreign")
		frappe.set_user(user_a.name)

		with (
			patch.object(v1, "_account_read_enabled", return_value=True),
			patch.object(v1, "_cursor_signing_key", return_value=TEST_CURSOR_KEY),
			patch.object(v1, "_now_timestamp", return_value=20_000),
		):
			cursor = v1.list_repairs_for_current_user(None, 1)["next_cursor"]
			with patch.object(v1, "SCHEMA_REVISION", 2):
				wrong_schema_cursor = v1.list_repairs_for_current_user(None, 1)["next_cursor"]
		self.assertIsNotNone(cursor)
		payload, signature = cursor.split(".")
		tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
		tampered = f"{payload}.{tampered_signature}"

		def assert_invalid(candidate, now):
			with (
				patch.object(v1, "_account_read_enabled", return_value=True),
				patch.object(v1, "_cursor_signing_key", return_value=TEST_CURSOR_KEY),
				patch.object(v1, "_now_timestamp", return_value=now),
			):
				with self.assertRaises(v1.PublicContractError) as caught:
					v1.list_repairs_for_current_user(candidate, 1)
			self.assertEqual(caught.exception.code, "INVALID_CURSOR")
			public_error = repr(caught.exception)
			for forbidden in (candidate, user_a.name, customer_a.name, customer_b.name):
				self.assertNotIn(forbidden, public_error)

		assert_invalid(tampered, 20_000)
		assert_invalid(cursor, 20_000 + v1.CURSOR_TTL_SECONDS + 1)
		assert_invalid(wrong_schema_cursor, 20_000)

		frappe.set_user(user_b.name)
		assert_invalid(cursor, 20_000)

		frappe.set_user(user_a.name)
		_make_customer(user_a)
		assert_invalid(cursor, 20_000)

	def test_audit_events_are_sanitized_and_cover_success_denials_and_dependency(self):
		user = _make_user()
		customer = _make_customer(user)
		repair = _make_repair(
			customer,
			klient_email="audit-private@example.test",
			klient_telefon="+48999888777",
		)
		missing_handle = "rpr_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
		raw_cursor = "raw-cursor-audit-private@example.test"
		frappe.set_user(user.name)

		with patch.object(v1, "_account_read_enabled", return_value=True):
			v1.list_repairs_for_current_user(None, 20)
			v1.get_repair_for_current_user(repair.public_id)
			with self.assertRaises(v1.PublicContractError):
				v1.get_repair_for_current_user(missing_handle)
			with (
				patch.object(v1, "_cursor_signing_key", return_value=TEST_CURSOR_KEY),
				self.assertRaises(v1.PublicContractError),
			):
				v1.list_repairs_for_current_user(raw_cursor, 20)

		frappe.set_user("Guest")
		with (
			patch.object(v1, "_account_read_enabled", return_value=True),
			self.assertRaises(v1.PublicContractError),
		):
			v1.list_repairs_for_current_user(None, 20)

		frappe.set_user(user.name)
		with self.assertRaises(v1.PublicContractError):
			v1.list_repairs_for_current_user(None, 20)

		events = self.audit_sink.events
		self.assertEqual(
			[
				(event["operation"], event["result_code"], event["outcome"], event["count"])
				for event in events
			],
			[
				("list", "OK", "success", 1),
				("get", "OK", "success", 1),
				("get", "NOT_FOUND", "deny", 0),
				("list", "INVALID_CURSOR", "deny", 0),
				("list", "AUTH_REQUIRED", "deny", 0),
				("list", "DEPENDENCY_UNAVAILABLE", "error", 0),
			],
		)
		expected_fields = {
			"event",
			"contract",
			"schema_revision",
			"correlation_id",
			"operation",
			"outcome",
			"actor_class",
			"actor_hash",
			"repair_handle_hash",
			"result_code",
			"count",
			"latency_ms",
		}
		for event in events:
			self.assertEqual(set(event), expected_fields)
			self.assertEqual(event["event"], "kuck_serwis.public_contract.audit.v1")
			self.assertEqual(event["contract"], "kuck-serwis/v1")
			self.assertEqual(event["schema_revision"], 1)
			self.assertRegex(event["correlation_id"], r"^corr_[A-Za-z0-9_-]{24}$")
			self.assertRegex(event["actor_hash"], r"^[0-9a-f]{64}$")
			self.assertIs(type(event["latency_ms"]), int)
			self.assertGreaterEqual(event["latency_ms"], 0)
		self.assertEqual(len({event["correlation_id"] for event in events}), len(events))
		self.assertEqual(
			[event["actor_class"] for event in events], ["website_user"] * 4 + ["guest", "website_user"]
		)
		self.assertIsNone(events[0]["repair_handle_hash"])
		self.assertRegex(events[1]["repair_handle_hash"], r"^[0-9a-f]{64}$")
		self.assertRegex(events[2]["repair_handle_hash"], r"^[0-9a-f]{64}$")

		serialized_events = repr(events)
		for forbidden in (
			user.name,
			customer.name,
			repair.name,
			repair.public_id,
			missing_handle,
			raw_cursor,
			"audit-private@example.test",
			"+48999888777",
			"Model publiczny",
			"SECRET-SERIAL",
			"Guest",
		):
			self.assertNotIn(forbidden, serialized_events)

	def test_unacknowledged_audit_sink_fails_closed_before_returning_data(self):
		user = _make_user()
		customer = _make_customer(user)
		_make_repair(customer)
		frappe.set_user(user.name)
		rejecting_sink = _CapturingAuditSink(acknowledge=False)

		with (
			patch.object(v1, "_account_read_enabled", return_value=True),
			patch.object(v1, "_get_audit_sink", return_value=rejecting_sink),
			patch.object(v1, "_log_audit_sink_failure") as diagnostic,
			self.assertRaises(v1.PublicContractError) as caught,
		):
			v1.list_repairs_for_current_user(None, 20)

		self.assertEqual(caught.exception.code, "DEPENDENCY_UNAVAILABLE")
		self.assertEqual(len(rejecting_sink.events), 1)
		self.assertEqual(rejecting_sink.events[0]["result_code"], "OK")
		diagnostic.assert_called_once()

	def test_unexpected_dependency_error_is_audited_and_sanitized(self):
		private_detail = "customer-private@example.test table tabNaprawa"

		with (
			patch.object(v1, "_list_repairs_for_current_user", side_effect=RuntimeError(private_detail)),
			self.assertRaises(v1.PublicContractError) as caught,
		):
			v1.list_repairs_for_current_user(None, 20)

		self.assertEqual(caught.exception.code, "DEPENDENCY_UNAVAILABLE")
		self.assertNotIn(private_detail, str(caught.exception))
		self.assertIsNone(caught.exception.__cause__)
		self.assertIsNone(caught.exception.__context__)
		self.assertEqual(len(self.audit_sink.events), 1)
		self.assertEqual(self.audit_sink.events[0]["result_code"], "INTERNAL_ERROR")
		self.assertNotIn(private_detail, repr(self.audit_sink.events[0]))

	def test_backfill_is_idempotent_and_report_contains_only_counters(self):
		customer = _make_customer()
		repair = _make_repair(customer)
		frappe.db.set_value("Naprawa", repair.name, "public_id", None, update_modified=False)

		first = backfill_naprawa_public_id.execute()
		assigned = frappe.db.get_value("Naprawa", repair.name, "public_id")
		second = backfill_naprawa_public_id.execute()

		self.assertRegex(assigned, PUBLIC_ID_PATTERN)
		self.assertEqual(frappe.db.get_value("Naprawa", repair.name, "public_id"), assigned)
		self.assertGreaterEqual(first["assigned_count"], 1)
		self.assertEqual(second["assigned_count"], 0)
		self.assertEqual(set(first), {"total_count", "preserved_count", "assigned_count", "missing_count"})
		self.assertNotIn(repair.name, repr(first))
		self.assertNotIn(customer.name, repr(first))
