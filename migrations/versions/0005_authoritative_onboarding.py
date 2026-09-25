"""Add one authoritative onboarding state per user account.

Revision ID: 0005_authoritative_onboarding
Revises: 0004_auth_contract_v1
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0005_authoritative_onboarding"
down_revision: str | None = "0004_auth_contract_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "onboarding_states",
        sa.Column("user_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_version", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "priority_life_areas",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("unit_system", sa.String(length=16), nullable=True),
        sa.Column("unit_system_confirmed", sa.Boolean(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("currency_confirmed", sa.Boolean(), nullable=False),
        sa.Column("goals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "integration_preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "notification_preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version > 0", name="ck_onboarding_states__version_positive"
        ),
        sa.CheckConstraint(
            "unit_system IS NULL OR unit_system IN ('metric', 'imperial')",
            name="ck_onboarding_states__valid_unit_system",
        ),
        sa.CheckConstraint(
            "currency IS NULL OR currency IN ('USD', 'INR', 'CAD', 'GBP', 'EUR', 'AUD')",
            name="ck_onboarding_states__valid_currency",
        ),
        sa.CheckConstraint(
            "unit_system_confirmed = false OR unit_system IS NOT NULL",
            name="ck_onboarding_states__unit_confirmation_has_value",
        ),
        sa.CheckConstraint(
            "currency_confirmed = false OR currency IS NOT NULL",
            name="ck_onboarding_states__currency_confirmation_has_value",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR ("
            "jsonb_typeof(priority_life_areas) = 'array' AND "
            "jsonb_array_length(priority_life_areas) > 0 AND "
            "unit_system IS NOT NULL AND unit_system_confirmed = true AND "
            "currency IS NOT NULL AND currency_confirmed = true)",
            name="ck_onboarding_states__completed_state_has_required_facts",
        ),
        sa.ForeignKeyConstraint(
            ["user_account_id"],
            ["user_accounts.id"],
            name="fk_onboarding_states__user_account_id__user_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_account_id", name="pk_onboarding_states"),
    )


def downgrade() -> None:
    op.drop_table("onboarding_states")
