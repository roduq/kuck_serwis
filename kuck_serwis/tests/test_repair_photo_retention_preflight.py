import unittest
from dataclasses import fields
from unittest.mock import patch

from kuck_serwis.operational_policy_v1 import POLICY_REVISION_SHA256
from kuck_serwis.repair_photo_inventory import (
	RepairPhotoInventoryCode,
	RepairPhotoInventoryCounters,
	RepairPhotoInventoryError,
	RepairPhotoInventoryLimits,
	RepairPhotoInventoryReport,
	RepairPhotoInventoryStatus,
)
from kuck_serwis.repair_photo_retention_preflight import (
	RepairPhotoRetentionPreflightCode,
	RepairPhotoRetentionPreflightError,
	RepairPhotoRetentionPreflightErrorCode,
	RepairPhotoRetentionPreflightResult,
	assess_repair_photo_retention_inventory_v1,
	collect_repair_photo_retention_preflight_v1,
)


def _counters(**updates):
	values = {item.name: 0 for item in fields(RepairPhotoInventoryCounters)}
	values.update(updates)
	return RepairPhotoInventoryCounters(**values)


def _report(*, counters=None, truncated=None):
	truncated = truncated or ()
	return RepairPhotoInventoryReport(
		status=(RepairPhotoInventoryStatus.TRUNCATED if truncated else RepairPhotoInventoryStatus.COMPLETE),
		counters=counters or _counters(),
		naprawa_truncated="naprawa" in truncated,
		przyjecie_truncated="przyjecie" in truncated,
		files_truncated="files" in truncated,
	)


class _StopProbe(BaseException):
	pass


