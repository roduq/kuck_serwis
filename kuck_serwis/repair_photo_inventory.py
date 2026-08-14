"""Bounded, count-only inventory of repair photo attachment metadata.

This dark adapter reads metadata only.  It does not read blobs, authorize a
download, prove storage existence, or make the FILE-01 control pass.
"""

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import TypeAlias

from kuck_serwis.repair_photo_policy import (
	PhotoReferenceKind,
	RepairPhotoPolicyError,
	classify_photo_reference,
)

DEFAULT_CHILD_ROWS_PER_SOURCE = 5_000
MAX_CHILD_ROWS_PER_SOURCE = 25_000
DEFAULT_FILE_ROWS = 10_000
MAX_FILE_ROWS = 50_000
MAX_REFERENCE_LENGTH = 512
_MAX_ID_LENGTH = 140


class RepairPhotoInventoryCode(StrEnum):
	INVALID_INPUT = "INVALID_INPUT"
	UNSUPPORTED_DATABASE = "UNSUPPORTED_DATABASE"
	UNSAFE_ISOLATION = "UNSAFE_ISOLATION"
	INVENTORY_READ_FAILED = "INVENTORY_READ_FAILED"
	INVENTORY_MALFORMED = "INVENTORY_MALFORMED"
	INVENTORY_TRUNCATED = "INVENTORY_TRUNCATED"


class RepairPhotoInventoryError(RuntimeError):
	"""Sanitized, code-only inventory failure."""

	def __init__(self, code: RepairPhotoInventoryCode) -> None:
		if type(code) is not RepairPhotoInventoryCode:
			raise TypeError("INVALID_ERROR_CODE")
		self.code = code
		super().__init__(code.value)

	def __repr__(self) -> str:
		return f"RepairPhotoInventoryError(code={self.code.value!r})"


class RepairPhotoInventoryStatus(StrEnum):
	COMPLETE = "COMPLETE"
	TRUNCATED = "TRUNCATED"


@dataclass(frozen=True, slots=True)
class RepairPhotoInventoryLimits:
	child_rows_per_source: int = DEFAULT_CHILD_ROWS_PER_SOURCE
	file_rows: int = DEFAULT_FILE_ROWS

	def __post_init__(self) -> None:
		_validate_positive_int(self.child_rows_per_source, MAX_CHILD_ROWS_PER_SOURCE)
		_validate_positive_int(self.file_rows, MAX_FILE_ROWS)


@dataclass(frozen=True, slots=True)
class RepairPhotoInventoryCounters:
	naprawa_child_rows: int = 0
	przyjecie_child_rows: int = 0
	file_rows: int = 0
	empty_reference_rows: int = 0
	invalid_child_identity_rows: int = 0
	malformed_reference_rows: int = 0
	public_reference_rows: int = 0
	private_reference_rows: int = 0
	legacy_public_exact_rows: int = 0
	legacy_public_missing_file_rows: int = 0
	legacy_public_mismatched_file_rows: int = 0
	legacy_public_duplicate_file_rows: int = 0
	private_exact_rows: int = 0
	private_missing_file_rows: int = 0
	private_mismatched_file_rows: int = 0
	private_duplicate_file_rows: int = 0
	duplicate_child_url_groups: int = 0
	duplicate_file_url_groups: int = 0
	duplicate_orphan_file_url_groups: int = 0
	orphan_public_file_rows: int = 0
	orphan_private_file_rows: int = 0
	orphan_malformed_file_rows: int = 0
	unclassified_reference_rows: int = 0

	def __post_init__(self) -> None:
		for item in fields(self):
			value = getattr(self, item.name)
			if type(value) is not int or value < 0:
				raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVALID_INPUT)


