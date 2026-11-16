"""Fail-closed tenant filter against a REAL Qdrant server (Phase 5N).

Complements test_face_tenant_filter.py (in-process qdrant-client) by running
the same face-module code paths against an actual Qdrant server: collection
creation, the tenant_id PAYLOAD INDEX (a no-op in local mode), filtered
search, filtered deletion and the request contract.

Only image decoding and face DETECTION are stubbed (they require the
InsightFace model, which is never loaded or downloaded here); vectors are
synthetic one-hot 512-d vectors, not face data.

Gated: runs only when QDRANT_TEST_URL is set AND the worker's configured
QDRANT_URL equals it (so it can never touch a non-test store). Uses a unique
collection name, deleted at the end.

    QDRANT_TEST_URL=http://<test-qdrant>:6333 QDRANT_URL=$QDRANT_TEST_URL \
    FACE_COLLECTION=zz_test_faces_<run> python tests/test_face_qdrant_server.py
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_URL = os.environ.get("QDRANT_TEST_URL", "")

import numpy as np  # noqa: E402

import face  # noqa: E402

TENANT_A, TENANT_B = 101, 202
STUDENT_A, STUDENT_B, STUDENT_LEGACY = 11, 22, 33


def _guard() -> None:
    cfg = face.get_settings()
    if not TEST_URL:
        raise SystemExit("SKIP: QDRANT_TEST_URL not set")
    if cfg.QDRANT_URL.strip() != TEST_URL or not cfg.FACE_COLLECTION.startswith("zz_test_"):
        raise SystemExit("REFUSED: worker QDRANT_URL/FACE_COLLECTION are not the test values")


def _vec(i: int) -> list[float]:
    v = np.zeros(face.EMBEDDING_DIM, dtype=np.float32)
    v[i] = 1.0
    return v.tolist()


def _photo(*indices: int) -> list:
    return [SimpleNamespace(normed_embedding=np.array(_vec(i)), det_score=0.99,
                            bbox=(0, 0, 10, 10)) for i in indices]


_state = {"faces": []}


def _client():
    """Fresh REAL server client through the module's own factory."""
    face._qdrant_client = None
    face._decode_image = lambda _b64: np.zeros((1, 1, 3), dtype=np.uint8)
    face._detect_faces = lambda _img: _state["faces"]
    return face._get_qdrant()


def _reset_collection(client) -> None:
    name = face.get_settings().FACE_COLLECTION
    if client.collection_exists(name):
        client.delete_collection(name)
    face._qdrant_client = None
    _client()


def _enroll(student_id, tenant_id, idx):
    _state["faces"] = _photo(idx)
    return face.enroll(face.EnrollRequest(student_id=student_id, tenant_id=tenant_id,
                                          image_base64="x"))


def _ids(client) -> set[int]:
    pts, _ = client.scroll(face.get_settings().FACE_COLLECTION, limit=100)
    return {int(p.id) for p in pts}


def test_server_collection_has_tenant_payload_index():
    client = _client()
    _reset_collection(client)
    face._ensure_collection(client)  # second call on an existing collection: no error
    info = client.get_collection(face.get_settings().FACE_COLLECTION)
    schema = info.payload_schema.get("tenant_id")
    assert schema is not None, "tenant_id payload index missing on server"
    assert str(getattr(schema, "data_type", "")).lower().endswith("integer"), schema


def test_server_enroll_stores_tenant_and_match_is_tenant_scoped():
    client = _client()
    _reset_collection(client)
    _enroll(STUDENT_A, TENANT_A, 0)
    _enroll(STUDENT_B, TENANT_B, 1)
    pts, _ = client.scroll(face.get_settings().FACE_COLLECTION, with_payload=True)
    assert {(p.payload["student_id"], p.payload["tenant_id"]) for p in pts} == {
        (STUDENT_A, TENANT_A), (STUDENT_B, TENANT_B)}
    _state["faces"] = _photo(0, 1)  # both students' faces in one photo
    res_a = face.match(face.MatchRequest(image_base64="x", tenant_id=TENANT_A))
    assert [m.student_id for m in res_a.matched] == [STUDENT_A]  # same-tenant success
    assert res_a.unmatched_faces == 1                              # B's face denied
    res_b = face.match(face.MatchRequest(image_base64="x", tenant_id=TENANT_B))
    assert [m.student_id for m in res_b.matched] == [STUDENT_B]
    res_x = face.match(face.MatchRequest(image_base64="x", tenant_id=999))
    assert res_x.matched == [] and res_x.unmatched_faces == 2      # unknown tenant


def test_server_untagged_legacy_point_never_matches():
    from qdrant_client.models import PointStruct

    client = _client()
    _reset_collection(client)
    client.upsert(face.get_settings().FACE_COLLECTION, points=[PointStruct(
        id=STUDENT_LEGACY, vector=_vec(2), payload={"student_id": STUDENT_LEGACY})])
    _state["faces"] = _photo(2)
    for tenant in (TENANT_A, TENANT_B):
        res = face.match(face.MatchRequest(image_base64="x", tenant_id=tenant))
        assert res.matched == [] and res.unmatched_faces == 1


def test_server_delete_requires_matching_tenant():
    client = _client()
    _reset_collection(client)
    _enroll(STUDENT_A, TENANT_A, 0)
    _enroll(STUDENT_B, TENANT_B, 1)
    face.delete_enrollment(STUDENT_B, tenant_id=TENANT_A)   # cross-tenant: no effect
    assert _ids(client) == {STUDENT_A, STUDENT_B}
    face.delete_enrollment(STUDENT_B, tenant_id=TENANT_B)   # same tenant: deleted
    assert _ids(client) == {STUDENT_A}


def test_server_routes_reject_missing_or_invalid_tenant():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    _client()
    api = FastAPI()
    api.include_router(face.router)
    c = TestClient(api)
    assert c.post("/face/enroll", json={"student_id": 1, "image_base64": "x"}).status_code == 422
    assert c.post("/face/match", json={"image_base64": "x"}).status_code == 422
    assert c.delete("/face/enroll/1").status_code == 422
    assert c.delete("/face/enroll/1?tenant_id=0").status_code == 422


def _cleanup():
    client = _client()
    name = face.get_settings().FACE_COLLECTION
    if client.collection_exists(name):
        client.delete_collection(name)
    return not client.collection_exists(name)


if __name__ == "__main__":
    _guard()
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASSED {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAILED {name}: {type(exc).__name__}: {exc}")
    print(f"cleanup: test collection removed = {_cleanup()}")
    print(f"== {len(tests) - failed} passed, {failed} failed ==")
    sys.exit(1 if failed else 0)
