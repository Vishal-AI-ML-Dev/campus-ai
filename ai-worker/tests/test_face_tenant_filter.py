"""Fail-closed tenant filter for the shared face vector store (Phase 5B).

What this proves: the worker's enroll/match/delete code tags, filters and
deletes by ``tenant_id`` and fails closed for untagged (legacy) points and for
requests without a tenant - evaluated by qdrant-client's IN-PROCESS local mode
(``QdrantClient(":memory:")``).

What this does NOT prove: behaviour of a real Qdrant server (filter/index
semantics on the server, payload-index creation on an existing collection,
performance). That still needs a test against an isolated Qdrant instance.

No model is loaded or downloaded: face detection/decoding are replaced by
synthetic one-hot "embeddings" (not face data). No network is used.

Run (no pytest in the worker image):  python tests/test_face_tenant_filter.py
Also collectable by pytest if it is installed.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from qdrant_client import QdrantClient  # noqa: E402
from qdrant_client.models import PointStruct  # noqa: E402

import face  # noqa: E402

TENANT_A, TENANT_B = 101, 202
STUDENT_A, STUDENT_B, STUDENT_LEGACY = 11, 22, 33


def _vec(i: int) -> list[float]:
    v = np.zeros(face.EMBEDDING_DIM, dtype=np.float32)
    v[i] = 1.0
    return v.tolist()


class _FakeFace(SimpleNamespace):
    pass


def _photo(*indices: int) -> list:
    """A synthetic 'photo': one fake face per basis-vector index."""
    return [
        _FakeFace(normed_embedding=np.array(_vec(i)), det_score=0.99,
                  bbox=(0, 0, 10, 10))
        for i in indices
    ]


def _setup(faces_in_next_image: list | None = None):
    """Fresh in-memory store + stubbed decode/detect. Returns (client, set_faces)."""
    client = QdrantClient(":memory:")
    face._ensure_collection(client)
    face._qdrant_client = client
    state = {"faces": faces_in_next_image or []}
    face._decode_image = lambda _b64: np.zeros((1, 1, 3), dtype=np.uint8)
    face._detect_faces = lambda _img: state["faces"]

    def set_faces(faces):
        state["faces"] = faces

    return client, set_faces


def _enroll(set_faces, student_id, tenant_id, idx):
    set_faces(_photo(idx))
    return face.enroll(face.EnrollRequest(
        student_id=student_id, tenant_id=tenant_id, image_base64="x"))


def _ids(client) -> set[int]:
    pts, _ = client.scroll(face.get_settings().FACE_COLLECTION, limit=100,
                           with_payload=True)
    return {int(p.id) for p in pts}


def test_enroll_stores_tenant_on_point():
    client, set_faces = _setup()
    _enroll(set_faces, STUDENT_A, TENANT_A, 0)
    pts, _ = client.scroll(face.get_settings().FACE_COLLECTION, with_payload=True)
    assert [p.payload for p in pts] == [{"student_id": STUDENT_A, "tenant_id": TENANT_A}]


def test_match_only_returns_requesting_tenants_students():
    client, set_faces = _setup()
    _enroll(set_faces, STUDENT_A, TENANT_A, 0)
    _enroll(set_faces, STUDENT_B, TENANT_B, 1)
    set_faces(_photo(0, 1))  # both students' faces are in the photo
    res_a = face.match(face.MatchRequest(image_base64="x", tenant_id=TENANT_A))
    assert [m.student_id for m in res_a.matched] == [STUDENT_A]
    assert res_a.unmatched_faces == 1  # B's face: no candidate in tenant A
    res_b = face.match(face.MatchRequest(image_base64="x", tenant_id=TENANT_B))
    assert [m.student_id for m in res_b.matched] == [STUDENT_B]  # own-tenant control


def test_untagged_legacy_point_never_matches_fail_closed():
    client, set_faces = _setup()
    client.upsert(face.get_settings().FACE_COLLECTION, points=[PointStruct(
        id=STUDENT_LEGACY, vector=_vec(2), payload={"student_id": STUDENT_LEGACY})])
    set_faces(_photo(2))
    for tenant in (TENANT_A, TENANT_B):
        res = face.match(face.MatchRequest(image_base64="x", tenant_id=tenant))
        assert res.matched == [] and res.unmatched_faces == 1


def test_delete_requires_matching_tenant():
    client, set_faces = _setup()
    _enroll(set_faces, STUDENT_A, TENANT_A, 0)
    _enroll(set_faces, STUDENT_B, TENANT_B, 1)
    face.delete_enrollment(STUDENT_B, tenant_id=TENANT_A)  # wrong tenant
    assert _ids(client) == {STUDENT_A, STUDENT_B}
    face.delete_enrollment(STUDENT_B, tenant_id=TENANT_B)  # own-tenant control
    assert _ids(client) == {STUDENT_A}


def test_post_filter_drops_hit_with_foreign_tenant_payload():
    """Defence in depth independent of the store: even if the vector store
    returned another tenant's point, match() must not report it."""
    _setup(_photo(0))
    foreign = SimpleNamespace(id=STUDENT_B, score=0.99,
                              payload={"student_id": STUDENT_B, "tenant_id": TENANT_B})

    class _LyingClient:
        def query_points(self, **kw):
            return SimpleNamespace(points=[foreign])

    face._qdrant_client = _LyingClient()
    res = face.match(face.MatchRequest(image_base64="x", tenant_id=TENANT_A))
    assert res.matched == [] and res.unmatched_faces == 1


def test_requests_without_tenant_are_rejected():
    _setup()
    api = FastAPI()
    api.include_router(face.router)
    c = TestClient(api)
    assert c.post("/face/enroll", json={"student_id": 1, "image_base64": "x"}).status_code == 422
    assert c.post("/face/match", json={"image_base64": "x"}).status_code == 422
    assert c.delete("/face/enroll/1").status_code == 422
    assert c.delete("/face/enroll/1?tenant_id=0").status_code == 422


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASSED {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAILED {name}: {type(exc).__name__}: {exc}")
    print(f"== {len(tests) - failed} passed, {failed} failed ==")
    sys.exit(1 if failed else 0)