@dataclass(frozen=True, slots=True)
class RepairPhotoInventoryReport:
	status: RepairPhotoInventoryStatus
	counters: RepairPhotoInventoryCounters
	naprawa_truncated: bool
	przyjecie_truncated: bool
	files_truncated: bool

	def __post_init__(self) -> None:
		if type(self.status) is not RepairPhotoInventoryStatus:
			raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVALID_INPUT)
		if type(self.counters) is not RepairPhotoInventoryCounters:
			raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVALID_INPUT)
		try:
			validated_counters = RepairPhotoInventoryCounters(
				**{
					item.name: getattr(self.counters, item.name)
					for item in fields(RepairPhotoInventoryCounters)
				}
			)
		except (AttributeError, RepairPhotoInventoryError):
			raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVALID_INPUT) from None
		object.__setattr__(self, "counters", validated_counters)
		flags = (self.naprawa_truncated, self.przyjecie_truncated, self.files_truncated)
		if any(type(flag) is not bool for flag in flags):
			raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVALID_INPUT)
		if (self.status is RepairPhotoInventoryStatus.TRUNCATED) is not any(flags):
			raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVALID_INPUT)
		if self.status is RepairPhotoInventoryStatus.TRUNCATED:
			classified = (
				self.counters.public_reference_rows
				+ self.counters.private_reference_rows
				+ self.counters.malformed_reference_rows
				+ self.counters.invalid_child_identity_rows
			)
			if classified != 0:
				raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVALID_INPUT)


RawInventoryRow: TypeAlias = tuple[object, ...]
PhotoInventoryReader: TypeAlias = Callable[..., tuple[RawInventoryRow, ...]]


