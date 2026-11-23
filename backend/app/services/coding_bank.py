"""Coding problem bank: PUBLIC bundle loader and validator (Phase 8.1).

The bank lives in ``backend/app/coding_bank/`` (spec section 3.2):

    problems/<key>/problem.json   one problem's PUBLIC parts
    templates/<language>.txt      default starter code per language

Only public material may be here: statements, constraints, examples, visible
tests, authored hints and counts/digests of the hidden test set. Hidden inputs,
expected outputs, reference solutions and calibration data belong to the
runner-private test pack (decision P8-D2 / P8-S7) and must NEVER be added to
this repository. The validator enforces that with an ALLOW-LIST: any key that
is not explicitly public makes the whole bank invalid, so a stray
``hidden_tests`` or ``reference_solution`` field cannot slip through.

An invalid bank disables coding (logged once); the rest of the app is
unaffected. Problems are selectable only when ``status == "published"`` (a
human reviewed them), or ``"draft"`` when the development-only setting
``AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS`` is on - never in production
(decision B1-b).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger("campus_ai.mock_interview.coding")

BANK_DIR = Path(__file__).resolve().parent.parent / "coding_bank"

# Decision P8-D1. Order = display order.
LANGUAGES: dict[str, str] = {
    "python313": "Python 3.13",
    "cpp17": "C++17",
    "java21": "Java 21",
    "c11": "C11",
}
TOPICS: dict[str, str] = {
    "arrays": "Arrays",
    "strings": "Strings",
    "hashing": "Hashing",
    "two_pointers": "Two pointers",
    "stack_queue": "Stacks and queues",
    "sorting_searching": "Sorting and searching",
    "recursion": "Recursion",
    "trees": "Trees",
    "graphs": "Graphs",
    "greedy": "Greedy",
    "dp": "Dynamic programming",
    "math": "Math",
}
DIFFICULTIES = ("easy", "medium", "hard")
CHECKERS = ("exact", "tokens", "float", "unordered_lines")
MEMORY_PROFILES = {"standard": 256, "large": 512}  # MB (Java gets more, spec 4.4)
STATUSES = ("draft", "published", "disabled")
TEST_GROUPS = ("basic", "edge", "performance")

_TOP_KEYS = {
    "key", "version", "title", "difficulty", "topics", "statement", "input_format",
    "output_format", "constraints", "examples", "visible_tests", "checker", "limits",
    "starter", "hints", "editorial", "editorial_public", "expected_complexity",
    "hidden_test_set", "source", "status",
}
_REQUIRED = _TOP_KEYS - {"starter", "editorial", "editorial_public"}
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,78}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Restricted Markdown (spec 3.2): no HTML, images, links or URLs.
_FORBIDDEN_MARKUP = re.compile(r"</?[A-Za-z!]|!\[|\]\(|https?://|javascript:", re.I)

MAX_TEXT = 6_000
MAX_IO = 4_096
MAX_STARTER = 4_096
MAX_HINT = 500


class BankError(ValueError):
    """The public bundle is invalid (message names the problem and field)."""


@dataclass(frozen=True)
class Problem:
    key: str
    version: int
    title: str
    difficulty: str
    topics: tuple[str, ...]
    statement: str
    input_format: str
    output_format: str
    constraints: str
    examples: tuple[dict, ...]
    visible_tests: tuple[dict, ...]
    checker: str
    time_ms: int
    memory_profile: str
    starter: dict[str, str]
    hints: tuple[str, ...]
    expected_complexity: dict[str, str]
    hidden_groups: dict[str, int]
    status: str
    source: dict[str, str] = field(default_factory=dict)

    @property
    def memory_mb(self) -> int:
        return MEMORY_PROFILES[self.memory_profile]


@dataclass(frozen=True)
class Bank:
    problems: dict[str, Problem]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _text(where: str, value: Any, *, max_len: int = MAX_TEXT, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise BankError(f"{where}: must be a string")
    if not allow_empty and not value.strip():
        raise BankError(f"{where}: must not be empty")
    if len(value) > max_len:
        raise BankError(f"{where}: longer than {max_len} characters")
    if "\x00" in value:
        raise BankError(f"{where}: contains a NUL character")
    return value


def _markdown(where: str, value: Any) -> str:
    v = _text(where, value)
    if _FORBIDDEN_MARKUP.search(v):
        raise BankError(f"{where}: HTML, images, links and URLs are not allowed")
    return v


def _only_keys(where: str, obj: Any, allowed: set[str], required: set[str] | None = None) -> dict:
    if not isinstance(obj, dict):
        raise BankError(f"{where}: must be an object")
    extra = set(obj) - allowed
    if extra:
        # The allow-list is the hidden-data guard: unknown keys are refused.
        raise BankError(f"{where}: field(s) not allowed in the public bundle: {sorted(extra)}")
    missing = (required if required is not None else allowed) - set(obj)
    if missing:
        raise BankError(f"{where}: missing field(s) {sorted(missing)}")
    return obj


def _io_cases(where: str, value: Any, *, explanation: bool, lo: int, hi: int) -> tuple[dict, ...]:
    if not isinstance(value, list) or not lo <= len(value) <= hi:
        raise BankError(f"{where}: must be a list of {lo}-{hi} items")
    allowed = {"input", "output", "explanation"} if explanation else {"input", "output"}
    out = []
    for i, case in enumerate(value):
        c = _only_keys(f"{where}[{i}]", case, allowed, {"input", "output"})
        item = {
            "input": _text(f"{where}[{i}].input", c["input"], max_len=MAX_IO, allow_empty=True),
            "output": _text(f"{where}[{i}].output", c["output"], max_len=MAX_IO),
        }
        if explanation and "explanation" in c:
            item["explanation"] = _markdown(f"{where}[{i}].explanation", c["explanation"])
        out.append(item)
    return tuple(out)


def _templates(bank_dir: Path) -> dict[str, str]:
    out = {}
    for lang in LANGUAGES:
        path = bank_dir / "templates" / f"{lang}.txt"
        if path.is_file():
            out[lang] = _text(f"templates/{lang}.txt", path.read_text("utf-8"), max_len=MAX_STARTER)
    return out


def parse_problem(data: Any, *, dir_name: str, templates: dict[str, str]) -> Problem:
    """Validate one problem.json (public parts only) and return it."""
    w = f"problem {dir_name!r}"
    d = _only_keys(w, data, _TOP_KEYS, _REQUIRED)
    key = d["key"]
    if not isinstance(key, str) or not _KEY_RE.match(key) or key != dir_name:
        raise BankError(f"{w}: key must match the folder name and [a-z0-9-]{{3,79}}")
    version = d["version"]
    if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= 10_000:
        raise BankError(f"{w}: version must be a positive integer")
    if d["difficulty"] not in DIFFICULTIES:
        raise BankError(f"{w}: difficulty must be one of {DIFFICULTIES}")
    topics = d["topics"]
    if (
        not isinstance(topics, list)
        or not 1 <= len(topics) <= 4
        or any(t not in TOPICS for t in topics)
        or len(set(topics)) != len(topics)
    ):
        raise BankError(f"{w}: topics must be 1-4 distinct known topics")
    if d["checker"] not in CHECKERS:
        raise BankError(f"{w}: checker must be one of {CHECKERS}")
    limits = _only_keys(f"{w}.limits", d["limits"], {"time_ms", "memory_profile"})
    time_ms = limits["time_ms"]
    if not isinstance(time_ms, int) or isinstance(time_ms, bool) or not 100 <= time_ms <= 6_000:
        raise BankError(f"{w}.limits.time_ms must be 100-6000")
    if limits["memory_profile"] not in MEMORY_PROFILES:
        raise BankError(f"{w}.limits.memory_profile must be one of {sorted(MEMORY_PROFILES)}")

    starter_in = d.get("starter", {})
    if not isinstance(starter_in, dict) or set(starter_in) - set(LANGUAGES):
        raise BankError(f"{w}.starter: keys must be supported languages")
    starter = dict(templates)
    for lang, code in starter_in.items():
        starter[lang] = _text(f"{w}.starter.{lang}", code, max_len=MAX_STARTER)
    if set(starter) != set(LANGUAGES):
        raise BankError(f"{w}: no starter code for {sorted(set(LANGUAGES) - set(starter))}")

    hints = d["hints"]
    if not isinstance(hints, list) or len(hints) > 3:
        raise BankError(f"{w}.hints: at most 3")
    for i, h in enumerate(hints):
        _markdown(f"{w}.hints[{i}]", h)
        if len(h) > MAX_HINT or "```" in h:
            raise BankError(f"{w}.hints[{i}]: at most {MAX_HINT} characters and no code blocks")

    complexity = _only_keys(f"{w}.expected_complexity", d["expected_complexity"], {"time", "space"})
    for k in ("time", "space"):
        _text(f"{w}.expected_complexity.{k}", complexity[k], max_len=40)

    hidden = _only_keys(
        f"{w}.hidden_test_set", d["hidden_test_set"], {"id", "version", "digest_sha256", "groups"}
    )
    if not isinstance(hidden["id"], str) or not _KEY_RE.match(hidden["id"]):
        raise BankError(f"{w}.hidden_test_set.id is invalid")
    if not isinstance(hidden["version"], int) or isinstance(hidden["version"], bool) or hidden["version"] < 1:
        raise BankError(f"{w}.hidden_test_set.version must be a positive integer")
    digest = hidden["digest_sha256"]
    if digest is not None and (not isinstance(digest, str) or not _DIGEST_RE.match(digest)):
        raise BankError(f"{w}.hidden_test_set.digest_sha256 must be null or 64 hex characters")
    groups = _only_keys(f"{w}.hidden_test_set.groups", hidden["groups"], set(TEST_GROUPS))
    for g, n in groups.items():
        if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= 40:
            raise BankError(f"{w}.hidden_test_set.groups.{g} must be 0-40 (a count only)")
    if sum(groups.values()) > 40:
        raise BankError(f"{w}.hidden_test_set: at most 40 tests in total")

    status = d["status"]
    if status not in STATUSES:
        raise BankError(f"{w}: status must be one of {STATUSES}")
    source = _only_keys(
        f"{w}.source", d["source"], {"kind", "author", "reviewer", "reviewed_on"}, {"kind", "author"}
    )
    if source["kind"] != "original":
        raise BankError(f"{w}.source.kind must be 'original' (no copied problems)")
    _text(f"{w}.source.author", source["author"], max_len=100)
    if status == "published":
        # A human must review statements and reference solutions first (P8-D2).
        reviewer = source.get("reviewer")
        reviewed_on = source.get("reviewed_on")
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise BankError(f"{w}: a published problem needs source.reviewer")
        if not isinstance(reviewed_on, str) or not _DATE_RE.match(reviewed_on):
            raise BankError(f"{w}: a published problem needs source.reviewed_on (YYYY-MM-DD)")
        if digest is None:
            raise BankError(f"{w}: a published problem needs the hidden test set digest")
    if "editorial" in d:
        _markdown(f"{w}.editorial", d["editorial"])
    if "editorial_public" in d and not isinstance(d["editorial_public"], bool):
        raise BankError(f"{w}.editorial_public must be true or false")

    return Problem(
        key=key,
        version=version,
        title=_text(f"{w}.title", d["title"], max_len=120),
        difficulty=d["difficulty"],
        topics=tuple(topics),
        statement=_markdown(f"{w}.statement", d["statement"]),
        input_format=_markdown(f"{w}.input_format", d["input_format"]),
        output_format=_markdown(f"{w}.output_format", d["output_format"]),
        constraints=_markdown(f"{w}.constraints", d["constraints"]),
        examples=_io_cases(f"{w}.examples", d["examples"], explanation=True, lo=1, hi=3),
        visible_tests=_io_cases(f"{w}.visible_tests", d["visible_tests"], explanation=False, lo=1, hi=10),
        checker=d["checker"],
        time_ms=time_ms,
        memory_profile=limits["memory_profile"],
        starter=starter,
        hints=tuple(hints),
        expected_complexity={"time": complexity["time"], "space": complexity["space"]},
        hidden_groups={g: int(groups[g]) for g in TEST_GROUPS},
        status=status,
        source={k: str(v) for k, v in source.items()},
    )


def load_bank(bank_dir: Path | None = None) -> Bank:
    """Read and validate every problem. Any error -> an empty, invalid bank."""
    root = bank_dir or BANK_DIR
    try:
        templates = _templates(root)
        problems: dict[str, Problem] = {}
        problems_dir = root / "problems"
        if not problems_dir.is_dir():
            raise BankError("problems/ folder not found")
        for folder in sorted(p for p in problems_dir.iterdir() if p.is_dir()):
            files = {f.name for f in folder.iterdir()}
            if files != {"problem.json"}:
                # Nothing but the public problem.json may live next to it
                # (no tests/, no reference/ - those belong to the private pack).
                raise BankError(f"problem {folder.name!r}: only problem.json is allowed in its folder")
            data = json.loads((folder / "problem.json").read_text("utf-8"))
            problem = parse_problem(data, dir_name=folder.name, templates=templates)
            problems[problem.key] = problem
        return Bank(problems=problems)
    except (BankError, OSError, json.JSONDecodeError) as exc:
        logger.error("coding problem bank invalid; coding rounds are disabled: %s", exc)
        return Bank(problems={}, error=str(exc))


@lru_cache(maxsize=1)
def bank() -> Bank:
    """The validated bank, loaded once per process."""
    return load_bank()


_warned_production_drafts = False


def drafts_allowed() -> bool:
    """Decision B1-b: drafts only when explicitly enabled AND not in production."""
    global _warned_production_drafts
    if not settings.AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS:
        return False
    if settings.ENVIRONMENT == "production":
        if not _warned_production_drafts:
            _warned_production_drafts = True
            logger.warning(
                "AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS is ignored in production"
            )
        return False
    return True


def selectable(problem: Problem) -> bool:
    if problem.status == "published":
        return True
    return problem.status == "draft" and drafts_allowed()


def selectable_problems() -> list[Problem]:
    b = bank()
    return [p for p in b.problems.values() if selectable(p)] if b.ok else []


def get_problem(key: str) -> Problem | None:
    b = bank()
    return b.problems.get(key) if b.ok else None


def public_view(problem: Problem, hints_used: int) -> dict:
    """What a student may see. No hidden-test data, no expected complexity."""
    return {
        "key": problem.key,
        "version": problem.version,
        "title": problem.title,
        "difficulty": problem.difficulty,
        "topics": list(problem.topics),
        "statement": problem.statement,
        "input_format": problem.input_format,
        "output_format": problem.output_format,
        "constraints": problem.constraints,
        "examples": [dict(e) for e in problem.examples],
        "visible_tests": [dict(t) for t in problem.visible_tests],
        "starter": dict(problem.starter),
        "time_limit_ms": problem.time_ms,
        "memory_limit_mb": problem.memory_mb,
        "hints_revealed": list(problem.hints[: max(0, hints_used)]),
    }
