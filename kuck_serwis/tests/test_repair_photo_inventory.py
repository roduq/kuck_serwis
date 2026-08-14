import unittest
from hashlib import sha256

from kuck_serwis.repair_photo_inventory import (
	MAX_CHILD_ROWS_PER_SOURCE,
	RepairPhotoInventoryCode,
	RepairPhotoInventoryCounters,
	RepairPhotoInventoryError,
	RepairPhotoInventoryLimits,
	RepairPhotoInventoryReport,
	RepairPhotoInventoryStatus,
	collect_repair_photo_inventory,
)

PRIVATE_URL = "/private/files/synthetic-photo.png"
PUBLIC_URL = "/files/legacy-photo.png"


def _meta(isolation="REPEATABLE-READ"):
	return (
		isolation,
		"META",
		"META",
		"",
		0,
		"",
		0,
		"",
		"",
		"",
		0,
		None,
		"",
		"",
		0,
		"",
		None,
		None,
		"0" * 64,
	)


def _ref(
	*,
	source="NAPRAWA",
	record_id="row-1",
	owner_name="SER-00001",
	parenttype=None,
	parentfield=None,
	file_url=PRIVATE_URL,
	record_id_bytes=None,
	owner_name_bytes=None,
	url_bytes=None,
):
	if parenttype is None:
		parenttype = "Naprawa" if source == "NAPRAWA" else "Przyjecie Zbiorcze"
	if parentfield is None:
		parentfield = "zdjecia" if source == "NAPRAWA" else "pozycje"
	return (
		"REPEATABLE-READ",
		"REF",
		source,
		record_id,
		len(record_id.encode()) if record_id_bytes is None else record_id_bytes,
		owner_name,
		len(owner_name.encode()) if owner_name_bytes is None else owner_name_bytes,
		parenttype,
		parentfield,
		file_url,
		len(file_url.encode()) if url_bytes is None else url_bytes,
		None,
		"",
		"",
		0,
		"",
		None,
		None,
		sha256(file_url.encode()).hexdigest(),
	)


def _file(
	*,
	record_id="file-1",
	file_url=PRIVATE_URL,
	is_private=1,
	attached_to_doctype="Naprawa",
	attached_to_name="SER-00001",
	attached_to_field="zdjecie",
	is_folder=0,
	record_id_bytes=None,
	attached_name_bytes=None,
	url_bytes=None,
):
	return (
		"REPEATABLE-READ",
		"FILE",
		"FILE",
		record_id,
		len(record_id.encode()) if record_id_bytes is None else record_id_bytes,
		"",
		0,
		"",
		"",
		file_url,
		len(file_url.encode()) if url_bytes is None else url_bytes,
		is_private,
		attached_to_doctype,
		attached_to_name,
		len(attached_to_name.encode()) if attached_name_bytes is None else attached_name_bytes,
		attached_to_field,
		is_folder,
		None,
		sha256(file_url.encode()).hexdigest(),
	)


def _collect(*rows, limits=None):
	files = [row for row in rows if row[1] == "FILE"]
	prepared = []
	for row in rows:
		if row[1] != "REF" or not (1 <= row[10] <= 512):
			prepared.append(row)
			continue
		matches = [item for item in files if item[9] == row[9]]
		if not matches:
			state = 0
		elif len(matches) > 1:
			state = 3
		else:
			item = matches[0]
			expected_doctype = "Naprawa" if row[2] == "NAPRAWA" else "Przyjecie Zbiorcze"
			expected_private = row[9].startswith("/private/files/")
			exact = (
				item[11] in (expected_private, int(expected_private))
				and item[12] == expected_doctype
				and item[13] == row[5]
				and item[15] == "zdjecie"
				and item[16] in (False, 0)
			)
			state = 1 if exact else 2
		prepared.append((*row[:17], state, row[18]))
	return collect_repair_photo_inventory(limits=limits, reader=lambda **_kwargs: (_meta(), *prepared))


class _StopProbe(BaseException):
	pass