# Every branch is independently capped at limit + 1 before union/join output.
# The result is one SELECT and therefore one InnoDB statement snapshot.  The
# adapter never starts, commits, or rolls back the caller's transaction.
INVENTORY_SQL = r"""
WITH
naprawa_refs AS (
	SELECT
		'NAPRAWA' AS source_kind,
		LEFT(`name`, 141) AS record_id,
		OCTET_LENGTH(COALESCE(`name`, '')) AS record_id_bytes,
		LEFT(`parent`, 141) AS owner_name,
		OCTET_LENGTH(COALESCE(`parent`, '')) AS owner_name_bytes,
		LEFT(COALESCE(`parenttype`, ''), 141) AS parenttype,
		LEFT(COALESCE(`parentfield`, ''), 141) AS parentfield,
		COALESCE(`zdjecie`, '') AS raw_url,
		OCTET_LENGTH(COALESCE(`zdjecie`, '')) AS url_bytes
	FROM `tabNaprawa Zdjecie`
	ORDER BY `name`
	LIMIT %(child_fetch_limit)s
),
przyjecie_refs AS (
	SELECT
		'PRZYJECIE' AS source_kind,
		LEFT(`name`, 141) AS record_id,
		OCTET_LENGTH(COALESCE(`name`, '')) AS record_id_bytes,
		LEFT(`parent`, 141) AS owner_name,
		OCTET_LENGTH(COALESCE(`parent`, '')) AS owner_name_bytes,
		LEFT(COALESCE(`parenttype`, ''), 141) AS parenttype,
		LEFT(COALESCE(`parentfield`, ''), 141) AS parentfield,
		COALESCE(`zdjecie`, '') AS raw_url,
		OCTET_LENGTH(COALESCE(`zdjecie`, '')) AS url_bytes
	FROM `tabPrzyjecie Zbiorcze Pozycja`
	ORDER BY `name`
	LIMIT %(child_fetch_limit)s
),
all_refs AS (
	SELECT * FROM naprawa_refs
	UNION ALL
	SELECT * FROM przyjecie_refs
),
evidenced_refs AS (
	SELECT
		r.*,
		CASE
			WHEN r.url_bytes NOT BETWEEN 1 AND 512 THEN NULL
			WHEN NOT EXISTS (
				SELECT 1
				FROM `tabFile` f FORCE INDEX (`file_url_index`)
				WHERE f.`file_url` = r.raw_url
				LIMIT 1
			) THEN 0
			WHEN EXISTS (
				SELECT 1
				FROM `tabFile` f FORCE INDEX (`file_url_index`)
				WHERE f.`file_url` = r.raw_url
				LIMIT 1 OFFSET 1
			) THEN 3
			WHEN EXISTS (
				SELECT 1
				FROM `tabFile` f FORCE INDEX (`file_url_index`)
				WHERE
					f.`file_url` = r.raw_url
					AND f.`is_private` = IF(r.raw_url LIKE '/private/files/%%', 1, 0)
					AND f.`is_folder` = 0
					AND f.`attached_to_doctype` = IF(
						r.source_kind = 'NAPRAWA', 'Naprawa', 'Przyjecie Zbiorcze'
					)
					AND f.`attached_to_name` = r.owner_name
					AND f.`attached_to_field` = 'zdjecie'
				LIMIT 1
			) THEN 1
			ELSE 2
		END AS match_state
	FROM all_refs r
),
naprawa_files AS (
	SELECT
		LEFT(f.`name`, 141) AS record_id,
		OCTET_LENGTH(COALESCE(f.`name`, '')) AS record_id_bytes,
		COALESCE(f.`file_url`, '') AS raw_url,
		OCTET_LENGTH(COALESCE(f.`file_url`, '')) AS url_bytes,
		f.`is_private`,
		LEFT(COALESCE(f.`attached_to_doctype`, ''), 141) AS attached_to_doctype,
		LEFT(COALESCE(f.`attached_to_name`, ''), 141) AS attached_to_name,
		OCTET_LENGTH(COALESCE(f.`attached_to_name`, '')) AS attached_to_name_bytes,
		LEFT(COALESCE(f.`attached_to_field`, ''), 141) AS attached_to_field,
		f.`is_folder`
	FROM `tabFile` f FORCE INDEX (`attached_to_doctype_attached_to_name_index`)
	WHERE f.`attached_to_doctype` = 'Naprawa' AND f.`attached_to_field` = 'zdjecie'
	ORDER BY f.`attached_to_name`, f.`name`
	LIMIT %(file_fetch_limit)s
),
przyjecie_files AS (
	SELECT
		LEFT(f.`name`, 141) AS record_id,
		OCTET_LENGTH(COALESCE(f.`name`, '')) AS record_id_bytes,
		COALESCE(f.`file_url`, '') AS raw_url,
		OCTET_LENGTH(COALESCE(f.`file_url`, '')) AS url_bytes,
		f.`is_private`,
		LEFT(COALESCE(f.`attached_to_doctype`, ''), 141) AS attached_to_doctype,
		LEFT(COALESCE(f.`attached_to_name`, ''), 141) AS attached_to_name,
		OCTET_LENGTH(COALESCE(f.`attached_to_name`, '')) AS attached_to_name_bytes,
		LEFT(COALESCE(f.`attached_to_field`, ''), 141) AS attached_to_field,
		f.`is_folder`
	FROM `tabFile` f FORCE INDEX (`attached_to_doctype_attached_to_name_index`)
	WHERE f.`attached_to_doctype` = 'Przyjecie Zbiorcze' AND f.`attached_to_field` = 'zdjecie'
	ORDER BY f.`attached_to_name`, f.`name`
	LIMIT %(file_fetch_limit)s
),
file_candidates AS (
	SELECT * FROM naprawa_files
	UNION ALL
	SELECT * FROM przyjecie_files
),
bounded_files AS (
	SELECT * FROM file_candidates
	ORDER BY attached_to_doctype, attached_to_name, record_id
	LIMIT %(file_fetch_limit)s
),
inventory_rows AS (
	SELECT
		0 AS row_order, 'META' AS row_kind, 'META' AS source_kind,
		'' AS record_id, 0 AS record_id_bytes, '' AS owner_name, 0 AS owner_name_bytes,
		'' AS parenttype, '' AS parentfield, '' AS raw_url, 0 AS url_bytes,
		NULL AS is_private, '' AS attached_to_doctype, '' AS attached_to_name,
		0 AS attached_to_name_bytes, '' AS attached_to_field, NULL AS is_folder,
		NULL AS match_state, REPEAT('0', 64) AS url_sha256
	UNION ALL
	SELECT
		1, 'REF', source_kind, record_id, record_id_bytes, owner_name, owner_name_bytes,
		parenttype, parentfield, LEFT(raw_url, 513), url_bytes,
		NULL, '', '', 0, '', NULL, match_state, LOWER(SHA2(raw_url, 256))
	FROM evidenced_refs
	UNION ALL
	SELECT
		2, 'FILE', 'FILE', record_id, record_id_bytes, '', 0,
		'', '', LEFT(raw_url, 513), url_bytes,
		is_private, attached_to_doctype, attached_to_name, attached_to_name_bytes,
		attached_to_field, is_folder, NULL, LOWER(SHA2(raw_url, 256))
	FROM bounded_files
)
SELECT
	@@tx_isolation AS isolation_level,
	row_kind,
	source_kind,
	record_id,
	record_id_bytes,
	owner_name,
	owner_name_bytes,
	parenttype,
	parentfield,
	raw_url,
	url_bytes,
	is_private,
	attached_to_doctype,
	attached_to_name,
	attached_to_name_bytes,
	attached_to_field,
	is_folder,
	match_state,
	url_sha256
FROM inventory_rows
ORDER BY row_order, source_kind, record_id
"""


