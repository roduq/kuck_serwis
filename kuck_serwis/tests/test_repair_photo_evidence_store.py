import ast
import inspect
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from kuck_serwis.repair_photo_evidence_store import (
	EVIDENCE_SQL,
	FILE_REVALIDATION_SQL,
	ActorScopedRepairAccess,
	RepairPhotoEvidenceStoreCode,
	RepairPhotoEvidenceStoreError,
	ScopedPrivateFileAccess,
	_issue_actor_scoped_repair_access,
	read_scoped_repair_photo_evidence,
	read_scoped_repair_photo_file_access,
	resolve_actor_scoped_repair_access,
	revalidate_scoped_repair_photo_file_access,
)
from kuck_serwis.repair_photo_metadata import MAX_PHOTOS_PER_REPAIR, ScopedRepairPhotoEvidence

REPAIR_NAME = "NAP-2026-00001"
REPAIR_ID = "rpr_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
ACTOR = "synthetic@example.test"
URL = "/private/files/synthetic.png"
FILE_ID = "G080-FILE"
FILE_REVISION = "2026-08-15 10:11:12.123456"


def access(**overrides):
	values = {"repair_name": REPAIR_NAME, "repair_id": REPAIR_ID, "actor_identity": ACTOR}
	values.update(overrides)
	return _issue_actor_scoped_repair_access(**values)


def meta(*, scoped=1, photos=0, attached=0, orphan=0, isolation="REPEATABLE-READ"):
	return (
		isolation,
		"META",
		scoped,
		photos,
		attached,
		orphan,
		None,
		None,
		None,
		None,
		None,
		None,
		None,
		None,
	)


def photo(
	position=1,
	*,
	url=URL,
	url_chars=None,
	duplicate=0,
	match_state=1,
	file_identity=FILE_ID,
	file_basename=None,
	file_revision=FILE_REVISION,
	isolation="REPEATABLE-READ",
):
	return (
		isolation,
		"PHOTO",
		None,
		None,
		None,
		None,
		position,
		url,
		len(url) if url_chars is None else url_chars,
		duplicate,
		match_state,
		file_identity,
		url.removeprefix("/private/files/") if file_basename is None else file_basename,
		file_revision,
	)


def rows(*photos, scoped=1, attached=None, orphan=0, isolation="REPEATABLE-READ"):
	attached = len(photos) if attached is None else attached
	return (
		meta(scoped=scoped, photos=len(photos), attached=attached, orphan=orphan, isolation=isolation),
		*photos,
	)


class FatalProbe(BaseException):
	pass