class TestRepairPhotoInventory(unittest.TestCase):
	def assert_code(self, code, callback):
		with self.assertRaises(RepairPhotoInventoryError) as raised:
			callback()
		self.assertIs(raised.exception.code, code)
		self.assertEqual(str(raised.exception), code.value)

	def test_empty_inventory_is_complete_and_count_only(self):
		report = _collect()
		self.assertIs(report.status, RepairPhotoInventoryStatus.COMPLETE)
		self.assertEqual(report.counters, RepairPhotoInventoryCounters())

	def test_reader_is_called_exactly_once_with_limit_plus_one(self):
		calls = []

		def reader(**kwargs):
			calls.append(kwargs)
			return (_meta(),)

		limits = RepairPhotoInventoryLimits(child_rows_per_source=2, file_rows=3)
		collect_repair_photo_inventory(limits=limits, reader=reader)
		self.assertEqual(calls, [{"child_fetch_limit": 3, "file_fetch_limit": 4}])

	def test_exact_private_references_for_both_sources(self):
		report = _collect(
			_ref(),
			_ref(source="PRZYJECIE", record_id="row-2", owner_name="PZ-00001"),
			_file(),
			_file(
				record_id="file-2",
				attached_to_doctype="Przyjecie Zbiorcze",
				attached_to_name="PZ-00001",
			),
		)
		self.assertEqual(report.counters.private_reference_rows, 2)
		self.assertEqual(report.counters.private_duplicate_file_rows, 2)

	def test_exact_private_reference(self):
		report = _collect(_ref(), _file())
		self.assertEqual(report.counters.private_exact_rows, 1)

	def test_private_missing_file(self):
		self.assertEqual(_collect(_ref()).counters.private_missing_file_rows, 1)

	def test_private_mismatched_file_metadata(self):
		for item in (
			_file(is_private=0),
			_file(attached_to_doctype="Przyjecie Zbiorcze"),
			_file(attached_to_name="SER-OTHER"),
			_file(attached_to_field="other"),
			_file(is_folder=1),
		):
			with self.subTest(item=item):
				self.assertEqual(_collect(_ref(), item).counters.private_mismatched_file_rows, 1)

	def test_private_duplicate_file_rows_win_over_exact(self):
		report = _collect(_ref(), _file(), _file(record_id="file-2"))
		self.assertEqual(report.counters.private_duplicate_file_rows, 1)
		self.assertEqual(report.counters.private_exact_rows, 0)

	def test_exact_legacy_public_reference_still_only_counts_metadata(self):
		report = _collect(_ref(file_url=PUBLIC_URL), _file(file_url=PUBLIC_URL, is_private=0))
		self.assertEqual(report.counters.legacy_public_exact_rows, 1)

	def test_legacy_public_missing_file(self):
		self.assertEqual(
			_collect(_ref(file_url=PUBLIC_URL)).counters.legacy_public_missing_file_rows,
			1,
		)

	def test_legacy_public_mismatched_file(self):
		report = _collect(_ref(file_url=PUBLIC_URL), _file(file_url=PUBLIC_URL, is_private=1))
		self.assertEqual(report.counters.legacy_public_mismatched_file_rows, 1)

	def test_legacy_public_duplicate_file(self):
		report = _collect(
			_ref(file_url=PUBLIC_URL),
			_file(file_url=PUBLIC_URL, is_private=0),
			_file(record_id="file-2", file_url=PUBLIC_URL, is_private=0),
		)
		self.assertEqual(report.counters.legacy_public_duplicate_file_rows, 1)

	def test_empty_reference_is_counted_without_url_output(self):
		report = _collect(_ref(file_url=""))
		self.assertEqual(report.counters.empty_reference_rows, 1)
		self.assertNotIn(PRIVATE_URL, repr(report))
		self.assertNotIn(PUBLIC_URL, repr(report))

	def test_invalid_child_identity_is_separate_from_reference_shape(self):
		report = _collect(_ref(parentfield="wrong"))
		self.assertEqual(report.counters.invalid_child_identity_rows, 1)

	def test_malformed_reference_is_counted(self):
		report = _collect(_ref(file_url="/files/../synthetic.png"))
		self.assertEqual(report.counters.malformed_reference_rows, 1)

	def test_oversize_reference_is_malformed_without_retaining_it(self):
		url = "/files/" + ("a" * 506)
		report = _collect(_ref(file_url=url, url_bytes=513))
		self.assertEqual(report.counters.empty_reference_rows, 0)
		self.assertEqual(report.counters.malformed_reference_rows, 1)

	def test_reference_length_mismatch_is_malformed_not_empty(self):
		report = _collect(_ref(file_url=PRIVATE_URL, url_bytes=1))
		self.assertEqual(report.counters.empty_reference_rows, 0)
		self.assertEqual(report.counters.malformed_reference_rows, 1)

	def test_duplicate_child_url_groups(self):
		report = _collect(_ref(), _ref(record_id="row-2"), _file())
		self.assertEqual(report.counters.duplicate_child_url_groups, 1)

	def test_duplicate_file_url_groups(self):
		report = _collect(_file(), _file(record_id="file-2"))
		self.assertEqual(report.counters.duplicate_file_url_groups, 1)
		self.assertEqual(report.counters.duplicate_orphan_file_url_groups, 1)

	def test_referenced_duplicate_is_distinct_from_orphan_duplicate(self):
		referenced = _collect(_ref(), _file(), _file(record_id="file-2"))
		orphan = _collect(
			_file(attached_to_name="SER-OTHER"),
			_file(record_id="file-2", attached_to_name="SER-OTHER"),
		)
		self.assertEqual(referenced.counters.duplicate_file_url_groups, 1)
		self.assertEqual(referenced.counters.duplicate_orphan_file_url_groups, 0)
		self.assertEqual(orphan.counters.duplicate_orphan_file_url_groups, 1)

	def test_orphan_public_private_and_malformed_files(self):
		report = _collect(
			_file(file_url=PUBLIC_URL, is_private=0),
			_file(record_id="file-2"),
			_file(record_id="file-3", file_url="invalid", is_private=0),
		)
		self.assertEqual(report.counters.orphan_public_file_rows, 1)
		self.assertEqual(report.counters.orphan_private_file_rows, 1)
		self.assertEqual(report.counters.orphan_malformed_file_rows, 1)

	def test_file_with_exact_attachment_is_not_orphan_even_when_privacy_mismatches(self):
		report = _collect(_ref(), _file(is_private=0))
		self.assertEqual(report.counters.private_mismatched_file_rows, 1)
		self.assertEqual(report.counters.orphan_public_file_rows, 0)

	def test_naprawa_truncation_suppresses_relationship_classification(self):
		limits = RepairPhotoInventoryLimits(child_rows_per_source=1, file_rows=2)
		report = _collect(_ref(), _ref(record_id="row-2"), limits=limits)
		self.assertIs(report.status, RepairPhotoInventoryStatus.TRUNCATED)
		self.assertTrue(report.naprawa_truncated)
		self.assertEqual(report.counters.unclassified_reference_rows, 1)
		self.assertEqual(report.counters.private_reference_rows, 0)

	def test_przyjecie_truncation_is_independent(self):
		limits = RepairPhotoInventoryLimits(child_rows_per_source=1, file_rows=2)
		report = _collect(
			_ref(source="PRZYJECIE", owner_name="PZ-1"),
			_ref(source="PRZYJECIE", record_id="row-2", owner_name="PZ-2"),
			limits=limits,
		)
		self.assertTrue(report.przyjecie_truncated)
		self.assertFalse(report.naprawa_truncated)

	def test_file_truncation_suppresses_false_missing_classification(self):
		limits = RepairPhotoInventoryLimits(child_rows_per_source=2, file_rows=1)
		report = _collect(_ref(), _file(), _file(record_id="file-2"), limits=limits)
		self.assertTrue(report.files_truncated)
		self.assertEqual(report.counters.private_missing_file_rows, 0)

	def test_invalid_limits_reject_bool_zero_and_overflow(self):
		for value in (True, 0, MAX_CHILD_ROWS_PER_SOURCE + 1):
			with self.subTest(value=value):
				self.assert_code(
					RepairPhotoInventoryCode.INVALID_INPUT,
					lambda value=value: RepairPhotoInventoryLimits(child_rows_per_source=value),
				)

	def test_forged_limits_do_not_leak_attribute_error(self):
		forged = object.__new__(RepairPhotoInventoryLimits)
		self.assert_code(
			RepairPhotoInventoryCode.INVALID_INPUT,
			lambda: collect_repair_photo_inventory(limits=forged, reader=lambda **_kwargs: (_meta(),)),
		)

	def test_malformed_outer_and_nested_reader_results_fail_closed(self):
		for value in ([], (), (_meta()[:-1],), (_meta(), ["bad"])):
			with self.subTest(value=value):
				self.assert_code(
					RepairPhotoInventoryCode.INVENTORY_MALFORMED,
					lambda value=value: collect_repair_photo_inventory(reader=lambda **_kwargs: value),
				)

	def test_unsafe_or_inconsistent_isolation_fails_closed(self):
		self.assert_code(
			RepairPhotoInventoryCode.UNSAFE_ISOLATION,
			lambda: collect_repair_photo_inventory(reader=lambda **_kwargs: (_meta("READ-UNCOMMITTED"),)),
		)
		self.assert_code(
			RepairPhotoInventoryCode.INVENTORY_MALFORMED,
			lambda: collect_repair_photo_inventory(
				reader=lambda **_kwargs: (_meta(), ("READ-COMMITTED", *_ref()[1:]))
			),
		)

	def test_reader_exception_is_sanitized_without_echo(self):
		marker = "synthetic-person@example.test"

		def reader(**_kwargs):
			raise ValueError(marker)

		with self.assertRaises(RepairPhotoInventoryError) as raised:
			collect_repair_photo_inventory(reader=reader)
		self.assertIs(raised.exception.code, RepairPhotoInventoryCode.INVENTORY_READ_FAILED)
		self.assertNotIn(marker, str(raised.exception))
		self.assertNotIn(marker, repr(raised.exception))

	def test_forged_inventory_error_and_subclass_are_sanitized(self):
		marker = "synthetic-secret-marker"

		class ForgedInventoryError(RepairPhotoInventoryError):
			pass

		def subclass_reader(**_kwargs):
			error = ForgedInventoryError(RepairPhotoInventoryCode.UNSUPPORTED_DATABASE)
			error.args = (marker,)
			raise error

		def forged_reader(**_kwargs):
			error = object.__new__(RepairPhotoInventoryError)
			error.code = marker
			error.args = (marker,)
			raise error

		for reader in (subclass_reader, forged_reader):
			with self.subTest(reader=reader):
				with self.assertRaises(RepairPhotoInventoryError) as raised:
					collect_repair_photo_inventory(reader=reader)
				self.assertIs(raised.exception.code, RepairPhotoInventoryCode.INVENTORY_READ_FAILED)
				self.assertNotIn(marker, str(raised.exception))

	def test_base_exception_from_reader_is_not_caught(self):
		def reader(**_kwargs):
			raise _StopProbe()

		with self.assertRaises(_StopProbe):
			collect_repair_photo_inventory(reader=reader)

	def test_input_order_does_not_change_counters(self):
		rows = (_ref(), _file(), _file(record_id="file-2", file_url=PUBLIC_URL, is_private=0))
		self.assertEqual(_collect(*rows).counters, _collect(*reversed(rows)).counters)

	def test_report_constructor_enforces_status_and_flags(self):
		self.assert_code(
			RepairPhotoInventoryCode.INVALID_INPUT,
			lambda: RepairPhotoInventoryReport(
				status=RepairPhotoInventoryStatus.COMPLETE,
				counters=RepairPhotoInventoryCounters(),
				naprawa_truncated=True,
				przyjecie_truncated=False,
				files_truncated=False,
			),
		)

	def test_report_rejects_forged_nested_counters(self):
		forged = object.__new__(RepairPhotoInventoryCounters)
		self.assert_code(
			RepairPhotoInventoryCode.INVALID_INPUT,
			lambda: RepairPhotoInventoryReport(
				status=RepairPhotoInventoryStatus.COMPLETE,
				counters=forged,
				naprawa_truncated=False,
				przyjecie_truncated=False,
				files_truncated=False,
			),
		)

	def test_invalid_database_boolean_is_mismatch_not_truthy(self):
		report = _collect(_ref(), _file(is_private="1"))
		self.assertEqual(report.counters.private_mismatched_file_rows, 1)


if __name__ == "__main__":
	unittest.main()
