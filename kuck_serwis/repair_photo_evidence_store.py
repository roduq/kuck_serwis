"""Actor-scoped, metadata-only reader for repair photo evidence.

The reader deliberately returns only the pure G0-64 evidence DTO.  It neither
authorizes nor performs a download, and it never reads File content.  A scoped
access capability is an internal server-side object and must not cross an HTTP
or serialization boundary.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias

from kuck_serwis.repair_photo_metadata import (
	MAX_PHOTO_POSITION,
	MAX_PHOTOS_PER_REPAIR,
	ScopedRepairPhotoEvidence,
)
from kuck_serwis.repair_photo_policy import (
	PhotoReferenceKind,
	RepairPhotoPolicyError,
	classify_photo_reference,
)

MAX_INTERNAL_ID_LENGTH = 140
MAX_ACTOR_IDENTITY_LENGTH = 254
MAX_REFERENCE_LENGTH = 512
_PUBLIC_REPAIR_ID = re.compile(r"^rpr_[A-Za-z0-9_-]{32}$")
_ACCESS_SEAL = object()


class RepairPhotoEvidenceStoreCode(StrEnum):
	INVALID_INPUT = "INVALID_INPUT"
	UNSUPPORTED_DATABASE = "UNSUPPORTED_DATABASE"
	UNSAFE_ISOLATION = "UNSAFE_ISOLATION"
	SCOPED_REPAIR_NOT_FOUND = "SCOPED_REPAIR_NOT_FOUND"
	PHOTO_EVIDENCE_UNSAFE = "PHOTO_EVIDENCE_UNSAFE"
	EVIDENCE_READ_FAILED = "EVIDENCE_READ_FAILED"
	EVIDENCE_MALFORMED = "EVIDENCE_MALFORMED"


class RepairPhotoEvidenceStoreError(RuntimeError):
	"""Stable code-only error without actor, repair, File, or URL data."""

	def __init__(self, code: RepairPhotoEvidenceStoreCode) -> None:
		if type(code) is not RepairPhotoEvidenceStoreCode:
			raise TypeError("INVALID_ERROR_CODE")
		self.code = code
		super().__init__(code.value)

	def __repr__(self) -> str:
		return f"RepairPhotoEvidenceStoreError(code={self.code.value!r})"


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ActorScopedRepairAccess:
	"""Sealed internal capability binding one actor to one repair identity."""

	_repair_name: str = field(repr=False)
	repair_id: str = field(repr=False)
	_actor_identity: str = field(repr=False)
	_seal: object = field(repr=False, compare=False)

	def __init__(
		self,
		*,
		repair_name: str,
		repair_id: str,
		actor_identity: str,
		_seal: object,
	) -> None:
		if _seal is not _ACCESS_SEAL:
			_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)
		_validate_internal_id(repair_name)
		_validate_repair_id(repair_id)
		_validate_actor_identity(actor_identity)
		object.__setattr__(self, "_repair_name", repair_name)
		object.__setattr__(self, "repair_id", repair_id)
		object.__setattr__(self, "_actor_identity", actor_identity)
		object.__setattr__(self, "_seal", _seal)

	def __repr__(self) -> str:
		return "ActorScopedRepairAccess(<redacted>)"


def _issue_actor_scoped_repair_access(
	*, repair_name: str, repair_id: str, actor_identity: str
) -> ActorScopedRepairAccess:
	"""Issue a capability for trusted server code after actor resolution.

	The database reader still revalidates the actor relationship in its own
	statement snapshot, so this capability is not a stale authorization cache.
	"""

	return ActorScopedRepairAccess(
		repair_name=repair_name,
		repair_id=repair_id,
		actor_identity=actor_identity,
		_seal=_ACCESS_SEAL,
	)


RawEvidenceRow: TypeAlias = tuple[object, ...]
PhotoEvidenceReader: TypeAlias = Callable[..., tuple[RawEvidenceRow, ...]]


# Both bounded collections use LIMIT max + 1 before any correlated check.  The
# result is one SELECT and therefore one InnoDB statement snapshot.  No URL or
# File identity leaves this module in the returned domain DTO.
EVIDENCE_SQL = r"""
WITH
scoped_repair AS (
	SELECT n.`name`, n.`public_id`
	FROM `tabNaprawa` n
	WHERE
		n.`name` = %(repair_name)s
		AND n.`public_id` = %(repair_id)s
		AND EXISTS (
			SELECT 1
			FROM `tabUser` u
			WHERE
				u.`name` = %(actor_identity)s
				AND u.`enabled` = 1
				AND u.`user_type` = 'Website User'
			LIMIT 1
		)
		AND EXISTS (
			SELECT 1
			FROM `tabPortal User` pu
			WHERE
				pu.`user` = %(actor_identity)s
				AND pu.`parent` = n.`klient`
				AND pu.`parenttype` = 'Customer'
				AND pu.`parentfield` = 'portal_users'
			LIMIT 1
		)
	LIMIT 2
),
photo_rows AS (
	SELECT
		c.`name` AS child_name,
		c.`idx` AS position,
		LEFT(COALESCE(c.`zdjecie`, ''), 513) AS raw_url,
		CHAR_LENGTH(COALESCE(c.`zdjecie`, '')) AS url_chars
	FROM `tabNaprawa Zdjecie` c FORCE INDEX (`parent`)
	WHERE
		c.`parent` = (SELECT s.`name` FROM scoped_repair s LIMIT 1)
		AND c.`parenttype` = 'Naprawa'
		AND c.`parentfield` = 'zdjecia'
	ORDER BY c.`idx`, c.`name`
	LIMIT %(fetch_limit)s
),
attached_files AS (
	SELECT f.`name`, f.`file_url`
	FROM `tabFile` f FORCE INDEX (`attached_to_doctype_attached_to_name_index`)
	WHERE
		f.`attached_to_doctype` = 'Naprawa'
		AND f.`attached_to_name` = (SELECT s.`name` FROM scoped_repair s LIMIT 1)
		AND f.`attached_to_field` = 'zdjecie'
	LIMIT %(fetch_limit)s
),
meta AS (
	SELECT
		(SELECT COUNT(*) FROM scoped_repair) AS scoped_count,
		(SELECT COUNT(*) FROM photo_rows) AS photo_count,
		(SELECT COUNT(*) FROM attached_files) AS attached_count,
		EXISTS (
			SELECT 1
			FROM attached_files af
			WHERE NOT EXISTS (SELECT 1 FROM photo_rows p WHERE p.`raw_url` = af.`file_url`)
			LIMIT 1
		) AS has_orphan
),
evaluated_photos AS (
	SELECT
		p.`position`,
		p.`raw_url`,
		p.`url_chars`,
		EXISTS (
			SELECT 1 FROM photo_rows other
			WHERE other.`child_name` <> p.`child_name` AND other.`raw_url` = p.`raw_url`
			LIMIT 1
		) AS duplicate_child_url,
		CASE
			WHEN NOT EXISTS (
				SELECT 1 FROM `tabFile` f FORCE INDEX (`file_url_index`)
				WHERE f.`file_url` = p.`raw_url` LIMIT 1
			) THEN 0
			WHEN EXISTS (
				SELECT 1 FROM `tabFile` f FORCE INDEX (`file_url_index`)
				WHERE f.`file_url` = p.`raw_url` LIMIT 1 OFFSET 1
			) THEN 3
			WHEN EXISTS (
				SELECT 1
				FROM `tabFile` f FORCE INDEX (`file_url_index`)
				JOIN scoped_repair s ON s.`name` = f.`attached_to_name`
				WHERE
					f.`file_url` = p.`raw_url`
					AND f.`is_private` = 1
					AND f.`is_folder` = 0
					AND f.`attached_to_doctype` = 'Naprawa'
					AND f.`attached_to_field` = 'zdjecie'
				LIMIT 1
			) THEN 1
			ELSE 2
		END AS match_state
	FROM photo_rows p
)
SELECT
	@@tx_isolation AS isolation_level,
	'META' AS row_kind,
	m.`scoped_count`,
	m.`photo_count`,
	m.`attached_count`,
	m.`has_orphan`,
	NULL AS position,
	NULL AS raw_url,
	NULL AS url_chars,
	NULL AS duplicate_child_url,
	NULL AS match_state
