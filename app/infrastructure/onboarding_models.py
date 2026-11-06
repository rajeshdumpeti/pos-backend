"""SQLAlchemy model for authoritative onboarding persistence."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.relational import RelationalBase
from app.domain.onboarding import OnboardingState, validate_persisted_state


class OnboardingStateModel(RelationalBase):
    __tablename__ = "onboarding_states"
    __table_args__ = (
        CheckConstraint("version > 0", info={"purpose": "version_positive"}),
        CheckConstraint(
            "unit_system IS NULL OR unit_system IN ('metric', 'imperial')",
            info={"purpose": "valid_unit_system"},
        ),
        CheckConstraint(
            "currency IS NULL OR currency IN ('USD', 'INR', 'CAD', 'GBP', 'EUR', 'AUD')",
            info={"purpose": "valid_currency"},
        ),
        CheckConstraint(
            "unit_system_confirmed = false OR unit_system IS NOT NULL",
            info={"purpose": "unit_confirmation_has_value"},
        ),
        CheckConstraint(
            "currency_confirmed = false OR currency IS NOT NULL",
            info={"purpose": "currency_confirmation_has_value"},
        ),
        CheckConstraint(
            "completed_at IS NULL OR ("
            "jsonb_typeof(priority_life_areas) = 'array' AND "
            "jsonb_array_length(priority_life_areas) > 0 AND "
            "unit_system IS NOT NULL AND unit_system_confirmed = true AND "
            "currency IS NOT NULL AND currency_confirmed = true)",
            info={"purpose": "completed_state_has_required_facts"},
        ),
    )
    user_account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    contract_version: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    priority_life_areas: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    unit_system: Mapped[str | None] = mapped_column(String(16), nullable=True)
    unit_system_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    currency_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    goals: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    integration_preferences: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False
    )
    notification_preferences: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )


def reconstruct_onboarding_state(model: OnboardingStateModel) -> OnboardingState:
    """Build and strictly validate the canonical domain value from one row."""
    if not isinstance(model.priority_life_areas, list):
        from app.domain.onboarding import CorruptOnboardingStateError

        raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
    return validate_persisted_state(
        OnboardingState(
            user_account_id=model.user_account_id,
            contract_version=model.contract_version,
            version=model.version,
            priority_life_areas=tuple(model.priority_life_areas),
            unit_system=model.unit_system,
            unit_system_confirmed=model.unit_system_confirmed,
            currency=model.currency,
            currency_confirmed=model.currency_confirmed,
            goals=model.goals,
            integration_preferences=model.integration_preferences,
            notification_preferences=model.notification_preferences,
            completed_at=model.completed_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
    )
