import json
import unittest
from unittest.mock import patch

import frappe
from frappe.core.doctype.file.file import File
from frappe.tests import IntegrationTestCase

from kuck_serwis.repair_photo_evidence_store import (
	EVIDENCE_SQL,
	RepairPhotoEvidenceStoreCode,
	RepairPhotoEvidenceStoreError,
	_issue_actor_scoped_repair_access,
	read_scoped_repair_photo_evidence,
)


def _make_user(*, user_type="Website User", enabled=1):
	email = f"g074-{frappe.generate_hash(length=12).lower()}@example.test"
	return frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Synthetic G074",
			"enabled": enabled,
			"user_type": user_type,
			"send_welcome_email": 0,
		}
	).insert(ignore_permissions=True)


def _make_customer(*users):
	document = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": "Synthetic G074 " + frappe.generate_hash(length=10),
			"customer_type": "Individual",
		}
	)
	for user in users:
		document.append("portal_users", {"user": user.name})
	return document.insert(ignore_permissions=True)


def _make_repair(customer):
	return frappe.get_doc(
		{
			"doctype": "Naprawa",
			"klient": customer.name,
			"status": "Przyjęto",
			"rodzaj_naprawy": "Naprawa krótka",
			"model_zegarka": "Synthetic",
			"sposob_dostarczenia": "Stacjonarnie",
			"sposob_odbioru": "Stacjonarnie",
		}
	).insert(ignore_permissions=True)


def _insert_child(*, repair, name, position, file_url):
	frappe.db.sql(
		"""
		INSERT INTO `tabNaprawa Zdjecie`
			(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`,
			 `parent`, `parentfield`, `parenttype`, `zdjecie`)
		VALUES
			(%s, NOW(6), NOW(6), 'Administrator', 'Administrator', 0, %s,
			 %s, 'zdjecia', 'Naprawa', %s)
		""",
		(name, position, repair.name, file_url),
	)


def _insert_file(
	*, repair, name, file_url, is_private=1, is_folder=0, attached_to_name=None, attached_to_field="zdjecie"
):
	frappe.db.sql(
		"""
		INSERT INTO `tabFile`
			(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`,
			 `file_name`, `is_private`, `file_url`, `is_folder`, `attached_to_doctype`,
			 `attached_to_name`, `attached_to_field`)
		VALUES
			(%s, NOW(6), NOW(6), 'Administrator', 'Administrator', 0, 0,
			 'synthetic.png', %s, %s, %s, 'Naprawa', %s, %s)
		""",
		(
			name,
			is_private,
			file_url,
			is_folder,
			attached_to_name if attached_to_name is not None else repair.name,
			attached_to_field,
		),
	)


def _access(repair, user):
	return _issue_actor_scoped_repair_access(
		repair_name=repair.name,
		repair_id=repair.public_id,
		actor_identity=user.name,
	)


def _walk(value):
	if type(value) is dict:
		yield value
		for item in value.values():
			yield from _walk(item)
	elif type(value) is list:
		for item in value:
			yield from _walk(item)


