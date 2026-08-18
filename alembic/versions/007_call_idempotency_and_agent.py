"""call routing uniqueness + stored agent; telephony workspace index

Revision ID: 007
Revises: 006
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calls",
        sa.Column("agent_user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=True),
    )
    op.execute(
        """
        DELETE FROM calls a
        USING calls b
        WHERE a.provider_call_id IS NOT NULL
          AND a.workspace_id = b.workspace_id
          AND a.provider_call_id = b.provider_call_id
          AND a.id > b.id
        """
    )
    op.create_unique_constraint(
        "uq_calls_workspace_provider_call_id",
        "calls",
        ["workspace_id", "provider_call_id"],
    )
    op.create_index(
        "ix_agent_telephony_identities_workspace_id",
        "agent_telephony_identities",
        ["workspace_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_telephony_identities_workspace_id",
        table_name="agent_telephony_identities",
    )
    op.drop_constraint("uq_calls_workspace_provider_call_id", "calls", type_="unique")
    op.drop_column("calls", "agent_user_id")
