"""Release decision B-8: face recognition is FAIL-CLOSED (FACE_ENABLED defaults to false).

Pure settings / route-guard tests: no model is loaded, no Qdrant store is opened
(the guard answers 503 before any handler runs) and no .env file is read.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import face


@pytest.fixture
def no_face_env(monkeypatch):
    monkeypatch.delenv("FACE_ENABLED", raising=False)
    cached = face.get_settings  # a test may monkeypatch it; clear the real cache
    cached.cache_clear()
    yield
    cached.cache_clear()


def test_face_disabled_when_variable_missing(no_face_env):
    assert face.FaceSettings(_env_file=None).FACE_ENABLED is False


def test_field_default_is_false():
    assert face.FaceSettings.model_fields["FACE_ENABLED"].default is False


@pytest.mark.parametrize("value, expected", [("true", True), ("false", False), ("0", False)])
def test_explicit_value_is_honoured(no_face_env, monkeypatch, value, expected):
    monkeypatch.setenv("FACE_ENABLED", value)
    assert face.FaceSettings(_env_file=None).FACE_ENABLED is expected


def test_guard_refuses_every_face_route_by_default(no_face_env, monkeypatch):
    monkeypatch.setattr(face, "get_settings", lambda: face.FaceSettings(_env_file=None))
    app = FastAPI()
    app.include_router(face.router)
    with TestClient(app) as c:
        for method, path in (("get", "/face/health"), ("post", "/face/enroll"),
                             ("post", "/face/match"), ("post", "/face/embed")):
            resp = getattr(c, method)(path, json={}) if method == "post" else c.get(path)
            assert resp.status_code == 503, path
            assert resp.json()["detail"] == "Face attendance is disabled on this deployment."


def test_production_face_issue_only_when_enabled(no_face_env, monkeypatch):
    assert face.production_face_issue(face.FaceSettings(_env_file=None)) is None
    monkeypatch.setenv("FACE_ENABLED", "false")
    assert face.production_face_issue(face.FaceSettings(_env_file=None)) is None
    monkeypatch.setenv("FACE_ENABLED", "true")
    issue = face.production_face_issue(face.FaceSettings(_env_file=None))
    assert issue and "FACE_ENABLED=true is not allowed in production" in issue