class TestRepairPhotoRetentionPreflight(unittest.TestCase):
	def assert_invalid(self, callback):
		with self.assertRaises(RepairPhotoRetentionPreflightError) as raised:
			callback()
		self.assertIs(raised.exception.code, RepairPhotoRetentionPreflightErrorCode.INVALID_INPUT)
		self.assertEqual(str(raised.exception), "INVALID_INPUT")

	def assert_codes(self, result, *codes):
		self.assertEqual(result.codes, codes)

	def test_clean_empty_inventory_is_only_partial_evidence(self):
		result = assess_repair_photo_retention_inventory_v1(_report())
		self.assert_codes(
			result,
			RepairPhotoRetentionPreflightCode.EXISTING_INVENTORY_PARTIAL_EVIDENCE,
		)
		self.assertTrue(result.inventory_evidence_ok)

	def test_clean_exact_private_inventory_is_only_partial_evidence(self):
		result = assess_repair_photo_retention_inventory_v1(
			_report(
				counters=_counters(
					naprawa_child_rows=1,
					file_rows=1,
					private_reference_rows=1,
					private_exact_rows=1,
				)
			)
		)
		self.assertTrue(result.inventory_evidence_ok)
		self.assertFalse(result.retention_evidence_ok)

	def test_every_executable_flag_is_always_false(self):
		result = assess_repair_photo_retention_inventory_v1(_report())
		for fieldname in (
			"retention_evidence_ok",
			"assessment_authorized",
			"dry_run_authorized",
			"purge_authorized",
			"download_authorized",
			"activation_authorized",
			"capability_ready",
		):
			with self.subTest(fieldname=fieldname):
				self.assertIs(getattr(result, fieldname), False)

	def test_result_is_bound_to_exact_policy_revision(self):
		result = assess_repair_photo_retention_inventory_v1(_report())
		self.assertEqual(result.policy_revision_sha256, POLICY_REVISION_SHA256)

	def test_each_truncation_source_is_fail_closed(self):
		for source in ("naprawa", "przyjecie", "files"):
			with self.subTest(source=source):
				result = assess_repair_photo_retention_inventory_v1(
					_report(counters=_counters(unclassified_reference_rows=1), truncated=(source,))
				)
				self.assert_codes(result, RepairPhotoRetentionPreflightCode.INVENTORY_TRUNCATED)
				self.assertFalse(result.inventory_evidence_ok)

	def test_empty_reference_maps_to_child_gap(self):
		result = assess_repair_photo_retention_inventory_v1(
			_report(counters=_counters(naprawa_child_rows=1, empty_reference_rows=1))
		)
		self.assert_codes(result, RepairPhotoRetentionPreflightCode.EMPTY_OR_INVALID_CHILD_PRESENT)

	def test_invalid_child_identity_maps_to_child_gap(self):
		result = assess_repair_photo_retention_inventory_v1(
			_report(counters=_counters(przyjecie_child_rows=1, invalid_child_identity_rows=1))
		)
		self.assert_codes(result, RepairPhotoRetentionPreflightCode.EMPTY_OR_INVALID_CHILD_PRESENT)

	def test_malformed_reference_maps_to_public_or_malformed_gap(self):
		result = assess_repair_photo_retention_inventory_v1(
			_report(counters=_counters(naprawa_child_rows=1, malformed_reference_rows=1))
		)
		self.assert_codes(result, RepairPhotoRetentionPreflightCode.PUBLIC_OR_MALFORMED_REFERENCE_PRESENT)

	def test_every_public_binding_state_maps_to_public_gap(self):
		for fieldname in (
			"legacy_public_exact_rows",
			"legacy_public_missing_file_rows",
			"legacy_public_mismatched_file_rows",
			"legacy_public_duplicate_file_rows",
		):
			with self.subTest(fieldname=fieldname):
				result = assess_repair_photo_retention_inventory_v1(
					_report(
						counters=_counters(
							naprawa_child_rows=1,
							public_reference_rows=1,
							**{fieldname: 1},
						)
					)
				)
				self.assert_codes(
					result,
					RepairPhotoRetentionPreflightCode.PUBLIC_OR_MALFORMED_REFERENCE_PRESENT,
				)

	def test_every_unsafe_private_binding_maps_to_binding_gap(self):
		for fieldname in (
			"private_missing_file_rows",
			"private_mismatched_file_rows",
			"private_duplicate_file_rows",
		):
			with self.subTest(fieldname=fieldname):
				result = assess_repair_photo_retention_inventory_v1(
					_report(
						counters=_counters(
							naprawa_child_rows=1,
							private_reference_rows=1,
							**{fieldname: 1},
						)
					)
				)
				self.assert_codes(result, RepairPhotoRetentionPreflightCode.PRIVATE_BINDING_NOT_PROVEN)

	def test_each_duplicate_counter_maps_to_duplicate_gap(self):
		for fieldname in (
			"duplicate_child_url_groups",
			"duplicate_file_url_groups",
			"duplicate_orphan_file_url_groups",
		):
			with self.subTest(fieldname=fieldname):
				result = assess_repair_photo_retention_inventory_v1(
					_report(counters=_counters(**{fieldname: 1}))
				)
				self.assert_codes(result, RepairPhotoRetentionPreflightCode.DUPLICATE_REFERENCE_PRESENT)

	def test_each_orphan_counter_maps_to_orphan_gap(self):
		for fieldname in (
			"orphan_public_file_rows",
			"orphan_private_file_rows",
			"orphan_malformed_file_rows",
		):
			with self.subTest(fieldname=fieldname):
				result = assess_repair_photo_retention_inventory_v1(
					_report(counters=_counters(file_rows=1, **{fieldname: 1}))
				)
				self.assert_codes(result, RepairPhotoRetentionPreflightCode.ORPHAN_FILE_PRESENT)

	def test_unclassified_complete_evidence_is_fail_closed(self):
		result = assess_repair_photo_retention_inventory_v1(
			_report(counters=_counters(unclassified_reference_rows=1))
		)
		self.assert_codes(result, RepairPhotoRetentionPreflightCode.UNCLASSIFIED_REFERENCE_PRESENT)

	def test_multiple_gaps_use_fixed_enum_order(self):
		result = assess_repair_photo_retention_inventory_v1(
			_report(
				counters=_counters(
					naprawa_child_rows=2,
					empty_reference_rows=1,
					private_reference_rows=1,
					private_missing_file_rows=1,
					duplicate_child_url_groups=1,
					orphan_private_file_rows=1,
				)
			)
		)
		self.assert_codes(
			result,
			RepairPhotoRetentionPreflightCode.EMPTY_OR_INVALID_CHILD_PRESENT,
			RepairPhotoRetentionPreflightCode.PRIVATE_BINDING_NOT_PROVEN,
			RepairPhotoRetentionPreflightCode.DUPLICATE_REFERENCE_PRESENT,
			RepairPhotoRetentionPreflightCode.ORPHAN_FILE_PRESENT,
		)

	def test_report_type_is_exact(self):
		for value in (None, object(), {}):
			with self.subTest(value=value):
				self.assert_invalid(lambda value=value: assess_repair_photo_retention_inventory_v1(value))

	def test_forged_report_does_not_leak_attribute_error(self):
		forged = object.__new__(RepairPhotoInventoryReport)
		self.assert_invalid(lambda: assess_repair_photo_retention_inventory_v1(forged))

	def test_forged_nested_counters_are_rejected(self):
		report = _report()
		object.__setattr__(report, "counters", object.__new__(RepairPhotoInventoryCounters))
		self.assert_invalid(lambda: assess_repair_photo_retention_inventory_v1(report))

	def test_inconsistent_child_totals_are_rejected(self):
		report = _report(counters=_counters(naprawa_child_rows=1))
		self.assert_invalid(lambda: assess_repair_photo_retention_inventory_v1(report))

	def test_inconsistent_public_binding_totals_are_rejected(self):
		report = _report(
			counters=_counters(
				naprawa_child_rows=1,
				public_reference_rows=1,
				legacy_public_exact_rows=2,
			)
		)
		self.assert_invalid(lambda: assess_repair_photo_retention_inventory_v1(report))

	def test_inconsistent_private_binding_totals_are_rejected(self):
		report = _report(
			counters=_counters(
				naprawa_child_rows=1,
				private_reference_rows=1,
				private_exact_rows=2,
			)
		)
		self.assert_invalid(lambda: assess_repair_photo_retention_inventory_v1(report))

	def test_repr_is_code_only_and_redacts_policy(self):
		result = assess_repair_photo_retention_inventory_v1(_report())
		self.assertNotIn(POLICY_REVISION_SHA256, repr(result))
		self.assertIn("EXISTING_INVENTORY_PARTIAL_EVIDENCE", repr(result))

	def test_result_constructor_rejects_noncanonical_codes(self):
		self.assert_invalid(
			lambda: RepairPhotoRetentionPreflightResult(
				codes=(
					RepairPhotoRetentionPreflightCode.ORPHAN_FILE_PRESENT,
					RepairPhotoRetentionPreflightCode.EMPTY_OR_INVALID_CHILD_PRESENT,
				),
				policy_revision_sha256=POLICY_REVISION_SHA256,
				inventory_evidence_ok=False,
			)
		)

	def test_result_constructor_rejects_mixed_exclusive_code(self):
		self.assert_invalid(
			lambda: RepairPhotoRetentionPreflightResult(
				codes=(
					RepairPhotoRetentionPreflightCode.INVENTORY_UNAVAILABLE,
					RepairPhotoRetentionPreflightCode.INVENTORY_TRUNCATED,
				),
				policy_revision_sha256=POLICY_REVISION_SHA256,
				inventory_evidence_ok=False,
			)
		)

	def test_result_constructor_rejects_true_executable_flags(self):
		for fieldname in (
			"retention_evidence_ok",
			"assessment_authorized",
			"dry_run_authorized",
			"purge_authorized",
			"download_authorized",
			"activation_authorized",
			"capability_ready",
		):
			with self.subTest(fieldname=fieldname):
				kwargs = {fieldname: True}
				self.assert_invalid(
					lambda kwargs=kwargs: RepairPhotoRetentionPreflightResult(
						codes=(RepairPhotoRetentionPreflightCode.INVENTORY_UNAVAILABLE,),
						policy_revision_sha256=POLICY_REVISION_SHA256,
						inventory_evidence_ok=False,
						**kwargs,
					)
				)

	def test_result_constructor_rejects_wrong_policy_revision(self):
		self.assert_invalid(
			lambda: RepairPhotoRetentionPreflightResult(
				codes=(RepairPhotoRetentionPreflightCode.INVENTORY_UNAVAILABLE,),
				policy_revision_sha256="0" * 64,
				inventory_evidence_ok=False,
			)
		)

	def test_collector_calls_inventory_once_with_exact_inputs(self):
		limits = RepairPhotoInventoryLimits(child_rows_per_source=7, file_rows=11)

		def reader(**_kwargs):
			return ()

		with patch(
			"kuck_serwis.repair_photo_retention_preflight.collect_repair_photo_inventory",
			return_value=_report(),
		) as collector:
			result = collect_repair_photo_retention_preflight_v1(limits=limits, reader=reader)
		collector.assert_called_once_with(limits=limits, reader=reader)
		self.assertTrue(result.inventory_evidence_ok)

	def test_collector_rebuilds_limits_before_forwarding(self):
		limits = RepairPhotoInventoryLimits(child_rows_per_source=7, file_rows=11)
		with patch(
			"kuck_serwis.repair_photo_retention_preflight.collect_repair_photo_inventory",
			return_value=_report(),
		) as collector:
			collect_repair_photo_retention_preflight_v1(limits=limits)
		forwarded = collector.call_args.kwargs["limits"]
		self.assertEqual(forwarded, limits)
		self.assertIsNot(forwarded, limits)

	def test_collector_rejects_invalid_limits_and_reader(self):
		for kwargs in ({"limits": object()}, {"reader": object()}):
			with self.subTest(kwargs=kwargs):
				self.assert_invalid(
					lambda kwargs=kwargs: collect_repair_photo_retention_preflight_v1(**kwargs)
				)

	def test_collector_maps_trusted_inventory_failures_to_unavailable(self):
		for code in (
			RepairPhotoInventoryCode.UNSUPPORTED_DATABASE,
			RepairPhotoInventoryCode.UNSAFE_ISOLATION,
			RepairPhotoInventoryCode.INVENTORY_READ_FAILED,
			RepairPhotoInventoryCode.INVENTORY_MALFORMED,
		):
			with self.subTest(code=code):
				with patch(
					"kuck_serwis.repair_photo_retention_preflight.collect_repair_photo_inventory",
					side_effect=RepairPhotoInventoryError(code),
				):
					result = collect_repair_photo_retention_preflight_v1()
				self.assert_codes(result, RepairPhotoRetentionPreflightCode.INVENTORY_UNAVAILABLE)

	def test_collector_preserves_only_its_own_invalid_input(self):
		with patch(
			"kuck_serwis.repair_photo_retention_preflight.collect_repair_photo_inventory",
			side_effect=RepairPhotoInventoryError(RepairPhotoInventoryCode.INVALID_INPUT),
		):
			self.assert_invalid(collect_repair_photo_retention_preflight_v1)

	def test_collector_sanitizes_forged_and_subclassed_inventory_errors(self):
		marker = "synthetic-person@example.test"

		class SubclassedError(RepairPhotoInventoryError):
			pass

		for error in (
			SubclassedError(RepairPhotoInventoryCode.INVALID_INPUT),
			RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_READ_FAILED),
		):
			with self.subTest(error=error):
				error.args = (marker,)
				with patch(
					"kuck_serwis.repair_photo_retention_preflight.collect_repair_photo_inventory",
					side_effect=error,
				):
					result = collect_repair_photo_retention_preflight_v1()
				self.assert_codes(result, RepairPhotoRetentionPreflightCode.INVENTORY_UNAVAILABLE)
				self.assertNotIn(marker, repr(result))

	def test_collector_sanitizes_unexpected_exception(self):
		with patch(
			"kuck_serwis.repair_photo_retention_preflight.collect_repair_photo_inventory",
			side_effect=RuntimeError("synthetic-person@example.test"),
		):
			result = collect_repair_photo_retention_preflight_v1()
		self.assert_codes(result, RepairPhotoRetentionPreflightCode.INVENTORY_UNAVAILABLE)

	def test_collector_maps_internally_inconsistent_report_to_unavailable(self):
		with patch(
			"kuck_serwis.repair_photo_retention_preflight.collect_repair_photo_inventory",
			return_value=_report(counters=_counters(naprawa_child_rows=1)),
		):
			result = collect_repair_photo_retention_preflight_v1()
		self.assert_codes(result, RepairPhotoRetentionPreflightCode.INVENTORY_UNAVAILABLE)

	def test_collector_does_not_catch_base_exception(self):
		with patch(
			"kuck_serwis.repair_photo_retention_preflight.collect_repair_photo_inventory",
			side_effect=_StopProbe(),
		):
			with self.assertRaises(_StopProbe):
				collect_repair_photo_retention_preflight_v1()


if __name__ == "__main__":
	unittest.main()
