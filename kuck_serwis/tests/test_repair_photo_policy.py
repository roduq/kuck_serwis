import unittest

from kuck_serwis.repair_photo_policy import (
	PhotoReferenceKind,
	PrivateFileEvidence,
	RepairPhotoPolicyCode,
	RepairPhotoPolicyError,
	RepairPhotoRow,
	classify_photo_reference,
	require_transferable_private_photo,
	validate_repair_photo_references,
)

OWNER = "SER-00001"
PRIVATE_URL = "/private/files/synthetic-photo.png"
PUBLIC_URL = "/files/legacy-photo.png"


def _row(*, name="row-1", parent=OWNER, file_url=PRIVATE_URL):
	return RepairPhotoRow(name=name, parent=parent, file_url=file_url)


def _file(
	*,
	name="file-1",
	file_url=PRIVATE_URL,
	is_private=True,
	attached_to_doctype="Naprawa",
	attached_to_name=OWNER,
	attached_to_field="zdjecie",
):
	return PrivateFileEvidence(
		name=name,
		file_url=file_url,
		is_private=is_private,
		attached_to_doctype=attached_to_doctype,
		attached_to_name=attached_to_name,
		attached_to_field=attached_to_field,
	)


def _validate(*, current=None, stored=(), files=None):
	return validate_repair_photo_references(
		owner_doctype="Naprawa",
		owner_name=OWNER,
		current_rows=(_row(),) if current is None else current,
		stored_rows=stored,
		private_files=(_file(),) if files is None else files,
	)


class TestRepairPhotoPolicy(unittest.TestCase):
	def assert_code(self, code, callback):
		with self.assertRaises(RepairPhotoPolicyError) as raised:
			callback()
		self.assertIs(raised.exception.code, code)
		self.assertEqual(str(raised.exception), code.value)

	def test_classifies_only_canonical_local_references(self):
		self.assertIs(classify_photo_reference(PUBLIC_URL), PhotoReferenceKind.PUBLIC)
		self.assertIs(classify_photo_reference(PRIVATE_URL), PhotoReferenceKind.PRIVATE)

	def test_rejects_external_data_and_unsafe_paths(self):
		unsafe = (
			"https://example.test/photo.png",
			"data:image/png;base64,AAAA",
			"/files/../photo.png",
			"/files/a//photo.png",
			"/files/a\\photo.png",
			"/files/photo.png?token=x",
			"/files/photo.png#x",
			"/files/%2e%2e/photo.png",
			"/files/%2fprivate/photo.png",
			"/files/photo\x00.png",
			"/files/photo name.png",
			"/files/photo<name>.png",
			"/files/photo>name.png",
		)
		for value in unsafe:
			with self.subTest(value=value):
				self.assert_code(
					RepairPhotoPolicyCode.INVALID_PHOTO_REFERENCE,
					lambda value=value: classify_photo_reference(value),
				)

	def test_private_attachment_is_accepted(self):
		result = _validate()
		self.assertEqual((result.private_count, result.legacy_count), (1, 0))

	def test_private_url_without_file_is_rejected(self):
		self.assert_code(
			RepairPhotoPolicyCode.PRIVATE_FILE_REQUIRED,
			lambda: _validate(files=()),
		)

	def test_public_file_disguised_by_private_url_is_rejected(self):
		self.assert_code(
			RepairPhotoPolicyCode.PRIVATE_FILE_MISMATCH,
			lambda: _validate(files=(_file(is_private=False),)),
		)

	def test_wrong_owner_doctype_name_or_field_is_rejected(self):
		for evidence in (
			_file(attached_to_doctype="Przyjecie Zbiorcze"),
			_file(attached_to_name="SER-OTHER"),
			_file(attached_to_field="zdjecia"),
		):
			with self.subTest(evidence=evidence):
				self.assert_code(
					RepairPhotoPolicyCode.PRIVATE_FILE_REQUIRED,
					lambda evidence=evidence: _validate(files=(evidence,)),
				)

	def test_duplicate_matching_file_evidence_is_rejected(self):
		self.assert_code(
			RepairPhotoPolicyCode.PRIVATE_FILE_MISMATCH,
			lambda: _validate(files=(_file(), _file(name="file-2"))),
		)

	def test_exact_unchanged_public_child_is_grandfathered(self):
		legacy = _row(file_url=PUBLIC_URL)
		result = _validate(current=(legacy,), stored=(legacy,), files=())
		self.assertEqual((result.private_count, result.legacy_count), (0, 1))

	def test_new_public_child_is_rejected(self):
		self.assert_code(
			RepairPhotoPolicyCode.PUBLIC_PHOTO_FORBIDDEN,
			lambda: _validate(current=(_row(file_url=PUBLIC_URL),), stored=(), files=()),
		)

	def test_changed_public_child_is_rejected(self):
		self.assert_code(
			RepairPhotoPolicyCode.PUBLIC_PHOTO_FORBIDDEN,
			lambda: _validate(
				current=(_row(file_url="/files/changed.png"),),
				stored=(_row(file_url=PUBLIC_URL),),
				files=(),
			),
		)

	def test_readded_public_child_with_new_name_is_rejected(self):
		self.assert_code(
			RepairPhotoPolicyCode.PUBLIC_PHOTO_FORBIDDEN,
			lambda: _validate(
				current=(_row(name="row-2", file_url=PUBLIC_URL),),
				stored=(_row(name="row-1", file_url=PUBLIC_URL),),
				files=(),
			),
		)

	def test_public_child_with_wrong_parent_is_not_grandfathered(self):
		self.assert_code(
			RepairPhotoPolicyCode.INVALID_INPUT,
			lambda: _validate(
				current=(_row(file_url=PUBLIC_URL),),
				stored=(_row(parent="SER-OTHER", file_url=PUBLIC_URL),),
				files=(),
			),
		)

	def test_transfer_requires_exact_private_source_attachment(self):
		evidence = require_transferable_private_photo(
			owner_doctype="Naprawa",
			owner_name=OWNER,
			file_url=PRIVATE_URL,
			private_files=(_file(),),
		)
		self.assertEqual(evidence.name, "file-1")
		self.assert_code(
			RepairPhotoPolicyCode.PUBLIC_PHOTO_FORBIDDEN,
			lambda: require_transferable_private_photo(
				owner_doctype="Naprawa",
				owner_name=OWNER,
				file_url=PUBLIC_URL,
				private_files=(),
			),
		)

	def test_bool_and_container_spoofs_are_rejected(self):
		self.assert_code(
			RepairPhotoPolicyCode.INVALID_INPUT,
			lambda: _file(is_private=1),
		)
		self.assert_code(
			RepairPhotoPolicyCode.INVALID_INPUT,
			lambda: validate_repair_photo_references(
				owner_doctype="Naprawa",
				owner_name=OWNER,
				current_rows=[_row()],  # type: ignore[arg-type]
				stored_rows=(),
				private_files=(_file(),),
			),
		)

	def test_errors_and_dtos_do_not_expose_identifiers_or_urls(self):
		marker = "/private/files/customer-marker.png"
		row = _row(name="row-secret", parent=OWNER, file_url=marker)
		evidence = _file(name="file-secret", file_url=marker)
		for rendered in (repr(row), repr(evidence)):
			self.assertNotIn(marker, rendered)
			self.assertNotIn("secret", rendered)
		with self.assertRaises(RepairPhotoPolicyError) as raised:
			_validate(current=(row,), files=())
		self.assertNotIn(marker, repr(raised.exception))
		self.assertNotIn(marker, str(raised.exception))


if __name__ == "__main__":
	unittest.main()
