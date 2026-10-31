"""Account preference and immutable Inbox snapshots on canonical metadata."""

from datetime import date, datetime
from uuid import UUID
from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from app.infrastructure.relational import RelationalBase
from app.infrastructure.authentication.models import UserAccountModel
from app.infrastructure.planning_models import PlanItemModel


class AccountTimezoneModel(RelationalBase):
    __tablename__ = "account_timezones"
    __table_args__ = (
        CheckConstraint("version > 0", info={"purpose": "version_positive"}),
        CheckConstraint(
            "length(timezone) BETWEEN 1 AND 128", info={"purpose": "timezone_bounded"}
        ),
    )
    user_account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(UserAccountModel.id, ondelete="CASCADE"),
        primary_key=True,
    )
    timezone: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class NotificationModel(RelationalBase):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_account_id", "occurrence_digest"),
        CheckConstraint(
            "(source = 'plan' AND category = 'reminder' AND event_type = 'plan.item_starting_soon') OR (source = 'today' AND category = 'attention' AND event_type = 'today.items_remaining')",
            info={"purpose": "approved_event"},
        ),
        CheckConstraint(
            "expires_at = created_at + INTERVAL '2160 hours'",
            info={"purpose": "exact_retention"},
        ),
        CheckConstraint(
            "read_at IS NULL OR read_at >= created_at", info={"purpose": "read_order"}
        ),
        CheckConstraint(
            "updated_at >= created_at AND version > 0", info={"purpose": "audit_order"}
        ),
        CheckConstraint(
            "length(title) BETWEEN 1 AND 200 AND length(message) BETWEEN 1 AND 512",
            info={"purpose": "copy_bounded"},
        ),
        Index(
            "ix_notifications__account_order",
            "user_account_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_notifications__unread",
            "user_account_id",
            "source",
            "expires_at",
            postgresql_where=text("read_at IS NULL"),
        ),
        Index("ix_notifications__expiry", "expires_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(UserAccountModel.id, ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    plan_item_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(PlanItemModel.id, ondelete="SET NULL"),
        nullable=True,
    )
    selected_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_timezone: Mapped[str] = mapped_column(String(128), nullable=False)
    occurrence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
