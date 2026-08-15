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
MAX_REVALIDATION_ROWS = MAX_PHOTOS_PER_REPAIR * MAX_PHOTOS_PER_REPAIR + 1
_PUBLIC_REPAIR_ID = re.compile(r"^rpr_[A-Za-z0-9_-]{32}$")
_ACCESS_SEAL = object()
_FILE_ACCESS_SEAL = object()


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


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ScopedPrivateFileAccess:
	"""Sealed, non-serializable binding to one actor-scoped private File row."""

	evidence: ScopedRepairPhotoEvidence = field(repr=False)
	_actor_access: ActorScopedRepairAccess = field(repr=False)
	_file_identity: str = field(repr=False)
	_file_basename: str = field(repr=False)
	_file_url: str = field(repr=False)
	_file_revision: str = field(repr=False)
	_seal: object = field(repr=False, compare=False)

	def __init__(
		self,
		*,
		evidence: ScopedRepairPhotoEvidence,
		actor_access: ActorScopedRepairAccess,
		file_identity: str,
		file_basename: str,
		file_url: str,
		file_revision: str,
		_seal: object,
	) -> None:
		if _seal is not _FILE_ACCESS_SEAL:
			_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)
		validated_actor = _validated_access(actor_access)
		validated_evidence = _validated_evidence(evidence)
		_validate_internal_id(file_identity)
		_validate_file_binding(file_basename, file_url, file_revision)
		if validated_evidence.repair_id != validated_actor.repair_id:
			_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)
		object.__setattr__(self, "evidence", validated_evidence)
		object.__setattr__(self, "_actor_access", validated_actor)
		object.__setattr__(self, "_file_identity", file_identity)
		object.__setattr__(self, "_file_basename", file_basename)
		object.__setattr__(self, "_file_url", file_url)
		object.__setattr__(self, "_file_revision", file_revision)
		object.__setattr__(self, "_seal", _seal)

	def __repr__(self) -> str:
		return "ScopedPrivateFileAccess(<redacted>)"


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
		,
		(
			SELECT f.`name`
			FROM `tabFile` f FORCE INDEX (`file_url_index`)
			WHERE f.`file_url` = p.`raw_url`
			LIMIT 1
		) AS file_identity,
		(
			SELECT f.`file_name`
			FROM `tabFile` f FORCE INDEX (`file_url_index`)
			WHERE f.`file_url` = p.`raw_url`
			LIMIT 1
		) AS file_basename,
		(
			SELECT CAST(f.`modified` AS CHAR(26))
			FROM `tabFile` f FORCE INDEX (`file_url_index`)
			WHERE f.`file_url` = p.`raw_url`
			LIMIT 1
		) AS file_revision
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
	NULL AS match_state,
	NULL AS file_identity,
	NULL AS file_basename,
	NULL AS file_revision
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
	p.`match_state`,
	p.`file_identity`,
	p.`file_basename`,
	p.`file_revision`
FROM evaluated_photos p
ORDER BY row_kind, position
"""


# A locking read is deliberately used for post-filesystem revalidation.  Under
# MariaDB REPEATABLE READ a second plain SELECT could return the old consistent
# snapshot; LOCK IN SHARE MODE is a current read and retains transaction
# ownership with the caller.
FILE_REVALIDATION_SQL = r"""
SELECT
	@@tx_isolation AS isolation_level,
	c.`name` AS child_identity,
	c.`idx` AS position,
	LEFT(COALESCE(c.`zdjecie`, ''), 513) AS raw_url,
	CHAR_LENGTH(COALESCE(c.`zdjecie`, '')) AS url_chars,
	matched.`name` AS file_identity,
	matched.`file_name` AS file_basename,
	CAST(matched.`modified` AS CHAR(26)) AS file_revision,
	matched.`is_private` AS matched_is_private,
	matched.`is_folder` AS matched_is_folder,
	matched.`attached_to_doctype` AS matched_owner_doctype,
	matched.`attached_to_name` AS matched_owner_name,
	matched.`attached_to_field` AS matched_owner_field,
	attached.`name` AS attached_identity,
	LEFT(COALESCE(attached.`file_url`, ''), 513) AS attached_url,
	CHAR_LENGTH(COALESCE(attached.`file_url`, '')) AS attached_url_chars
