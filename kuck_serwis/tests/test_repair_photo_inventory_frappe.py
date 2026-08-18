import json
from unittest.mock import patch

import frappe
from frappe.core.doctype.file.file import File
from frappe.tests import IntegrationTestCase

from kuck_serwis.repair_photo_inventory import (
	INVENTORY_SQL,
	RepairPhotoInventoryLimits,
	RepairPhotoInventoryStatus,
	collect_repair_photo_inventory,
)
from kuck_serwis.repair_photo_retention_preflight import (
	RepairPhotoRetentionPreflightCode,
	assess_repair_photo_retention_inventory_v1,
	collect_repair_photo_retention_preflight_v1,
)


def _insert_child(*, name, parent, file_url):
	frappe.db.sql(
		"""
		INSERT INTO `tabNaprawa Zdjecie`
			(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`,
			 `parent`, `parentfield`, `parenttype`, `zdjecie`)
		VALUES
			(%s, NOW(6), NOW(6), 'Administrator', 'Administrator', 0, 1,
			 %s, 'zdjecia', 'Naprawa', %s)
		""",
		(name, parent, file_url),
	)


def _insert_file(*, name, file_url, is_private, owner_name):
	frappe.db.sql(
		"""
		INSERT INTO `tabFile`
			(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`,
			 `file_name`, `is_private`, `file_url`, `is_folder`, `attached_to_doctype`,
			 `attached_to_name`, `attached_to_field`)
		VALUES
			(%s, NOW(6), NOW(6), 'Administrator', 'Administrator', 0, 0,
			 'synthetic.png', %s, %s, 0, 'Naprawa', %s, 'zdjecie')
		""",
		(name, is_private, file_url, owner_name),
	)


def _walk(value):
	if type(value) is dict:
		yield value
		for item in value.values():
			yield from _walk(item)
	elif type(value) is list:
		for item in value:
			yield from _walk(item)


