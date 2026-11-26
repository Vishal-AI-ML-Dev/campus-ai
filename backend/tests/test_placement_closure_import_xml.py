"""Phase 9 closure L3: spreadsheet XML / DOCTYPE security for the academic import.

Spec: docs/mock-interview/specs/phase-9-placement-institute-management.md §20.
Before L3 the guard looked for ``<!doctype`` in the first 4 KB only and for
``<!entity`` as ASCII bytes, so (a) a DOCTYPE after 4 KB of prolog and (b) any
UTF-16 encoded part with a DTD and entities passed the guard and reached
ElementTree. These tests pin the whole-part, encoding-aware guard and prove
normal CSV / XLSX uploads still work and that a refused upload writes nothing.
"""

# ruff: noqa: F811 - pytest fixtures are imported from test_placement_management and re-bound as arguments
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.models as models
from app.services import placement_import as imp
from tests.test_placement_management import (  # noqa: F401 - fixtures
    P,
    on,
    put_profile,
    world,
)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REJECT = "This .xlsx file contains unsupported XML declarations."
DAMAGED = "This .xlsx file is damaged and could not be read."


def _sheet_xml(rows: list[list[str]], prolog: str = '<?xml version="1.0" encoding="UTF-8"?>') -> str:
    def cell(ref, v):
        return f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>' if v != "" else ""

    letters = "ABCDEFG"
    body = "".join(
        f'<row r="{i + 1}">' + "".join(cell(f"{letters[j]}{i + 1}", v) for j, v in enumerate(r)) + "</row>"
        for i, r in enumerate(rows)
    )
    return prolog + f'<worksheet xmlns="{MAIN_NS}"><sheetData>{body}</sheetData></worksheet>'