FROM meta m
UNION ALL
SELECT
	@@tx_isolation,
	'PHOTO',
	NULL,
	NULL,
	NULL,
	NULL,
	p.`position`,
	p.`raw_url`,
	p.`url_chars`,
	p.`duplicate_child_url`,
	p.`match_state`
FROM evaluated_photos p
ORDER BY row_kind, position
"""


def read_scoped_repair_photo_evidence(
	access: ActorScopedRepairAccess,
	*,
	reader: PhotoEvidenceReader | None = None,
) -> tuple[ScopedRepairPhotoEvidence, ...]:
	"""Read safe metadata for one repair while revalidating its actor scope."""

	validated = _validated_access(access)
	selected_reader = _read_evidence_rows if reader is None else reader
	if not callable(selected_reader):
		_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)
	try:
		rows = selected_reader(
			repair_name=validated._repair_name,
			repair_id=validated.repair_id,
			actor_identity=validated._actor_identity,
			fetch_limit=MAX_PHOTOS_PER_REPAIR + 1,
		)
	except RepairPhotoEvidenceStoreError as error:
		if _trusted_reader_error(error):
			raise RepairPhotoEvidenceStoreError(error.code) from None
		_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_READ_FAILED)
	except Exception:
		_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_READ_FAILED)
	return _build_evidence(rows, validated.repair_id)


def _read_evidence_rows(
	*, repair_name: str, repair_id: str, actor_identity: str, fetch_limit: int
) -> tuple[RawEvidenceRow, ...]:
	try:
		import frappe
	except ImportError:
		raise RepairPhotoEvidenceStoreError(RepairPhotoEvidenceStoreCode.UNSUPPORTED_DATABASE) from None
	if getattr(frappe.db, "db_type", None) != "mariadb":
		raise RepairPhotoEvidenceStoreError(RepairPhotoEvidenceStoreCode.UNSUPPORTED_DATABASE)
	rows = frappe.db.sql(
		EVIDENCE_SQL,
		{
			"repair_name": repair_name,
			"repair_id": repair_id,
			"actor_identity": actor_identity,
			"fetch_limit": fetch_limit,
		},
	)
	return tuple(tuple(row) for row in rows)


def _build_evidence(
	rows: tuple[RawEvidenceRow, ...], repair_id: str
) -> tuple[ScopedRepairPhotoEvidence, ...]:
	if type(rows) is not tuple or not rows or len(rows) > MAX_PHOTOS_PER_REPAIR + 2:
		_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
	meta: tuple[object, ...] | None = None
	photos: list[tuple[int, str, int, bool, int]] = []
	isolation: str | None = None
	for row in rows:
		if type(row) is not tuple or len(row) != 11:
			_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
		row_isolation = row[0]
		if type(row_isolation) is not str:
			_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
		canonical_isolation = row_isolation.upper().replace("_", "-")
		if isolation is None:
			isolation = canonical_isolation
		elif isolation != canonical_isolation:
			_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
		if row[1] == "META":
			if meta is not None:
				_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
			meta = row
		elif row[1] == "PHOTO":
			photos.append(_parse_photo_row(row))
		else:
			_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)

	if isolation not in {"READ-COMMITTED", "REPEATABLE-READ", "SERIALIZABLE"}:
		_raise(RepairPhotoEvidenceStoreCode.UNSAFE_ISOLATION)
	if meta is None:
		_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
	scoped_count = _exact_nonnegative_int(meta[2])
	photo_count = _exact_nonnegative_int(meta[3])
	attached_count = _exact_nonnegative_int(meta[4])
	has_orphan = _database_bool(meta[5])
	if any(value is not None for value in meta[6:11]):
		_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
	if scoped_count == 0:
		if photo_count or attached_count or has_orphan or photos:
			_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
		_raise(RepairPhotoEvidenceStoreCode.SCOPED_REPAIR_NOT_FOUND)
	if scoped_count != 1 or photo_count != len(photos):
		_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
	if photo_count > MAX_PHOTOS_PER_REPAIR or attached_count > MAX_PHOTOS_PER_REPAIR:
		_raise(RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE)
	if has_orphan or attached_count != photo_count:
		_raise(RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE)

	positions: set[int] = set()
	urls: set[str] = set()
	result = []
	for position, raw_url, url_chars, duplicate_child_url, match_state in photos:
		if position in positions or raw_url in urls or duplicate_child_url:
			_raise(RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE)
		positions.add(position)
		urls.add(raw_url)
		if url_chars != len(raw_url) or url_chars > MAX_REFERENCE_LENGTH:
			_raise(RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE)
		try:
			kind = classify_photo_reference(raw_url)
		except RepairPhotoPolicyError:
			_raise(RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE)
		if kind is not PhotoReferenceKind.PRIVATE or match_state != 1:
			_raise(RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE)
		try:
			result.append(
				ScopedRepairPhotoEvidence(
					repair_id=repair_id,
					position=position,
					is_private=True,
					exact_attachment=True,
					metadata_only=True,
				)
			)
		except Exception:
			_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
	return tuple(sorted(result, key=lambda item: item.position))


def _parse_photo_row(row: tuple[object, ...]) -> tuple[int, str, int, bool, int]:
	if any(value is not None for value in row[2:6]):
		_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
	position = row[6]
	raw_url = row[7]
	url_chars = row[8]
	duplicate_child_url = _database_bool(row[9])
	match_state = row[10]
	if (
		type(position) is not int
		or not 1 <= position <= MAX_PHOTO_POSITION
		or type(raw_url) is not str
		or type(url_chars) is not int
		or url_chars < 0
		or type(match_state) is not int
		or match_state not in {0, 1, 2, 3}
	):
		_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
	return position, raw_url, url_chars, duplicate_child_url, match_state


def _validated_access(value: object) -> ActorScopedRepairAccess:
	if type(value) is not ActorScopedRepairAccess:
		_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)
	try:
		if value._seal is not _ACCESS_SEAL:
			raise ValueError
		return ActorScopedRepairAccess(
			repair_name=value._repair_name,
			repair_id=value.repair_id,
			actor_identity=value._actor_identity,
			_seal=_ACCESS_SEAL,
		)
	except (AttributeError, RepairPhotoEvidenceStoreError, TypeError, ValueError):
		_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)


def _trusted_reader_error(error: RepairPhotoEvidenceStoreError) -> bool:
	return (
		type(error) is RepairPhotoEvidenceStoreError
		and type(getattr(error, "code", None)) is RepairPhotoEvidenceStoreCode
		and error.code
		in {
			RepairPhotoEvidenceStoreCode.UNSUPPORTED_DATABASE,
			RepairPhotoEvidenceStoreCode.EVIDENCE_READ_FAILED,
		}
		and error.args == (error.code.value,)
	)


def _validate_internal_id(value: object) -> None:
	_validate_bounded_text(value, MAX_INTERNAL_ID_LENGTH)


def _validate_repair_id(value: object) -> None:
	if type(value) is not str or _PUBLIC_REPAIR_ID.fullmatch(value) is None:
		_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)


def _validate_actor_identity(value: object) -> None:
	_validate_bounded_text(value, MAX_ACTOR_IDENTITY_LENGTH)
	if value == "Guest":
		_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)


def _validate_bounded_text(value: object, maximum: int) -> None:
	if type(value) is not str or not value or len(value) > maximum:
		_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)
	if any(ord(character) < 32 or ord(character) == 127 for character in value):
		_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)


def _exact_nonnegative_int(value: object) -> int:
	if type(value) is not int or value < 0:
		_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
	return value


def _database_bool(value: object) -> bool:
	if type(value) is bool:
		return value
	if type(value) is int and value in (0, 1):
		return bool(value)
	_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)


def _raise(code: RepairPhotoEvidenceStoreCode) -> None:
	raise RepairPhotoEvidenceStoreError(code) from None
