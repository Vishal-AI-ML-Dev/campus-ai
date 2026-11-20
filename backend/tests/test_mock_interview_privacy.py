"""Phase 6.6: the mock-interview API cannot receive media (OpenAPI contract check).

Complements ``test_openapi_has_no_media_route`` (paths) in test_mock_interview_video.py:
every mock-interview operation's request body is walked (``$ref`` resolved) and
must be JSON only, with no media-like field anywhere. The approved design (D2)
keeps the camera in the browser; only reviewed answer text is accepted.
"""

import re

MEDIA = re.compile(r"video|camera|frame|image|photo|media|audio|blob|file|upload|stream|snapshot|device", re.I)


def _resolve(spec, schema):
    while isinstance(schema, dict) and "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        schema = spec["components"]["schemas"][name]
    return schema


def _field_names(spec, schema, seen=None):
    seen = set() if seen is None else seen
    schema = _resolve(spec, schema)
    if not isinstance(schema, dict) or id(schema) in seen:
        return
    seen.add(id(schema))
    for name, sub in (schema.get("properties") or {}).items():
        yield name
        yield from _field_names(spec, sub, seen)
    for key in ("items", "additionalProperties"):
        if isinstance(schema.get(key), dict):
            yield from _field_names(spec, schema[key], seen)
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in schema.get(key) or []:
            yield from _field_names(spec, sub, seen)


def _operations(spec):
    for path, item in spec["paths"].items():
        if "mock-interview" not in path:
            continue
        for method, op in item.items():
            if isinstance(op, dict):
                yield path, method, op


def test_mock_interview_request_bodies_are_json_without_media_fields(client):
    spec = client.app.openapi()
    ops = list(_operations(spec))
    assert len(ops) >= 15  # every route, at /v1 and unversioned
    bodies = 0
    for path, method, op in ops:
        body = op.get("requestBody")
        if not body:
            continue
        bodies += 1
        content = body.get("content", {})
        assert set(content) == {"application/json"}, (method, path, set(content))
        names = set(_field_names(spec, content["application/json"]["schema"]))
        assert names, (method, path)
        leaked = sorted(n for n in names if MEDIA.search(n))
        assert not leaked, (method, path, leaked)
    assert bodies >= 4  # create session + answer, at /v1 and unversioned


def test_answer_and_create_bodies_have_exactly_the_documented_fields(client):
    spec = client.app.openapi()
    schemas = spec["components"]["schemas"]
    answer = set(_resolve(spec, schemas["MockInterviewAnswerIn"])["properties"])
    assert answer == {"turn_seq", "answer", "source"}
    create = set(_resolve(spec, schemas["MockInterviewSessionCreate"])["properties"])
    assert create == {
        "interview_type", "difficulty", "duration_minutes", "target_role", "company_name",
        "resume_id", "job_description", "language", "mode",
    }