def _zip(parts: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        for name, content in parts.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _xlsx_with_sheet(sheet: bytes | str) -> bytes:
    return _zip({"xl/worksheets/sheet1.xml": sheet})


def _full_xlsx(rows: list[list[str]]) -> bytes:
    """Workbook + relationships + shared strings, the shape Excel writes."""
    strings: list[str] = []
    cells_xml = []
    for i, r in enumerate(rows):
        cs = []
        for j, v in enumerate(r):
            if v == "":
                continue
            ref = f"{'ABCDEFG'[j]}{i + 1}"
            if v.replace(".", "", 1).isdigit():
                cs.append(f'<c r="{ref}"><v>{v}</v></c>')
            else:
                strings.append(v)
                cs.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
        cells_xml.append(f'<row r="{i + 1}">' + "".join(cs) + "</row>")
    head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
    sst = head + f'<sst xmlns="{MAIN_NS}" count="{len(strings)}">' + "".join(
        f"<si><t>{s}</t></si>" for s in strings) + "</sst>"
    wb = (head + f'<workbook xmlns="{MAIN_NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
          'relationships"><sheets><sheet name="Marks" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = (head + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'worksheet" Target="worksheets/data.xml"/></Relationships>')
    sheet = head + f'<worksheet xmlns="{MAIN_NS}"><sheetData>' + "".join(cells_xml) + "</sheetData></worksheet>"
    return _zip({"xl/workbook.xml": wb, "xl/_rels/workbook.xml.rels": rels,
                 "xl/sharedStrings.xml": sst, "xl/worksheets/data.xml": sheet})


def _parse_detail(data: bytes) -> str | None:
    try:
        imp.parse_rows(data, "xlsx")
    except HTTPException as exc:
        assert exc.status_code == 400
        return exc.detail
    return None


ROWS = [["email", "graduation_year"], ["s1@a.edu", "2027"]]
PAD_5K = "<!--" + "x" * 5000 + "-->"


# --- parser-level guard -------------------------------------------------------------
@pytest.mark.parametrize(
    "prolog",
    [
        '<?xml version="1.0"?><!DOCTYPE worksheet>',                                    # first 4 KB
        '<?xml version="1.0"?><!DocType worksheet>',                                    # mixed case
        f'<?xml version="1.0"?>{PAD_5K}<!DOCTYPE worksheet>',                           # after 4 KB
        f'<?xml version="1.0"?>{PAD_5K}<!DOCTYPE worksheet SYSTEM "file:///etc/passwd">',  # external DTD after 4 KB
        f'<?xml version="1.0"?>{PAD_5K}<?pi x?>{PAD_5K}<!DOCTYPE w PUBLIC "-//x//EN" "http://127.0.0.1:9/x.dtd">',
        '<?xml version="1.0"?><!DOCTYPE w [<!ENTITY xxe SYSTEM "file:///etc/hostname">]>',  # external entity
        '<?xml version="1.0"?><!DOCTYPE w [<!ENTITY % p SYSTEM "http://127.0.0.1:9/p.dtd"> %p;]>',  # parameter
        '<?xml version="1.0"?><!DOCTYPE w [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>',
        '<?xml version="1.0"?><!DOCTYPE w [<!NOTATION n SYSTEM "x">]>',
    ],
)
def test_dtd_and_entity_declarations_rejected_anywhere_in_prolog(prolog):
    assert _parse_detail(_xlsx_with_sheet(_sheet_xml(ROWS, prolog))) == REJECT


@pytest.mark.parametrize("codec", ["utf-16", "utf-16-le", "utf-16-be"])
def test_utf16_encoded_dtd_is_rejected(codec):
    """The old byte scan could not see these at all (the entity used to expand)."""
    prolog = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE w [<!ENTITY a "INJECTED">]>'
    xml = _sheet_xml([["email", "graduation_year"], ["s1@a.edu", "&a;"]], prolog)
    raw = xml.encode(codec)
    if codec != "utf-16":
        raw = ("﻿" + xml).encode(codec)  # explicit BOM for the LE/BE variants
    assert b"<!doctype" not in raw.lower() and b"<!entity" not in raw.lower()  # byte scan blind
    assert _parse_detail(_xlsx_with_sheet(raw)) == REJECT


@pytest.mark.parametrize("part", ["xl/sharedStrings.xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels"])
def test_dtd_in_any_parsed_part_is_rejected(part):
    good = zipfile.ZipFile(io.BytesIO(_full_xlsx(ROWS)))
    parts = {n: good.read(n) for n in good.namelist() if n != "[Content_Types].xml"}
    parts[part] = parts[part].replace(b"?>", b"?>" + PAD_5K.encode() + b"<!DOCTYPE x>", 1)
    assert _parse_detail(_zip(parts)) == REJECT


def test_billion_laughs_never_expands():
    lol = '<!ENTITY l0 "lol">' + "".join(f'<!ENTITY l{i} "{("&l" + str(i - 1) + ";") * 10}">' for i in range(1, 10))
    xml = _sheet_xml([["email"], ["&l9;"]], f'<?xml version="1.0"?>{PAD_5K}<!DOCTYPE w [{lol}]>')
    assert _parse_detail(_xlsx_with_sheet(xml)) == REJECT


def test_external_entity_never_reads_a_local_file(tmp_path, monkeypatch):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-MARKER")
    calls = []
    real = imp.ET.fromstring
    monkeypatch.setattr(imp.ET, "fromstring", lambda raw: calls.append(raw) or real(raw))
    uri = secret.as_uri()
    xml = _sheet_xml([["email"], ["&x;"]], f'<?xml version="1.0"?><!DOCTYPE w [<!ENTITY x SYSTEM "{uri}">]>')
    assert _parse_detail(_xlsx_with_sheet(xml)) == REJECT
    assert calls == []  # ElementTree never saw the part


@pytest.mark.parametrize(
    "sheet",
    [
        b"<worksheet><sheetData><row></sheetData></worksheet>",  # mismatched tag
        b"\x00\x01\x02 not xml",
        b"",
    ],
)
def test_malformed_xml_is_reported_as_damaged(sheet):
    assert _parse_detail(_xlsx_with_sheet(sheet)) == DAMAGED


def test_doctype_after_the_root_element_is_refused():
    assert _parse_detail(_xlsx_with_sheet(_sheet_xml(ROWS).encode() + b"<!DOCTYPE late>")) == REJECT
    late16 = (_sheet_xml(ROWS).replace('encoding="UTF-8"', 'encoding="UTF-16"') + "<!DOCTYPE late>").encode("utf-16")
    assert _parse_detail(_xlsx_with_sheet(late16)) in (REJECT, DAMAGED)  # never parsed


@pytest.mark.parametrize(
    "encode",
    [
        lambda x: x.encode("utf-8"),
        lambda x: b"\xef\xbb\xbf" + x.encode("utf-8"),                                 # UTF-8 BOM
        lambda x: x.replace('encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16"),  # UTF-16 + BOM
        lambda x: x.replace('<?xml version="1.0" encoding="UTF-8"?>', "").encode("utf-8"),  # no declaration
    ],
)
def test_legitimate_encodings_still_parse(encode):
    rows = imp.parse_rows(_xlsx_with_sheet(encode(_sheet_xml(ROWS))), "xlsx")
    assert rows == ROWS


def test_doctype_text_inside_a_cell_is_not_a_false_positive():
    rows = imp.parse_rows(_xlsx_with_sheet(_sheet_xml([["email"], ["&lt;!DOCTYPE x&gt; &lt;!ENTITY&gt;"]])), "xlsx")
    assert rows[1] == ["<!DOCTYPE x> <!ENTITY>"]


def test_excel_shaped_workbook_still_parses():
    rows = imp.parse_rows(_full_xlsx([["email", "graduation_year", "active_backlogs"], ["s1@a.edu", "2027", "0"]]),
                          "xlsx")
    assert rows == [["email", "graduation_year", "active_backlogs"], ["s1@a.edu", "2027", "0"]]


def test_existing_limits_unchanged():
    assert imp.MAX_IMPORT_BYTES == 2 * 1024 * 1024 and imp.MAX_IMPORT_ROWS == 5000
    assert (imp._XLSX_MAX_MEMBERS, imp._XLSX_MAX_UNCOMPRESSED, imp._XLSX_MAX_RATIO) == (300, 40 * 1024 * 1024, 200)


# --- API level: refused uploads write nothing; blanks never overwrite -----------------
def _upload(client, h, content: bytes, name="marks.xlsx", ctype=XLSX, sha=None):
    files = {"file": (name, content, ctype)}
    if sha is None:
        return client.post(f"{P}/import/preview", files=files, headers=h)
    return client.post(f"{P}/import/commit", files=files, data={"file_sha256": sha}, headers=h)


def _profile(session, student):
    session.expire_all()
    return session.scalar(select(models.StudentPlacementProfile).where(
        models.StudentPlacementProfile.student_id == student.id))


def test_rejected_xlsx_never_overwrites_existing_records(client, world, on, session):
    on(world["a"])
    h = world["h_tpo"]
    put_profile(client, h, world["s1"], roll_number="CSE/1", graduation_year=2026, tenth_percent=70.0)
    before = _profile(session, world["s1"])
    snapshot = (before.roll_number, before.graduation_year, before.tenth_percent, before.updated_at)
    audits_before = session.scalars(select(models.AuditLog).where(
        models.AuditLog.action == "placement.academic_import")).all()
    for evil in (
        _xlsx_with_sheet(_sheet_xml([["email", "graduation_year"], ["s1@a.edu", "2030"]],
                                    f'<?xml version="1.0"?>{PAD_5K}<!DOCTYPE w>')),
        _xlsx_with_sheet(_sheet_xml([["email", "graduation_year"], ["s1@a.edu", "&a;"]],
                                    '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE w [<!ENTITY a "2030">]>'
                                    ).encode("utf-16")),
        _xlsx_with_sheet(b"<worksheet><sheetData>"),
    ):
        assert _upload(client, h, evil).status_code == 400
        sha = imp.sha256(evil)
        r = _upload(client, h, evil, sha=sha)  # commit straight away with a matching hash
        assert r.status_code == 400, r.text
    after = _profile(session, world["s1"])
    assert (after.roll_number, after.graduation_year, after.tenth_percent, after.updated_at) == snapshot
    assert len(session.scalars(select(models.AuditLog).where(
        models.AuditLog.action == "placement.academic_import")).all()) == len(audits_before)


def test_valid_xlsx_commit_and_blank_cells_keep_stored_values(client, world, on, session):
    on(world["a"])
    h = world["h_tpo"]
    put_profile(client, h, world["s1"], roll_number="CSE/1", graduation_year=2026, tenth_percent=70.0)
    data = _full_xlsx([
        ["email", "roll_number", "graduation_year", "tenth_percent", "twelfth_percent"],
        ["s1@a.edu", "", "", "", "88.5"],  # blanks: roll, year, tenth stay as stored
    ])
    pv = _upload(client, h, data)
    assert pv.status_code == 200, pv.text
    assert pv.json()["rows"][0]["changed_fields"] == ["twelfth_percent"]
    done = _upload(client, h, data, sha=pv.json()["file_sha256"])
    assert done.status_code == 200 and done.json()["updated"] == 1
    p = _profile(session, world["s1"])
    assert (p.roll_number, p.graduation_year, p.tenth_percent, p.twelfth_percent) == ("CSE/1", 2026, 70.0, 88.5)


def test_valid_csv_still_imports(client, world, on, session):
    on(world["a"])
    h = world["h_tpo"]
    data = "﻿email,graduation_year,active_backlogs\r\ns2@a.edu,2028,1\r\n".encode("utf-8")
    pv = _upload(client, h, data, "marks.csv", "text/csv")
    assert pv.status_code == 200 and pv.json()["to_create"] == 1
    done = _upload(client, h, data, "marks.csv", "text/csv", sha=pv.json()["file_sha256"])
    assert done.status_code == 200 and done.json()["created"] == 1
    p = _profile(session, world["s2"])
    assert (p.graduation_year, p.active_backlogs) == (2028, 1)
