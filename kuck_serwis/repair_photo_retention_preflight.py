"""Dark, read-only preflight for existing repair-photo inventory evidence.

This module composes the existing bounded metadata inventory.  It does not
derive a lifecycle timestamp, inspect blobs, read legal holds, authorize a
retention dry-run, delete data, serve a download, or enable a capability.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import StrEnum
from typing import Final

from kuck_serwis.operational_policy_v1 import POLICY_REVISION_SHA256
from kuck_serwis.repair_photo_inventory import (
	PhotoInventoryReader,
	RepairPhotoInventoryCode,
	RepairPhotoInventoryCounters,
	RepairPhotoInventoryError,
	RepairPhotoInventoryLimits,
	RepairPhotoInventoryReport,
	RepairPhotoInventoryStatus,
	collect_repair_photo_inventory,
)


class RepairPhotoRetentionPreflightCode(StrEnum):
	INVENTORY_UNAVAILABLE = "INVENTORY_UNAVAILABLE"
	INVENTORY_TRUNCATED = "INVENTORY_TRUNCATED"
	EMPTY_OR_INVALID_CHILD_PRESENT = "EMPTY_OR_INVALID_CHILD_PRESENT"
	PUBLIC_OR_MALFORMED_REFERENCE_PRESENT = "PUBLIC_OR_MALFORMED_REFERENCE_PRESENT"
	PRIVATE_BINDING_NOT_PROVEN = "PRIVATE_BINDING_NOT_PROVEN"
	DUPLICATE_REFERENCE_PRESENT = "DUPLICATE_REFERENCE_PRESENT"
	ORPHAN_FILE_PRESENT = "ORPHAN_FILE_PRESENT"
	UNCLASSIFIED_REFERENCE_PRESENT = "UNCLASSIFIED_REFERENCE_PRESENT"
	EXISTING_INVENTORY_PARTIAL_EVIDENCE = "EXISTING_INVENTORY_PARTIAL_EVIDENCE"


class RepairPhotoRetentionPreflightErrorCode(StrEnum):
	INVALID_INPUT = "INVALID_INPUT"


class RepairPhotoRetentionPreflightError(ValueError):
	"""Stable code-only caller error which never echoes rejected evidence."""

	def __init__(self, code: RepairPhotoRetentionPreflightErrorCode) -> None:
		if type(code) is not RepairPhotoRetentionPreflightErrorCode:
			raise TypeError("INVALID_ERROR_CODE")
		self.code = code
		super().__init__(code.value)

	def __repr__(self) -> str:
		return f"RepairPhotoRetentionPreflightError(code={self.code.value!r})"


_CODE_ORDER: Final = tuple(RepairPhotoRetentionPreflightCode)
_EXCLUSIVE_CODES: Final = frozenset(
	{
		RepairPhotoRetentionPreflightCode.INVENTORY_UNAVAILABLE,
		RepairPhotoRetentionPreflightCode.INVENTORY_TRUNCATED,
		RepairPhotoRetentionPreflightCode.EXISTING_INVENTORY_PARTIAL_EVIDENCE,
	}
)


@dataclass(frozen=True, slots=True, repr=False)
class RepairPhotoRetentionPreflightResult:
	"""Code-only partial evidence; it can never authorize retention or access."""

	codes: tuple[RepairPhotoRetentionPreflightCode, ...]
	policy_revision_sha256: str = field(repr=False)
	inventory_evidence_ok: bool
	retention_evidence_ok: bool = False
	assessment_authorized: bool = False
	dry_run_authorized: bool = False
	purge_authorized: bool = False
	download_authorized: bool = False
	activation_authorized: bool = False
	capability_ready: bool = False

	def __post_init__(self) -> None:
		if (
			type(self.codes) is not tuple
			or not self.codes
			or any(type(code) is not RepairPhotoRetentionPreflightCode for code in self.codes)
			or len(set(self.codes)) != len(self.codes)
			or self.codes != tuple(code for code in _CODE_ORDER if code in self.codes)
			or (any(code in _EXCLUSIVE_CODES for code in self.codes) and len(self.codes) != 1)
			or type(self.policy_revision_sha256) is not str
			or self.policy_revision_sha256 != POLICY_REVISION_SHA256
			or type(self.inventory_evidence_ok) is not bool
			or self.inventory_evidence_ok
			is not (self.codes == (RepairPhotoRetentionPreflightCode.EXISTING_INVENTORY_PARTIAL_EVIDENCE,))
			or self.retention_evidence_ok is not False
			or self.assessment_authorized is not False
			or self.dry_run_authorized is not False
			or self.purge_authorized is not False
			or self.download_authorized is not False
			or self.activation_authorized is not False
			or self.capability_ready is not False
		):
			_fail()

	def __repr__(self) -> str:
		return (
			"RepairPhotoRetentionPreflightResult("
			f"codes={tuple(code.value for code in self.codes)!r}, "
			f"inventory_evidence_ok={self.inventory_evidence_ok!r}, <redacted>)"
		)


def assess_repair_photo_retention_inventory_v1(
	report: RepairPhotoInventoryReport,
) -> RepairPhotoRetentionPreflightResult:
	"""Classify existing inventory without claiming full retention evidence."""

	validated = _rebuild_report(report)
	if validated.status is RepairPhotoInventoryStatus.TRUNCATED:
		return _result({RepairPhotoRetentionPreflightCode.INVENTORY_TRUNCATED})

	counters = validated.counters
	_validate_complete_counter_relationships(counters)
	codes: set[RepairPhotoRetentionPreflightCode] = set()
	if counters.empty_reference_rows or counters.invalid_child_identity_rows:
		codes.add(RepairPhotoRetentionPreflightCode.EMPTY_OR_INVALID_CHILD_PRESENT)
	if counters.malformed_reference_rows or counters.public_reference_rows:
		codes.add(RepairPhotoRetentionPreflightCode.PUBLIC_OR_MALFORMED_REFERENCE_PRESENT)
	if (
		counters.private_missing_file_rows
		or counters.private_mismatched_file_rows
		or counters.private_duplicate_file_rows
	):
		codes.add(RepairPhotoRetentionPreflightCode.PRIVATE_BINDING_NOT_PROVEN)
	if (
		counters.duplicate_child_url_groups
		or counters.duplicate_file_url_groups
		or counters.duplicate_orphan_file_url_groups
	):
		codes.add(RepairPhotoRetentionPreflightCode.DUPLICATE_REFERENCE_PRESENT)
	if (
		counters.orphan_public_file_rows
		or counters.orphan_private_file_rows
		or counters.orphan_malformed_file_rows
	):
		codes.add(RepairPhotoRetentionPreflightCode.ORPHAN_FILE_PRESENT)
	if counters.unclassified_reference_rows:
		codes.add(RepairPhotoRetentionPreflightCode.UNCLASSIFIED_REFERENCE_PRESENT)
	if not codes:
		codes.add(RepairPhotoRetentionPreflightCode.EXISTING_INVENTORY_PARTIAL_EVIDENCE)
	return _result(codes)


def collect_repair_photo_retention_preflight_v1(
	*,
	limits: RepairPhotoInventoryLimits | None = None,
	reader: PhotoInventoryReader | None = None,
) -> RepairPhotoRetentionPreflightResult:
	"""Run one fresh bounded inventory read and return sanitized partial evidence."""

	validated_limits = _rebuild_limits(limits)
	if reader is not None and not callable(reader):
		_fail()
	try:
		report = collect_repair_photo_inventory(limits=validated_limits, reader=reader)
		return assess_repair_photo_retention_inventory_v1(report)
	except RepairPhotoInventoryError as error:
		if _trusted_invalid_input(error):
			_fail()
		return _result({RepairPhotoRetentionPreflightCode.INVENTORY_UNAVAILABLE})
	except Exception:
		return _result({RepairPhotoRetentionPreflightCode.INVENTORY_UNAVAILABLE})


def _rebuild_limits(value: object) -> RepairPhotoInventoryLimits:
	if value is None:
		return RepairPhotoInventoryLimits()
	if type(value) is not RepairPhotoInventoryLimits:
		_fail()
	try:
		return RepairPhotoInventoryLimits(
			child_rows_per_source=value.child_rows_per_source,
			file_rows=value.file_rows,
		)
	except (AttributeError, RepairPhotoInventoryError, TypeError, ValueError):
		_fail()


def _rebuild_report(value: object) -> RepairPhotoInventoryReport:
	if type(value) is not RepairPhotoInventoryReport:
		_fail()
	try:
		counters = RepairPhotoInventoryCounters(
			**{item.name: getattr(value.counters, item.name) for item in fields(RepairPhotoInventoryCounters)}
		)
		return RepairPhotoInventoryReport(
			status=value.status,
			counters=counters,
			naprawa_truncated=value.naprawa_truncated,
			przyjecie_truncated=value.przyjecie_truncated,
			files_truncated=value.files_truncated,
		)
	except (AttributeError, RepairPhotoInventoryError, TypeError, ValueError):
		_fail()


def _validate_complete_counter_relationships(counters: RepairPhotoInventoryCounters) -> None:
	classified_children = (
		counters.empty_reference_rows
		+ counters.invalid_child_identity_rows
		+ counters.malformed_reference_rows
		+ counters.public_reference_rows
		+ counters.private_reference_rows
	)
	public_bindings = (
		counters.legacy_public_exact_rows
		+ counters.legacy_public_missing_file_rows
		+ counters.legacy_public_mismatched_file_rows
		+ counters.legacy_public_duplicate_file_rows
	)
	private_bindings = (
		counters.private_exact_rows
		+ counters.private_missing_file_rows
		+ counters.private_mismatched_file_rows
		+ counters.private_duplicate_file_rows
	)
	if (
		classified_children != counters.naprawa_child_rows + counters.przyjecie_child_rows
		or public_bindings != counters.public_reference_rows
		or private_bindings != counters.private_reference_rows
	):
		_fail()


def _trusted_invalid_input(error: RepairPhotoInventoryError) -> bool:
	return (
		type(error) is RepairPhotoInventoryError
		and type(getattr(error, "code", None)) is RepairPhotoInventoryCode
		and error.code is RepairPhotoInventoryCode.INVALID_INPUT
		and error.args == (error.code.value,)
	)


def _result(
	codes: set[RepairPhotoRetentionPreflightCode],
) -> RepairPhotoRetentionPreflightResult:
	ordered = tuple(code for code in _CODE_ORDER if code in codes)
	return RepairPhotoRetentionPreflightResult(
		codes=ordered,
		policy_revision_sha256=POLICY_REVISION_SHA256,
		inventory_evidence_ok=ordered
		== (RepairPhotoRetentionPreflightCode.EXISTING_INVENTORY_PARTIAL_EVIDENCE,),
	)


def _fail() -> None:
	raise RepairPhotoRetentionPreflightError(RepairPhotoRetentionPreflightErrorCode.INVALID_INPUT) from None
