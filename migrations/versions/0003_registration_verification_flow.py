"""Add registration and verification flow lifecycle metadata.

Revision ID: 0003_registration_flow
Revises: 0002_auth_persistence
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_registration_flow"
down_revision: str | None = "0002_auth_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "email_verification_challenges"
_SUPERSEDED_FK = (
    "fk_email_verification_challenges__superseded_by_id__c933f65beb"
)


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("flow_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "digest_key_id",
            sa.String(length=64),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "digest_key_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "completion_action",
            sa.String(length=32),
            nullable=False,
            server_default="activate_account",
        ),
    )
    op.execute(
        sa.text(
            "UPDATE email_verification_challenges "
            "SET flow_expires_at = expires_at "
            "WHERE flow_expires_at IS NULL"
        )
    )
    op.alter_column(_TABLE, "flow_expires_at", nullable=False)
    op.alter_column(_TABLE, "digest_key_id", server_default=None)
    op.alter_column(_TABLE, "digest_key_version", server_default=None)
    op.alter_column(_TABLE, "completion_action", server_default=None)
    op.create_check_constraint(
        op.f("ck_email_verification_challenges__flow_expiry_after_code_expiry"),
        _TABLE,
        "flow_expires_at >= expires_at",
    )
    op.create_check_constraint(
        op.f("ck_email_verification_challenges__digest_key_version_positive"),
        _TABLE,
        "digest_key_version > 0",
    )
    op.create_check_constraint(
        op.f("ck_email_verification_challenges__valid_completion_action"),
        _TABLE,
        "completion_action IN ('activate_account', 'account_exists_notice')",
    )
    op.drop_constraint(_SUPERSEDED_FK, _TABLE, type_="foreignkey")
    op.create_foreign_key(
        _SUPERSEDED_FK,
        _TABLE,
        _TABLE,
        ["superseded_by_id"],
        ["id"],
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint(_SUPERSEDED_FK, _TABLE, type_="foreignkey")
    op.create_foreign_key(
        _SUPERSEDED_FK,
        _TABLE,
        _TABLE,
        ["superseded_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        op.f("ck_email_verification_challenges__valid_completion_action"),
        _TABLE,
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_email_verification_challenges__digest_key_version_positive"),
        _TABLE,
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_email_verification_challenges__flow_expiry_after_code_expiry"),
        _TABLE,
        type_="check",
    )
    op.drop_column(_TABLE, "completion_action")
    op.drop_column(_TABLE, "digest_key_version")
    op.drop_column(_TABLE, "digest_key_id")
    op.drop_column(_TABLE, "flow_expires_at")
