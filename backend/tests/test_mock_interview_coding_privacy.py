"""Phase 8.1: coding privacy and safety guards.

  * Source code is student content: it never appears in logs, the AI usage
    ledger or audit rows, and no API response echoes it except the student's
    own draft in GET .../coding.
  * No response ever carries hidden-test, rubric-reference or reference
    material (spec 3.2, P8-D2).
  * No backend module that handles student code can execute anything
    (principle 1): a static guard forbids process/eval primitives.
  * No AI call happens in a coding round in Batch 1 (no ledger rows at all).
"""

# Fixtures are shared by importing them from the sessions module; ruff reads
# the fixture parameters as redefinitions of those imports.
# ruff: noqa: F811

import ast
import json
import logging
from pathlib import Path

from sqlalchemy import func, select

import app.models as models
from tests.test_mock_interview_coding_sessions import (  # noqa: F401 (fixtures)
    draft,
    flag,
    gate,
    no_engine,
    no_rate_limit,
    started_coding,
    stu,
    submit,
)
from tests.test_mock_interview_sessions import BASE

SENTINEL = "ZZ_SECRET_SOURCE_7f3a91"
CODE = f"# {SENTINEL}\nprint(input())\n"
APP = Path(__file__).resolve().parents[1] / "app"
CODE_HANDLING_MODULES = [
    APP / "services" / "mock_interview_coding.py",
    APP / "services" / "coding_bank.py",
    APP / "routers" / "mock_interview_coding.py",
    APP / "schemas" / "mock_interview_coding.py",
    APP / "models" / "mock_interview_coding.py",
]
HIDDEN_WORDS = ("hidden", "expected_complexity", "reference", "solution", "reviewer", "digest")


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def test_source_never_reaches_logs_ledger_or_audit(client, gate, session, caplog):
    _, h = gate
    caplog.set_level(logging.DEBUG)
    sid, _ = started_coding(client, h)
    assert draft(client, h, sid, source=CODE).status_code == 200
    sub = submit(client, h, sid, source=CODE + "\n")
    assert sub.status_code == 202
    assert SENTINEL not in sub.text
    assert SENTINEL not in client.get(f"{BASE}/sessions/{sid}/coding/attempts/1/submissions", headers=h).text
    assert SENTINEL not in client.get(f"{BASE}/sessions/{sid}/coding/submissions/{sub.json()['id']}", headers=h).text
    assert client.post(f"{BASE}/sessions/{sid}/finish", headers=h).status_code == 200
    assert SENTINEL not in caplog.text
    # No AI call in Batch 1 -> no ledger row; nothing audited with content.
    assert session.scalar(select(func.count(models.AIUsageEvent.id))) == 0
    audit = session.scalars(select(models.AuditLog)).all()
    assert all(SENTINEL not in json.dumps(a.summary or "") for a in audit)
    # The student's own draft is the only place their code is returned.
    assert SENTINEL in client.get(f"{BASE}/sessions/{sid}/coding", headers=h).text


def test_no_response_carries_hidden_or_reference_material(client, gate):
    _, h = gate
    sid, _ = started_coding(client, h)
    client.post(f"{BASE}/sessions/{sid}/coding/attempts/1/hints", headers=h)
    responses = [
        client.get(f"{BASE}/coding/catalog", headers=h).json(),
        client.get(f"{BASE}/sessions/{sid}/coding", headers=h).json(),
        client.get(f"{BASE}/sessions/{sid}", headers=h).json(),
        submit(client, h, sid).json(),
    ]
    for body in responses:
        for key in _keys(body):
            assert not any(w in key.lower() for w in HIDDEN_WORDS), key


def test_code_handling_modules_cannot_execute_anything():
    banned_calls = {"exec", "eval", "compile", "__import__"}
    banned_modules = {"subprocess", "pty", "multiprocessing", "ctypes", "pickle", "marshal"}
    for path in CODE_HANDLING_MODULES:
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {a.name.split(".")[0] for a in node.names} & banned_modules, path.name
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in banned_modules, path.name
            elif isinstance(node, ast.Call):
                fn = node.func
                # Bare builtins only: re.compile(...) is a regex, not code.
                if isinstance(fn, ast.Name):
                    assert fn.id not in banned_calls, (path.name, fn.id)
                if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "os":
                    assert not fn.attr.startswith(("system", "exec", "spawn", "popen", "fork")), path.name


def test_openapi_coding_bodies_carry_only_documented_fields(client):
    schemas = client.app.openapi()["components"]["schemas"]
    assert set(schemas["CodingDraftIn"]["properties"]) == {"language", "source", "draft_rev"}
    assert set(schemas["CodingSubmitIn"]["properties"]) == {"language", "source", "idempotency_key"}
    assert set(schemas["MockInterviewCodingConfigIn"]["properties"]) == {"languages", "topics", "problem_key"}
    # Batch 1 (B1-c): no run endpoint, no custom input, no runner fields.
    paths = client.app.openapi()["paths"]
    assert not [p for p in paths if p.endswith("/runs")]
