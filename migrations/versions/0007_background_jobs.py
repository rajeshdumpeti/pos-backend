"""Durable bounded jobs, with no backfill or producer registration."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0007_background_jobs"
down_revision = "0006_daily_planning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("user_account_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("deduplication_digest", sa.String(64), nullable=False),
        sa.Column("intent_digest", sa.String(64), nullable=False),
        sa.Column("scheduled_at", pg.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", pg.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("failure_category", sa.String(32), nullable=True),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", pg.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("started_at", pg.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", pg.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= max_attempts AND max_attempts BETWEEN 1 AND 5",
            name="ck_background_jobs__attempt_bounds",
        ),
        sa.CheckConstraint(
            "(state IN ('succeeded','dead','cancelled','superseded')) = (completed_at IS NOT NULL)",
            name="ck_background_jobs__completion_state",
        ),
        sa.CheckConstraint(
            "(state = 'running') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) AND ((lease_owner IS NULL) = (lease_expires_at IS NULL))",
            name="ck_background_jobs__lease_state",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_background_jobs__payload_object",
        ),
        sa.CheckConstraint(
            "failure_category IS NULL OR failure_category IN ('transient','dependency_unavailable','permanent','invalid_payload','unsupported_type','handler_failure','lease_expired')",
            name="ck_background_jobs__safe_failure",
        ),
        sa.CheckConstraint(
            "state IN ('pending','running','succeeded','retryable','dead','cancelled','superseded')",
            name="ck_background_jobs__supported_state",
        ),
        sa.CheckConstraint(
            "job_type = 'maintenance.jobs.cleanup'",
            name="ck_background_jobs__supported_type",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at AND (started_at IS NULL OR started_at >= created_at) AND (completed_at IS NULL OR completed_at >= created_at)",
            name="ck_background_jobs__timestamp_order",
        ),
        sa.ForeignKeyConstraint(
            ["user_account_id"],
            ["user_accounts.id"],
            ondelete="CASCADE",
            name="fk_background_jobs__user_account_id__user_accounts",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_background_jobs"),
        sa.UniqueConstraint(
            "deduplication_digest", name="uq_background_jobs__deduplication_digest"
        ),
    )
    op.create_index(
        "ix_background_jobs__due_claim",
        "background_jobs",
        ["scheduled_at", "id"],
        postgresql_where=sa.text("state IN ('pending','retryable')"),
    )
    op.create_index(
        "ix_background_jobs__lease_recovery",
        "background_jobs",
        ["lease_expires_at", "id"],
        postgresql_where=sa.text("state = 'running'"),
    )
    op.create_index(
        "ix_background_jobs__terminal_cleanup",
        "background_jobs",
        ["user_account_id", "completed_at", "id"],
        postgresql_where=sa.text(
            "state IN ('succeeded','dead','cancelled','superseded')"
        ),
    )


def downgrade() -> None:
    op.drop_table("background_jobs")
