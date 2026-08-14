"""Pure metadata-only projection for already actor-scoped repair photos.

This module does not authorize an actor, read a File, or declare content safe.
The trusted adapter must establish actor scope and provide exact attachment
evidence before calling the planner.  Its output intentionally cannot describe
or enable a download.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

MAX_PHOTOS_PER_REPAIR = 20
MAX_PHOTO_POSITION = 1000
_PUBLIC_REPAIR_ID = re.compile(r"^rpr_[A-Za-z0-9_-]{32}$")


class RepairPhotoMetadataState(StrEnum):
	METADATA_ONLY = "METADATA_ONLY"


class RepairPhotoMetadataCode(StrEnum):
	INVALID_INPUT = "INVALID_INPUT"
	INVALID_REPAIR_ID = "INVALID_REPAIR_ID"
	INVALID_POSITION = "INVALID_POSITION"
	TOO_MANY_PHOTOS = "TOO_MANY_PHOTOS"
	REPAIR_BINDING_MISMATCH = "REPAIR_BINDING_MISMATCH"
	PHOTO_NOT_PRIVATE = "PHOTO_NOT_PRIVATE"
	ATTACHMENT_MISMATCH = "ATTACHMENT_MISMATCH"
	DUPLICATE_POSITION = "DUPLICATE_POSITION"


class RepairPhotoMetadataError(ValueError):
	"""Stable code-only failure without repair or storage identifiers."""

	def __init__(self, code: RepairPhotoMetadataCode) -> None:
		if type(code) is not RepairPhotoMetadataCode:
			raise TypeError("INVALID_ERROR_CODE")
		self.code = code
		super().__init__(code.value)

	def __repr__(self) -> str:
		return f"RepairPhotoMetadataError(code={self.code.value!r})"


@dataclass(frozen=True, slots=True)
class ScopedRepairPhotoEvidence:
	repair_id: str = field(repr=False)
	position: int
	is_private: bool
	exact_attachment: bool
	metadata_only: Literal[True]

	def __post_init__(self) -> None:
		_validate_repair_id(self.repair_id)
		_validate_position(self.position)
		if type(self.is_private) is not bool or type(self.exact_attachment) is not bool:
			_raise(RepairPhotoMetadataCode.INVALID_INPUT)
		if self.metadata_only is not True:
			_raise(RepairPhotoMetadataCode.INVALID_INPUT)

	def __repr__(self) -> str:
		return (
			"ScopedRepairPhotoEvidence(<redacted>, "
			f"position={self.position!r}, is_private={self.is_private!r}, "
			f"exact_attachment={self.exact_attachment!r}, metadata_only=True)"
		)


@dataclass(frozen=True, slots=True)
class RepairPhotoMetadata:
	position: int
	state: RepairPhotoMetadataState = RepairPhotoMetadataState.METADATA_ONLY

	def __post_init__(self) -> None:
		_validate_position(self.position)
		if type(self.state) is not RepairPhotoMetadataState:
			_raise(RepairPhotoMetadataCode.INVALID_INPUT)
		if self.state is not RepairPhotoMetadataState.METADATA_ONLY:
			_raise(RepairPhotoMetadataCode.INVALID_INPUT)


def plan_repair_photo_metadata(
	*,
	actor_scope_confirmed: Literal[True],
	repair_id: str,
	evidence: tuple[ScopedRepairPhotoEvidence, ...],
) -> tuple[RepairPhotoMetadata, ...]:
	"""Return canonical metadata after revalidating trusted adapter evidence."""

	if actor_scope_confirmed is not True:
		_raise(RepairPhotoMetadataCode.INVALID_INPUT)
	_validate_repair_id(repair_id)
	if type(evidence) is not tuple:
		_raise(RepairPhotoMetadataCode.INVALID_INPUT)
	if len(evidence) > MAX_PHOTOS_PER_REPAIR:
		_raise(RepairPhotoMetadataCode.TOO_MANY_PHOTOS)

	validated = tuple(_revalidate_evidence(item) for item in evidence)
	for item in validated:
		if item.repair_id != repair_id:
			_raise(RepairPhotoMetadataCode.REPAIR_BINDING_MISMATCH)
		if not item.is_private:
			_raise(RepairPhotoMetadataCode.PHOTO_NOT_PRIVATE)
		if not item.exact_attachment:
			_raise(RepairPhotoMetadataCode.ATTACHMENT_MISMATCH)

	positions = tuple(sorted(item.position for item in validated))
	if len(set(positions)) != len(positions):
		_raise(RepairPhotoMetadataCode.DUPLICATE_POSITION)
	return tuple(RepairPhotoMetadata(position=position) for position in positions)


def _revalidate_evidence(value: object) -> ScopedRepairPhotoEvidence:
	if type(value) is not ScopedRepairPhotoEvidence:
		_raise(RepairPhotoMetadataCode.INVALID_INPUT)
	try:
		return ScopedRepairPhotoEvidence(
			repair_id=value.repair_id,
			position=value.position,
			is_private=value.is_private,
			exact_attachment=value.exact_attachment,
			metadata_only=value.metadata_only,
		)
	except (AttributeError, TypeError, RepairPhotoMetadataError):
		_raise(RepairPhotoMetadataCode.INVALID_INPUT)


def _validate_repair_id(value: object) -> None:
	if type(value) is not str or _PUBLIC_REPAIR_ID.fullmatch(value) is None:
		_raise(RepairPhotoMetadataCode.INVALID_REPAIR_ID)


def _validate_position(value: object) -> None:
	if type(value) is not int or not 1 <= value <= MAX_PHOTO_POSITION:
		_raise(RepairPhotoMetadataCode.INVALID_POSITION)


def _raise(code: RepairPhotoMetadataCode) -> None:
	raise RepairPhotoMetadataError(code) from None