@dataclass(frozen=True, slots=True, repr=False)
class _ReferenceRow:
	source: str
	record_id: str
	owner_name: str
	parenttype: str
	parentfield: str
	file_url: str
	identity_valid: bool
	url_present: bool
	url_valid: bool
	match_state: int | None
	url_sha256: str

	@property
	def owner_doctype(self) -> str:
		return "Naprawa" if self.source == "NAPRAWA" else "Przyjecie Zbiorcze"

	@property
	def expected_parentfield(self) -> str:
		return "zdjecia" if self.source == "NAPRAWA" else "pozycje"


@dataclass(frozen=True, slots=True, repr=False)
class _FileRow:
	record_id: str
	file_url: str
	is_private: bool | None
	attached_to_doctype: str
	attached_to_name: str
	attached_to_field: str
	is_folder: bool | None
	identity_valid: bool
	url_present: bool
	url_valid: bool
	url_sha256: str


def collect_repair_photo_inventory(
	*,
	limits: RepairPhotoInventoryLimits | None = None,
	reader: PhotoInventoryReader | None = None,
) -> RepairPhotoInventoryReport:
	"""Collect one bounded metadata inventory without reading file content."""

	if limits is None:
		limits = RepairPhotoInventoryLimits()
	if type(limits) is not RepairPhotoInventoryLimits:
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVALID_INPUT)
	try:
		limits = RepairPhotoInventoryLimits(
			child_rows_per_source=limits.child_rows_per_source,
			file_rows=limits.file_rows,
		)
	except (AttributeError, RepairPhotoInventoryError):
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVALID_INPUT) from None
	selected_reader = _read_inventory_rows if reader is None else reader
	if not callable(selected_reader):
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVALID_INPUT)

	try:
		raw_rows = selected_reader(
			child_fetch_limit=limits.child_rows_per_source + 1,
			file_fetch_limit=limits.file_rows + 1,
		)
	except RepairPhotoInventoryError as error:
		trusted_codes = {
			RepairPhotoInventoryCode.UNSUPPORTED_DATABASE,
			RepairPhotoInventoryCode.INVENTORY_READ_FAILED,
		}
		if (
			type(error) is RepairPhotoInventoryError
			and type(getattr(error, "code", None)) is RepairPhotoInventoryCode
			and error.code in trusted_codes
			and error.args == (error.code.value,)
		):
			raise RepairPhotoInventoryError(error.code) from None
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_READ_FAILED) from None
	except Exception:
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_READ_FAILED) from None

	return _build_report(raw_rows, limits)


