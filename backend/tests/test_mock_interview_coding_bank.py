"""Phase 8.1: the PUBLIC coding problem bank and its hidden-data guard.

Decision P8-D2: this (possibly public) repository may hold only public
problem material. Hidden inputs, expected outputs, reference solutions and
calibration data live in the runner-private test pack. The validator is an
allow-list, so any extra field or file makes the whole bank invalid.
"""

import copy
import json
from pathlib import Path

import pytest

from app.services import coding_bank
from app.services.coding_bank import BANK_DIR, BankError, load_bank, parse_problem

REAL = json.loads((BANK_DIR / "problems" / "count-islands" / "problem.json").read_text("utf-8"))


def _templates():
    return coding_bank._templates(BANK_DIR)


def _parse(doc):
    return parse_problem(doc, dir_name=doc["key"], templates=_templates())


def _write_bank(root: Path, docs: dict[str, dict]):
    (root / "templates").mkdir(parents=True)
    for lang in coding_bank.LANGUAGES:
        (root / "templates" / f"{lang}.txt").write_text("// starter\n", "utf-8")
    for key, doc in docs.items():
        folder = root / "problems" / key
        folder.mkdir(parents=True)
        (folder / "problem.json").write_text(json.dumps(doc), "utf-8")


# --- The shipped bank ---------------------------------------------------------------


def test_shipped_bank_is_valid_and_complete():
    bank = load_bank()
    assert bank.ok, bank.error
    problems = list(bank.problems.values())
    assert len(problems) == 24
    for difficulty in ("easy", "medium", "hard"):
        assert sum(p.difficulty == difficulty for p in problems) == 8
    # Pending human review (P8-D2): nothing is published in Batch 1.
    assert {p.status for p in problems} == {"draft"}
    for p in problems:
        assert set(p.starter) == set(coding_bank.LANGUAGES)
        assert p.visible_tests and p.examples
        assert p.source["kind"] == "original"
        assert p.hidden_groups["basic"] >= 1  # counts only; the tests are not here


def test_repository_holds_no_hidden_test_material():
    """Only problem.json files and language templates may exist in the bank."""
    files = sorted(p.relative_to(BANK_DIR).as_posix() for p in BANK_DIR.rglob("*") if p.is_file())
    assert files, "bank folder is empty"
    for f in files:
        assert (
            (f.startswith("problems/") and f.endswith("/problem.json") and f.count("/") == 2)
            or (f.startswith("templates/") and f.endswith(".txt"))
        ), f
    banned = ("hidden_tests", "expected_output", "reference", "solution\"", "\"tests\"")
    for path in BANK_DIR.rglob("problem.json"):
        text = path.read_text("utf-8")
        for word in banned:
            assert word not in text, (path.name, word)


# --- Allow-list guard ---------------------------------------------------------------


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(hidden_tests=[{"input": "1", "output": "2"}]),
    lambda d: d.update(reference_solution="print(1)"),
    lambda d: d.update(expected_outputs=["1"]),
    lambda d: d["visible_tests"][0].update(expected="2"),
    lambda d: d["examples"][0].update(hidden=True),
    lambda d: d["hidden_test_set"].update(tests=[{"input": "1"}]),
    lambda d: d["hidden_test_set"]["groups"].update(basic=[{"input": "1"}]),
    lambda d: d["hidden_test_set"]["groups"].update(secret=3),
    lambda d: d["hidden_test_set"].update(digest_sha256="not-a-digest"),
    lambda d: d["limits"].update(checker_code="def check(): pass"),
    lambda d: d["source"].update(kind="leetcode"),
    lambda d: d.update(status="published"),  # no reviewer, date or digest
    lambda d: d.update(statement="See https://example.com"),
    lambda d: d.update(statement="Click [here](x)"),
    lambda d: d.update(statement="<script>alert(1)</script>"),
    lambda d: d.update(topics=["blockchain"]),
    lambda d: d.update(key="Bad Key"),
    lambda d: d.update(hints=["one", "two", "three", "four"]),
    lambda d: d.update(hints=["```python\nprint(1)\n```"]),
    lambda d: d.pop("visible_tests"),
    lambda d: d.update(starter={"ruby": "puts 1"}),
])
def test_unknown_or_unsafe_content_is_rejected(mutate):
    doc = copy.deepcopy(REAL)
    mutate(doc)
    with pytest.raises(BankError):
        parse_problem(doc, dir_name=REAL["key"], templates=_templates())


