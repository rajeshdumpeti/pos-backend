"""Focused SQLAlchemy repositories for authentication persistence."""

from collections.abc import Sequence
from dataclasses import fields
from datetime import datetime, timedelta
from typing import NoReturn
from uuid import UUID, uuid4
from enum import Enum

from sqlalchemy import Select, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.application.authentication_persistence import (
    AuthenticationConstraintError,
    AuthenticationPersistenceUnavailableError,
    DuplicateNormalizedIdentityError,
    DuplicateProviderIdentityError,
    DuplicateRefreshRequestIdError,
    UnexpectedAuthenticationPersistenceError,
)
from app.domain.authentication import (
    AccountId,
    AccountStatus,
    AuthenticationAttemptStateRecord,
    AuthenticationEmailOutboxRecord,
    AuthenticationProvider,
    ChallengeCompletionAction,
    ChallengeId,
    ChallengePurpose,
    EmailIdentityRecord,
    EmailVerificationChallengeRecord,
    ExternalProviderIdentityRecord,
    IdentityId,
    OutboxMessageKind,
    OutboxStatus,
    PasswordResetChallengeRecord,
    RefreshRotationReceiptRecord,
    RefreshSessionId,
    RefreshSessionRecord,
    SecurityEventRecord,
    UserAccountRecord,
)
from app.infrastructure.authentication.models import (
    AuthenticationAttemptStateModel,
    AuthenticationEmailOutboxModel,
    EmailIdentityModel,
    EmailVerificationChallengeModel,
    ExternalProviderIdentityModel,
    PasswordResetChallengeModel,
    RefreshRotationRetryReceiptModel,
    RefreshSessionModel,
    SecurityEventModel,
    UserAccountModel,
)
from app.infrastructure.database_diagnostics import raise_safe_database_error


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _raise_integrity_error(error: IntegrityError) -> NoReturn:
    name = _constraint_name(error) or ""
    if "email_identities" in name and "normalized_email" in name:
        raise_safe_database_error(
            DuplicateNormalizedIdentityError,
            "Normalized identity already exists",
            error,
        )
    if "external_provider_identities" in name and "provider_subject" in name:
        raise_safe_database_error(
            DuplicateProviderIdentityError,
            "Provider identity already exists",
            error,
        )
    if name == "uq_refresh_rotation_retry_receipts__refresh_request_id":
        raise_safe_database_error(
            DuplicateRefreshRequestIdError,
            "Refresh request identifier already exists",
            error,
        )
    raise_safe_database_error(
        AuthenticationConstraintError,
        "Authentication persistence constraint was rejected",
        error,
    )


def _flush(session: Session) -> None:
    """Expose database invariants before a use case chooses to commit."""
    try:
        session.flush()
    except IntegrityError as error:
        _raise_integrity_error(error)
    except SQLAlchemyError as error:
        raise_safe_database_error(
            AuthenticationPersistenceUnavailableError,
            "Authentication persistence operation failed",
            error,
        )


def _record_values(record: object) -> dict[str, object]:
    values = {field.name: getattr(record, field.name) for field in fields(record)}
    return {
        name: value.value if isinstance(value, Enum) else value
        for name, value in values.items()
    }


def _execute_one(session: Session, statement: Select[tuple[object]]) -> object | None:
    try:
        return session.execute(statement).scalar_one_or_none()
    except SQLAlchemyError as error:
        raise_safe_database_error(
            AuthenticationPersistenceUnavailableError,
            "Authentication persistence operation failed",
            error,
        )


class SqlAlchemyUserAccountRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: UserAccountRecord) -> None:
        self._session.add(UserAccountModel(**_record_values(record)))
        _flush(self._session)

    def get(self, account_id: AccountId) -> UserAccountRecord | None:
        model = self._session.get(UserAccountModel, account_id)
        return None if model is None else _account_record(model)

    def get_for_update(self, account_id: AccountId) -> UserAccountRecord | None:
        model = _execute_one(
            self._session,
            select(UserAccountModel)
            .where(UserAccountModel.id == account_id)
            .with_for_update(),
        )
        return None if model is None else _account_record(model)

    def activate_pending(self, account_id: AccountId, verified_at: datetime) -> bool:
        try:
            result = self._session.execute(
                update(UserAccountModel)
                .where(
                    UserAccountModel.id == account_id,
                    UserAccountModel.status == AccountStatus.PENDING_VERIFICATION.value,
                )
                .values(
                    status=AccountStatus.ACTIVE.value,
                    email_verified_at=verified_at,
                    updated_at=verified_at,
                )
            )
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                error,
            )
        return result.rowcount == 1

    def update_password_hash(
        self, account_id: AccountId, password_hash: str, updated_at: datetime
    ) -> bool:
        try:
            result = self._session.execute(
                update(UserAccountModel)
                .where(UserAccountModel.id == account_id)
                .values(password_hash=password_hash, updated_at=updated_at)
            )
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                error,
            )
        return result.rowcount == 1

    def replace_password_and_increment_epoch(
        self, account_id: AccountId, password_hash: str, updated_at: datetime
    ) -> bool:
        try:
            result = self._session.execute(
                update(UserAccountModel)
                .where(UserAccountModel.id == account_id)
                .values(
                    password_hash=password_hash,
                    authentication_epoch=UserAccountModel.authentication_epoch + 1,
                    updated_at=updated_at,
                )
            )
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                error,
            )
        return result.rowcount == 1


class SqlAlchemyEmailIdentityRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: EmailIdentityRecord) -> None:
        self._session.add(EmailIdentityModel(**_record_values(record)))
        _flush(self._session)

    def get_by_normalized_email(self, normalized_email: str) -> EmailIdentityRecord | None:
        model = _execute_one(
            self._session,
            select(EmailIdentityModel).where(
                EmailIdentityModel.normalized_email == normalized_email
            ),
        )
        return None if model is None else _email_record(model)

    def get_by_normalized_email_for_update(
        self, normalized_email: str
    ) -> EmailIdentityRecord | None:
        model = _execute_one(
            self._session,
            select(EmailIdentityModel)
            .where(EmailIdentityModel.normalized_email == normalized_email)
            .with_for_update(),
        )
        return None if model is None else _email_record(model)

    def get_primary_for_account_for_update(
        self, account_id: AccountId
    ) -> EmailIdentityRecord | None:
        model = _execute_one(
            self._session,
            select(EmailIdentityModel)
            .where(
                EmailIdentityModel.user_account_id == account_id,
                EmailIdentityModel.is_primary.is_(True),
            )
            .with_for_update(),
        )
        return None if model is None else _email_record(model)

    def replace_unverified_email(
        self,
        identity_id: UUID,
        display_email: str,
        normalized_email: str,
        updated_at: datetime,
    ) -> bool:
        try:
            result = self._session.execute(
                update(EmailIdentityModel)
                .where(
                    EmailIdentityModel.id == identity_id,
                    EmailIdentityModel.is_verified.is_(False),
                )
                .values(
                    display_email=display_email,
                    normalized_email=normalized_email,
                    updated_at=updated_at,
                )
            )
            _flush(self._session)
        except IntegrityError as error:
            _raise_integrity_error(error)
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                error,
            )
        return result.rowcount == 1

    def mark_verified(self, identity_id: UUID, verified_at: datetime) -> bool:
        try:
            result = self._session.execute(
                update(EmailIdentityModel)
                .where(
                    EmailIdentityModel.id == identity_id,
                    EmailIdentityModel.is_verified.is_(False),
                )
                .values(
                    is_verified=True,
                    verified_at=verified_at,
                    updated_at=verified_at,
                )
            )
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                error,
            )
        return result.rowcount == 1


class SqlAlchemyExternalProviderIdentityRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: ExternalProviderIdentityRecord) -> None:
        self._session.add(ExternalProviderIdentityModel(**_record_values(record)))
        _flush(self._session)

    def get_by_subject(
        self, provider: AuthenticationProvider, provider_subject: str
    ) -> ExternalProviderIdentityRecord | None:
        model = _execute_one(
            self._session,
            select(ExternalProviderIdentityModel).where(
                ExternalProviderIdentityModel.provider == provider.value,
                ExternalProviderIdentityModel.provider_subject == provider_subject,
            ),
        )
        return None if model is None else _provider_record(model)


class SqlAlchemyEmailVerificationChallengeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: EmailVerificationChallengeRecord) -> None:
        self._session.add(EmailVerificationChallengeModel(**_record_values(record)))
        _flush(self._session)

    def get_consumable_for_update(
        self, account_id: AccountId, purpose: ChallengePurpose, now: datetime
    ) -> EmailVerificationChallengeRecord | None:
        model = _execute_one(
            self._session,
            select(EmailVerificationChallengeModel)
            .where(
                EmailVerificationChallengeModel.user_account_id == account_id,
                EmailVerificationChallengeModel.purpose == purpose.value,
                EmailVerificationChallengeModel.consumed_at.is_(None),
                EmailVerificationChallengeModel.superseded_by_id.is_(None),
                EmailVerificationChallengeModel.expires_at > now,
            )
            .with_for_update(),
        )
        return None if model is None else _verification_record(model)

    def get_current_for_update(
        self, account_id: AccountId, purpose: ChallengePurpose
    ) -> EmailVerificationChallengeRecord | None:
        model = _execute_one(
            self._session,
            select(EmailVerificationChallengeModel)
            .where(
                EmailVerificationChallengeModel.user_account_id == account_id,
                EmailVerificationChallengeModel.purpose == purpose.value,
                EmailVerificationChallengeModel.consumed_at.is_(None),
                EmailVerificationChallengeModel.superseded_by_id.is_(None),
            )
            .with_for_update(),
        )
        return None if model is None else _verification_record(model)

    def get_by_flow_digest_for_update(
        self, flow_token_digest: str
    ) -> EmailVerificationChallengeRecord | None:
        model = _execute_one(
            self._session,
            select(EmailVerificationChallengeModel)
            .where(
                EmailVerificationChallengeModel.flow_token_digest
                == flow_token_digest
            )
            .with_for_update(),
        )
        return None if model is None else _verification_record(model)

    def supersede(
        self,
        challenge_id: UUID,
        replacement_id: UUID,
        expected_status_version: int,
    ) -> bool:
        try:
            result = self._session.execute(
                update(EmailVerificationChallengeModel)
                .where(
                    EmailVerificationChallengeModel.id == challenge_id,
                    EmailVerificationChallengeModel.consumed_at.is_(None),
                    EmailVerificationChallengeModel.superseded_by_id.is_(None),
                    EmailVerificationChallengeModel.status_version
                    == expected_status_version,
                )
                .values(
                    superseded_by_id=replacement_id,
                    status_version=expected_status_version + 1,
                )
            )
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                error,
            )
        return result.rowcount == 1

    def rotate_flow_token(
        self,
        challenge_id: UUID,
        expected_status_version: int,
        flow_token_digest: str,
        digest_key_id: str,
        digest_key_version: int,
    ) -> bool:
        try:
            result = self._session.execute(
                update(EmailVerificationChallengeModel)
                .where(
                    EmailVerificationChallengeModel.id == challenge_id,
                    EmailVerificationChallengeModel.consumed_at.is_(None),
                    EmailVerificationChallengeModel.superseded_by_id.is_(None),
                    EmailVerificationChallengeModel.status_version
                    == expected_status_version,
                )
                .values(
                    flow_token_digest=flow_token_digest,
                    digest_key_id=digest_key_id,
                    digest_key_version=digest_key_version,
                    status_version=expected_status_version + 1,
                )
            )
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                error,
            )
        return result.rowcount == 1

    def increment_attempts(
        self, challenge_id: UUID, expected_status_version: int
    ) -> int | None:
        try:
            attempts = self._session.execute(
                update(EmailVerificationChallengeModel)
                .where(
                    EmailVerificationChallengeModel.id == challenge_id,
                    EmailVerificationChallengeModel.consumed_at.is_(None),
                    EmailVerificationChallengeModel.superseded_by_id.is_(None),
                    EmailVerificationChallengeModel.status_version
                    == expected_status_version,
                    EmailVerificationChallengeModel.attempts
                    < EmailVerificationChallengeModel.max_attempts,
                )
                .values(
                    attempts=EmailVerificationChallengeModel.attempts + 1,
                    status_version=expected_status_version + 1,
                )
                .returning(EmailVerificationChallengeModel.attempts)
            ).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                error,
            )
        return attempts

    def consume(
        self, challenge_id: UUID, consumed_at: datetime, expected_status_version: int
    ) -> bool:
        try:
            result = self._session.execute(
                update(EmailVerificationChallengeModel)
                .where(
                    EmailVerificationChallengeModel.id == challenge_id,
                    EmailVerificationChallengeModel.consumed_at.is_(None),
                    EmailVerificationChallengeModel.status_version
                    == expected_status_version,
                )
                .values(
                    consumed_at=consumed_at,
                    status_version=expected_status_version + 1,
                )
            )
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                error,
            )
        return result.rowcount == 1


class SqlAlchemyPasswordResetChallengeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: PasswordResetChallengeRecord) -> None:
        self._session.add(PasswordResetChallengeModel(**_record_values(record)))
        _flush(self._session)

    def get_by_digest_for_update(
        self, token_digest: str
    ) -> PasswordResetChallengeRecord | None:
        model = _execute_one(
            self._session,
            select(PasswordResetChallengeModel)
            .where(PasswordResetChallengeModel.token_digest == token_digest)
            .with_for_update(),
        )
        return None if model is None else _reset_record(model)

    def consume(self, challenge_id: UUID, consumed_at: datetime) -> bool:
        try:
            result = self._session.execute(
                update(PasswordResetChallengeModel)
                .where(
                    PasswordResetChallengeModel.id == challenge_id,
                    PasswordResetChallengeModel.consumed_at.is_(None),
                )
                .values(consumed_at=consumed_at)
            )
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                error,
            )
        return result.rowcount == 1

    def consume_active_for_account(
        self, account_id: AccountId, consumed_at: datetime
    ) -> int:
        try:
            result = self._session.execute(
                update(PasswordResetChallengeModel)
                .where(
                    PasswordResetChallengeModel.user_account_id == account_id,
                    PasswordResetChallengeModel.consumed_at.is_(None),
                )
                .values(consumed_at=consumed_at)
            )
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                error,
            )
        return result.rowcount


class SqlAlchemyRefreshSessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: RefreshSessionRecord) -> None:
        self._session.add(RefreshSessionModel(**_record_values(record)))
        _flush(self._session)

    def get_by_token_digest_for_update(
        self, token_digest: str
    ) -> RefreshSessionRecord | None:
        model = _execute_one(
            self._session,
            select(RefreshSessionModel)
            .where(RefreshSessionModel.current_token_digest == token_digest)
            .with_for_update(),
        )
        return None if model is None else _refresh_record(model)

    def get_for_update(
        self, session_id: RefreshSessionId
    ) -> RefreshSessionRecord | None:
        model = _execute_one(
            self._session,
            select(RefreshSessionModel)
            .where(RefreshSessionModel.id == session_id)
            .with_for_update(),
        )
        return None if model is None else _refresh_record(model)

    def list_family(self, token_family_id: UUID) -> Sequence[RefreshSessionRecord]:
        try:
            models = self._session.scalars(
                select(RefreshSessionModel).where(
                    RefreshSessionModel.token_family_id == token_family_id
                )
            ).all()
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                error,
            )
        return [_refresh_record(model) for model in models]

    def replace_token(
        self,
        session_id: RefreshSessionId,
        expected_digest: str,
        replacement_digest: str,
        used_at: datetime,
    ) -> bool:
        try:
            result = self._session.execute(
                update(RefreshSessionModel)
                .where(
                    RefreshSessionModel.id == session_id,
                    RefreshSessionModel.current_token_digest == expected_digest,
                    RefreshSessionModel.revoked_at.is_(None),
                )
                .values(
                    current_token_digest=replacement_digest,
                    last_used_at=used_at,
                )
            )
        except IntegrityError as error:
            _raise_integrity_error(error)
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                error,
            )
        return result.rowcount == 1

    def revoke(
        self, session_id: RefreshSessionId, revoked_at: datetime, reason: str
    ) -> bool:
        return self._revoke_where(
            RefreshSessionModel.id == session_id, revoked_at, reason
        ) == 1

    def revoke_family(
        self, token_family_id: UUID, revoked_at: datetime, reason: str
    ) -> int:
        return self._revoke_where(
            RefreshSessionModel.token_family_id == token_family_id,
            revoked_at,
            reason,
        )

    def revoke_all_for_account(
        self, account_id: AccountId, revoked_at: datetime, reason: str
    ) -> int:
        return self._revoke_where(
            RefreshSessionModel.user_account_id == account_id, revoked_at, reason
        )

    def _revoke_where(self, predicate: object, revoked_at: datetime, reason: str) -> int:
        try:
            result = self._session.execute(
                update(RefreshSessionModel)
                .where(predicate, RefreshSessionModel.revoked_at.is_(None))
                .values(revoked_at=revoked_at, revocation_reason=reason)
            )
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                error,
            )
        return result.rowcount


class SqlAlchemyRefreshRotationReceiptRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: RefreshRotationReceiptRecord) -> None:
        self._session.add(RefreshRotationRetryReceiptModel(**_record_values(record)))
        _flush(self._session)

    def get_retry(
        self, previous_token_digest: str, refresh_request_id: str, now: datetime
    ) -> RefreshRotationReceiptRecord | None:
        model = _execute_one(
            self._session,
            select(RefreshRotationRetryReceiptModel).where(
                RefreshRotationRetryReceiptModel.previous_token_digest
                == previous_token_digest,
                RefreshRotationRetryReceiptModel.refresh_request_id
                == refresh_request_id,
                RefreshRotationRetryReceiptModel.expires_at > now,
            ),
        )
        return None if model is None else _receipt_record(model)

    def get_by_previous_digest_for_update(
        self, previous_token_digest: str
    ) -> RefreshRotationReceiptRecord | None:
        model = _execute_one(
            self._session,
            select(RefreshRotationRetryReceiptModel)
            .where(
                RefreshRotationRetryReceiptModel.previous_token_digest
                == previous_token_digest
            )
            .with_for_update(),
        )
        return None if model is None else _receipt_record(model)

    def get_by_request_id_for_update(
        self, refresh_request_id: str
    ) -> RefreshRotationReceiptRecord | None:
        model = _execute_one(
            self._session,
            select(RefreshRotationRetryReceiptModel)
            .where(
                RefreshRotationRetryReceiptModel.refresh_request_id
                == refresh_request_id
            )
            .with_for_update(),
        )
        return None if model is None else _receipt_record(model)


class SqlAlchemyAuthenticationAttemptStateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: AuthenticationAttemptStateRecord) -> None:
        self._session.add(AuthenticationAttemptStateModel(**_record_values(record)))
        _flush(self._session)

    def get_for_update(
        self, identity_digest: str, risk_bucket_digest: str
    ) -> AuthenticationAttemptStateRecord | None:
        model = _execute_one(
            self._session,
            select(AuthenticationAttemptStateModel)
            .where(
                AuthenticationAttemptStateModel.identity_digest == identity_digest,
                AuthenticationAttemptStateModel.risk_bucket_digest
                == risk_bucket_digest,
            )
            .with_for_update(),
        )
        return None if model is None else _attempt_record(model)

    def increment_failure(
        self,
        identity_digest: str,
        risk_bucket_digest: str,
        now: datetime,
        progressive_delay_until: datetime | None,
        locked_until: datetime | None,
    ) -> AuthenticationAttemptStateRecord:
        statement = (
            insert(AuthenticationAttemptStateModel)
            .values(
                id=uuid4(),
                identity_digest=identity_digest,
                risk_bucket_digest=risk_bucket_digest,
                failed_attempts=1,
                window_started_at=now,
                last_attempt_at=now,
                progressive_delay_until=progressive_delay_until,
                locked_until=locked_until,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_authentication_attempt_states__identity_digest__62b111e31b",
                set_={
                    "failed_attempts": AuthenticationAttemptStateModel.failed_attempts + 1,
                    "last_attempt_at": now,
                    "progressive_delay_until": progressive_delay_until,
                    "locked_until": locked_until,
                    "updated_at": now,
                },
            )
            .returning(AuthenticationAttemptStateModel)
        )
        try:
            model = self._session.execute(statement).scalar_one()
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                error,
            )
        return _attempt_record(model)


class SqlAlchemySecurityEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, record: SecurityEventRecord) -> None:
        # Revalidate mutable metadata immediately before persistence.
        record.__post_init__()
        self._session.add(SecurityEventModel(**_record_values(record)))
        _flush(self._session)


