"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-08-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use metadata create_all via connection for full schema
    from phera.db.models import Base

    bind = op.get_bind()
    Base.metadata.create_all(bind)


def downgrade() -> None:
    from phera.db.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(bind)
