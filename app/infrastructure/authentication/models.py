"""Infrastructure-owned SQLAlchemy models for authentication persistence."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.relational import RelationalBase


UUID_TYPE = PG_UUID(as_uuid=True)
TIMESTAMPTZ = TIMESTAMP(timezone=True)


class UserAccountModel(RelationalBase):
    __tablename__ = "user_accounts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_verification', 'active', 'disabled', "
            "'deactivated', 'deletion_pending', 'deleted')",
            info={"purpose": "valid_status"},
        ),
        CheckConstraint(
            "authentication_epoch >= 0",
            info={"purpose": "authentication_epoch_nonnegative"},
        ),
        CheckConstraint(
            "(status <> 'active') OR email_verified_at IS NOT NULL",
            info={"purpose": "active_account_email_verified"},
        ),
        CheckConstraint(
            "(status <> 'deleted') OR deleted_at IS NOT NULL",
            info={"purpose": "deleted_account_timestamped"},
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    onboarding_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    authentication_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    terms_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    privacy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    privacy_accepted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    deletion_requested_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)


class EmailIdentityModel(RelationalBase):
    __tablename__ = "email_identities"
    __table_args__ = (
        UniqueConstraint("normalized_email"),
        CheckConstraint(
            "(is_verified = false AND verified_at IS NULL) OR "
            "(is_verified = true AND verified_at IS NOT NULL)",
            info={"purpose": "verification_timestamp_consistent"},
        ),
        Index(None, "user_account_id"),
    )

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4, nullable=False)
    user_account_id: Mapped[UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    display_email: Mapped[str] = mapped_column(String(320), nullable=False)
    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    verified_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)


Index(
    None,
    EmailIdentityModel.user_account_id,
    unique=True,
    postgresql_where=EmailIdentityModel.is_primary.is_(True),
    info={"purpose": "one_primary_email_per_account"},
)


class ExternalProviderIdentityModel(RelationalBase):
    __tablename__ = "external_provider_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject"),
        CheckConstraint(
            "provider IN ('google', 'apple')",
            info={"purpose": "valid_provider"},
        ),
        Index(None, "user_account_id"),
    )

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4, nullable=False)
    user_account_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email_snapshot: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_private_relay: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_authenticated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    last_authenticated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)


class EmailVerificationChallengeModel(RelationalBase):
    __tablename__ = "email_verification_challenges"
    __table_args__ = (
        UniqueConstraint("flow_token_digest"),
        UniqueConstraint("code_digest"),
        CheckConstraint("attempts >= 0", info={"purpose": "attempts_nonnegative"}),
        CheckConstraint("max_attempts > 0", info={"purpose": "max_attempts_positive"}),
        CheckConstraint("attempts <= max_attempts", info={"purpose": "attempts_within_maximum"}),
        CheckConstraint("expires_at > issued_at", info={"purpose": "expiry_after_issue"}),
        CheckConstraint("resend_available_at >= issued_at", info={"purpose": "resend_after_issue"}),
        CheckConstraint("status_version > 0", info={"purpose": "status_version_positive"}),
        CheckConstraint("purpose IN ('registration', 'email_change')", info={"purpose": "valid_purpose"}),
        Index(None, "user_account_id", "purpose", "expires_at"),
        Index(None, "expires_at", info={"purpose": "expiry_cleanup"}),
    )

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4, nullable=False)
    user_account_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    flow_token_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    code_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    target_normalized_email: Mapped[str] = mapped_column(String(320), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    resend_available_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    superseded_by_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, ForeignKey("email_verification_challenges.id", ondelete="SET NULL"), nullable=True)
    status_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


Index(
    None,
    EmailVerificationChallengeModel.user_account_id,
    EmailVerificationChallengeModel.purpose,
    unique=True,
    postgresql_where=text("consumed_at IS NULL AND superseded_by_id IS NULL"),
    info={"purpose": "one_active_challenge_per_account_purpose"},
)


class PasswordResetChallengeModel(RelationalBase):
    __tablename__ = "password_reset_challenges"
    __table_args__ = (
        UniqueConstraint("token_digest"),
        CheckConstraint("expires_at > issued_at", info={"purpose": "expiry_after_issue"}),
        Index(None, "user_account_id"),
        Index(None, "expires_at", info={"purpose": "expiry_cleanup"}),
    )

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4, nullable=False)
    user_account_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False)
    token_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    request_security_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)


class RefreshSessionModel(RelationalBase):
    __tablename__ = "refresh_sessions"
    __table_args__ = (
        UniqueConstraint("current_token_digest"),
        CheckConstraint("authentication_epoch >= 0", info={"purpose": "authentication_epoch_nonnegative"}),
        CheckConstraint("expires_at > created_at", info={"purpose": "expiry_after_creation"}),
        CheckConstraint("last_used_at >= created_at", info={"purpose": "last_use_after_creation"}),
        CheckConstraint("(revoked_at IS NULL) = (revocation_reason IS NULL)", info={"purpose": "revocation_fields_consistent"}),
        Index(None, "user_account_id", "revoked_at"),
        Index(None, "token_family_id"),
        Index(None, "expires_at", info={"purpose": "expiry_cleanup"}),
    )

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4, nullable=False)
    user_account_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False)
    token_family_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    current_token_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    remember_me: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    authentication_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    device_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RefreshRotationRetryReceiptModel(RelationalBase):
    __tablename__ = "refresh_rotation_retry_receipts"
    __table_args__ = (
        UniqueConstraint("previous_token_digest", "refresh_request_id"),
        CheckConstraint("expires_at > created_at", info={"purpose": "expiry_after_creation"}),
        CheckConstraint("encryption_key_version > 0", info={"purpose": "key_version_positive"}),
        Index(None, "refresh_session_id"),
        Index(None, "token_family_id"),
        Index(None, "expires_at", info={"purpose": "expiry_cleanup"}),
    )

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4, nullable=False)
    refresh_session_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey("refresh_sessions.id", ondelete="CASCADE"), nullable=False)
    token_family_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    previous_token_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    refresh_request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    response_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    encryption_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)


class AuthenticationAttemptStateModel(RelationalBase):
    __tablename__ = "authentication_attempt_states"
    __table_args__ = (
        UniqueConstraint("identity_digest", "risk_bucket_digest"),
        CheckConstraint("failed_attempts >= 0", info={"purpose": "failed_attempts_nonnegative"}),
        CheckConstraint("last_attempt_at >= window_started_at", info={"purpose": "last_attempt_within_window"}),
        Index(None, "updated_at", info={"purpose": "retention_cleanup"}),
    )

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4, nullable=False)
    identity_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    risk_bucket_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_started_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    last_attempt_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    progressive_delay_until: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)


class SecurityEventModel(RelationalBase):
    __tablename__ = "security_events"
    __table_args__ = (
        Index(None, "user_account_id", "occurred_at"),
        Index(None, "request_id"),
        Index(None, "event_type", "occurred_at"),
        Index(None, "occurred_at", info={"purpose": "retention_cleanup"}),
    )

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4, nullable=False)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    user_account_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True)
    refresh_session_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, ForeignKey("refresh_sessions.id", ondelete="SET NULL"), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    sanitized_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class AuthenticationEmailOutboxModel(RelationalBase):
    __tablename__ = "authentication_email_outbox"
    __table_args__ = (
        UniqueConstraint("deduplication_key"),
        CheckConstraint("status IN ('pending', 'claimed', 'delivered', 'failed')", info={"purpose": "valid_status"}),
        CheckConstraint("message_kind IN ('email_verification', 'password_reset')", info={"purpose": "valid_message_kind"}),
        CheckConstraint("attempt_count >= 0", info={"purpose": "attempt_count_nonnegative"}),
        CheckConstraint("encryption_key_version > 0", info={"purpose": "key_version_positive"}),
        CheckConstraint("(status <> 'claimed') OR (claimed_at IS NOT NULL AND claim_id IS NOT NULL)", info={"purpose": "claim_fields_consistent"}),
        CheckConstraint("(status <> 'delivered') OR delivered_at IS NOT NULL", info={"purpose": "delivery_timestamp_consistent"}),
        Index(None, "status", "available_at", "created_at", info={"purpose": "delivery_claim"}),
        Index(None, "user_account_id"),
        Index(None, "delivered_at", "failed_at", info={"purpose": "delivery_cleanup"}),
    )

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4, nullable=False)
    user_account_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True)
    message_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient_normalized_email: Mapped[str] = mapped_column(String(320), nullable=False)
    payload_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    encryption_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    available_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    claim_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