class SqlAlchemyAuthenticationEmailOutboxRepository:
    _CLAIM_LEASE = timedelta(minutes=5)
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: AuthenticationEmailOutboxRecord) -> None:
        self._session.add(AuthenticationEmailOutboxModel(**_record_values(record)))
        _flush(self._session)

    def claim_available(
        self, now: datetime, claim_id: UUID, limit: int
    ) -> Sequence[AuthenticationEmailOutboxRecord]:
        if limit <= 0:
            return []
        try:
            models = self._session.scalars(
                select(AuthenticationEmailOutboxModel)
                .where(
                    or_(
                        (
                            AuthenticationEmailOutboxModel.status
                            == OutboxStatus.PENDING.value
                        )
                        & (AuthenticationEmailOutboxModel.available_at <= now),
                        (
                            AuthenticationEmailOutboxModel.status
                            == OutboxStatus.CLAIMED.value
                        )
                        & (
                            AuthenticationEmailOutboxModel.claimed_at
                            <= now - self._CLAIM_LEASE
                        ),
                    ),
                )
                .order_by(AuthenticationEmailOutboxModel.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for model in models:
                model.status = OutboxStatus.CLAIMED.value
                model.claimed_at = now
                model.claim_id = claim_id
            _flush(self._session)
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                error,
            )
        return [_outbox_record(model) for model in models]

    def mark_delivered(
        self, outbox_id: UUID, claim_id: UUID, delivered_at: datetime,
        provider_message_id: str,
    ) -> bool:
        return self._mark_claimed(
            outbox_id, claim_id, status=OutboxStatus.DELIVERED,
            delivered_at=delivered_at, provider_message_id=provider_message_id,
        )

    def mark_failed(
        self, outbox_id: UUID, claim_id: UUID, failed_at: datetime, category: str
    ) -> bool:
        return self._mark_claimed(
            outbox_id, claim_id, status=OutboxStatus.FAILED,
            failed_at=failed_at, failure_category=category,
        )

    def release_for_retry(
        self, outbox_id: UUID, claim_id: UUID, available_at: datetime, category: str
    ) -> bool:
        try:
            result = self._session.execute(
                update(AuthenticationEmailOutboxModel)
                .where(
                    AuthenticationEmailOutboxModel.id == outbox_id,
                    AuthenticationEmailOutboxModel.status == OutboxStatus.CLAIMED.value,
                    AuthenticationEmailOutboxModel.claim_id == claim_id,
                )
                .values(
                    status=OutboxStatus.PENDING.value,
                    available_at=available_at,
                    claimed_at=None,
                    claim_id=None,
                    failure_category=category,
                    attempt_count=AuthenticationEmailOutboxModel.attempt_count + 1,
                )
            )
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed", error,
            )
        return result.rowcount == 1

    def _mark_claimed(
        self, outbox_id: UUID, claim_id: UUID, *, status: OutboxStatus,
        delivered_at: datetime | None = None,
        failed_at: datetime | None = None,
        provider_message_id: str | None = None,
        failure_category: str | None = None,
    ) -> bool:
        try:
            result = self._session.execute(
                update(AuthenticationEmailOutboxModel)
                .where(
                    AuthenticationEmailOutboxModel.id == outbox_id,
                    AuthenticationEmailOutboxModel.status == OutboxStatus.CLAIMED.value,
                    AuthenticationEmailOutboxModel.claim_id == claim_id,
                )
                .values(
                    status=status.value, delivered_at=delivered_at,
                    failed_at=failed_at, provider_message_id=provider_message_id,
                    failure_category=failure_category,
                    attempt_count=AuthenticationEmailOutboxModel.attempt_count + 1,
                )
            )
        except SQLAlchemyError as error:
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed", error,
            )
        return result.rowcount == 1


def _account_record(model: object) -> UserAccountRecord:
    if not isinstance(model, UserAccountModel):
        raise UnexpectedAuthenticationPersistenceError("Unexpected account record")
    return UserAccountRecord(
        id=AccountId(model.id), status=AccountStatus(model.status), display_name=model.display_name,
        password_hash=model.password_hash, email_verified_at=model.email_verified_at,
        onboarding_required=model.onboarding_required, authentication_epoch=model.authentication_epoch,
        terms_version=model.terms_version, terms_accepted_at=model.terms_accepted_at,
        privacy_version=model.privacy_version, privacy_accepted_at=model.privacy_accepted_at,
        deletion_requested_at=model.deletion_requested_at, deleted_at=model.deleted_at,
        created_at=model.created_at, updated_at=model.updated_at,
    )


def _email_record(model: object) -> EmailIdentityRecord:
    assert isinstance(model, EmailIdentityModel)
    return EmailIdentityRecord(id=IdentityId(model.id), user_account_id=AccountId(model.user_account_id), display_email=model.display_email, normalized_email=model.normalized_email, is_verified=model.is_verified, is_primary=model.is_primary, verified_at=model.verified_at, created_at=model.created_at, updated_at=model.updated_at)


