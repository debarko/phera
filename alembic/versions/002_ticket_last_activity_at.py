"""add tickets.last_activity_at for inbox sort

Revision ID: 002
Revises: 001
Create Date: 2026-08-16
"""

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE tickets t
        SET last_activity_at = COALESCE(
            (SELECT MAX(m.occurred_at) FROM messages m WHERE m.ticket_id = t.id),
            t.updated_at,
            t.created_at
        )
        """
    )
    op.alter_column("tickets", "last_activity_at", nullable=False)
    op.create_index(
        "ix_tickets_workspace_last_activity",
        "tickets",
        ["workspace_id", "last_activity_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tickets_workspace_last_activity", table_name="tickets")
    op.drop_column("tickets", "last_activity_at")
