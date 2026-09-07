"""Framework-independent authentication persistence concepts and lifecycle rules."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, NewType
from uuid import UUID


AccountId = NewType("AccountId", UUID)
IdentityId = NewType("IdentityId", UUID)
ChallengeId = NewType("ChallengeId", UUID)
RefreshSessionId = NewType("RefreshSessionId", UUID)


class AccountStatus(StrEnum):
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    DISABLED = "disabled"
    DEACTIVATED = "deactivated"
    DELETION_PENDING = "deletion_pending"
    DELETED = "deleted"


class AuthenticationProvider(StrEnum):
    GOOGLE = "google"
    APPLE = "apple"


class ChallengePurpose(StrEnum):
    REGISTRATION = "registration"
    EMAIL_CHANGE = "email_change"


class ChallengeCompletionAction(StrEnum):
    ACTIVATE_ACCOUNT = "activate_account"
    ACCOUNT_EXISTS_NOTICE = "account_exists_notice"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DELIVERED = "delivered"
    FAILED = "failed"


class OutboxMessageKind(StrEnum):
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"


_ALLOWED_ACCOUNT_TRANSITIONS: dict[AccountStatus, frozenset[AccountStatus]] = {
    AccountStatus.PENDING_VERIFICATION: frozenset(
        {AccountStatus.ACTIVE, AccountStatus.DELETION_PENDING}
    ),
    AccountStatus.ACTIVE: frozenset(
        {
            AccountStatus.DISABLED,
            AccountStatus.DEACTIVATED,
            AccountStatus.DELETION_PENDING,
        }
    ),
    AccountStatus.DISABLED: frozenset(
        {AccountStatus.ACTIVE, AccountStatus.DELETION_PENDING}
    ),
    AccountStatus.DEACTIVATED: frozenset(
        {AccountStatus.ACTIVE, AccountStatus.DELETION_PENDING}
    ),
    AccountStatus.DELETION_PENDING: frozenset(
        {AccountStatus.ACTIVE, AccountStatus.DELETED}
    ),
    AccountStatus.DELETED: frozenset(),
}


class InvalidAccountTransitionError(ValueError):
    """Raised when a requested account lifecycle transition is forbidden."""


def require_account_transition(
    current: AccountStatus, target: AccountStatus
) -> AccountStatus:
    """Validate one explicit lifecycle transition; deleted accounts stay terminal."""
    if target not in _ALLOWED_ACCOUNT_TRANSITIONS[current]:
        raise InvalidAccountTransitionError(
            f"Account cannot transition from {current.value} to {target.value}"
        )
    return target


@dataclass(frozen=True, slots=True)
class UserAccountRecord:
    id: AccountId
    status: AccountStatus
    display_name: str
    password_hash: str | None = field(repr=False)
    email_verified_at: datetime | None
    onboarding_required: bool
    authentication_epoch: int
    terms_version: str | None
    terms_accepted_at: datetime | None
    privacy_version: str | None
    privacy_accepted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deletion_requested_at: datetime | None = None
    deleted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EmailIdentityRecord:
    id: IdentityId
    user_account_id: AccountId
    display_email: str = field(repr=False)
    normalized_email: str = field(repr=False)
    is_verified: bool
    is_primary: bool
    created_at: datetime
    updated_at: datetime
    verified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExternalProviderIdentityRecord:
    id: IdentityId
    user_account_id: AccountId
    provider: AuthenticationProvider
    provider_subject: str = field(repr=False)
    email_snapshot: str | None = field(default=None, repr=False)
    email_verified: bool | None = None
    is_private_relay: bool = False
    first_authenticated_at: datetime | None = None
    last_authenticated_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EmailVerificationChallengeRecord:
    id: ChallengeId
    user_account_id: AccountId
    purpose: ChallengePurpose
    flow_token_digest: str = field(repr=False)
    code_digest: str = field(repr=False)
    target_normalized_email: str = field(repr=False)
    issued_at: datetime
    expires_at: datetime
    flow_expires_at: datetime
    resend_available_at: datetime
    digest_key_id: str = field(repr=False)
    digest_key_version: int = field(repr=False)
    completion_action: ChallengeCompletionAction
    attempts: int = 0
    max_attempts: int = 5
    status_version: int = 1
    consumed_at: datetime | None = None
    superseded_by_id: ChallengeId | None = None


@dataclass(frozen=True, slots=True)
class PasswordResetChallengeRecord:
    id: ChallengeId
    user_account_id: AccountId
    token_digest: str = field(repr=False)
    request_security_digest: str = field(repr=False)
    issued_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RefreshSessionRecord:
    id: RefreshSessionId
    user_account_id: AccountId
    token_family_id: UUID
    current_token_digest: str = field(repr=False)
    remember_me: bool
    authentication_epoch: int
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    device_metadata: dict[str, Any] = field(default_factory=dict, repr=False)
    revoked_at: datetime | None = None
    revocation_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RefreshRotationReceiptRecord:
    id: UUID
    refresh_session_id: RefreshSessionId
    token_family_id: UUID
    previous_token_digest: str = field(repr=False)
    refresh_request_id: str
    response_ciphertext: bytes = field(repr=False)
    encryption_key_id: str = field(repr=False)
    encryption_key_version: int = field(repr=False)
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AuthenticationAttemptStateRecord:
    id: UUID
    identity_digest: str = field(repr=False)
    risk_bucket_digest: str = field(repr=False)
    failed_attempts: int
    window_started_at: datetime
    last_attempt_at: datetime
    progressive_delay_until: datetime | None
    locked_until: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SecurityEventRecord:
    id: UUID
    event_type: str
    occurred_at: datetime
    user_account_id: AccountId | None = None
    refresh_session_id: RefreshSessionId | None = None
    provider: AuthenticationProvider | None = None
    request_id: str | None = None
    source_digest: str | None = field(default=None, repr=False)
    sanitized_metadata: dict[str, Any] = field(default_factory=dict, repr=False)


    def __post_init__(self) -> None:
        import re
        allowed = {
            "email_sign_in_succeeded", "refresh_token_rotated", "refresh_token_replayed",
            "session_logged_out", "all_sessions_logged_out", "registration_accepted",
            "unverified_email_change_accepted", "email_verification_resend_accepted",
            "email_verification_rejected", "existing_account_ownership_proved",
            "email_verification_succeeded", "password_reset_succeeded", "password_reset_requested",
        }
        if self.event_type not in allowed:
            raise ValueError("Security event type is not approved")
        if self.request_id is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", self.request_id):
            raise ValueError("Security event request ID is invalid")
        if any(value is not None and not isinstance(value, UUID)
               for value in (self.id, self.user_account_id, self.refresh_session_id)):
            raise ValueError("Security event identifier is invalid")
        if self.source_digest is not None and not re.fullmatch(r"[a-f0-9]{64}", self.source_digest):
            raise ValueError("Security event source is invalid")
        if self.sanitized_metadata not in ({}, {"category": "invalid_code"}):
            raise ValueError("Security event metadata is not approved")


@dataclass(frozen=True, slots=True)
class AuthenticationEmailOutboxRecord:
    id: UUID
    message_kind: OutboxMessageKind
    recipient_normalized_email: str = field(repr=False)
    payload_ciphertext: bytes = field(repr=False)
    encryption_key_id: str = field(repr=False)
    encryption_key_version: int = field(repr=False)
    deduplication_key: str = field(repr=False)
    status: OutboxStatus
    created_at: datetime
    available_at: datetime
    attempt_count: int = 0
    user_account_id: AccountId | None = None
    claimed_at: datetime | None = None
    claim_id: UUID | None = None
    delivered_at: datetime | None = None
    failed_at: datetime | None = None
    provider_message_id: str | None = field(default=None, repr=False)
    failure_category: str | None = None
