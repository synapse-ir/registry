"""Add pypi_hash and adapter_signature columns (§9 G-S08 Phase 1)

Revision ID: 0001
Revises:
Create Date: 2026-05-06

pypi_hash     — SHA-256 of the PyPI sdist tarball, verified at registration.
adapter_signature — reserved nullable column for Phase 2 adapter signing;
                    populated when the signing flow ships without requiring
                    another migration.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("manifests", sa.Column("pypi_hash", sa.String(64), nullable=True))
    op.add_column("manifests", sa.Column("adapter_signature", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("manifests", "adapter_signature")
    op.drop_column("manifests", "pypi_hash")
