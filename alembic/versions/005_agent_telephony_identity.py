"""add agent_telephony_identities — per-agent SIP credentials for the WebRTC softphone

Revision ID: 005
Revises: 004
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_telephony_identities",
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False, server_default="exotel"),
        sa.Column("sip_user", sa.String(255), nullable=False),
        sa.Column("sip_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("sip_domain", sa.String(255), nullable=False),
        sa.Column("sip_port", sa.Integer(), nullable=False, server_default="443"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("agent_telephony_identities")
