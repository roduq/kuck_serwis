"""Pure, dark-only retention eligibility planning for repair photos.

The caller is responsible for producing current policy, lifecycle, attachment,
reference, and legal-hold evidence.  This module defensively validates that
evidence and can only produce a dry-run classification.  Its output contains
neither a storage identity nor an executable deletion capability.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

MAX_RETENTION_CANDIDATES = 1000
MAX_PHOTO_POSITION = 1000
MAX_INTERNAL_ID_LENGTH = 140
_PUBLIC_REPAIR_ID = re.compile(r"^rpr_[A-Za-z0-9_-]{32}$")
_TECHNICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,139}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PhotoRetentionPolicyState(StrEnum):
	APPROVED = "APPROVED"
	UNAPPROVED = "UNAPPROVED"
	REVOKED = "REVOKED"


class PhotoLegalHoldState(StrEnum):
	CLEAR = "CLEAR"
	ACTIVE = "ACTIVE"
	UNKNOWN = "UNKNOWN"


class PhotoRetentionDisposition(StrEnum):
	KEEP_POLICY_UNAPPROVED = "KEEP_POLICY_UNAPPROVED"
	KEEP_NOT_DUE = "KEEP_NOT_DUE"
	KEEP_ACTIVE_HOLD = "KEEP_ACTIVE_HOLD"
	KEEP_HOLD_UNKNOWN = "KEEP_HOLD_UNKNOWN"
	KEEP_UNSAFE_EVIDENCE = "KEEP_UNSAFE_EVIDENCE"
	DRY_RUN_ELIGIBLE = "DRY_RUN_ELIGIBLE"


class PhotoRetentionMode(StrEnum):
	DRY_RUN_ONLY = "DRY_RUN_ONLY"


class RepairPhotoRetentionCode(StrEnum):
	INVALID_INPUT = "INVALID_INPUT"
	TOO_MANY_CANDIDATES = "TOO_MANY_CANDIDATES"
	DUPLICATE_CANDIDATE = "DUPLICATE_CANDIDATE"


class RepairPhotoRetentionError(ValueError):
	"""Stable code-only failure without repair, File, policy, or hold data."""

	def __init__(self, code: RepairPhotoRetentionCode) -> None:
		if type(code) is not RepairPhotoRetentionCode:
			raise TypeError("INVALID_ERROR_CODE")
		self.code = code
		super().__init__(code.value)

	def __repr__(self) -> str:
		return f"RepairPhotoRetentionError(code={self.code.value!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PhotoRetentionPolicyEvidence:
	"""Exact policy revision and its current approval state.

	APPROVED is evidence supplied by a future trusted adapter, not an approval
	mechanism.  The planner remains dry-run even for approved evidence.
	"""

	policy_revision_sha256: str = field(repr=False)
	state: PhotoRetentionPolicyState

	def __post_init__(self) -> None:
		_validate_sha256(self.policy_revision_sha256)
		if type(self.state) is not PhotoRetentionPolicyState:
			_raise(RepairPhotoRetentionCode.INVALID_INPUT)

	def __repr__(self) -> str:
		return f"PhotoRetentionPolicyEvidence(state={self.state.value!r}, <redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class RepairPhotoRetentionEvidence:
	"""Current candidate evidence prepared by a future trusted read adapter."""

	repair_id: str = field(repr=False)
	file_identity: str = field(repr=False)
	position: int
	eligible_at: datetime = field(repr=False)
	policy_revision_sha256: str = field(repr=False)
	evidence_revision_sha256: str = field(repr=False)
	hold_state: PhotoLegalHoldState
	hold_revision_sha256: str | None = field(repr=False)
	lifecycle_eligible: bool
	is_private: bool
	exact_attachment: bool
	child_reference_count: int
	file_reference_count: int
	blob_reference_count: int

	def __post_init__(self) -> None:
		_validate_repair_id(self.repair_id)
		_validate_technical_id(self.file_identity)
		_validate_position(self.position)
		_validate_utc(self.eligible_at)
		_validate_sha256(self.policy_revision_sha256)
		_validate_sha256(self.evidence_revision_sha256)
		if type(self.hold_state) is not PhotoLegalHoldState:
			_raise(RepairPhotoRetentionCode.INVALID_INPUT)
		if self.hold_state is PhotoLegalHoldState.UNKNOWN:
			if self.hold_revision_sha256 is not None:
				_raise(RepairPhotoRetentionCode.INVALID_INPUT)
		else:
			_validate_sha256(self.hold_revision_sha256)
		for value in (self.lifecycle_eligible, self.is_private, self.exact_attachment):
			if type(value) is not bool:
				_raise(RepairPhotoRetentionCode.INVALID_INPUT)
		for value in (
			self.child_reference_count,
			self.file_reference_count,
			self.blob_reference_count,
		):
			_validate_reference_count(value)

	def __repr__(self) -> str:
		return (
			"RepairPhotoRetentionEvidence(<redacted>, "
			f"position={self.position!r}, hold_state={self.hold_state.value!r})"
		)


@dataclass(frozen=True, slots=True, repr=False)
class PhotoRetentionCandidateDecision:
	"""Non-executable decision: its fingerprint cannot address a File or blob."""

	candidate_fingerprint_sha256: str = field(repr=False)
	disposition: PhotoRetentionDisposition

	def __post_init__(self) -> None:
		_validate_sha256(self.candidate_fingerprint_sha256)
		if type(self.disposition) is not PhotoRetentionDisposition:
			_raise(RepairPhotoRetentionCode.INVALID_INPUT)

	def __repr__(self) -> str:
		return f"PhotoRetentionCandidateDecision(disposition={self.disposition.value!r}, <redacted>)"


@dataclass(frozen=True, slots=True)
class PhotoRetentionCounters:
	total: int
	keep_policy_unapproved: int
	keep_not_due: int
	keep_active_hold: int
	keep_hold_unknown: int
	keep_unsafe_evidence: int
	dry_run_eligible: int

	def __post_init__(self) -> None:
		values = (
			self.keep_policy_unapproved,
			self.keep_not_due,
			self.keep_active_hold,
			self.keep_hold_unknown,
			self.keep_unsafe_evidence,
			self.dry_run_eligible,
		)
		if type(self.total) is not int or self.total < 0 or self.total > MAX_RETENTION_CANDIDATES:
			_raise(RepairPhotoRetentionCode.INVALID_INPUT)
		if any(type(value) is not int or value < 0 for value in values):
			_raise(RepairPhotoRetentionCode.INVALID_INPUT)
		if sum(values) != self.total:
			_raise(RepairPhotoRetentionCode.INVALID_INPUT)


@dataclass(frozen=True, slots=True, repr=False)
class PhotoRetentionDryRunPlan:
	mode: PhotoRetentionMode
	assessed_at: datetime = field(repr=False)
	policy_state: PhotoRetentionPolicyState
	candidates: tuple[PhotoRetentionCandidateDecision, ...] = field(repr=False)
	counters: PhotoRetentionCounters

	def __post_init__(self) -> None:
		if type(self.mode) is not PhotoRetentionMode or self.mode is not PhotoRetentionMode.DRY_RUN_ONLY:
			_raise(RepairPhotoRetentionCode.INVALID_INPUT)
		_validate_utc(self.assessed_at)
		if type(self.policy_state) is not PhotoRetentionPolicyState:
			_raise(RepairPhotoRetentionCode.INVALID_INPUT)
		if type(self.candidates) is not tuple or len(self.candidates) > MAX_RETENTION_CANDIDATES:
			_raise(RepairPhotoRetentionCode.INVALID_INPUT)
		validated = tuple(_revalidate_decision(value) for value in self.candidates)
		if validated != self.candidates:
			_raise(RepairPhotoRetentionCode.INVALID_INPUT)
		fingerprints = tuple(value.candidate_fingerprint_sha256 for value in validated)
		if len(set(fingerprints)) != len(fingerprints):
			_raise(RepairPhotoRetentionCode.INVALID_INPUT)
		if type(self.counters) is not PhotoRetentionCounters:
			_raise(RepairPhotoRetentionCode.INVALID_INPUT)
		try:
			validated_counters = PhotoRetentionCounters(
				**{name: getattr(self.counters, name) for name in PhotoRetentionCounters.__dataclass_fields__}
			)
		except (AttributeError, TypeError, RepairPhotoRetentionError):
			_raise(RepairPhotoRetentionCode.INVALID_INPUT)
		if validated_counters != self.counters or self.counters != _counters_for(validated):
			_raise(RepairPhotoRetentionCode.INVALID_INPUT)

	def __repr__(self) -> str:
		return (
			"PhotoRetentionDryRunPlan(mode='DRY_RUN_ONLY', "
			f"policy_state={self.policy_state.value!r}, counters={self.counters!r}, <redacted>)"
		)


def plan_repair_photo_retention_dry_run(
	*,
	assessed_at: datetime,
	policy: PhotoRetentionPolicyEvidence,
	candidates: tuple[RepairPhotoRetentionEvidence, ...],
) -> PhotoRetentionDryRunPlan:
	"""Classify bounded current evidence without authorizing or performing purge."""

	_validate_utc(assessed_at)
	validated_policy = _revalidate_policy(policy)
	if type(candidates) is not tuple:
		_raise(RepairPhotoRetentionCode.INVALID_INPUT)
	if len(candidates) > MAX_RETENTION_CANDIDATES:
		_raise(RepairPhotoRetentionCode.TOO_MANY_CANDIDATES)
	validated_candidates = tuple(_revalidate_candidate(value) for value in candidates)
	ordered = tuple(
		sorted(
			validated_candidates,
			key=lambda value: (
				value.eligible_at,
				value.repair_id,
				value.position,
				value.file_identity,
				value.evidence_revision_sha256,
			),
		)
	)
	_seen_candidate_keys: set[tuple[str, str]] = set()
	_seen_positions: set[tuple[str, int]] = set()
	for value in ordered:
		candidate_key = (value.repair_id, value.file_identity)
		position_key = (value.repair_id, value.position)
		if candidate_key in _seen_candidate_keys or position_key in _seen_positions:
			_raise(RepairPhotoRetentionCode.DUPLICATE_CANDIDATE)
		_seen_candidate_keys.add(candidate_key)
		_seen_positions.add(position_key)

	decisions = tuple(
		PhotoRetentionCandidateDecision(
			candidate_fingerprint_sha256=_candidate_fingerprint(value),
			disposition=_disposition(value, validated_policy, assessed_at),
		)
		for value in ordered
	)
	counters = _counters_for(decisions)
	return PhotoRetentionDryRunPlan(
		mode=PhotoRetentionMode.DRY_RUN_ONLY,
		assessed_at=assessed_at,
		policy_state=validated_policy.state,
		candidates=decisions,
		counters=counters,
	)


def _disposition(
	value: RepairPhotoRetentionEvidence,
	policy: PhotoRetentionPolicyEvidence,
	assessed_at: datetime,
) -> PhotoRetentionDisposition:
	if (
		policy.state is not PhotoRetentionPolicyState.APPROVED
		or value.policy_revision_sha256 != policy.policy_revision_sha256
	):
		return PhotoRetentionDisposition.KEEP_POLICY_UNAPPROVED
	if value.hold_state is PhotoLegalHoldState.ACTIVE:
		return PhotoRetentionDisposition.KEEP_ACTIVE_HOLD
	if value.hold_state is PhotoLegalHoldState.UNKNOWN:
		return PhotoRetentionDisposition.KEEP_HOLD_UNKNOWN
	if not (
		value.lifecycle_eligible
		and value.is_private
		and value.exact_attachment
		and value.child_reference_count == 1
		and value.file_reference_count == 1
		and value.blob_reference_count == 1
	):
		return PhotoRetentionDisposition.KEEP_UNSAFE_EVIDENCE
	if assessed_at < value.eligible_at:
		return PhotoRetentionDisposition.KEEP_NOT_DUE
	return PhotoRetentionDisposition.DRY_RUN_ELIGIBLE


def _counters_for(
	decisions: tuple[PhotoRetentionCandidateDecision, ...],
) -> PhotoRetentionCounters:
	counts = {disposition: 0 for disposition in PhotoRetentionDisposition}
	for decision in decisions:
		counts[decision.disposition] += 1
	return PhotoRetentionCounters(
		total=len(decisions),
		keep_policy_unapproved=counts[PhotoRetentionDisposition.KEEP_POLICY_UNAPPROVED],
		keep_not_due=counts[PhotoRetentionDisposition.KEEP_NOT_DUE],
		keep_active_hold=counts[PhotoRetentionDisposition.KEEP_ACTIVE_HOLD],
		keep_hold_unknown=counts[PhotoRetentionDisposition.KEEP_HOLD_UNKNOWN],
		keep_unsafe_evidence=counts[PhotoRetentionDisposition.KEEP_UNSAFE_EVIDENCE],
		dry_run_eligible=counts[PhotoRetentionDisposition.DRY_RUN_ELIGIBLE],
	)


def _candidate_fingerprint(value: RepairPhotoRetentionEvidence) -> str:
	components = (
		value.repair_id,
		value.file_identity,
		str(value.position),
		value.eligible_at.isoformat(timespec="microseconds"),
		value.policy_revision_sha256,
		value.evidence_revision_sha256,
		value.hold_state.value,
		value.hold_revision_sha256 or "",
	)
	payload = b"repair-photo-retention-candidate/v1\x00" + b"\x00".join(
		component.encode("ascii") for component in components
	)
	return hashlib.sha256(payload).hexdigest()


def _revalidate_policy(value: object) -> PhotoRetentionPolicyEvidence:
	if type(value) is not PhotoRetentionPolicyEvidence:
		_raise(RepairPhotoRetentionCode.INVALID_INPUT)
	try:
		return PhotoRetentionPolicyEvidence(
			policy_revision_sha256=value.policy_revision_sha256,
			state=value.state,
		)
	except (AttributeError, TypeError, RepairPhotoRetentionError):
		_raise(RepairPhotoRetentionCode.INVALID_INPUT)


def _revalidate_candidate(value: object) -> RepairPhotoRetentionEvidence:
	if type(value) is not RepairPhotoRetentionEvidence:
		_raise(RepairPhotoRetentionCode.INVALID_INPUT)
	try:
		return RepairPhotoRetentionEvidence(
			repair_id=value.repair_id,
			file_identity=value.file_identity,
			position=value.position,
			eligible_at=value.eligible_at,
			policy_revision_sha256=value.policy_revision_sha256,
			evidence_revision_sha256=value.evidence_revision_sha256,
			hold_state=value.hold_state,
			hold_revision_sha256=value.hold_revision_sha256,
			lifecycle_eligible=value.lifecycle_eligible,
			is_private=value.is_private,
			exact_attachment=value.exact_attachment,
			child_reference_count=value.child_reference_count,
			file_reference_count=value.file_reference_count,
			blob_reference_count=value.blob_reference_count,
		)
	except (AttributeError, TypeError, RepairPhotoRetentionError):
		_raise(RepairPhotoRetentionCode.INVALID_INPUT)


def _revalidate_decision(value: object) -> PhotoRetentionCandidateDecision:
	if type(value) is not PhotoRetentionCandidateDecision:
		_raise(RepairPhotoRetentionCode.INVALID_INPUT)
	try:
		return PhotoRetentionCandidateDecision(
			candidate_fingerprint_sha256=value.candidate_fingerprint_sha256,
			disposition=value.disposition,
		)
	except (AttributeError, TypeError, RepairPhotoRetentionError):
		_raise(RepairPhotoRetentionCode.INVALID_INPUT)


def _validate_repair_id(value: object) -> None:
	if type(value) is not str or _PUBLIC_REPAIR_ID.fullmatch(value) is None:
		_raise(RepairPhotoRetentionCode.INVALID_INPUT)


def _validate_technical_id(value: object) -> None:
	if type(value) is not str or _TECHNICAL_ID.fullmatch(value) is None:
		_raise(RepairPhotoRetentionCode.INVALID_INPUT)


def _validate_position(value: object) -> None:
	if type(value) is not int or not 1 <= value <= MAX_PHOTO_POSITION:
		_raise(RepairPhotoRetentionCode.INVALID_INPUT)


def _validate_sha256(value: object) -> None:
	if type(value) is not str or _SHA256.fullmatch(value) is None:
		_raise(RepairPhotoRetentionCode.INVALID_INPUT)


def _validate_utc(value: object) -> None:
	if type(value) is not datetime or value.tzinfo is not UTC or value.fold != 0:
		_raise(RepairPhotoRetentionCode.INVALID_INPUT)


def _validate_reference_count(value: object) -> None:
	if type(value) is not int or not 0 <= value <= MAX_RETENTION_CANDIDATES:
		_raise(RepairPhotoRetentionCode.INVALID_INPUT)


def _raise(code: RepairPhotoRetentionCode) -> None:
	raise RepairPhotoRetentionError(code) from None
