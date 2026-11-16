"""Reset one user's password in the DB DATABASE_URL points at.
Users table has no RLS so app_user (UPDATE granted) can do this.

    uv run python -m scripts.reset_password <email> <new_password>

No example password is given on purpose - choose a fresh one. Note that the
password is passed on the command line, so it can end up in shell history;
switching this to an interactive prompt is a tracked follow-up (Phase 3 report).
"""
import sys
from sqlalchemy import text
from app.core.db import engine
from app.core.security import hash_password

if len(sys.argv) != 3:
    print("usage: uv run python -m scripts.reset_password <email> <new_password>")
    raise SystemExit(1)

email, new_pw = sys.argv[1], sys.argv[2]
hashed = hash_password(new_pw)
with engine.begin() as conn:
    res = conn.execute(
        text("UPDATE users SET hashed_password = :h WHERE email = :e"),
        {"h": hashed, "e": email},
    )
    print(f"rows updated for {email!r}: {res.rowcount}")
    if res.rowcount == 0:
        print("  (no user with that exact email - check list_users.py output)")
