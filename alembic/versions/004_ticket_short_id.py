"""add tickets.short_id — compact date+random display id, replaces raw UUID in subjects

Revision ID: 004
Revises: 003
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("short_id", sa.String(16), nullable=True))
    # Backfill existing rows with a collision-proof value (row_number, not random) —
    # new tickets going forward use modules.tickets.short_id.generate_ticket_short_id().
    op.execute(
        """
        WITH numbered AS (
            SELECT id, row_number() OVER (ORDER BY created_at, id) AS rn
            FROM tickets
            WHERE short_id IS NULL
        )
        UPDATE tickets t
        SET short_id = to_char(t.created_at, 'YYMMDD') || '-' || lpad(numbered.rn::text, 4, '0')
        FROM numbered
        WHERE t.id = numbered.id
        """
    )
    op.create_unique_constraint("uq_tickets_short_id", "tickets", ["short_id"])


def downgrade() -> None:
    op.drop_constraint("uq_tickets_short_id", "tickets", type_="unique")
    op.drop_column("tickets", "short_id")
