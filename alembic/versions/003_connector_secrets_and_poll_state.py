"""add connectors.secrets_encrypted and email_poll_state for DB-backed email adapters

Revision ID: 003
Revises: 002
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connectors",
        sa.Column("secrets_encrypted", sa.Text(), nullable=True),
    )
    op.create_table(
        "email_poll_state",
        sa.Column("channel_account_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("last_uid", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="idle"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["channel_account_id"], ["channel_accounts.id"]),
    )


def downgrade() -> None:
    op.drop_table("email_poll_state")
    op.drop_column("connectors", "secrets_encrypted")