def _read_inventory_rows(*, child_fetch_limit: int, file_fetch_limit: int) -> tuple[RawInventoryRow, ...]:
	try:
		import frappe
	except ImportError:
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.UNSUPPORTED_DATABASE) from None

	if getattr(frappe.db, "db_type", None) != "mariadb":
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.UNSUPPORTED_DATABASE)
	rows = frappe.db.sql(
		INVENTORY_SQL,
		{"child_fetch_limit": child_fetch_limit, "file_fetch_limit": file_fetch_limit},
	)
	return tuple(tuple(row) for row in rows)


def _build_report(
	raw_rows: tuple[RawInventoryRow, ...], limits: RepairPhotoInventoryLimits
) -> RepairPhotoInventoryReport:
	if type(raw_rows) is not tuple or not raw_rows:
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_MALFORMED)
	max_result_rows = 1 + (2 * (limits.child_rows_per_source + 1)) + (limits.file_rows + 1)
	if len(raw_rows) > max_result_rows:
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_MALFORMED)

	meta_count = 0
	references: list[_ReferenceRow] = []
	file_rows: list[_FileRow] = []
	isolation: str | None = None
	for raw_row in raw_rows:
		if type(raw_row) is not tuple or len(raw_row) != 19:
			raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_MALFORMED)
		row_isolation = raw_row[0]
		if type(row_isolation) is not str:
			raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_MALFORMED)
		canonical_isolation = row_isolation.upper().replace("_", "-")
		if isolation is None:
			isolation = canonical_isolation
		elif isolation != canonical_isolation:
			raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_MALFORMED)

		row_kind = raw_row[1]
		if row_kind == "META":
			meta_count += 1
			continue
		if row_kind == "REF":
			references.append(_parse_reference(raw_row))
			continue
		if row_kind == "FILE":
			file_rows.append(_parse_file(raw_row))
			continue
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_MALFORMED)

	if meta_count != 1:
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_MALFORMED)
	if isolation not in {"READ-COMMITTED", "REPEATABLE-READ", "SERIALIZABLE"}:
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.UNSAFE_ISOLATION)

	naprawa_all = [row for row in references if row.source == "NAPRAWA"]
	przyjecie_all = [row for row in references if row.source == "PRZYJECIE"]
	if len(naprawa_all) > limits.child_rows_per_source + 1:
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_MALFORMED)
	if len(przyjecie_all) > limits.child_rows_per_source + 1:
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_MALFORMED)
	if len(file_rows) > limits.file_rows + 1:
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_MALFORMED)

	naprawa_truncated = len(naprawa_all) > limits.child_rows_per_source
	przyjecie_truncated = len(przyjecie_all) > limits.child_rows_per_source
	files_truncated = len(file_rows) > limits.file_rows
	naprawa = naprawa_all[: limits.child_rows_per_source]
	przyjecie = przyjecie_all[: limits.child_rows_per_source]
	files = file_rows[: limits.file_rows]
	references = naprawa + przyjecie

	if naprawa_truncated or przyjecie_truncated or files_truncated:
		return RepairPhotoInventoryReport(
			status=RepairPhotoInventoryStatus.TRUNCATED,
			counters=RepairPhotoInventoryCounters(
				naprawa_child_rows=len(naprawa),
				przyjecie_child_rows=len(przyjecie),
				file_rows=len(files),
				unclassified_reference_rows=len(references),
			),
			naprawa_truncated=naprawa_truncated,
			przyjecie_truncated=przyjecie_truncated,
			files_truncated=files_truncated,
		)

	counters = _classify_complete(references, files)
	return RepairPhotoInventoryReport(
		status=RepairPhotoInventoryStatus.COMPLETE,
		counters=counters,
		naprawa_truncated=False,
		przyjecie_truncated=False,
		files_truncated=False,
	)


