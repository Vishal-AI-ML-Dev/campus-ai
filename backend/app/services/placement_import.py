"""Academic spreadsheet import for student placement profiles (Phase 9.2).

Flow: template -> preview (parse + validate, nothing written) -> commit (the
SAME file is uploaded again with the SHA-256 the preview returned; it is
re-parsed and re-validated server-side against the current database, then all
valid rows are saved in ONE transaction).

Rules (spec §12):
* Columns: email (identifies the student; or roll_number of an existing
  profile), roll_number, graduation_year, tenth_percent, twelfth_percent,
  diploma_percent, active_backlogs. Headers are case-insensitive; unknown
  columns are ignored and reported by NAME only.
* A blank cell never overwrites a stored value (to clear a value, edit the
  profile). Placement status and every verified metric (CGPA, attendance,
  skills, projects) are NOT importable.
* Students are matched inside the caller's institute only (a student of
  another institute is "not found" - same message, no oracle). Roll numbers
  must be unique within the institute and within the file; a student may
  appear only once per file.
* Limits: 2 MB, 5 000 data rows, .csv (UTF-8) or .xlsx. Content is never
  logged; the audit row holds counts and a hash prefix only.

The XLSX reader uses only the standard library: zip member count / size /
compression-ratio limits, and any XML with a DOCTYPE is rejected before
parsing (no entity expansion is possible).
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import StudentPlacementProfile, User, UserRole
from app.routers.audit import record_audit

MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 5000
_XLSX_MAX_MEMBERS = 300
_XLSX_MAX_UNCOMPRESSED = 40 * 1024 * 1024
_XLSX_MAX_RATIO = 200

COLUMNS = (
    "email",
    "roll_number",
    "graduation_year",
    "tenth_percent",
    "twelfth_percent",
    "diploma_percent",
    "active_backlogs",
)
VALUE_FIELDS = COLUMNS[1:]
TEMPLATE_CSV = ",".join(COLUMNS) + "\r\n"

_ROLL_RE = re.compile(r"^[A-Za-z0-9/_.\-]{1,40}$")
_EMAIL_RE = re.compile(r"^[^@\s]{1,128}@[^@\s]{1,128}\.[^@\s]{1,64}$")
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CSV_TYPES = {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain", "application/octet-stream", ""}
_XLSX_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",
    "application/zip",
    "",
}


def _bad(detail: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


# --- Reading the upload ---------------------------------------------------------
async def read_upload(file: UploadFile) -> tuple[bytes, str]:
    """Return (bytes, 'csv'|'xlsx'). Extension, content type, size and magic
    bytes are all checked here, on the server."""
    name = (file.filename or "").lower().strip()
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if name.endswith(".csv"):
        kind = "csv"
        if ctype not in _CSV_TYPES:
            raise _bad(f"Unsupported content type for a CSV file: {ctype}", status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    elif name.endswith(".xlsx"):
        kind = "xlsx"
        if ctype not in _XLSX_TYPES:
            raise _bad(f"Unsupported content type for an XLSX file: {ctype}", status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    else:
        raise _bad("Only .csv and .xlsx files are accepted.", status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_IMPORT_BYTES:
            raise _bad("File too large (max 2 MB).", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise _bad("The file is empty.")
    if kind == "xlsx" and not data.startswith(b"PK\x03\x04"):
        raise _bad("This is not a valid .xlsx file.", status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    if kind == "csv" and (data.startswith(b"PK\x03\x04") or b"\x00" in data):
        raise _bad("This is not a valid CSV text file.", status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    return data, kind


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_csv(data: bytes) -> list[list[str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _bad("The CSV file must be UTF-8 encoded.")
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error:
        raise _bad("The CSV file could not be read.")
    return rows


def _xml(zf: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        raw = zf.read(name)
    except KeyError:
        return None
    if b"<!doctype" in raw[:4096].lower() or b"<!entity" in raw.lower():
        raise _bad("This .xlsx file contains unsupported XML declarations.")
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise _bad("This .xlsx file is damaged and could not be read.")


def _col_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha()).upper()
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - 64)
    return max(idx - 1, 0)


def _parse_xlsx(data: bytes) -> list[list[str]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise _bad("This is not a valid .xlsx file.", status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    with zf:
        infos = zf.infolist()
        if len(infos) > _XLSX_MAX_MEMBERS:
            raise _bad("This .xlsx file has too many parts.")
        total = 0
        for info in infos:
            total += info.file_size
            if info.compress_size and info.file_size / info.compress_size > _XLSX_MAX_RATIO and info.file_size > 1_000_000:
                raise _bad("This .xlsx file is not supported (suspicious compression).")
        if total > _XLSX_MAX_UNCOMPRESSED:
            raise _bad("This .xlsx file is too large once unpacked.")

        shared: list[str] = []
        sst = _xml(zf, "xl/sharedStrings.xml")
        if sst is not None:
            for si in sst.findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))

        sheet_path = _first_sheet_path(zf)
        sheet = _xml(zf, sheet_path)
        if sheet is None:
            raise _bad("This .xlsx file has no worksheet.")
        rows: list[list[str]] = []
        for row in sheet.iterfind("m:sheetData/m:row", _NS):
            cells: dict[int, str] = {}
            for i, c in enumerate(row.findall("m:c", _NS)):
                col = _col_index(c.get("r", "")) if c.get("r") else i
                if col > 50:
                    continue
                kind = c.get("t", "n")
                if kind == "s":
                    v = c.find("m:v", _NS)
                    try:
                        value = shared[int(v.text)] if v is not None and v.text is not None else ""
                    except (ValueError, IndexError):
                        raise _bad("This .xlsx file is damaged and could not be read.")
                elif kind == "inlineStr":
                    value = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
                else:
                    v = c.find("m:v", _NS)
                    value = v.text if v is not None and v.text is not None else ""
                cells[col] = value
            if cells:
                width = max(cells) + 1
                rows.append([cells.get(i, "") for i in range(width)])
            else:
                rows.append([])
            if len(rows) > MAX_IMPORT_ROWS + 1:
                break
        return rows


def _first_sheet_path(zf: zipfile.ZipFile) -> str:
    wb = _xml(zf, "xl/workbook.xml")
    rels = _xml(zf, "xl/_rels/workbook.xml.rels")
    if wb is not None and rels is not None:
        first = wb.find("m:sheets/m:sheet", _NS)
        rid = first.get(f"{{{_REL_NS}}}id") if first is not None else None
        for rel in rels.findall(f"{{{_PKG_REL_NS}}}Relationship"):
            if rel.get("Id") == rid:
                target = (rel.get("Target") or "").lstrip("/")
                target = target if target.startswith("xl/") else f"xl/{target}"
                if re.fullmatch(r"xl/worksheets/[A-Za-z0-9_]+\.xml", target):
                    return target
    return "xl/worksheets/sheet1.xml"


def parse_rows(data: bytes, kind: str) -> list[list[str]]:
    return _parse_csv(data) if kind == "csv" else _parse_xlsx(data)


# --- Validation -----------------------------------------------------------------
@dataclass
class RowResult:
    row: int
    action: str = "error"
    student_id: int | None = None
    student_name: str | None = None
    email: str | None = None
    roll_number: str | None = None
    values: dict = field(default_factory=dict)
    changed_fields: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class Plan:
    file_sha256: str
    file_type: str
    ignored_columns: list[str]
    rows: list[RowResult]

    def count(self, action: str) -> int:
        return sum(1 for r in self.rows if r.action == action)


def _number(raw: str, label: str, lo: float, hi: float, integer: bool, errors: list[str]):
    text = raw.strip().rstrip("%").strip()
    try:
        num = float(text)
    except ValueError:
        errors.append(f"{label} must be a number")
        return None
    if num != num or num in (float("inf"), float("-inf")):
        errors.append(f"{label} must be a number")
        return None
    if integer:
        if abs(num - round(num)) > 1e-9:
            errors.append(f"{label} must be a whole number")
            return None
        num = int(round(num))
    else:
        num = round(num, 2)
    if num < lo or num > hi:
        errors.append(f"{label} must be between {lo:g} and {hi:g}")
        return None
    return num


def _validate_values(cells: dict[str, str], errors: list[str]) -> dict:
    values: dict = {}
    roll = cells.get("roll_number", "").strip()
    if roll:
        if _ROLL_RE.match(roll):
            values["roll_number"] = roll
        else:
            errors.append("Roll number may contain letters, digits and / _ . - only (max 40)")
    for name, label, lo, hi, integer in (
        ("graduation_year", "Graduation year", 2000, 2100, True),
        ("tenth_percent", "10th %", 0, 100, False),
        ("twelfth_percent", "12th %", 0, 100, False),
        ("diploma_percent", "Diploma %", 0, 100, False),
        ("active_backlogs", "Active backlogs", 0, 99, True),
    ):
        raw = cells.get(name, "").strip()
        if raw:
            num = _number(raw, label, lo, hi, integer, errors)
            if num is not None:
                values[name] = num
    return values


def build_plan(db: Session, tenant_id: int, data: bytes, kind: str) -> Plan:
    rows = parse_rows(data, kind)
    # Skip leading empty rows to find the header.
    while rows and not any((c or "").strip() for c in rows[0]):
        rows.pop(0)
    if not rows:
        raise _bad("The file has no header row.")
    header = [(h or "").strip().lower().replace(" ", "_") for h in rows[0]]
    positions: dict[str, int] = {}
    ignored: list[str] = []
    for i, name in enumerate(header):
        if not name:
            continue
        if name in COLUMNS and name not in positions:
            positions[name] = i
        elif name[:40] not in ignored:
            ignored.append(name[:40])
    if "email" not in positions and "roll_number" not in positions:
        raise _bad("The file needs an 'email' column (or 'roll_number' for students who already have one).")
    if not any(c in positions for c in VALUE_FIELDS):
        raise _bad("The file has no academic columns to import.")
    body = rows[1:]
    if len(body) > MAX_IMPORT_ROWS:
        raise _bad(f"Too many rows (max {MAX_IMPORT_ROWS}). Split the file.")

    students = {
        u.email.lower(): u
        for u in db.scalars(
            select(User).where(User.tenant_id == tenant_id, User.role == UserRole.student, User.is_active.is_(True))
        )
    }
    by_id = {u.id: u for u in students.values()}
    profiles = {
        p.student_id: p
        for p in db.scalars(select(StudentPlacementProfile).where(StudentPlacementProfile.tenant_id == tenant_id))
    }
    roll_owner = {p.roll_number.lower(): p.student_id for p in profiles.values() if p.roll_number}

    results: list[RowResult] = []
    seen_students: dict[int, int] = {}
    seen_rolls: dict[str, int] = {}
    for offset, raw in enumerate(body):
        line = offset + 2
        cells = {name: (raw[i] if i < len(raw) else "") or "" for name, i in positions.items()}
        if not any(v.strip() for v in cells.values()):
            continue  # blank line
        res = RowResult(row=line)
        errors: list[str] = []
        email = cells.get("email", "").strip().lower()
        res.email = email[:254] or None
        values = _validate_values(cells, errors)
        res.roll_number = values.get("roll_number") or (cells.get("roll_number", "").strip()[:40] or None)

        student: User | None = None
        if email:
            if not _EMAIL_RE.match(email):
                errors.append("Email is not valid")
            else:
                student = students.get(email)
                if student is None:
                    errors.append("No active student with this email in your institute")
        elif values.get("roll_number"):
            owner = roll_owner.get(values["roll_number"].lower())
            student = by_id.get(owner) if owner is not None else None
            if student is None:
                errors.append("No student with this roll number yet; add an email column to match the student")
        else:
            errors.append("Email is required")

        if student is not None:
            res.student_id, res.student_name = student.id, student.full_name
            if student.id in seen_students:
                errors.append(f"This student already appears on row {seen_students[student.id]}")
            else:
                seen_students[student.id] = line
            roll = values.get("roll_number")
            if roll:
                key = roll.lower()
                if key in seen_rolls:
                    errors.append(f"Roll number repeated on row {seen_rolls[key]}")
                else:
                    seen_rolls[key] = line
                owner = roll_owner.get(key)
                if owner is not None and owner != student.id:
                    errors.append("Another student of your institute already has this roll number")

        res.values = values
        if errors:
            res.errors = errors
            res.action = "error"
        else:
            profile = profiles.get(student.id)
            changed = [
                name for name, val in values.items() if profile is None or getattr(profile, name) != val
            ]
            res.changed_fields = sorted(changed)
            res.action = "create" if profile is None else ("update" if changed else "unchanged")
        results.append(res)
    return Plan(file_sha256=sha256(data), file_type=kind, ignored_columns=ignored, rows=results)


def commit_plan(db: Session, actor: User, plan: Plan) -> dict:
    """Write every valid row in ONE transaction; blanks never overwrite."""
    tenant_id = actor.tenant_id
    profiles = {
        p.student_id: p
        for p in db.scalars(select(StudentPlacementProfile).where(StudentPlacementProfile.tenant_id == tenant_id))
    }
    created = updated = 0
    for r in plan.rows:
        if r.action not in ("create", "update"):
            continue
        profile = profiles.get(r.student_id)
        if profile is None:
            profile = StudentPlacementProfile(tenant_id=tenant_id, student_id=r.student_id, placement_status="active")
            db.add(profile)
            created += 1
        else:
            updated += 1
        for name, value in r.values.items():
            setattr(profile, name, value)
        profile.updated_by_id = actor.id
    summary = dict(
        file_sha256=plan.file_sha256,
        total_rows=len(plan.rows),
        created=created,
        updated=updated,
        unchanged=plan.count("unchanged"),
        invalid=plan.count("error"),
    )
    record_audit(
        db,
        actor,
        "placement.academic_import",
        f"Academic import {plan.file_sha256[:12]} ({plan.file_type}): {summary['total_rows']} rows, "
        f"{created} created, {updated} updated, {summary['unchanged']} unchanged, {summary['invalid']} invalid",
        target_type="tenant",
        target_id=tenant_id,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _bad(
            "Nothing was saved: profiles or roll numbers were changed at the same time. Preview the file again.",
            status.HTTP_409_CONFLICT,
        )
    return summary


__all__ = [
    "COLUMNS",
    "MAX_IMPORT_BYTES",
    "MAX_IMPORT_ROWS",
    "TEMPLATE_CSV",
    "build_plan",
    "commit_plan",
    "read_upload",
]