class TestRepairPhotoEvidenceStore(unittest.TestCase):
	def assert_code(self, code, call):
		with self.assertRaises(RepairPhotoEvidenceStoreError) as raised:
			call()
		self.assertIs(raised.exception.code, code)
		self.assertEqual(str(raised.exception), code.value)

	def read(self, value, *, scoped_access=None):
		return read_scoped_repair_photo_evidence(scoped_access or access(), reader=lambda **_kwargs: value)

	def test_public_id_resolver_issues_one_redacted_sealed_access(self):
		calls = []

		def reader(**kwargs):
			calls.append(kwargs)
			return ((REPAIR_NAME,),)

		proof = resolve_actor_scoped_repair_access(repair_id=REPAIR_ID, actor_identity=ACTOR, reader=reader)
		self.assertIs(type(proof), ActorScopedRepairAccess)
		self.assertEqual(repr(proof), "ActorScopedRepairAccess(<redacted>)")
		self.assertEqual(calls, [{"repair_id": REPAIR_ID, "actor_identity": ACTOR, "limit": 2}])

	def test_public_id_resolver_rejects_missing_duplicate_and_malformed_rows(self):
		for value, code in (
			((), RepairPhotoEvidenceStoreCode.SCOPED_REPAIR_NOT_FOUND),
			(((REPAIR_NAME,), ("NAP-2",)), RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED),
			(((1,),), RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED),
		):
			with self.subTest(value=value):
				self.assert_code(
					code,
					lambda value=value: resolve_actor_scoped_repair_access(
						repair_id=REPAIR_ID,
						actor_identity=ACTOR,
						reader=lambda **_kwargs: value,
					),
				)

	def test_empty_and_one_photo_return_exact_g064_dto(self):
		self.assertEqual(self.read(rows()), ())
		result = self.read(rows(photo()))
		self.assertEqual(len(result), 1)
		self.assertIs(type(result[0]), ScopedRepairPhotoEvidence)
		self.assertEqual(result[0].position, 1)
		self.assertTrue(result[0].is_private)
		self.assertTrue(result[0].exact_attachment)
		self.assertTrue(result[0].metadata_only)

	def test_file_capability_is_exact_sealed_frozen_and_redacted(self):
		result = read_scoped_repair_photo_file_access(access(), reader=lambda **_kwargs: rows(photo()))
		self.assertEqual(len(result), 1)
		self.assertIs(type(result[0]), ScopedPrivateFileAccess)
		self.assertEqual(repr(result[0]), "ScopedPrivateFileAccess(<redacted>)")
		for marker in (REPAIR_NAME, REPAIR_ID, ACTOR, URL, FILE_ID, FILE_REVISION):
			self.assertNotIn(marker, repr(result[0]))
		with self.assertRaises(FrozenInstanceError):
			result[0].evidence = None

	def test_file_capability_revalidation_requires_exact_current_snapshot(self):
		proof = read_scoped_repair_photo_file_access(access(), reader=lambda **_kwargs: rows(photo()))[0]
		revalidate_scoped_repair_photo_file_access(proof, reader=lambda **_kwargs: rows(photo()))
		for changed in (
			rows(photo(file_identity="G080-OTHER")),
			rows(photo(file_revision="2026-08-15 10:11:13.000000")),
		):
			self.assert_code(
				RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE,
				lambda changed=changed: revalidate_scoped_repair_photo_file_access(
					proof, reader=lambda **_kwargs: changed
				),
			)
		self.assert_code(
			RepairPhotoEvidenceStoreCode.SCOPED_REPAIR_NOT_FOUND,
			lambda: revalidate_scoped_repair_photo_file_access(
				proof, reader=lambda **_kwargs: rows(scoped=0, attached=0)
			),
		)

	def test_forged_file_capability_and_nonlocal_binding_fail_closed(self):
		forged = object.__new__(ScopedPrivateFileAccess)
		self.assert_code(
			RepairPhotoEvidenceStoreCode.INVALID_INPUT,
			lambda: revalidate_scoped_repair_photo_file_access(forged, reader=lambda **_kwargs: rows()),
		)
		for item in (
			photo(file_basename="nested/synthetic.png"),
			photo(file_basename="other.png"),
			photo(file_revision=""),
		):
			self.assert_code(
				RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED,
				lambda item=item: read_scoped_repair_photo_file_access(
					access(), reader=lambda **_kwargs: rows(item)
				),
			)

	def test_positions_are_deterministic_and_maximum_count_is_accepted(self):
		items = tuple(
			photo(position, url=f"/private/files/{position}.png")
			for position in range(MAX_PHOTOS_PER_REPAIR, 0, -1)
		)
		result = self.read(rows(*items))
		self.assertEqual(tuple(item.position for item in result), tuple(range(1, 21)))

	def test_capability_is_sealed_frozen_and_redacted(self):
		proof = access()
		self.assertEqual(repr(proof), "ActorScopedRepairAccess(<redacted>)")
		for marker in (REPAIR_NAME, REPAIR_ID, ACTOR):
			self.assertNotIn(marker, repr(proof))
		with self.assertRaises(FrozenInstanceError):
			proof.repair_id = "rpr_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
		self.assert_code(
			RepairPhotoEvidenceStoreCode.INVALID_INPUT,
			lambda: ActorScopedRepairAccess(
				repair_name=REPAIR_NAME,
				repair_id=REPAIR_ID,
				actor_identity=ACTOR,
				_seal=object(),
			),
		)

	def test_capability_rejects_invalid_exact_inputs(self):
		for overrides in (
			{"repair_name": ""},
			{"repair_name": "bad\x00name"},
			{"repair_name": 1},
			{"repair_id": "NAP-1"},
			{"repair_id": str.__new__(type("Text", (str,), {}), REPAIR_ID)},
			{"actor_identity": "Guest"},
			{"actor_identity": "bad\nactor"},
			{"actor_identity": 1},
		):
			with self.subTest(overrides=overrides):
				self.assert_code(RepairPhotoEvidenceStoreCode.INVALID_INPUT, lambda: access(**overrides))

	def test_forged_and_partial_capabilities_fail_before_reader(self):
		calls = []

		def reader(**kwargs):
			calls.append(kwargs)
			return rows()

		for forged in (object(), object.__new__(ActorScopedRepairAccess)):
			with self.subTest(forged=type(forged)):
				self.assert_code(
					RepairPhotoEvidenceStoreCode.INVALID_INPUT,
					lambda forged=forged: read_scoped_repair_photo_evidence(forged, reader=reader),
				)
		self.assertEqual(calls, [])

	def test_reader_receives_exact_bounded_identity_once(self):
		calls = []

		def reader(**kwargs):
			calls.append(kwargs)
			return rows()

		self.assertEqual(read_scoped_repair_photo_evidence(access(), reader=reader), ())
		self.assertEqual(
			calls,
			[
				{
					"repair_name": REPAIR_NAME,
					"repair_id": REPAIR_ID,
					"actor_identity": ACTOR,
					"fetch_limit": MAX_PHOTOS_PER_REPAIR + 1,
				}
			],
		)

	def test_wrong_actor_revoked_and_missing_are_indistinguishable(self):
		for value in (rows(scoped=0, attached=0),):
			self.assert_code(
				RepairPhotoEvidenceStoreCode.SCOPED_REPAIR_NOT_FOUND,
				lambda value=value: self.read(value),
			)
		with self.assertRaises(RepairPhotoEvidenceStoreError) as missing_context:
			self.read(rows(scoped=0, attached=0))
		with self.assertRaises(RepairPhotoEvidenceStoreError) as foreign_context:
			self.read(rows(scoped=0, attached=0))
		missing = missing_context.exception
		foreign = foreign_context.exception
		self.assertEqual((str(missing), repr(missing)), (str(foreign), repr(foreign)))

	def test_not_found_with_rows_is_malformed_not_an_oracle(self):
		self.assert_code(
			RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED,
			lambda: self.read((meta(scoped=0, photos=1, attached=0), photo())),
		)

	def test_all_metadata_defects_use_one_code(self):
		cases = (
			rows(photo(url="/files/public.png")),
			rows(photo(url="/private/files/../bad.png")),
			rows(photo(match_state=0), attached=0),
			rows(photo(match_state=2)),
			rows(photo(match_state=3), attached=2),
			rows(photo(), orphan=1, attached=2),
			rows(photo(), attached=0),
			rows(photo(), photo(2), attached=2),
			rows(photo(), photo(2, url=URL, duplicate=1), attached=2),
		)
		for value in cases:
			with self.subTest(value=value):
				self.assert_code(
					RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE,
					lambda value=value: self.read(value),
				)

	def test_duplicate_position_is_unsafe(self):
		value = rows(photo(1), photo(1, url="/private/files/other.png"), attached=2)
		self.assert_code(RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE, lambda: self.read(value))

	def test_too_many_children_or_attachments_is_unsafe(self):
		items = tuple(
			photo(position, url=f"/private/files/{position}.png")
			for position in range(1, MAX_PHOTOS_PER_REPAIR + 2)
		)
		self.assert_code(
			RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE,
			lambda: self.read(rows(*items)),
		)
		self.assert_code(
			RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE,
			lambda: self.read((meta(photos=0, attached=21),)),
		)

	def test_url_length_mismatch_and_oversize_are_unsafe_without_echo(self):
		marker = "/private/files/" + "X" * 498
		for value in (rows(photo(url_chars=len(URL) + 1)), rows(photo(url=marker))):
			with self.subTest(length=len(value[-1][7])):
				self.assert_code(
					RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE,
					lambda value=value: self.read(value),
				)
				try:
					self.read(value)
				except RepairPhotoEvidenceStoreError as error:
					self.assertNotIn(marker, repr(error))

	def test_isolation_allowlist_and_inconsistent_rows(self):
		for allowed in ("READ-COMMITTED", "REPEATABLE_READ", "SERIALIZABLE"):
			self.assertEqual(self.read(rows(isolation=allowed)), ())
		self.assert_code(
			RepairPhotoEvidenceStoreCode.UNSAFE_ISOLATION,
			lambda: self.read(rows(isolation="READ-UNCOMMITTED")),
		)
		self.assert_code(
			RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED,
			lambda: self.read((meta(photos=1, attached=1), photo(isolation="READ-COMMITTED"))),
		)

	def test_malformed_outer_and_rows_fail_code_only(self):
		values = (None, [], (), (object(),), (("REPEATABLE-READ",),), rows() + (photo(2),) * 22)
		for value in values:
			with self.subTest(value_type=type(value)):
				self.assert_code(
					RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED,
					lambda value=value: self.read(value),
				)

	def test_malformed_scalar_types_do_not_accept_bool_confusion(self):
		for value in (
			(meta(scoped=True),),
			(meta(photos=True),),
			(meta(attached=True),),
			(meta(orphan="0"),),
			(meta(photos=1, attached=1), photo(position=True)),
			(meta(photos=1, attached=1), photo(match_state=True)),
		):
			with self.subTest(value=value):
				self.assert_code(
					RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED,
					lambda value=value: self.read(value),
				)

	def test_reader_failures_are_sanitized_and_baseexception_escapes(self):
		marker = "customer@example.test"

		def broken(**_kwargs):
			raise ValueError(marker)

		self.assert_code(
			RepairPhotoEvidenceStoreCode.EVIDENCE_READ_FAILED,
			lambda: read_scoped_repair_photo_evidence(access(), reader=broken),
		)

		def fatal(**_kwargs):
			raise FatalProbe

		with self.assertRaises(FatalProbe):
			read_scoped_repair_photo_evidence(access(), reader=fatal)

	def test_forged_reader_errors_do_not_cross_boundary(self):
		class Forged(RepairPhotoEvidenceStoreError):
			pass

		partial = RepairPhotoEvidenceStoreError.__new__(RepairPhotoEvidenceStoreError)
		for error in (
			Forged(RepairPhotoEvidenceStoreCode.UNSUPPORTED_DATABASE),
			partial,
		):

			def reader(error=error, **_kwargs):
				raise error

			self.assert_code(
				RepairPhotoEvidenceStoreCode.EVIDENCE_READ_FAILED,
				lambda reader=reader: read_scoped_repair_photo_evidence(access(), reader=reader),
			)

	def test_trusted_reader_codes_are_reconstructed(self):
		for code in (
			RepairPhotoEvidenceStoreCode.UNSUPPORTED_DATABASE,
			RepairPhotoEvidenceStoreCode.EVIDENCE_READ_FAILED,
		):

			def reader(code=code, **_kwargs):
				raise RepairPhotoEvidenceStoreError(code)

			self.assert_code(
				code, lambda reader=reader: read_scoped_repair_photo_evidence(access(), reader=reader)
			)

	def test_output_and_failures_do_not_expose_storage_or_actor_data(self):
		result = self.read(rows(photo()))
		rendered = repr(result)
		for marker in (REPAIR_NAME, REPAIR_ID, ACTOR, URL):
			self.assertNotIn(marker, rendered)
		self.assertNotIn("file", rendered.lower())
		self.assertNotIn("url", rendered.lower())

	def test_sql_contract_is_single_read_only_bounded_statement(self):
		upper = EVIDENCE_SQL.upper()
		self.assertEqual(EVIDENCE_SQL.count(";"), 0)
		self.assertTrue(upper.lstrip().startswith("WITH\n"))
		self.assertEqual(upper.count("%(FETCH_LIMIT)S"), 2)
		for forbidden in (
			"FOR UPDATE",
			"FOR SHARE",
			"START TRANSACTION",
			"COMMIT",
			"ROLLBACK",
			"INSERT ",
			"UPDATE ",
			"DELETE ",
			"GET_CONTENT",
		):
			self.assertNotIn(forbidden, upper)

	def test_revalidation_is_one_bounded_current_read_without_transaction_ownership(self):
		upper = FILE_REVALIDATION_SQL.upper()
		self.assertEqual(FILE_REVALIDATION_SQL.count(";"), 0)
		self.assertIn("LOCK IN SHARE MODE", upper)
		self.assertEqual(upper.count("%(ROW_LIMIT)S"), 1)
		self.assertIn("FORCE INDEX (`file_url_index`)", FILE_REVALIDATION_SQL)
		self.assertIn("FORCE INDEX (`parent`)", FILE_REVALIDATION_SQL)
		for forbidden in (
			"START TRANSACTION",
			"COMMIT",
			"ROLLBACK",
			"INSERT ",
			"UPDATE ",
			"DELETE ",
			"GET_CONTENT",
			"LOAD_FILE",
			"CONTENT_HASH",
		):
			self.assertNotIn(forbidden, upper)

	def test_module_has_no_blob_filesystem_or_network_boundary(self):
		path = Path(inspect.getfile(read_scoped_repair_photo_evidence))
		source = path.read_text(encoding="utf-8")
		tree = ast.parse(source)
		imports = {
			alias.name.split(".", 1)[0]
			for node in ast.walk(tree)
			for alias in (node.names if isinstance(node, ast.Import) else ())
		}
		self.assertTrue(imports.isdisjoint({"os", "pathlib", "requests", "socket", "urllib"}))
		self.assertNotIn("get_content", source)


if __name__ == "__main__":
	unittest.main()
