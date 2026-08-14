"""Fail-closed admission policy for legacy repair photo references.

This module is deliberately independent from Frappe.  It only decides whether
the metadata supplied by a caller proves that a photo reference is either an
unchanged legacy public child row or an exact private attachment owned by the
current document.  It never reads file content and never authorizes download.
"""

from dataclasses import dataclass, field
from enum import StrEnum

MAX_REFERENCE_LENGTH = 512
MAX_TECHNICAL_ID_LENGTH = 140
_ALLOWED_OWNERS = frozenset({"Naprawa", "Przyjecie Zbiorcze"})
_ATTACH_FIELD_BY_OWNER = {
	"Naprawa": "zdjecie",
	"Przyjecie Zbiorcze": "zdjecie",
}


class RepairPhotoPolicyCode(StrEnum):
	INVALID_INPUT = "INVALID_INPUT"
	INVALID_PHOTO_REFERENCE = "INVALID_PHOTO_REFERENCE"
	PUBLIC_PHOTO_FORBIDDEN = "PUBLIC_PHOTO_FORBIDDEN"
	PRIVATE_FILE_REQUIRED = "PRIVATE_FILE_REQUIRED"
	PRIVATE_FILE_MISMATCH = "PRIVATE_FILE_MISMATCH"


class RepairPhotoPolicyError(ValueError):
	"""Sanitized, code-only policy failure."""

	def __init__(self, code: RepairPhotoPolicyCode) -> None:
		if type(code) is not RepairPhotoPolicyCode:
			raise TypeError("INVALID_ERROR_CODE")
		self.code = code
		super().__init__(code.value)

	def __repr__(self) -> str:
		return f"RepairPhotoPolicyError(code={self.code.value!r})"


class PhotoReferenceKind(StrEnum):
	PUBLIC = "PUBLIC"
	PRIVATE = "PRIVATE"


@dataclass(frozen=True, slots=True)
class RepairPhotoRow:
	name: str = field(init=False, repr=False)
	parent: str = field(init=False, repr=False)
	file_url: str = field(init=False, repr=False)

	def __init__(self, *, name: str, parent: str, file_url: str) -> None:
		_validate_technical_id(name)
		_validate_technical_id(parent)
		if type(file_url) is not str:
			raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_INPUT)
		object.__setattr__(self, "name", name)
		object.__setattr__(self, "parent", parent)
		object.__setattr__(self, "file_url", file_url)

	def __repr__(self) -> str:
		return "RepairPhotoRow(<redacted>)"


@dataclass(frozen=True, slots=True)
class PrivateFileEvidence:
	name: str = field(init=False, repr=False)
	file_url: str = field(init=False, repr=False)
	is_private: bool = field(init=False, repr=False)
	attached_to_doctype: str = field(init=False, repr=False)
	attached_to_name: str = field(init=False, repr=False)
	attached_to_field: str = field(init=False, repr=False)

	def __init__(
		self,
		*,
		name: str,
		file_url: str,
		is_private: bool,
		attached_to_doctype: str,
		attached_to_name: str,
		attached_to_field: str,
	) -> None:
		for value in (name, attached_to_doctype, attached_to_name, attached_to_field):
			_validate_technical_id(value)
		if type(file_url) is not str or type(is_private) is not bool:
			raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_INPUT)
		object.__setattr__(self, "name", name)
		object.__setattr__(self, "file_url", file_url)
		object.__setattr__(self, "is_private", is_private)
		object.__setattr__(self, "attached_to_doctype", attached_to_doctype)
		object.__setattr__(self, "attached_to_name", attached_to_name)
		object.__setattr__(self, "attached_to_field", attached_to_field)

	def __repr__(self) -> str:
		return "PrivateFileEvidence(<redacted>)"


@dataclass(frozen=True, slots=True)
class PhotoAdmissionResult:
	private_count: int
	legacy_count: int

	def __post_init__(self) -> None:
		if (
			type(self.private_count) is not int
			or type(self.legacy_count) is not int
			or self.private_count < 0
			or self.legacy_count < 0
		):
			raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_INPUT)


def classify_photo_reference(file_url: str) -> PhotoReferenceKind:
	"""Classify one canonical local File URL without accepting remote input."""

	if type(file_url) is not str or not file_url or len(file_url) > MAX_REFERENCE_LENGTH:
		raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_PHOTO_REFERENCE)
	if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in file_url):
		raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_PHOTO_REFERENCE)
	if (
		"\\" in file_url
		or "?" in file_url
		or "#" in file_url
		or "%" in file_url
		or "<" in file_url
		or ">" in file_url
	):
		raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_PHOTO_REFERENCE)

	if file_url.startswith("/private/files/"):
		kind = PhotoReferenceKind.PRIVATE
		path = file_url[len("/private/files/") :]
	elif file_url.startswith("/files/"):
		kind = PhotoReferenceKind.PUBLIC
		path = file_url[len("/files/") :]
	else:
		raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_PHOTO_REFERENCE)

	segments = path.split("/")
	if not path or any(not segment or segment in {".", ".."} for segment in segments):
		raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_PHOTO_REFERENCE)
	return kind


