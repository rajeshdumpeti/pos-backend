"""Application-facing authentication repositories and Unit-of-Work contracts."""

from collections.abc import Callable, Sequence
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self, runtime_checkable
from uuid import UUID

from app.domain.authentication import (
    AccountId,
    AuthenticationAttemptStateRecord,
    AuthenticationEmailOutboxRecord,
    AuthenticationProvider,
    ChallengePurpose,
    EmailIdentityRecord,
    EmailVerificationChallengeRecord,
    ExternalProviderIdentityRecord,
    PasswordResetChallengeRecord,
    RefreshRotationReceiptRecord,
    RefreshSessionId,
    RefreshSessionRecord,
    SecurityEventRecord,
    UserAccountRecord,
)


class AuthenticationPersistenceError(RuntimeError):
    """Base class for safe, framework-independent persistence failures."""


class DuplicateNormalizedIdentityError(AuthenticationPersistenceError):
    pass


class DuplicateProviderIdentityError(AuthenticationPersistenceError):
    pass


class PersistenceRecordNotFoundError(AuthenticationPersistenceError):
    pass


class ConcurrentPersistenceUpdateError(AuthenticationPersistenceError):
    pass


class AuthenticationConstraintError(AuthenticationPersistenceError):
    pass


class AuthenticationPersistenceUnavailableError(AuthenticationPersistenceError):
    pass


class UnexpectedAuthenticationPersistenceError(AuthenticationPersistenceError):
    pass


@runtime_checkable
class UserAccountRepository(Protocol):
    def add(self, record: UserAccountRecord) -> None: ...
    def get(self, account_id: AccountId) -> UserAccountRecord | None: ...
    def get_for_update(self, account_id: AccountId) -> UserAccountRecord | None: ...
    def activate_pending(self, account_id: AccountId, verified_at: datetime) -> bool: ...


@runtime_checkable
class EmailIdentityRepository(Protocol):
    def add(self, record: EmailIdentityRecord) -> None: ...
    def get_by_normalized_email(self, normalized_email: str) -> EmailIdentityRecord | None: ...
    def get_by_normalized_email_for_update(self, normalized_email: str) -> EmailIdentityRecord | None: ...
    def get_primary_for_account_for_update(self, account_id: AccountId) -> EmailIdentityRecord | None: ...
    def replace_unverified_email(self, identity_id: UUID, display_email: str, normalized_email: str, updated_at: datetime) -> bool: ...
    def mark_verified(self, identity_id: UUID, verified_at: datetime) -> bool: ...


@runtime_checkable
class ExternalProviderIdentityRepository(Protocol):
    def add(self, record: ExternalProviderIdentityRecord) -> None: ...
    def get_by_subject(self, provider: AuthenticationProvider, provider_subject: str) -> ExternalProviderIdentityRecord | None: ...


@runtime_checkable
class EmailVerificationChallengeRepository(Protocol):
    def add(self, record: EmailVerificationChallengeRecord) -> None: ...
    def get_consumable_for_update(self, account_id: AccountId, purpose: ChallengePurpose, now: datetime) -> EmailVerificationChallengeRecord | None: ...
    def get_current_for_update(self, account_id: AccountId, purpose: ChallengePurpose) -> EmailVerificationChallengeRecord | None: ...
    def get_by_flow_digest_for_update(self, flow_token_digest: str) -> EmailVerificationChallengeRecord | None: ...
    def supersede(self, challenge_id: UUID, replacement_id: UUID, expected_status_version: int) -> bool: ...
    def increment_attempts(self, challenge_id: UUID, expected_status_version: int) -> int | None: ...
    def consume(self, challenge_id: UUID, consumed_at: datetime, expected_status_version: int) -> bool: ...


@runtime_checkable
class PasswordResetChallengeRepository(Protocol):
    def add(self, record: PasswordResetChallengeRecord) -> None: ...
    def get_by_digest_for_update(self, token_digest: str) -> PasswordResetChallengeRecord | None: ...
    def consume(self, challenge_id: UUID, consumed_at: datetime) -> bool: ...


@runtime_checkable
class RefreshSessionRepository(Protocol):
    def add(self, record: RefreshSessionRecord) -> None: ...
    def get_by_token_digest_for_update(self, token_digest: str) -> RefreshSessionRecord | None: ...
    def list_family(self, token_family_id: UUID) -> Sequence[RefreshSessionRecord]: ...
    def replace_token(self, session_id: RefreshSessionId, expected_digest: str, replacement_digest: str, used_at: datetime) -> bool: ...


@runtime_checkable
class RefreshRotationReceiptRepository(Protocol):
    def add(self, record: RefreshRotationReceiptRecord) -> None: ...
    def get_retry(self, previous_token_digest: str, refresh_request_id: str, now: datetime) -> RefreshRotationReceiptRecord | None: ...


@runtime_checkable
class AuthenticationAttemptStateRepository(Protocol):
    def get_for_update(self, identity_digest: str, risk_bucket_digest: str) -> AuthenticationAttemptStateRecord | None: ...
    def add(self, record: AuthenticationAttemptStateRecord) -> None: ...
    def increment_failure(self, identity_digest: str, risk_bucket_digest: str, now: datetime, progressive_delay_until: datetime | None, locked_until: datetime | None) -> AuthenticationAttemptStateRecord: ...


@runtime_checkable
class SecurityEventRepository(Protocol):
    def append(self, record: SecurityEventRecord) -> None: ...


@runtime_checkable
class AuthenticationEmailOutboxRepository(Protocol):
    def add(self, record: AuthenticationEmailOutboxRecord) -> None: ...
    def claim_available(self, now: datetime, claim_id: UUID, limit: int) -> Sequence[AuthenticationEmailOutboxRecord]: ...


@runtime_checkable
class AuthenticationUnitOfWork(Protocol):
    @property
    def accounts(self) -> UserAccountRepository: ...
    @property
    def email_identities(self) -> EmailIdentityRepository: ...
    @property
    def provider_identities(self) -> ExternalProviderIdentityRepository: ...
    @property
    def verification_challenges(self) -> EmailVerificationChallengeRepository: ...
    @property
    def password_reset_challenges(self) -> PasswordResetChallengeRepository: ...
    @property
    def refresh_sessions(self) -> RefreshSessionRepository: ...
    @property
    def refresh_receipts(self) -> RefreshRotationReceiptRepository: ...
    @property
    def attempt_states(self) -> AuthenticationAttemptStateRepository: ...
    @property
    def security_events(self) -> SecurityEventRepository: ...
    @property
    def email_outbox(self) -> AuthenticationEmailOutboxRepository: ...
    def __enter__(self) -> Self: ...
    def __exit__(self, exception_type: type[BaseException] | None, exception: BaseException | None, traceback: TracebackType | None) -> bool | None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


AuthenticationUnitOfWorkFactory = Callable[[], AuthenticationUnitOfWork]
