"""Planning persistence uses canonical metadata and no ORM business policy."""

from datetime import date, datetime
from uuid import UUID
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID as PG_UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.infrastructure.relational import RelationalBase


class PlanItemModel(RelationalBase):
    __tablename__ = "plan_items"
    __table_args__ = (
        CheckConstraint("version > 0", info={"purpose": "version_positive"}),
        CheckConstraint(
            "length(btrim(title)) > 0 AND length(title) <= 200",
            info={"purpose": "title_bounded"},
        ),
        CheckConstraint(
            "scheduled_end_at IS NULL OR (scheduled_start_at IS NOT NULL AND scheduled_end_at > scheduled_start_at)",
            info={"purpose": "schedule_order"},
        ),
        CheckConstraint(
            "completed_at IS NULL OR is_priority = false",
            info={"purpose": "completed_not_priority"},
        ),
        CheckConstraint(
            "updated_at >= created_at AND (completed_at IS NULL OR completed_at >= created_at)",
            info={"purpose": "timestamp_order"},
        ),
        Index(None, "user_account_id", "scheduled_for_date", "scheduled_start_at"),
        Index(None, "user_account_id", "completed_at"),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    scheduled_for_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(128), nullable=False)
    scheduled_start_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    scheduled_end_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    is_priority: Mapped[bool] = mapped_column(Boolean, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class PlanCreationReceiptModel(RelationalBase):
    __tablename__ = "plan_creation_receipts"
    __table_args__ = (
        UniqueConstraint("user_account_id", "operation", "key_digest"),
        CheckConstraint(
            "operation = 'plan_item.create'", info={"purpose": "supported_operation"}
        ),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    key_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_item_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("plan_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    response_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
