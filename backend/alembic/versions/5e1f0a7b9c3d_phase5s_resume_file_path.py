"""phase5s: resume versions may reference one uploaded resume file

Revision ID: 5e1f0a7b9c3d
Revises: e7f8a9b0c1d2
Create Date: 2026-11-16

Why: resume files ("resume" upload kind) had no owning record, so a signed
download could not be authorized against anything and was denied by default
(Phase 5R). This adds the association: resumes.file_path holds the storage
object path of a file the version's OWNER uploaded; download authorization
reads ownership from this row, never from the client.

Schema (additive only)
  * resumes.file_path VARCHAR(300) NULL.
  * Partial UNIQUE index ix_resumes_file_path_unique ON (file_path) WHERE
    file_path IS NOT NULL - one stored file belongs to at most one version.

Existing rows: untouched. file_path starts NULL everywhere - ownership of any
already-uploaded file is NOT guessed and nothing is deleted. RLS on resumes
is unchanged (the existing tenant_isolation policy covers the new column).

Downgrade drops the index and column (would discard attachments; operator
decision only).
"""

from alembic import op
import sqlalchemy as sa

revision = "5e1f0a7b9c3d"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resumes", sa.Column("file_path", sa.String(length=300), nullable=True))
    op.create_index(
        "ix_resumes_file_path_unique",
        "resumes",
        ["file_path"],
        unique=True,
        postgresql_where=sa.text("file_path IS NOT NULL"),
        sqlite_where=sa.text("file_path IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_resumes_file_path_unique", table_name="resumes")
    op.drop_column("resumes", "file_path")