def _parse_reference(row: RawInventoryRow) -> _ReferenceRow:
	source = row[2]
	if source not in {"NAPRAWA", "PRZYJECIE"}:
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_MALFORMED)
	record_id, record_id_valid = _bounded_text(row[3], row[4], _MAX_ID_LENGTH)
	owner_name, owner_valid = _bounded_text(row[5], row[6], _MAX_ID_LENGTH)
	parenttype = _text(row[7])
	parentfield = _text(row[8])
	file_url, url_valid = _bounded_text(row[9], row[10], MAX_REFERENCE_LENGTH, allow_empty=True)
	expected_parenttype = "Naprawa" if source == "NAPRAWA" else "Przyjecie Zbiorcze"
	expected_parentfield = "zdjecia" if source == "NAPRAWA" else "pozycje"
	identity_valid = (
		record_id_valid
		and owner_valid
		and parenttype == expected_parenttype
		and parentfield == expected_parentfield
	)
	return _ReferenceRow(
		source=source,
		record_id=record_id,
		owner_name=owner_name,
		parenttype=parenttype,
		parentfield=parentfield,
		file_url=file_url,
		identity_valid=identity_valid,
		url_present=type(row[10]) is int and row[10] > 0,
		url_valid=url_valid,
		match_state=_match_state(row[17]),
		url_sha256=_sha256(row[18]),
	)


def _parse_file(row: RawInventoryRow) -> _FileRow:
	record_id, record_id_valid = _bounded_text(row[3], row[4], _MAX_ID_LENGTH)
	file_url, url_valid = _bounded_text(row[9], row[10], MAX_REFERENCE_LENGTH, allow_empty=True)
	is_private = _database_bool(row[11])
	attached_to_doctype = _text(row[12])
	attached_to_name, attached_name_valid = _bounded_text(row[13], row[14], _MAX_ID_LENGTH, allow_empty=True)
	attached_to_field = _text(row[15])
	is_folder = _database_bool(row[16])
	return _FileRow(
		record_id=record_id,
		file_url=file_url,
		is_private=is_private,
		attached_to_doctype=attached_to_doctype,
		attached_to_name=attached_to_name,
		attached_to_field=attached_to_field,
		is_folder=is_folder,
		identity_valid=record_id_valid and attached_name_valid and url_valid,
		url_present=type(row[10]) is int and row[10] > 0,
		url_valid=url_valid,
		url_sha256=_sha256(row[18]),
	)