class TestRepairPhotoInventoryFrappe(IntegrationTestCase):
	def test_synthetic_metadata_is_counted_and_rollback_restores_inventory(self):
		limits = RepairPhotoInventoryLimits(child_rows_per_source=100, file_rows=100)
		before = collect_repair_photo_inventory(limits=limits)
		self.assertIs(before.status, RepairPhotoInventoryStatus.COMPLETE)

		marker = frappe.generate_hash(length=12)
		private_url = f"/private/files/g058-{marker}.png"
		public_url = f"/files/g058-{marker}.png"
		private_parent = f"G058-PRIVATE-{marker}"
		public_parent = f"G058-PUBLIC-{marker}"
		orphan_parent = f"G058-ORPHAN-{marker}"
		duplicate_parent = f"G058-DUPLICATE-{marker}"
		duplicate_url = f"/private/files/g058-duplicate-{marker}.png"
		savepoint = f"g058_{marker}"
		frappe.db.savepoint(savepoint)
		try:
			_insert_child(name=f"G058-ROW-P-{marker}", parent=private_parent, file_url=private_url)
			_insert_child(name=f"G058-ROW-L-{marker}", parent=public_parent, file_url=public_url)
			_insert_child(
				name=f"G058-ROW-D-{marker}",
				parent=duplicate_parent,
				file_url=duplicate_url,
			)
			_insert_file(
				name=f"G058-FILE-P-{marker}",
				file_url=private_url,
				is_private=1,
				owner_name=private_parent,
			)
			_insert_file(
				name=f"G058-FILE-O-{marker}",
				file_url=f"/files/g058-orphan-{marker}.png",
				is_private=0,
				owner_name=orphan_parent,
			)
			for suffix in ("A", "B"):
				_insert_file(
					name=f"G058-FILE-D-{suffix}-{marker}",
					file_url=duplicate_url,
					is_private=1,
					owner_name=duplicate_parent,
				)

			after = collect_repair_photo_inventory(limits=limits)
			self.assertIs(after.status, RepairPhotoInventoryStatus.COMPLETE)
			self.assertEqual(after.counters.naprawa_child_rows, before.counters.naprawa_child_rows + 3)
			self.assertEqual(after.counters.file_rows, before.counters.file_rows + 4)
			self.assertEqual(after.counters.private_exact_rows, before.counters.private_exact_rows + 1)
			self.assertEqual(
				after.counters.private_duplicate_file_rows,
				before.counters.private_duplicate_file_rows + 1,
			)
			self.assertEqual(
				after.counters.duplicate_file_url_groups,
				before.counters.duplicate_file_url_groups + 1,
			)
			self.assertEqual(
				after.counters.duplicate_orphan_file_url_groups,
				before.counters.duplicate_orphan_file_url_groups,
			)
			self.assertEqual(
				after.counters.legacy_public_missing_file_rows,
				before.counters.legacy_public_missing_file_rows + 1,
			)
			self.assertEqual(
				after.counters.orphan_public_file_rows,
				before.counters.orphan_public_file_rows + 1,
			)
			preflight = assess_repair_photo_retention_inventory_v1(after)
			self.assertIn(
				RepairPhotoRetentionPreflightCode.PUBLIC_OR_MALFORMED_REFERENCE_PRESENT,
				preflight.codes,
			)
			self.assertIn(
				RepairPhotoRetentionPreflightCode.PRIVATE_BINDING_NOT_PROVEN,
				preflight.codes,
			)
			self.assertIn(
				RepairPhotoRetentionPreflightCode.DUPLICATE_REFERENCE_PRESENT,
				preflight.codes,
			)
			self.assertIn(RepairPhotoRetentionPreflightCode.ORPHAN_FILE_PRESENT, preflight.codes)
			self.assertFalse(preflight.retention_evidence_ok)
			self.assertFalse(preflight.purge_authorized)
			self.assertFalse(preflight.download_authorized)
		finally:
			frappe.db.rollback(save_point=savepoint)

		self.assertEqual(collect_repair_photo_inventory(limits=limits), before)

	def test_adapter_executes_one_select_without_transaction_or_content_io(self):
		real_sql = frappe.db.sql
		calls = []

		def sql_spy(query, values=None, *args, **kwargs):
			calls.append((query, values, args, kwargs))
			return real_sql(query, values, *args, **kwargs)

		with (
			patch.object(frappe.db, "sql", side_effect=sql_spy),
			patch.object(frappe.db, "begin", side_effect=AssertionError("BEGIN_FORBIDDEN")),
			patch.object(frappe.db, "commit", side_effect=AssertionError("COMMIT_FORBIDDEN")),
			patch.object(frappe.db, "rollback", side_effect=AssertionError("ROLLBACK_FORBIDDEN")),
			patch.object(File, "get_content", side_effect=AssertionError("CONTENT_FORBIDDEN")),
			patch("builtins.open", side_effect=AssertionError("FILESYSTEM_FORBIDDEN")),
		):
			report = collect_repair_photo_inventory(
				limits=RepairPhotoInventoryLimits(child_rows_per_source=10, file_rows=10)
			)

		self.assertIn(
			report.status, (RepairPhotoInventoryStatus.COMPLETE, RepairPhotoInventoryStatus.TRUNCATED)
		)
		self.assertEqual(len(calls), 1)
		query, values, args, kwargs = calls[0]
		self.assertEqual(query, INVENTORY_SQL)
		self.assertEqual(values, {"child_fetch_limit": 11, "file_fetch_limit": 11})
		self.assertEqual(args, ())
		self.assertEqual(kwargs, {})
		upper = query.upper()
		for forbidden in ("START TRANSACTION", "COMMIT", "ROLLBACK", "FOR UPDATE", "GET_CONTENT"):
			self.assertNotIn(forbidden, upper)

	def test_preflight_delegates_to_one_fresh_read_without_content_or_transaction_io(self):
		real_sql = frappe.db.sql
		calls = []

		def sql_spy(query, values=None, *args, **kwargs):
			calls.append((query, values, args, kwargs))
			return real_sql(query, values, *args, **kwargs)

		with (
			patch.object(frappe.db, "sql", side_effect=sql_spy),
			patch.object(frappe.db, "begin", side_effect=AssertionError("BEGIN_FORBIDDEN")),
			patch.object(frappe.db, "commit", side_effect=AssertionError("COMMIT_FORBIDDEN")),
			patch.object(frappe.db, "rollback", side_effect=AssertionError("ROLLBACK_FORBIDDEN")),
			patch.object(File, "get_content", side_effect=AssertionError("CONTENT_FORBIDDEN")),
			patch("builtins.open", side_effect=AssertionError("FILESYSTEM_FORBIDDEN")),
		):
			result = collect_repair_photo_retention_preflight_v1(
				limits=RepairPhotoInventoryLimits(child_rows_per_source=10, file_rows=10)
			)

		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0][0], INVENTORY_SQL)
		self.assertFalse(result.retention_evidence_ok)
		self.assertFalse(result.assessment_authorized)
		self.assertFalse(result.dry_run_authorized)
		self.assertFalse(result.purge_authorized)
		self.assertFalse(result.download_authorized)
		self.assertFalse(result.activation_authorized)
		self.assertFalse(result.capability_ready)

	def test_real_indexes_and_explain_preserve_bounded_plan_contract(self):
		index_rows = frappe.db.sql(
			"""
			SELECT `TABLE_NAME`, `INDEX_NAME`, `SEQ_IN_INDEX`, `COLUMN_NAME`, `SUB_PART`
			FROM `information_schema`.`STATISTICS`
			WHERE `TABLE_SCHEMA` = DATABASE()
			  AND `TABLE_NAME` IN ('tabFile', 'tabNaprawa Zdjecie', 'tabPrzyjecie Zbiorcze Pozycja')
			ORDER BY `TABLE_NAME`, `INDEX_NAME`, `SEQ_IN_INDEX`
			"""
		)
		indexes = {
			(table, index, sequence, column, sub_part)
			for table, index, sequence, column, sub_part in index_rows
		}
		self.assertIn(("tabFile", "file_url_index", 1, "file_url", 100), indexes)
		self.assertIn(
			("tabFile", "attached_to_doctype_attached_to_name_index", 1, "attached_to_doctype", None),
			indexes,
		)
		self.assertIn(
			("tabFile", "attached_to_doctype_attached_to_name_index", 2, "attached_to_name", None),
			indexes,
		)
		self.assertIn(("tabNaprawa Zdjecie", "PRIMARY", 1, "name", None), indexes)
		self.assertIn(("tabPrzyjecie Zbiorcze Pozycja", "PRIMARY", 1, "name", None), indexes)

		plan_row = frappe.db.sql(
			"EXPLAIN FORMAT=JSON " + INVENTORY_SQL,
			{"child_fetch_limit": 2, "file_fetch_limit": 2},
		)
		self.assertEqual(len(plan_row), 1)
		plan = json.loads(plan_row[0][0])
		serialized = json.dumps(plan, sort_keys=True, separators=(",", ":"))
		self.assertNotIn("block-nl-join", serialized)

		table_nodes = [node["table"] for node in _walk(plan) if type(node.get("table")) is dict]
		file_nodes = [node for node in table_nodes if node.get("table_name") == "f"]
		self.assertGreaterEqual(len(file_nodes), 5)
		self.assertTrue(
			all(
				node.get("access_type") == "ref"
				and node.get("key") in {"file_url_index", "attached_to_doctype_attached_to_name_index"}
				for node in file_nodes
			)
		)

		for table_name in ("tabNaprawa Zdjecie", "tabPrzyjecie Zbiorcze Pozycja"):
			nodes = [node for node in table_nodes if node.get("table_name") == table_name]
			self.assertTrue(nodes)
			self.assertTrue(
				all(node.get("access_type") == "index" and node.get("key") == "PRIMARY" for node in nodes)
			)

		filesorts = [node["filesort"] for node in _walk(plan) if type(node.get("filesort")) is dict]
		self.assertTrue(filesorts)
		self.assertTrue(
			all(
				type(item.get("table")) is dict
				and str(item["table"].get("table_name", "")).startswith("<derived")
				for item in filesorts
			)
		)

	def test_real_limit_plus_one_reports_truncation_without_false_classification(self):
		marker = frappe.generate_hash(length=12)
		savepoint = f"g058_limit_{marker}"
		frappe.db.savepoint(savepoint)
		try:
			_insert_child(
				name=f"G058-LIMIT-A-{marker}",
				parent=f"G058-LIMIT-P-{marker}",
				file_url=f"/private/files/g058-limit-a-{marker}.png",
			)
			_insert_child(
				name=f"G058-LIMIT-B-{marker}",
				parent=f"G058-LIMIT-P-{marker}",
				file_url=f"/private/files/g058-limit-b-{marker}.png",
			)
			report = collect_repair_photo_inventory(
				limits=RepairPhotoInventoryLimits(child_rows_per_source=1, file_rows=100)
			)
			self.assertIs(report.status, RepairPhotoInventoryStatus.TRUNCATED)
			self.assertTrue(report.naprawa_truncated)
			self.assertEqual(report.counters.unclassified_reference_rows, 1)
			self.assertEqual(report.counters.private_missing_file_rows, 0)
		finally:
			frappe.db.rollback(save_point=savepoint)
