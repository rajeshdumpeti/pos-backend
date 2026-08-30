"""Establish the empty migration foundation.

Revision ID: 0001_migration_foundation
Revises: None
"""

from collections.abc import Sequence


revision: str = "0001_migration_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Advance to the empty baseline without creating schema objects."""
    pass


def downgrade() -> None:
    """Return from the empty baseline without dropping schema objects."""
    pass
