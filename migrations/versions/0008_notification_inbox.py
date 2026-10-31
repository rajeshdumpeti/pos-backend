"""Account timezone, immutable Inbox snapshots and approved producer jobs. No backfill."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0008_notification_inbox"
down_revision = "0007_background_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_timezones",
        sa.Column("user_account_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("timezone", sa.String(128), nullable=False),
        sa.Column("updated_at", pg.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(timezone) BETWEEN 1 AND 128",
            name="ck_account_timezones__timezone_bounded",
        ),
        sa.CheckConstraint(
            "version > 0", name="ck_account_timezones__version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["user_account_id"],
            ["user_accounts.id"],
            ondelete="CASCADE",
            name="fk_account_timezones__user_account_id__user_accounts",
        ),
        sa.PrimaryKeyConstraint(*["user_account_id"], name="pk_account_timezones"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_account_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.String(512), nullable=False),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", pg.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("read_at", pg.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("plan_item_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("selected_date", sa.Date(), nullable=False),
        sa.Column("source_timezone", sa.String(128), nullable=False),
        sa.Column("occurrence_digest", sa.String(64), nullable=False),
        sa.Column("updated_at", pg.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "(source = 'plan' AND category = 'reminder' AND event_type = 'plan.item_starting_soon') OR (source = 'today' AND category = 'attention' AND event_type = 'today.items_remaining')",
            name="ck_notifications__approved_event",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at AND version > 0",
            name="ck_notifications__audit_order",
        ),
        sa.CheckConstraint(
            "length(title) BETWEEN 1 AND 200 AND length(message) BETWEEN 1 AND 512",
            name="ck_notifications__copy_bounded",
        ),
        sa.CheckConstraint(
            "expires_at = created_at + INTERVAL '2160 hours'",
            name="ck_notifications__exact_retention",
        ),
        sa.CheckConstraint(
            "read_at IS NULL OR read_at >= created_at",
            name="ck_notifications__read_order",
        ),
        sa.ForeignKeyConstraint(
            ["plan_item_id"],
            ["plan_items.id"],
            ondelete="SET NULL",
            name="fk_notifications__plan_item_id__plan_items",
        ),
        sa.ForeignKeyConstraint(
            ["user_account_id"],
            ["user_accounts.id"],
            ondelete="CASCADE",
            name="fk_notifications__user_account_id__user_accounts",
        ),
        sa.PrimaryKeyConstraint(*["id"], name="pk_notifications"),
        sa.UniqueConstraint(
            *["user_account_id", "occurrence_digest"],
            name="uq_notifications__user_account_id__occurrence_digest"
        ),
    )
    op.create_index(
        "ix_notifications__account_order",
        "notifications",
        ["user_account_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index("ix_notifications__expiry", "notifications", ["expires_at", "id"])
    op.create_index(
        "ix_notifications__unread",
        "notifications",
        ["user_account_id", "source", "expires_at"],
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.drop_constraint(
        op.f("ck_background_jobs__supported_type"), "background_jobs", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_background_jobs__supported_type"),
        "background_jobs",
        "job_type IN ('maintenance.jobs.cleanup','plan.item_starting_soon','today.items_remaining','maintenance.notifications.cleanup')",
    )
    op.create_index(
        "ix_background_jobs__producer_scope",
        "background_jobs",
        ["user_account_id", "job_type"],
        postgresql_where=sa.text("state IN ('pending','retryable')"),
    )


def downgrade() -> None:
    op.drop_index("ix_background_jobs__producer_scope", table_name="background_jobs")
    op.drop_constraint(
        op.f("ck_background_jobs__supported_type"), "background_jobs", type_="check"
    )
    op.execute(
        sa.text(
            "DELETE FROM background_jobs WHERE job_type IN ('plan.item_starting_soon','today.items_remaining','maintenance.notifications.cleanup')"
        )
    )
    op.create_check_constraint(
        op.f("ck_background_jobs__supported_type"),
        "background_jobs",
        "job_type = 'maintenance.jobs.cleanup'",
    )
    op.drop_table("notifications")
    op.drop_table("account_timezones")