class TestRepairPhotoEvidenceStoreFrappe(IntegrationTestCase):
	def assert_code(self, code, call):
		with self.assertRaises(RepairPhotoEvidenceStoreError) as raised:
			call()
		self.assertIs(raised.exception.code, code)
		self.assertEqual(str(raised.exception), code.value)

	def fixture(self):
		owner = _make_user()
		customer = _make_customer(owner)
		repair = _make_repair(customer)
		return owner, customer, repair

	def test_actor_scoped_private_metadata_and_savepoint_rollback(self):
		owner, _customer, repair = self.fixture()
		proof = _access(repair, owner)
		self.assertEqual(read_scoped_repair_photo_evidence(proof), ())

		marker = frappe.generate_hash(length=12)
		url = f"/private/files/g074-{marker}.png"
		savepoint = f"g074_{marker}"
		frappe.db.savepoint(savepoint)
		try:
			_insert_child(repair=repair, name=f"G074-ROW-{marker}", position=1, file_url=url)
			_insert_file(repair=repair, name=f"G074-FILE-{marker}", file_url=url)
			result = read_scoped_repair_photo_evidence(proof)
			self.assertEqual(tuple(item.position for item in result), (1,))
			self.assertTrue(result[0].is_private)
			self.assertTrue(result[0].exact_attachment)
		finally:
			frappe.db.rollback(save_point=savepoint)
		self.assertEqual(read_scoped_repair_photo_evidence(proof), ())

	def test_a_b_idor_revocation_system_user_and_missing_are_identical(self):
		owner, customer, repair = self.fixture()
		foreign = _make_user()
		_make_customer(foreign)
		missing = _issue_actor_scoped_repair_access(
			repair_name="NAP-MISSING-G074",
			repair_id="rpr_ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ",
			actor_identity=owner.name,
		)
		foreign_access = _access(repair, foreign)

		failures = []
		for proof in (missing, foreign_access):
			with self.assertRaises(RepairPhotoEvidenceStoreError) as raised:
				read_scoped_repair_photo_evidence(proof)
			failures.append(raised.exception)

		frappe.db.set_value("User", owner.name, "enabled", 0, update_modified=False)
		with self.assertRaises(RepairPhotoEvidenceStoreError) as revoked:
			read_scoped_repair_photo_evidence(_access(repair, owner))
		frappe.db.set_value("User", owner.name, "enabled", 1, update_modified=False)
		system_user = _make_user(user_type="System User")
		frappe.db.set_value("User", system_user.name, "user_type", "System User", update_modified=False)
		frappe.db.sql(
			"""INSERT INTO `tabPortal User`
			(`name`,`creation`,`modified`,`modified_by`,`owner`,`docstatus`,`idx`,
			 `parent`,`parentfield`,`parenttype`,`user`)
			VALUES (%s,NOW(6),NOW(6),'Administrator','Administrator',0,1,%s,
			 'portal_users','Customer',%s)""",
			(f"G074-PU-{frappe.generate_hash(length=10)}", customer.name, system_user.name),
		)
		with self.assertRaises(RepairPhotoEvidenceStoreError) as system:
			read_scoped_repair_photo_evidence(_access(repair, system_user))

		failures.extend((revoked.exception, system.exception))
		self.assertTrue(
			all(error.code is RepairPhotoEvidenceStoreCode.SCOPED_REPAIR_NOT_FOUND for error in failures)
		)
		self.assertEqual(len({(str(error), repr(error)) for error in failures}), 1)

	def test_public_duplicate_file_duplicate_child_orphan_and_overflow_fail_closed(self):
		owner, _customer, repair = self.fixture()
		proof = _access(repair, owner)
		marker = frappe.generate_hash(length=10)
		savepoint = f"g074_unsafe_{marker}"

		cases = []
		frappe.db.savepoint(savepoint)
		try:
			public_url = f"/files/g074-public-{marker}.png"
			_insert_child(repair=repair, name=f"G074-PUBLIC-{marker}", position=1, file_url=public_url)
			_insert_file(repair=repair, name=f"G074-PUBLIC-F-{marker}", file_url=public_url, is_private=0)
			cases.append(proof)
			self.assert_code(
				RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE,
				lambda: read_scoped_repair_photo_evidence(proof),
			)
		finally:
			frappe.db.rollback(save_point=savepoint)

		for kind in ("duplicate_file", "duplicate_child", "orphan", "overflow"):
			frappe.db.savepoint(savepoint)
			try:
				url = f"/private/files/g074-{kind}-{marker}.png"
				if kind == "duplicate_file":
					_insert_child(repair=repair, name=f"G074-DF-R-{marker}", position=1, file_url=url)
					for suffix in ("A", "B"):
						_insert_file(repair=repair, name=f"G074-DF-{suffix}-{marker}", file_url=url)
				elif kind == "duplicate_child":
					for position in (1, 2):
						_insert_child(
							repair=repair,
							name=f"G074-DC-{position}-{marker}",
							position=1,
							file_url=url,
						)
					_insert_file(repair=repair, name=f"G074-DC-F-{marker}", file_url=url)
				elif kind == "orphan":
					_insert_file(repair=repair, name=f"G074-O-{marker}", file_url=url)
				else:
					for position in range(1, 22):
						item_url = f"/private/files/g074-limit-{position}-{marker}.png"
						_insert_child(
							repair=repair,
							name=f"G074-L-R-{position}-{marker}",
							position=position,
							file_url=item_url,
						)
						_insert_file(
							repair=repair,
							name=f"G074-L-F-{position}-{marker}",
							file_url=item_url,
						)
				self.assert_code(
					RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE,
					lambda: read_scoped_repair_photo_evidence(proof),
				)
			finally:
				frappe.db.rollback(save_point=savepoint)

	def test_default_adapter_executes_exactly_one_select_without_io_or_transaction_control(self):
		owner, _customer, repair = self.fixture()
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
			self.assertEqual(read_scoped_repair_photo_evidence(_access(repair, owner)), ())

		self.assertEqual(len(calls), 1)
		query, values, args, kwargs = calls[0]
		self.assertEqual(query, EVIDENCE_SQL)
		self.assertEqual(
			values,
			{
				"repair_name": repair.name,
				"repair_id": repair.public_id,
				"actor_identity": owner.name,
				"fetch_limit": 21,
			},
		)
		self.assertEqual(args, ())
		self.assertEqual(kwargs, {})

	def test_real_indexes_and_explain_preserve_bounded_plan(self):
		index_rows = frappe.db.sql(
			"""
			SELECT `TABLE_NAME`, `INDEX_NAME`, `SEQ_IN_INDEX`, `COLUMN_NAME`, `SUB_PART`
			FROM `information_schema`.`STATISTICS`
			WHERE `TABLE_SCHEMA` = DATABASE()
			  AND `TABLE_NAME` IN
				('tabNaprawa', 'tabUser', 'tabPortal User', 'tabNaprawa Zdjecie', 'tabFile')
			ORDER BY `TABLE_NAME`, `INDEX_NAME`, `SEQ_IN_INDEX`
			"""
		)
		indexes = {
			(table, index, sequence, column, sub_part)
			for table, index, sequence, column, sub_part in index_rows
		}
		for expected in (
			("tabNaprawa", "PRIMARY", 1, "name", None),
			("tabUser", "PRIMARY", 1, "name", None),
			("tabPortal User", "user", 1, "user", None),
			("tabPortal User", "parent", 1, "parent", None),
			("tabNaprawa Zdjecie", "parent", 1, "parent", None),
			("tabFile", "file_url_index", 1, "file_url", 100),
			("tabFile", "attached_to_doctype_attached_to_name_index", 1, "attached_to_doctype", None),
			("tabFile", "attached_to_doctype_attached_to_name_index", 2, "attached_to_name", None),
		):
			self.assertIn(expected, indexes)

		owner, _customer, repair = self.fixture()
		plan_row = frappe.db.sql(
			"EXPLAIN FORMAT=JSON " + EVIDENCE_SQL,
			{
				"repair_name": repair.name,
				"repair_id": repair.public_id,
				"actor_identity": owner.name,
				"fetch_limit": 21,
			},
		)
		self.assertEqual(len(plan_row), 1)
		plan = json.loads(plan_row[0][0])
		serialized = json.dumps(plan, sort_keys=True, separators=(",", ":"))
		self.assertNotIn("block-nl-join", serialized)
		tables = [node["table"] for node in _walk(plan) if type(node.get("table")) is dict]
		for table_name in ("n", "u", "pu", "c", "f"):
			nodes = [node for node in tables if node.get("table_name") == table_name]
			self.assertTrue(nodes, table_name)
		allowed_keys = {
			"PRIMARY",
			"user",
			"parent",
			"file_url_index",
			"attached_to_doctype_attached_to_name_index",
		}
		self.assertTrue(
			all(
				node.get("access_type") in {"const", "eq_ref", "ref"} and node.get("key") in allowed_keys
				for node in tables
				if node.get("table_name") in {"n", "u", "pu", "c", "f"}
			)
		)
		child_nodes = [node for node in tables if node.get("table_name") == "c"]
		self.assertTrue(
			all(
				node.get("access_type") == "ref"
				and node.get("key") == "parent"
				and node.get("used_key_parts") == ["parent"]
				for node in child_nodes
			)
		)
		file_nodes = [node for node in tables if node.get("table_name") == "f"]
		attachment_nodes = [
			node for node in file_nodes if node.get("key") == "attached_to_doctype_attached_to_name_index"
		]
		url_nodes = [node for node in file_nodes if node.get("key") == "file_url_index"]
		self.assertTrue(attachment_nodes)
		self.assertTrue(url_nodes)
		self.assertTrue(
			all(
				node.get("access_type") == "ref"
				and node.get("used_key_parts") == ["attached_to_doctype", "attached_to_name"]
				for node in attachment_nodes
			)
		)
		self.assertTrue(
			all(
				node.get("access_type") == "ref" and node.get("used_key_parts") == ["file_url"]
				for node in url_nodes
			)
		)


if __name__ == "__main__":
	unittest.main()