def _provider_record(model: object) -> ExternalProviderIdentityRecord:
    assert isinstance(model, ExternalProviderIdentityModel)
    return ExternalProviderIdentityRecord(id=IdentityId(model.id), user_account_id=AccountId(model.user_account_id), provider=AuthenticationProvider(model.provider), provider_subject=model.provider_subject, email_snapshot=model.email_snapshot, email_verified=model.email_verified, is_private_relay=model.is_private_relay, first_authenticated_at=model.first_authenticated_at, last_authenticated_at=model.last_authenticated_at, created_at=model.created_at, updated_at=model.updated_at)


def _verification_record(model: object) -> EmailVerificationChallengeRecord:
    assert isinstance(model, EmailVerificationChallengeModel)
    return EmailVerificationChallengeRecord(id=ChallengeId(model.id), user_account_id=AccountId(model.user_account_id), purpose=ChallengePurpose(model.purpose), flow_token_digest=model.flow_token_digest, code_digest=model.code_digest, target_normalized_email=model.target_normalized_email, issued_at=model.issued_at, expires_at=model.expires_at, flow_expires_at=model.flow_expires_at, resend_available_at=model.resend_available_at, digest_key_id=model.digest_key_id, digest_key_version=model.digest_key_version, completion_action=ChallengeCompletionAction(model.completion_action), attempts=model.attempts, max_attempts=model.max_attempts, status_version=model.status_version, consumed_at=model.consumed_at, superseded_by_id=None if model.superseded_by_id is None else ChallengeId(model.superseded_by_id))


def _reset_record(model: object) -> PasswordResetChallengeRecord:
    assert isinstance(model, PasswordResetChallengeModel)
    return PasswordResetChallengeRecord(id=ChallengeId(model.id), user_account_id=AccountId(model.user_account_id), token_digest=model.token_digest, request_security_digest=model.request_security_digest, issued_at=model.issued_at, expires_at=model.expires_at, consumed_at=model.consumed_at)


def _refresh_record(model: object) -> RefreshSessionRecord:
    assert isinstance(model, RefreshSessionModel)
    return RefreshSessionRecord(id=RefreshSessionId(model.id), user_account_id=AccountId(model.user_account_id), token_family_id=model.token_family_id, current_token_digest=model.current_token_digest, remember_me=model.remember_me, authentication_epoch=model.authentication_epoch, device_metadata=model.device_metadata, created_at=model.created_at, last_used_at=model.last_used_at, expires_at=model.expires_at, revoked_at=model.revoked_at, revocation_reason=model.revocation_reason)


def _receipt_record(model: object) -> RefreshRotationReceiptRecord:
    assert isinstance(model, RefreshRotationRetryReceiptModel)
    return RefreshRotationReceiptRecord(id=model.id, refresh_session_id=RefreshSessionId(model.refresh_session_id), token_family_id=model.token_family_id, previous_token_digest=model.previous_token_digest, refresh_request_id=model.refresh_request_id, response_ciphertext=model.response_ciphertext, encryption_key_id=model.encryption_key_id, encryption_key_version=model.encryption_key_version, created_at=model.created_at, expires_at=model.expires_at, consumed_at=model.consumed_at)


def _attempt_record(model: object) -> AuthenticationAttemptStateRecord:
    assert isinstance(model, AuthenticationAttemptStateModel)
    return AuthenticationAttemptStateRecord(id=model.id, identity_digest=model.identity_digest, risk_bucket_digest=model.risk_bucket_digest, failed_attempts=model.failed_attempts, window_started_at=model.window_started_at, last_attempt_at=model.last_attempt_at, progressive_delay_until=model.progressive_delay_until, locked_until=model.locked_until, created_at=model.created_at, updated_at=model.updated_at)


def _outbox_record(model: AuthenticationEmailOutboxModel) -> AuthenticationEmailOutboxRecord:
    return AuthenticationEmailOutboxRecord(id=model.id, user_account_id=None if model.user_account_id is None else AccountId(model.user_account_id), message_kind=OutboxMessageKind(model.message_kind), recipient_normalized_email=model.recipient_normalized_email, payload_ciphertext=model.payload_ciphertext, encryption_key_id=model.encryption_key_id, encryption_key_version=model.encryption_key_version, deduplication_key=model.deduplication_key, status=OutboxStatus(model.status), created_at=model.created_at, available_at=model.available_at, claimed_at=model.claimed_at, claim_id=model.claim_id, attempt_count=model.attempt_count, delivered_at=model.delivered_at, failed_at=model.failed_at, provider_message_id=model.provider_message_id, failure_category=model.failure_category)
