"""Alembic migration environment for Campus AI.

Customised so that:
  * the database URL comes from our app settings (.env) - NOT alembic.ini,
    so secrets never sit in a committed file;
  * Alembic sees our `Base.metadata` and every model, enabling --autogenerate.

This file REPLACES the default `alembic/env.py` created by `alembic init`.
It is run from the `backend/` folder, so `config`, `db`, and `models` import
cleanly.
"""

import os
import secrets
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Phase 7: the app Settings require SECRET_KEY (no code default since Phase 6),
# but migrations never sign or verify tokens. Give the alembic PROCESS a random
# throwaway key when none is set, so a migration job does not need (and should
# not be handed) the production JWT secret. Never affects the API process.
os.environ.setdefault("SECRET_KEY", "alembic-unused-" + secrets.token_hex(32))

# --- App imports -----------------------------------------------------------
from app.core.config import settings  # noqa: E402
from app.core.db import Base  # noqa: E402
import app.models as models  # noqa: E402,F401  -> importing registers all tables on Base.metadata

# Alembic Config object (gives access to values in alembic.ini).
config = context.config

# Inject the real DB URL from settings. Escape '%' so ConfigParser does not
# treat it as interpolation (passwords can contain '%').
# Migrations run DDL, so they need the OWNER role. Use MIGRATION_DATABASE_URL
# (Supabase `postgres`) when set; otherwise fall back to DATABASE_URL (the
# app runtime role, e.g. for local SQLite tests).
_migration_url = settings.MIGRATION_DATABASE_URL or settings.DATABASE_URL
config.set_main_option(
    "sqlalchemy.url", _migration_url.replace("%", "%%")
)

# Configure Python logging from the ini file, if present.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for 'autogenerate' support.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (emits SQL)."""
    context.configure(
        url=_migration_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
