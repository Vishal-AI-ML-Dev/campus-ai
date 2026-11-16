"""Operational / diagnostic scripts for the Campus AI backend.

Run these from the backend/ directory as a module, so the app's flat imports
(``from db import ...``, ``from config import settings`` etc.) resolve against
the backend root, e.g.::

    uv run python -m scripts.seed_demo
    uv run python -m scripts.rls_verify_all

See scripts/README.md for the full catalogue.
"""