def test_published_problem_needs_review_record_and_digest():
    doc = copy.deepcopy(REAL)
    doc["status"] = "published"
    doc["source"].update(reviewer="Prof. Reviewer", reviewed_on="2026-11-30")
    with pytest.raises(BankError):
        _parse(doc)  # still no digest of the private hidden test set
    doc["hidden_test_set"]["digest_sha256"] = "a" * 64
    assert _parse(doc).status == "published"


def _reviewed(doc):
    doc["status"] = "published"
    doc["source"].update(reviewer="Synthetic Reviewer", reviewed_on="2026-11-30")
    doc["hidden_test_set"]["digest_sha256"] = "b" * 64
    return doc


@pytest.mark.parametrize("group", ["basic", "edge", "performance"])
def test_u2_published_problem_needs_every_hidden_group(group):
    """U2 (P8-D7): >= 1 basic, edge and performance hidden test; weights are never redistributed."""
    doc = _reviewed(copy.deepcopy(REAL))
    doc["hidden_test_set"]["groups"][group] = 0
    with pytest.raises(BankError, match="each group"):
        _parse(doc)


def test_u2_published_problem_with_one_test_per_group_is_accepted():
    doc = _reviewed(copy.deepcopy(REAL))
    doc["hidden_test_set"]["groups"] = {"basic": 1, "edge": 1, "performance": 1}
    assert _parse(doc).hidden_groups == {"basic": 1, "edge": 1, "performance": 1}


def test_u2_does_not_apply_to_drafts():
    """Drafts are never served, so an incomplete draft stays loadable while its pack is being written."""
    doc = copy.deepcopy(REAL)
    doc["hidden_test_set"]["groups"]["performance"] = 0
    assert _parse(doc).status == "draft"


def test_u2_published_problem_without_a_group_invalidates_the_whole_bank(tmp_path):
    bad = _reviewed(copy.deepcopy(REAL))
    bad["hidden_test_set"]["groups"]["edge"] = 0
    _write_bank(tmp_path, {REAL["key"]: bad})
    bank = load_bank(tmp_path)
    assert not bank.ok and bank.problems == {}
    assert "each group" in bank.error


def test_shipped_bank_has_no_digest_or_reviewer_before_human_review():
    """Batch 3: hidden-test digests reach the public bundle only after human review and publication approval."""
    for p in load_bank().problems.values():
        assert p.status == "draft"
        assert "reviewer" not in p.source and "reviewed_on" not in p.source
    for path in BANK_DIR.rglob("problem.json"):
        doc = json.loads(path.read_text("utf-8"))
        assert doc["hidden_test_set"]["digest_sha256"] is None, path.parent.name
        groups = doc["hidden_test_set"]["groups"]
        assert all(groups[g] >= 1 for g in ("basic", "edge", "performance")), path.parent.name


def test_comparison_signs_in_text_are_allowed():
    doc = copy.deepcopy(REAL)
    doc["constraints"] = "1 ≤ n ≤ 10 and a < b for every pair; x > 0."
    assert _parse(doc).constraints.startswith("1 ≤ n")


def test_any_extra_file_next_to_problem_json_invalidates_the_bank(tmp_path):
    _write_bank(tmp_path, {REAL["key"]: REAL})
    assert load_bank(tmp_path).ok
    (tmp_path / "problems" / REAL["key"] / "hidden.json").write_text("{}", "utf-8")
    bank = load_bank(tmp_path)
    assert not bank.ok and bank.problems == {}


def test_one_bad_problem_invalidates_the_whole_bank(tmp_path):
    bad = copy.deepcopy(REAL)
    bad["key"] = "broken-problem"
    bad["reference_solution"] = "x"
    _write_bank(tmp_path, {REAL["key"]: REAL, "broken-problem": bad})
    bank = load_bank(tmp_path)
    assert not bank.ok and "broken-problem" in bank.error


def test_missing_language_template_without_starter_is_rejected(tmp_path):
    _write_bank(tmp_path, {REAL["key"]: REAL})
    (tmp_path / "templates" / "c11.txt").unlink()
    assert not load_bank(tmp_path).ok


# --- Public view -----------------------------------------------------------------------


def test_public_view_has_no_hidden_or_rubric_material():
    p = _parse(copy.deepcopy(REAL))
    view = coding_bank.public_view(p, hints_used=1)
    assert "hidden_test_set" not in view and "expected_complexity" not in view
    assert "source" not in view and "status" not in view
    assert view["hints_revealed"] == [p.hints[0]]
    assert coding_bank.public_view(p, hints_used=0)["hints_revealed"] == []
