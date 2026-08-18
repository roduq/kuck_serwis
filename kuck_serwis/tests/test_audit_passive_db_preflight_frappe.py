from unittest.mock import patch

from frappe.tests import IntegrationTestCase

from kuck_serwis import audit_passive_db_preflight as preflight
from kuck_serwis.kuck_serwis.doctype.kuck_repair_audit_event import kuck_repair_audit_event
from kuck_serwis.public_contract.v1 import STATUS_MAP


class _RecordingDatabase:
	def __init__(self, database):
		self.database = database
		self.db_type = database.db_type
		self.queries = []

	def sql(self, query, values=(), *, as_dict=False):
		self.queries.append((query, values))
		return self.database.sql(query, values, as_dict=as_dict)

	def close(self):
		self.database.close()


class TestExistingDbPreflightOnSite(IntegrationTestCase):
	def test_real_existing_database_preflight_is_read_only_and_explained(self):
		self.assertEqual(tuple(STATUS_MAP), preflight._ALLOWED_STATUSES)
		real_database = kuck_repair_audit_event._new_isolated_database()
		recording = _RecordingDatabase(real_database)
		with patch.object(
			preflight,
			"_new_isolated_database",
			return_value=recording,
		):
			result = preflight.collect_existing_db_preflight_v1()

		self.assertTrue(result.codes)
		self.assertIs(result.assessment_authorized, False)
		self.assertIs(result.purge_authorized, False)
		self.assertIs(result.delivery_authorized, False)
		self.assertIs(result.activation_authorized, False)
		self.assertIs(result.capability_ready, False)
		self.assertIs(result.readiness_evidence_ok, False)
		queries = [query for query, _values in recording.queries]
		self.assertIn(preflight._STATUS_EXPLAIN_SQL, queries)
		if preflight.ExistingDbPreflightCode.STATUS_DATA_NOT_PROVEN in result.codes:
			self.assertNotIn(preflight._STATUS_SQL, queries)
		for query in queries:
			self.assertIn(query, preflight._ALLOWED_SQL)
			self.assertTrue(query.startswith(("SELECT ", "EXPLAIN SELECT ")))
