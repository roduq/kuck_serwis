import unittest
from unittest.mock import patch

from kuck_serwis import audit_passive_db_preflight as preflight


class _FakeDatabase:
	db_type = "mariadb"

	def __init__(self, *, bounded_status=False, invalid_public_id=False):
		self.bounded_status = bounded_status
		self.invalid_public_id = invalid_public_id
		self.queries = []
		self.closed = False

	def sql(self, query, values=(), *, as_dict=False):
		self.queries.append((query, values, as_dict))
		if query == preflight._COLUMNS_SQL:
			if values == (preflight._AUDIT_TABLE,):
				return [
					{"column_name": field, "data_type": "varchar", "is_nullable": "NO"}
					for field in preflight._AUDIT_FIELDS
				]
			return [
				{"column_name": "name", "data_type": "varchar", "is_nullable": "NO"},
				{"column_name": "public_id", "data_type": "varchar", "is_nullable": "YES"},
				{"column_name": "status", "data_type": "varchar", "is_nullable": "YES"},
			]
		if query == preflight._INDEXES_SQL:
			if values == (preflight._AUDIT_TABLE,):
				return [
					{"index_name": "event_id", "non_unique": 0, "seq_in_index": 1, "column_name": "event_id"},
					{
						"index_name": "correlation_id",
						"non_unique": 0,
						"seq_in_index": 1,
						"column_name": "correlation_id",
					},
				]
			return [
				{"index_name": "public_id", "non_unique": 0, "seq_in_index": 1, "column_name": "public_id"}
			]
		if query == preflight._PERMISSIONS_SQL:
			return []
		if query == preflight._PUBLIC_ID_EXPLAIN_SQL:
			return [{"type": "range", "key": "public_id", "rows": 2}]
		if query == preflight._PUBLIC_ID_SQL:
			return [{"1": 1}] if self.invalid_public_id else []
		if query == preflight._STATUS_EXPLAIN_SQL:
			if self.bounded_status:
				return [{"type": "range", "key": "status", "rows": 8}]
			return [{"type": "ALL", "key": None, "rows": 500_000}]
		if query == preflight._STATUS_SQL:
			return []
		raise AssertionError("unexpected query")

	def close(self):
		self.closed = True


class TestExistingDbPreflight(unittest.TestCase):
	def test_unbounded_status_is_not_scanned_and_is_explicitly_not_proven(self):
		database = _FakeDatabase()

		result = preflight._collect_existing_db_preflight_v1(database)

		self.assertEqual(
			result.codes,
			(
				preflight.ExistingDbPreflightCode.AUDIT_PURGE_INDEX_NOT_PROVEN,
				preflight.ExistingDbPreflightCode.STATUS_DATA_NOT_PROVEN,
			),
		)
		self.assertNotIn(preflight._STATUS_SQL, [query for query, _values, _as_dict in database.queries])
		self.assertIn(preflight._STATUS_EXPLAIN_SQL, [query for query, _values, _as_dict in database.queries])

	def test_bounded_negative_checks_produce_partial_evidence_only(self):
		database = _FakeDatabase(bounded_status=True)
		# Supply the missing purge index for this case.
		original_sql = database.sql

		def with_purge_index(query, values=(), *, as_dict=False):
			rows = original_sql(query, values, as_dict=as_dict)
			if query == preflight._INDEXES_SQL and values == (preflight._AUDIT_TABLE,):
				rows.extend(
					[
						{
							"index_name": "creation_name",
							"non_unique": 1,
							"seq_in_index": 1,
							"column_name": "creation",
						},
						{
							"index_name": "creation_name",
							"non_unique": 1,
							"seq_in_index": 2,
							"column_name": "name",
						},
					]
				)
			return rows

		database.sql = with_purge_index
		result = preflight._collect_existing_db_preflight_v1(database)

		self.assertEqual(result.codes, (preflight.ExistingDbPreflightCode.EXISTING_DB_PARTIAL_EVIDENCE,))
		for field in (
			"assessment_authorized",
			"purge_authorized",
			"delivery_authorized",
			"activation_authorized",
			"capability_ready",
			"readiness_evidence_ok",
		):
			self.assertIs(getattr(result, field), False)
		self.assertNotIn("@", repr(result))

	def test_invalid_public_id_is_code_only(self):
		result = preflight._collect_existing_db_preflight_v1(_FakeDatabase(invalid_public_id=True))
		self.assertIn(preflight.ExistingDbPreflightCode.PUBLIC_ID_DATA_INVALID, result.codes)
		self.assertNotIn("rpr_", repr(result).lower())

	def test_unsupported_dialect_fails_closed_without_queries(self):
		database = _FakeDatabase()
		database.db_type = "postgres"
		result = preflight._collect_existing_db_preflight_v1(database)
		self.assertEqual(
			result.codes,
			(preflight.ExistingDbPreflightCode.DATABASE_DIALECT_NOT_PROVEN,),
		)
		self.assertEqual(database.queries, [])

	def test_public_collector_closes_connection_without_transaction_control(self):
		database = _FakeDatabase()
		with patch.object(preflight, "_new_isolated_database", return_value=database):
			preflight.collect_existing_db_preflight_v1()
		self.assertIs(database.closed, True)
		queries = " ".join(query.upper() for query, _values, _as_dict in database.queries)
		for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "COMMIT", "ROLLBACK", "BEGIN"):
			self.assertNotIn(forbidden, queries)

	def test_result_rejects_forged_authorization(self):
		with self.assertRaisesRegex(ValueError, "INVALID_EXISTING_DB_PREFLIGHT_RESULT"):
			preflight.ExistingDbPreflightResult(
				codes=(preflight.ExistingDbPreflightCode.DATABASE_UNAVAILABLE,),
				policy_revision_sha256=preflight.POLICY_REVISION_SHA256,
				capability_ready=True,
			)

	def test_query_allowlist_rejects_forged_catalog_target(self):
		with self.assertRaisesRegex(ValueError, "QUERY_NOT_ALLOWLISTED"):
			preflight._select(_FakeDatabase(), preflight._COLUMNS_SQL, ("tabUser",))

	def test_result_rejects_forged_partial_success_combination(self):
		with self.assertRaisesRegex(ValueError, "INVALID_EXISTING_DB_PREFLIGHT_RESULT"):
			preflight.ExistingDbPreflightResult(
				codes=(
					preflight.ExistingDbPreflightCode.DATABASE_UNAVAILABLE,
					preflight.ExistingDbPreflightCode.EXISTING_DB_PARTIAL_EVIDENCE,
				),
				policy_revision_sha256=preflight.POLICY_REVISION_SHA256,
			)


if __name__ == "__main__":
	unittest.main()
