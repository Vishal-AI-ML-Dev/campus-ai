"""Show is_active for all users (app_user can read; users RLS is off).
    uv run python -m scripts.diag_active
"""
from sqlalchemy import text
from app.core.db import engine

with engine.connect() as c:
    rows = c.execute(text(
        'SELECT email, role, is_active FROM users ORDER BY is_active, email'
    )).fetchall()
    print(f"{'email':36} {'role':12} is_active")
    for r in rows:
        print(f"{str(r[0]):36} {str(r[1]):12} {r[2]!r}")