def _classify_complete(
	references: list[_ReferenceRow], file_rows: list[_FileRow]
) -> RepairPhotoInventoryCounters:
	counts: Counter[str] = Counter()
	counts["naprawa_child_rows"] = sum(row.source == "NAPRAWA" for row in references)
	counts["przyjecie_child_rows"] = sum(row.source == "PRZYJECIE" for row in references)
	counts["file_rows"] = len(file_rows)

	valid_references: list[tuple[_ReferenceRow, PhotoReferenceKind]] = []
	for reference in references:
		if not reference.url_present:
			counts["empty_reference_rows"] += 1
			continue
		if not reference.url_valid:
			counts["malformed_reference_rows"] += 1
			continue
		if not reference.identity_valid:
			counts["invalid_child_identity_rows"] += 1
			continue
		try:
			kind = classify_photo_reference(reference.file_url)
		except RepairPhotoPolicyError:
			counts["malformed_reference_rows"] += 1
			continue
		valid_references.append((reference, kind))
		counts[
			"public_reference_rows" if kind is PhotoReferenceKind.PUBLIC else "private_reference_rows"
		] += 1

	files_by_url: defaultdict[str, list[_FileRow]] = defaultdict(list)
	for file_row in file_rows:
		if file_row.url_valid and file_row.url_present:
			files_by_url[file_row.file_url].append(file_row)

	child_url_counts = Counter(reference.file_url for reference, _kind in valid_references)
	counts["duplicate_child_url_groups"] = sum(value > 1 for value in child_url_counts.values())
	duplicate_file_urls: set[str] = set()
	duplicate_file_urls.update(
		reference.url_sha256 for reference, _kind in valid_references if reference.match_state == 3
	)
	attached_file_hash_counts = Counter(file_row.url_sha256 for file_row in file_rows)
	duplicate_file_urls.update(url_hash for url_hash, count in attached_file_hash_counts.items() if count > 1)
	counts["duplicate_file_url_groups"] = len(duplicate_file_urls)

	claimed_attachments: set[tuple[str, str, str, str]] = set()
	for reference, kind in valid_references:
		expected = (
			reference.file_url,
			reference.owner_doctype,
			reference.owner_name,
			"zdjecie",
		)
		claimed_attachments.add(expected)
		prefix = "legacy_public" if kind is PhotoReferenceKind.PUBLIC else "private"
		if reference.match_state is None:
			raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_MALFORMED)
		if reference.match_state == 0:
			counts[f"{prefix}_missing_file_rows"] += 1
		elif reference.match_state == 3:
			counts[f"{prefix}_duplicate_file_rows"] += 1
		elif reference.match_state == 1:
			counts[f"{prefix}_exact_rows"] += 1
		else:
			counts[f"{prefix}_mismatched_file_rows"] += 1

	orphan_url_hashes: list[str] = []
	for file_row in file_rows:
		attachment = (
			file_row.file_url,
			file_row.attached_to_doctype,
			file_row.attached_to_name,
			file_row.attached_to_field,
		)
		if attachment in claimed_attachments:
			continue
		orphan_url_hashes.append(file_row.url_sha256)
		if not file_row.url_present or not file_row.url_valid:
			counts["orphan_malformed_file_rows"] += 1
			continue
		try:
			kind = classify_photo_reference(file_row.file_url)
		except RepairPhotoPolicyError:
			counts["orphan_malformed_file_rows"] += 1
		else:
			counts[
				"orphan_public_file_rows" if kind is PhotoReferenceKind.PUBLIC else "orphan_private_file_rows"
			] += 1
	counts["duplicate_orphan_file_url_groups"] = sum(
		count > 1 for count in Counter(orphan_url_hashes).values()
	)

	return RepairPhotoInventoryCounters(
		**{item.name: counts[item.name] for item in fields(RepairPhotoInventoryCounters)}
	)


def _validate_positive_int(value: object, maximum: int) -> None:
	if type(value) is not int or value < 1 or value > maximum:
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVALID_INPUT)


def _text(value: object) -> str:
	if type(value) is not str:
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_MALFORMED)
	return value


def _bounded_text(
	value: object, byte_length: object, maximum: int, *, allow_empty: bool = False
) -> tuple[str, bool]:
	text = _text(value)
	if type(byte_length) is not int or byte_length < 0:
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_MALFORMED)
	valid = byte_length == len(text.encode("utf-8")) and byte_length <= maximum
	if not allow_empty and not text:
		valid = False
	return text, valid


def _database_bool(value: object) -> bool | None:
	if type(value) is bool:
		return value
	if type(value) is int and value in (0, 1):
		return bool(value)
	if value is None:
		return None
	return None


def _match_state(value: object) -> int | None:
	if value is None:
		return None
	if type(value) is int and value in (0, 1, 2, 3):
		return value
	raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_MALFORMED)


def _sha256(value: object) -> str:
	if (
		type(value) is not str
		or len(value) != 64
		or any(character not in "0123456789abcdef" for character in value)
	):
		raise RepairPhotoInventoryError(RepairPhotoInventoryCode.INVENTORY_MALFORMED)
	return value
