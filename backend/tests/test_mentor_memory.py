"""Batch 10b - AI mentor durable memory (conversations + messages).

Runs against the throwaway SQLite DB (Postgres RLS is a no-op there), so this
suite proves the APP-LAYER behaviour: conversations persist, history is
replayed to the worker, and one student can never read/delete another's
threads. The real Postgres RLS guarantee is covered by
tests/test_rls_postgres.py.

The AI-worker HTTP call (ai_client.ask_mentor, imported into mentor) is
monkeypatched so the suite stays offline and deterministic.
"""

import app.routers.mentor as mentor


def _fake_ask_mentor(monkeypatch):
    """Deterministic, offline mentor; captures the history it was given."""
    calls = {}

    def _fake(profile, question, history):
        calls["history"] = history
        calls["question"] = question
        return {"answer": f"echo: {question}", "provider": "test"}

    monkeypatch.setattr(mentor, "ask_mentor", _fake)
    return calls


def test_chat_creates_and_persists_conversation(
    client, make_user, token_header, monkeypatch
):
    _fake_ask_mentor(monkeypatch)
    make_user("stu@x.edu")
    headers = token_header("stu@x.edu")

    resp = client.post(
        "/mentor/chat",
        json={"question": "Backend gap kya hai?"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer"] == "echo: Backend gap kya hai?"
    assert body["provider"] == "test"
    convo_id = body["conversation_id"]

    # It shows up in the list...
    lst = client.get("/mentor/conversations", headers=headers)
    assert lst.status_code == 200
    convos = lst.json()
    assert len(convos) == 1
    assert convos[0]["id"] == convo_id
    assert convos[0]["title"] == "Backend gap kya hai?"

    # ...with both turns persisted, oldest first.
    detail = client.get(f"/mentor/conversations/{convo_id}", headers=headers)
    assert detail.status_code == 200
    msgs = detail.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "Backend gap kya hai?"
    assert msgs[1]["content"] == "echo: Backend gap kya hai?"
    assert msgs[1]["provider"] == "test"


def test_history_is_replayed_on_followup(
    client, make_user, token_header, monkeypatch
):
    calls = _fake_ask_mentor(monkeypatch)
    make_user("stu2@x.edu")
    headers = token_header("stu2@x.edu")

    first = client.post(
        "/mentor/chat", json={"question": "Q1"}, headers=headers
    ).json()
    convo_id = first["conversation_id"]
    # First call saw no prior history.
    assert calls["history"] == []

    client.post(
        "/mentor/chat",
        json={"question": "Q2", "conversation_id": convo_id},
        headers=headers,
    )
    # Second call replayed Q1 + its answer as context.
    assert calls["history"] == [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "echo: Q1"},
    ]

    detail = client.get(
        f"/mentor/conversations/{convo_id}", headers=headers
    ).json()
    assert len(detail["messages"]) == 4


def test_cross_user_isolation(client, make_user, token_header, monkeypatch):
    _fake_ask_mentor(monkeypatch)
    make_user("a@x.edu")
    make_user("b@x.edu")
    ha = token_header("a@x.edu")
    hb = token_header("b@x.edu")

    convo_id = client.post(
        "/mentor/chat", json={"question": "secret"}, headers=ha
    ).json()["conversation_id"]

    # B cannot list A's conversation...
    assert client.get("/mentor/conversations", headers=hb).json() == []
    # ...nor read it...
    assert (
        client.get(f"/mentor/conversations/{convo_id}", headers=hb).status_code
        == 404
    )
    # ...nor delete it.
    assert (
        client.delete(
            f"/mentor/conversations/{convo_id}", headers=hb
        ).status_code
        == 404
    )
    # A can delete their own; it then disappears from their list.
    assert (
        client.delete(
            f"/mentor/conversations/{convo_id}", headers=ha
        ).status_code
        == 204
    )
    assert client.get("/mentor/conversations", headers=ha).json() == []
