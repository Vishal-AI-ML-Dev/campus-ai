"""Global exception handler tests - uniform error envelope + request id.

Builds a throwaway FastAPI app wired with the SAME request-id middleware and
exception handlers as the real app, so each error kind can be triggered on
demand without depending on a specific production route.
"""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.observability.errors import register_exception_handlers
from app.observability.observability import request_logging_middleware


def _make_client() -> TestClient:
    app = FastAPI()
    app.middleware("http")(request_logging_middleware)
    register_exception_handlers(app)

    class Body(BaseModel):
        name: str

    @app.get("/boom")
    def boom() -> dict:
        raise RuntimeError("kaboom - internal detail that must not leak")

    @app.get("/teapot")
    def teapot() -> dict:
        raise HTTPException(status_code=418, detail="i am a teapot")

    @app.post("/echo")
    def echo(body: Body) -> dict:
        return {"name": body.name}

    # raise_server_exceptions=False so the 500 flows through our handler instead
    # of being re-raised into the test.
    return TestClient(app, raise_server_exceptions=False)


def test_unhandled_exception_returns_uniform_500():
    client = _make_client()
    resp = client.get("/boom", headers={"X-Request-ID": "rid-500"})
    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == "Internal server error"  # never leaks internals
    assert "kaboom" not in resp.text
    assert body["request_id"] == "rid-500"
    assert body["error"] == {"type": "internal_error", "status": 500}
    assert resp.headers["X-Request-ID"] == "rid-500"


def test_http_exception_preserves_detail_and_adds_request_id():
    client = _make_client()
    resp = client.get("/teapot", headers={"X-Request-ID": "rid-418"})
    assert resp.status_code == 418
    body = resp.json()
    assert body["detail"] == "i am a teapot"  # back-compat: detail preserved
    assert body["request_id"] == "rid-418"
    assert body["error"]["type"] == "http_error"
    assert resp.headers["X-Request-ID"] == "rid-418"


def test_validation_error_returns_422_envelope():
    client = _make_client()
    resp = client.post("/echo", json={}, headers={"X-Request-ID": "rid-422"})
    assert resp.status_code == 422
    body = resp.json()
    assert isinstance(body["detail"], list)  # standard validation error list
    assert body["request_id"] == "rid-422"
    assert body["error"]["type"] == "validation_error"


def test_request_id_generated_when_client_omits_header():
    client = _make_client()
    resp = client.get("/teapot")
    assert resp.status_code == 418
    rid = resp.json()["request_id"]
    assert rid and rid != "-"
    assert resp.headers["X-Request-ID"] == rid
