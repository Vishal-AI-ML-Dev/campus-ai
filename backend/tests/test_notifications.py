"""In-app notifications: fan-out, unread count, mark-read, ownership."""

import app.models as models


def _announce(client, admin_h, title="Hello", audience="all"):
    resp = client.post(
        "/announcements",
        headers=admin_h,
        json={"title": title, "body": "body text", "audience": audience},
    )
    assert resp.status_code == 201, resp.text
    return resp


def test_announcement_fans_out_notifications(client, make_user, token_header):
    make_user("admin@n.dev", role=models.UserRole.admin)
    make_user("stu@n.dev", role=models.UserRole.student)
    admin_h = token_header("admin@n.dev")
    stu_h = token_header("stu@n.dev")

    r = client.get("/notifications/unread-count", headers=stu_h)
    assert r.status_code == 200 and r.json()["unread"] == 0

    _announce(client, admin_h)

    assert client.get(
        "/notifications/unread-count", headers=stu_h
    ).json()["unread"] == 1

    items = client.get("/notifications", headers=stu_h).json()
    assert len(items) == 1
    note = items[0]
    assert note["type"] == "announcement"
    assert note["title"] == "Hello"
    assert note["is_read"] is False

    assert client.get(
        "/notifications/unread-count", headers=admin_h
    ).json()["unread"] == 0

    rr = client.post(f"/notifications/{note['id']}/read", headers=stu_h)
    assert rr.status_code == 200 and rr.json()["is_read"] is True
    assert client.get(
        "/notifications/unread-count", headers=stu_h
    ).json()["unread"] == 0


def test_role_targeted_announcement_only_notifies_that_role(
    client, make_user, token_header
):
    make_user("admin2@n.dev", role=models.UserRole.admin)
    make_user("teach2@n.dev", role=models.UserRole.teacher)
    make_user("stu2@n.dev", role=models.UserRole.student)
    admin_h = token_header("admin2@n.dev")
    teach_h = token_header("teach2@n.dev")
    stu_h = token_header("stu2@n.dev")

    _announce(client, admin_h, title="Teachers only", audience="teacher")

    assert client.get(
        "/notifications/unread-count", headers=teach_h
    ).json()["unread"] == 1
    assert client.get(
        "/notifications/unread-count", headers=stu_h
    ).json()["unread"] == 0


def test_mark_all_read(client, make_user, token_header):
    make_user("admin3@n.dev", role=models.UserRole.admin)
    make_user("stu3@n.dev", role=models.UserRole.student)
    admin_h = token_header("admin3@n.dev")
    stu_h = token_header("stu3@n.dev")
    for i in range(3):
        _announce(client, admin_h, title=f"A{i}")
    assert client.get(
        "/notifications/unread-count", headers=stu_h
    ).json()["unread"] == 3

    res = client.post("/notifications/read-all", headers=stu_h)
    assert res.status_code == 200 and res.json()["updated"] == 3
    assert client.get(
        "/notifications/unread-count", headers=stu_h
    ).json()["unread"] == 0

    assert client.get(
        "/notifications?unread_only=true", headers=stu_h
    ).json() == []


def test_cannot_mark_someone_elses_notification(client, make_user, token_header):
    make_user("admin4@n.dev", role=models.UserRole.admin)
    make_user("s1@n.dev", role=models.UserRole.student)
    make_user("s2@n.dev", role=models.UserRole.student)
    admin_h = token_header("admin4@n.dev")
    s1_h = token_header("s1@n.dev")
    s2_h = token_header("s2@n.dev")
    _announce(client, admin_h)
    note_id = client.get("/notifications", headers=s1_h).json()[0]["id"]
    assert client.post(
        f"/notifications/{note_id}/read", headers=s2_h
    ).status_code == 404
