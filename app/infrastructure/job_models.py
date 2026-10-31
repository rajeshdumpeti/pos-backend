"""PostgreSQL storage and claim indexes for the internal background-job foundation."""

from datetime import datetime
from uuid import UUID
from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.infrastructure.relational import RelationalBase
from app.infrastructure.authentication.models import UserAccountModel


class BackgroundJobModel(RelationalBase):
    __tablename__ = "background_jobs"
    __table_args__ = (
        UniqueConstraint("deduplication_digest"),
        CheckConstraint(
            "job_type IN ('maintenance.jobs.cleanup','plan.item_starting_soon','today.items_remaining','maintenance.notifications.cleanup')",
            info={"purpose": "supported_type"},
        ),
        CheckConstraint(
            "state IN ('pending','running','succeeded','retryable','dead','cancelled','superseded')",
            info={"purpose": "supported_state"},
        ),
        CheckConstraint(
            "attempts >= 0 AND attempts <= max_attempts AND max_attempts BETWEEN 1 AND 5",
            info={"purpose": "attempt_bounds"},
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'", info={"purpose": "payload_object"}
        ),
        CheckConstraint(
            "(state = 'running') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) AND ((lease_owner IS NULL) = (lease_expires_at IS NULL))",
            info={"purpose": "lease_state"},
        ),
        CheckConstraint(
            "(state IN ('succeeded','dead','cancelled','superseded')) = (completed_at IS NOT NULL)",
            info={"purpose": "completion_state"},
        ),
        CheckConstraint(
            "failure_category IS NULL OR failure_category IN ('transient','dependency_unavailable','permanent','invalid_payload','unsupported_type','handler_failure','lease_expired')",
            info={"purpose": "safe_failure"},
        ),
        CheckConstraint(
            "updated_at >= created_at AND (started_at IS NULL OR started_at >= created_at) AND (completed_at IS NULL OR completed_at >= created_at)",
            info={"purpose": "timestamp_order"},
        ),
        Index(
            "ix_background_jobs__due_claim",
            "scheduled_at",
            "id",
            postgresql_where=text("state IN ('pending','retryable')"),
        ),
        Index(
            "ix_background_jobs__lease_recovery",
            "lease_expires_at",
            "id",
            postgresql_where=text("state = 'running'"),
        ),
        Index(
            "ix_background_jobs__terminal_cleanup",
            "user_account_id",
            "completed_at",
            "id",
            postgresql_where=text(
                "state IN ('succeeded','dead','cancelled','superseded')"
            ),
        ),
        Index(
            "ix_background_jobs__producer_scope",
            "user_account_id",
            "job_type",
            postgresql_where=text("state IN ('pending','retryable')"),
        ),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    user_account_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(UserAccountModel.id, ondelete="CASCADE"),
        nullable=True,
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    deduplication_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_owner: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    failure_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
