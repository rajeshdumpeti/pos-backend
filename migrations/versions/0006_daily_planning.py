"""Add domain-owned daily planning and account-scoped creation receipts.

Revision ID: 0006_daily_planning
Revises: 0005_authoritative_onboarding
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0006_daily_planning"
down_revision = "0005_authoritative_onboarding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_items",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_account_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("scheduled_for_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(128), nullable=False),
        sa.Column("scheduled_start_at", pg.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("scheduled_end_at", pg.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_priority", sa.Boolean(), nullable=False),
        sa.Column("completed_at", pg.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", pg.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_plan_items"),
        sa.ForeignKeyConstraint(
            ["user_account_id"],
            ["user_accounts.id"],
            ondelete="CASCADE",
            name="fk_plan_items__user_account_id__user_accounts",
        ),
        sa.CheckConstraint("version > 0", name="ck_plan_items__version_positive"),
        sa.CheckConstraint(
            "length(btrim(title)) > 0 AND length(title) <= 200",
            name="ck_plan_items__title_bounded",
        ),
        sa.CheckConstraint(
            "scheduled_end_at IS NULL OR (scheduled_start_at IS NOT NULL AND scheduled_end_at > scheduled_start_at)",
            name="ck_plan_items__schedule_order",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR is_priority = false",
            name="ck_plan_items__completed_not_priority",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at AND (completed_at IS NULL OR completed_at >= created_at)",
            name="ck_plan_items__timestamp_order",
        ),
    )
    op.create_index(
        "ix_plan_items__user_account_id__scheduled_for_date__f8687abffd",
        "plan_items",
        ["user_account_id", "scheduled_for_date", "scheduled_start_at"],
    )
    op.create_index(
        "ix_plan_items__user_account_id__completed_at",
        "plan_items",
        ["user_account_id", "completed_at"],
    )
    op.create_table(
        "plan_creation_receipts",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_account_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("key_digest", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("plan_item_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("response_snapshot", pg.JSONB(), nullable=False),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_plan_creation_receipts"),
        sa.ForeignKeyConstraint(
            ["user_account_id"],
            ["user_accounts.id"],
            ondelete="CASCADE",
            name="fk_plan_creation_receipts__user_account_id__user_accounts",
        ),
        sa.ForeignKeyConstraint(
            ["plan_item_id"],
            ["plan_items.id"],
            ondelete="CASCADE",
            name="fk_plan_creation_receipts__plan_item_id__plan_items",
        ),
        sa.UniqueConstraint(
            "user_account_id",
            "operation",
            "key_digest",
            name=op.f(
                "uq_plan_creation_receipts__user_account_id__operati__667c7b2043"
            ),
        ),
        sa.CheckConstraint(
            "operation = 'plan_item.create'",
            name="ck_plan_creation_receipts__supported_operation",
        ),
    )


def downgrade() -> None:
    op.drop_table("plan_creation_receipts")
    op.drop_index(
        "ix_plan_items__user_account_id__completed_at", table_name="plan_items"
    )
    op.drop_index(
        "ix_plan_items__user_account_id__scheduled_for_date__f8687abffd",
        table_name="plan_items",
    )
    op.drop_table("plan_items")
