"""Enforce one request payload per refresh idempotency key.

Revision ID: 0004_auth_contract_v1
Revises: 0003_registration_flow
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0004_auth_contract_v1"
down_revision: str | None = "0003_registration_flow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONSTRAINT = "uq_refresh_rotation_retry_receipts__refresh_request_id"


def upgrade() -> None:
    op.create_unique_constraint(
        _CONSTRAINT,
        "refresh_rotation_retry_receipts",
        ["refresh_request_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        _CONSTRAINT,
        "refresh_rotation_retry_receipts",
        type_="unique",
    )