def validate_repair_photo_references(
	*,
	owner_doctype: str,
	owner_name: str,
	current_rows: tuple[RepairPhotoRow, ...],
	stored_rows: tuple[RepairPhotoRow, ...],
	private_files: tuple[PrivateFileEvidence, ...],
) -> PhotoAdmissionResult:
	"""Validate the complete photo set for one document.

	A canonical public reference is admitted only when the exact child name,
	parent and URL already exist in storage.  Every private reference requires one
	exact File row attached to the current owner and field.
	"""

	_validate_owner(owner_doctype, owner_name)
	if type(current_rows) is not tuple or type(stored_rows) is not tuple or type(private_files) is not tuple:
		raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_INPUT)
	if any(type(row) is not RepairPhotoRow for row in current_rows + stored_rows):
		raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_INPUT)
	if any(type(item) is not PrivateFileEvidence for item in private_files):
		raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_INPUT)

	stored_by_name: dict[str, RepairPhotoRow] = {}
	for row in stored_rows:
		if row.parent != owner_name or row.name in stored_by_name:
			raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_INPUT)
		stored_by_name[row.name] = row

	current_names: set[str] = set()
	private_count = 0
	legacy_count = 0
	for row in current_rows:
		if row.parent != owner_name or row.name in current_names:
			raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_INPUT)
		current_names.add(row.name)
		kind = classify_photo_reference(row.file_url)
		if kind is PhotoReferenceKind.PUBLIC:
			stored = stored_by_name.get(row.name)
			if stored is None or stored.parent != row.parent or stored.file_url != row.file_url:
				raise RepairPhotoPolicyError(RepairPhotoPolicyCode.PUBLIC_PHOTO_FORBIDDEN)
			legacy_count += 1
			continue

		matches = tuple(
			item
			for item in private_files
			if item.file_url == row.file_url
			and item.attached_to_doctype == owner_doctype
			and item.attached_to_name == owner_name
			and item.attached_to_field == _ATTACH_FIELD_BY_OWNER[owner_doctype]
		)
		if not matches:
			raise RepairPhotoPolicyError(RepairPhotoPolicyCode.PRIVATE_FILE_REQUIRED)
		if len(matches) != 1 or not matches[0].is_private:
			raise RepairPhotoPolicyError(RepairPhotoPolicyCode.PRIVATE_FILE_MISMATCH)
		private_count += 1

	return PhotoAdmissionResult(private_count=private_count, legacy_count=legacy_count)


def require_transferable_private_photo(
	*,
	owner_doctype: str,
	owner_name: str,
	file_url: str,
	private_files: tuple[PrivateFileEvidence, ...],
) -> PrivateFileEvidence:
	"""Return the single exact private source attachment eligible for metadata copy."""

	_validate_owner(owner_doctype, owner_name)
	if classify_photo_reference(file_url) is not PhotoReferenceKind.PRIVATE:
		raise RepairPhotoPolicyError(RepairPhotoPolicyCode.PUBLIC_PHOTO_FORBIDDEN)
	if type(private_files) is not tuple or any(
		type(item) is not PrivateFileEvidence for item in private_files
	):
		raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_INPUT)
	matches = tuple(
		item
		for item in private_files
		if item.file_url == file_url
		and item.attached_to_doctype == owner_doctype
		and item.attached_to_name == owner_name
		and item.attached_to_field == _ATTACH_FIELD_BY_OWNER[owner_doctype]
	)
	if not matches:
		raise RepairPhotoPolicyError(RepairPhotoPolicyCode.PRIVATE_FILE_REQUIRED)
	if len(matches) != 1 or not matches[0].is_private:
		raise RepairPhotoPolicyError(RepairPhotoPolicyCode.PRIVATE_FILE_MISMATCH)
	return matches[0]


def _validate_owner(owner_doctype: str, owner_name: str) -> None:
	_validate_technical_id(owner_doctype)
	if owner_doctype not in _ALLOWED_OWNERS:
		raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_INPUT)
	_validate_technical_id(owner_name)


def _validate_technical_id(value: object) -> None:
	if type(value) is not str or not value or len(value) > MAX_TECHNICAL_ID_LENGTH:
		raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_INPUT)
	if not value.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in value):
		raise RepairPhotoPolicyError(RepairPhotoPolicyCode.INVALID_INPUT)