FROM `tabNaprawa` n
JOIN `tabUser` u
	ON u.`name` = %(actor_identity)s
	AND u.`enabled` = 1
	AND u.`user_type` = 'Website User'
JOIN `tabPortal User` pu FORCE INDEX (`user`)
	ON pu.`user` = u.`name`
	AND pu.`parent` = n.`klient`
	AND pu.`parenttype` = 'Customer'
	AND pu.`parentfield` = 'portal_users'
JOIN `tabNaprawa Zdjecie` c FORCE INDEX (`parent`)
	ON c.`parent` = n.`name`
	AND c.`parenttype` = 'Naprawa'
	AND c.`parentfield` = 'zdjecia'
LEFT JOIN `tabFile` matched FORCE INDEX (`file_url_index`)
	ON matched.`file_url` = c.`zdjecie`
LEFT JOIN `tabFile` attached FORCE INDEX (`attached_to_doctype_attached_to_name_index`)
	ON attached.`attached_to_doctype` = 'Naprawa'
	AND attached.`attached_to_name` = n.`name`
	AND attached.`attached_to_field` = 'zdjecie'
WHERE
	n.`name` = %(repair_name)s
	AND n.`public_id` = %(repair_id)s
ORDER BY c.`idx`, c.`name`, attached.`name`, matched.`name`
LIMIT %(row_limit)s
LOCK IN SHARE MODE
"""


def read_scoped_repair_photo_evidence(
	access: ActorScopedRepairAccess,
	*,
	reader: PhotoEvidenceReader | None = None,
) -> tuple[ScopedRepairPhotoEvidence, ...]:
	"""Read safe metadata for one repair while revalidating its actor scope."""

	validated = _validated_access(access)
	rows = _read_rows_at_boundary(validated, reader)
	return tuple(item.evidence for item in _build_file_accesses(rows, validated))


def read_scoped_repair_photo_file_access(
	access: ActorScopedRepairAccess,
	*,
	reader: PhotoEvidenceReader | None = None,
) -> tuple[ScopedPrivateFileAccess, ...]:
	"""Issue exact private File capabilities from one actor-revalidating snapshot."""

	validated = _validated_access(access)
	rows = _read_rows_at_boundary(validated, reader)
	return _build_file_accesses(rows, validated)


def revalidate_scoped_repair_photo_file_access(
	access: ScopedPrivateFileAccess,
	*,
	reader: PhotoEvidenceReader | None = None,
) -> None:
	"""Prove that a capability still exactly matches the current scoped snapshot."""

	validated = _validated_file_access(access)
	if reader is None:
		_read_current_binding(validated)
		return
	rows = _read_rows_at_boundary(validated._actor_access, reader)
	current = _build_file_accesses(rows, validated._actor_access)
	if validated not in current:
		_raise(RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE)


def _read_current_binding(access: ScopedPrivateFileAccess) -> None:
	try:
		import frappe
	except ImportError:
		_raise(RepairPhotoEvidenceStoreCode.UNSUPPORTED_DATABASE)
	if getattr(frappe.db, "db_type", None) != "mariadb":
		_raise(RepairPhotoEvidenceStoreCode.UNSUPPORTED_DATABASE)
	try:
		rows = frappe.db.sql(
			FILE_REVALIDATION_SQL,
			{
				"repair_name": access._actor_access._repair_name,
				"repair_id": access._actor_access.repair_id,
				"actor_identity": access._actor_access._actor_identity,
				"row_limit": MAX_REVALIDATION_ROWS,
			},
		)
	except RepairPhotoEvidenceStoreError:
		raise
	except Exception:
		_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_READ_FAILED)
	current = _build_current_file_accesses(rows, access._actor_access)
	if access not in current:
		_raise(RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE)


def _build_current_file_accesses(
	rows: object, actor_access: ActorScopedRepairAccess
) -> tuple[ScopedPrivateFileAccess, ...]:
	if type(rows) not in {list, tuple} or not rows or len(rows) >= MAX_REVALIDATION_ROWS:
		_raise(RepairPhotoEvidenceStoreCode.PHOTO_EVIDENCE_UNSAFE)
	isolation = None
	children: dict[str, tuple[object, ...]] = {}
	matches: dict[str, dict[str, tuple[object, ...]]] = {}
	attached: dict[str, tuple[str, int]] = {}
	for raw_row in rows:
		if type(raw_row) not in {list, tuple} or len(raw_row) != 16:
			_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
		row = tuple(raw_row)
		if type(row[0]) is not str:
			_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
		canonical_isolation = row[0].upper().replace("_", "-")
		if isolation is None:
			isolation = canonical_isolation
		elif isolation != canonical_isolation:
			_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
		child_identity = row[1]
		if type(child_identity) is not str:
			_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
		child = row[2:5]
		if child_identity in children and children[child_identity] != child:
			_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
		children[child_identity] = child

		file_identity = row[5]
		if file_identity is not None:
			if type(file_identity) is not str:
				_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
			match = row[6:13]
			previous = matches.setdefault(child_identity, {}).get(file_identity)
			if previous is not None and previous != match:
				_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
			matches[child_identity][file_identity] = match

		attached_identity, attached_url, attached_url_chars = row[13:16]
		if attached_identity is not None:
			if (
				type(attached_identity) is not str
				or type(attached_url) is not str
				or type(attached_url_chars) is not int
			):
				_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
			previous_attached = attached.get(attached_identity)
			if previous_attached is not None and previous_attached != (
				attached_url,
				attached_url_chars,
			):
				_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
			attached[attached_identity] = (attached_url, attached_url_chars)
	if isolation is None:
		_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
	child_urls = {child[1] for child in children.values() if type(child[1]) is str}
	has_orphan = any(
		url_chars != len(url) or url_chars > MAX_REFERENCE_LENGTH or url not in child_urls
		for url, url_chars in attached.values()
	)
	converted = []
	for child_identity, (position, raw_url, url_chars) in children.items():
		child_matches = matches.get(child_identity, {})
		if not child_matches:
			match_state = 0
			file_identity = file_basename = file_revision = ""
		elif len(child_matches) > 1:
			match_state = 3
			file_identity, match = next(iter(child_matches.items()))
			file_basename, file_revision = match[:2]
		else:
			file_identity, match = next(iter(child_matches.items()))
			(
				file_basename,
				file_revision,
				is_private,
				is_folder,
				owner_doctype,
				owner_name,
				owner_field,
			) = match
			match_state = (
				int(
					not (
						_database_bool(is_private)
						and not _database_bool(is_folder)
						and owner_doctype == "Naprawa"
						and owner_name == actor_access._repair_name
						and owner_field == "zdjecie"
					)
				)
				+ 1
			)
		duplicate_url = sum(child[1] == raw_url for child in children.values()) > 1
		converted.append(
			(
				isolation,
				"PHOTO",
				None,
				None,
				None,
				None,
				position,
				raw_url,
				url_chars,
				duplicate_url,
				match_state,
				file_identity,
				file_basename,
				file_revision,
			)
		)
	meta = (
		isolation,
		"META",
		1,
		len(children),
		len(attached),
		has_orphan,
		None,
		None,
		None,
		None,
		None,
		None,
		None,
		None,
	)
	return _build_file_accesses((meta, *converted), actor_access)


def _read_rows_at_boundary(
	access: ActorScopedRepairAccess, reader: PhotoEvidenceReader | None
) -> tuple[RawEvidenceRow, ...]:
	selected_reader = _read_evidence_rows if reader is None else reader
	if not callable(selected_reader):
		_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)
	try:
		return selected_reader(
			repair_name=access._repair_name,
			repair_id=access.repair_id,
			actor_identity=access._actor_identity,
			fetch_limit=MAX_PHOTOS_PER_REPAIR + 1,
		)
	except RepairPhotoEvidenceStoreError as error:
		if _trusted_reader_error(error):
			raise RepairPhotoEvidenceStoreError(error.code) from None
		_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_READ_FAILED)
	except Exception:
		_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_READ_FAILED)


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


def _build_file_accesses(
	rows: tuple[RawEvidenceRow, ...], actor_access: ActorScopedRepairAccess
) -> tuple[ScopedPrivateFileAccess, ...]:
	if type(rows) is not tuple or not rows or len(rows) > MAX_PHOTOS_PER_REPAIR + 2:
		_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
	meta: tuple[object, ...] | None = None
	photos: list[tuple[int, str, int, bool, int, str, str, str]] = []
	isolation: str | None = None
	for row in rows:
		if type(row) is not tuple or len(row) != 14:
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
	if any(value is not None for value in meta[6:14]):
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
	for (
		position,
		raw_url,
		url_chars,
		duplicate_child_url,
		match_state,
		file_identity,
		file_basename,
		file_revision,
	) in photos:
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
			evidence = ScopedRepairPhotoEvidence(
				repair_id=actor_access.repair_id,
				position=position,
				is_private=True,
				exact_attachment=True,
				metadata_only=True,
			)
			result.append(
				ScopedPrivateFileAccess(
					evidence=evidence,
					actor_access=actor_access,
					file_identity=file_identity,
					file_basename=file_basename,
					file_url=raw_url,
					file_revision=file_revision,
					_seal=_FILE_ACCESS_SEAL,
				)
			)
		except Exception:
			_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
	return tuple(sorted(result, key=lambda item: item.evidence.position))


def _parse_photo_row(
	row: tuple[object, ...],
) -> tuple[int, str, int, bool, int, str, str, str]:
	if any(value is not None for value in row[2:6]):
		_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
	position = row[6]
	raw_url = row[7]
	url_chars = row[8]
	duplicate_child_url = _database_bool(row[9])
	match_state = row[10]
	file_identity = row[11]
	file_basename = row[12]
	file_revision = row[13]
	if (
		type(position) is not int
		or not 1 <= position <= MAX_PHOTO_POSITION
		or type(raw_url) is not str
		or type(url_chars) is not int
		or url_chars < 0
		or type(match_state) is not int
		or match_state not in {0, 1, 2, 3}
		or type(file_identity) is not str
		or type(file_basename) is not str
		or type(file_revision) is not str
	):
		_raise(RepairPhotoEvidenceStoreCode.EVIDENCE_MALFORMED)
	return (
		position,
		raw_url,
		url_chars,
		duplicate_child_url,
		match_state,
		file_identity,
		file_basename,
		file_revision,
	)


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


def _validated_file_access(value: object) -> ScopedPrivateFileAccess:
	if type(value) is not ScopedPrivateFileAccess:
		_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)
	try:
		if value._seal is not _FILE_ACCESS_SEAL:
			raise ValueError
		return ScopedPrivateFileAccess(
			evidence=value.evidence,
			actor_access=value._actor_access,
			file_identity=value._file_identity,
			file_basename=value._file_basename,
			file_url=value._file_url,
			file_revision=value._file_revision,
			_seal=_FILE_ACCESS_SEAL,
		)
	except (AttributeError, RepairPhotoEvidenceStoreError, TypeError, ValueError):
		_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)


def _validated_evidence(value: object) -> ScopedRepairPhotoEvidence:
	if type(value) is not ScopedRepairPhotoEvidence:
		_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)
	try:
		result = ScopedRepairPhotoEvidence(
			repair_id=value.repair_id,
			position=value.position,
			is_private=value.is_private,
			exact_attachment=value.exact_attachment,
			metadata_only=value.metadata_only,
		)
	except Exception:
		_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)
	if (
		result.is_private is not True
		or result.exact_attachment is not True
		or result.metadata_only is not True
	):
		_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)
	return result


def _validate_file_binding(file_basename: object, file_url: object, file_revision: object) -> None:
	if (
		type(file_basename) is not str
		or not file_basename
		or len(file_basename) > MAX_INTERNAL_ID_LENGTH
		or type(file_url) is not str
		or type(file_revision) is not str
		or not file_revision
		or len(file_revision) > 32
	):
		_raise(RepairPhotoEvidenceStoreCode.INVALID_INPUT)
	if (
		file_basename in {".", ".."}
		or "/" in file_basename
		or "\\" in file_basename
		or file_url != f"/private/files/{file_basename}"
		or any(
			character.isspace() or ord(character) < 32 or ord(character) == 127 for character in file_basename
		)
		or any(ord(character) < 32 or ord(character) == 127 for character in file_revision)
	):
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
