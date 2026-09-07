"""Enumeration-resistant password recovery use cases."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from app.application.authentication_persistence import (
    AuthenticationPersistenceUnavailableError,
    AuthenticationUnitOfWork,
)
from app.application.email_registration import (
    AuthenticationCryptographyError,
    AuthenticationEventLogger,
    CompromisedPasswordChecker,
    EmailNormalizer,
    EncryptedOutboxPayload,
    PasswordHasher,
)
from app.domain.authentication import (
    AccountId,
    AccountStatus,
    AuthenticationEmailOutboxRecord,
    ChallengeId,
    OutboxMessageKind,
    OutboxStatus,
    PasswordResetChallengeRecord,
    SecurityEventRecord,
)
from app.domain.registration import EmailPolicyError, PasswordPolicyError, require_password_length


class PasswordRecoveryError(RuntimeError):
    """Safe base class translated at the HTTP boundary."""


class ResetTokenInvalidError(PasswordRecoveryError):
    pass


class ResetTokenExpiredError(PasswordRecoveryError):
    pass


class ResetPasswordRejectedError(PasswordRecoveryError):
    pass


class PasswordRecoveryDependencyUnavailableError(PasswordRecoveryError):
    pass


class PasswordRecoveryInternalError(PasswordRecoveryError):
    pass


class RedactedPasswordRecoveryCause(RuntimeError):
    """Safe traceback node retaining sensitive internals only out of band."""


def _raise_internal(message: str, internal_cause: BaseException) -> None:
    error = PasswordRecoveryInternalError(message)
    error._internal_cause = internal_cause  # type: ignore[attr-defined]
    raise error from RedactedPasswordRecoveryCause(
        f"{type(internal_cause).__name__} details redacted"
    )


@dataclass(frozen=True, slots=True)
class IssuedPasswordResetMaterial:
    token: str = field(repr=False)
    token_digest: str = field(repr=False)
    request_security_digest: str = field(repr=False)


class PasswordResetMaterialProvider(Protocol):
    def issue(self, challenge_id: UUID, request_id: str) -> IssuedPasswordResetMaterial: ...
    def lookup_digest(self, token: str) -> str: ...


@dataclass(frozen=True, slots=True)
class PasswordResetEmailMessage:
    token: str = field(repr=False)
    reset_url: str = field(repr=False)
    expires_at: datetime


class PasswordResetOutboxCipher(Protocol):
    def encrypt_password_reset(
        self, message: PasswordResetEmailMessage, *, context: bytes
    ) -> EncryptedOutboxPayload: ...


@dataclass(frozen=True, slots=True)
class RequestPasswordResetCommand:
    email: str = field(repr=False)
    request_id: str


@dataclass(frozen=True, slots=True)
class ConfirmPasswordResetCommand:
    token: str = field(repr=False)
    new_password: str = field(repr=False)
    request_id: str


@dataclass(frozen=True, slots=True)
class PasswordResetAcceptedResult:
    status: str = "instructions_if_eligible"


@dataclass(frozen=True, slots=True)
class PasswordRecoveryPolicy:
    reset_url: str = field(repr=False)
    token_lifetime: timedelta = timedelta(minutes=30)


class PasswordRecoveryService:
    """Owns reset issuance and confirmation transactions."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[str], AuthenticationUnitOfWork],
        password_hasher: PasswordHasher,
        compromised_passwords: CompromisedPasswordChecker,
        email_normalizer: EmailNormalizer,
        material: PasswordResetMaterialProvider,
        outbox_cipher: PasswordResetOutboxCipher,
        event_logger: AuthenticationEventLogger,
        policy: PasswordRecoveryPolicy,
        *,
        clock: Callable[[], datetime] | None = None,
        identifier_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._password_hasher = password_hasher
        self._compromised_passwords = compromised_passwords
        self._email_normalizer = email_normalizer
        self._material = material
        self._outbox_cipher = outbox_cipher
        self._event_logger = event_logger
        self._policy = policy
        self._clock = clock or (lambda: datetime.now(UTC))
        self._identifier_factory = identifier_factory

    def request(self, command: RequestPasswordResetCommand) -> PasswordResetAcceptedResult:
        try:
            now = self._clock()
            challenge_id = ChallengeId(self._identifier_factory())
            issued = self._material.issue(challenge_id, command.request_id)
            expires_at = now + self._policy.token_lifetime
            context = f"password-reset:{challenge_id}".encode("ascii")
            encrypted = self._outbox_cipher.encrypt_password_reset(
                PasswordResetEmailMessage(
                    issued.token, self._policy.reset_url, expires_at
                ),
                context=context,
            )
            try:
                normalized = self._email_normalizer.normalize(command.email)
            except EmailPolicyError:
                normalized = None
            with self._unit_of_work_factory(command.request_id) as unit:
                identity = None
                account = None
                if normalized is not None:
                    identity = unit.email_identities.get_by_normalized_email_for_update(
                        normalized.comparison
                    )
                    if identity is not None:
                        account = unit.accounts.get_for_update(identity.user_account_id)
                eligible = (
                    identity is not None
                    and account is not None
                    and identity.is_verified
                    and account.status is AccountStatus.ACTIVE
                    and account.password_hash is not None
                )
                if eligible:
                    assert identity is not None and account is not None
                    unit.password_reset_challenges.consume_active_for_account(
                        account.id, now
                    )
                    unit.password_reset_challenges.add(
                        PasswordResetChallengeRecord(
                            id=challenge_id,
                            user_account_id=account.id,
                            token_digest=issued.token_digest,
                            request_security_digest=issued.request_security_digest,
                            issued_at=now,
                            expires_at=expires_at,
                        )
                    )
                    unit.email_outbox.add(
                        AuthenticationEmailOutboxRecord(
                            id=self._identifier_factory(),
                            user_account_id=account.id,
                            message_kind=OutboxMessageKind.PASSWORD_RESET,
                            recipient_normalized_email=identity.normalized_email,
                            payload_ciphertext=encrypted.ciphertext,
                            encryption_key_id=encrypted.key_id,
                            encryption_key_version=encrypted.key_version,
                            deduplication_key=f"password-reset:{challenge_id}",
                            status=OutboxStatus.PENDING,
                            created_at=now,
                            available_at=now,
                        )
                    )
                    unit.security_events.append(
                        self._security_event("password_reset_requested", command, now, account.id)
                    )
                    unit.commit()
        except AuthenticationPersistenceUnavailableError as error:
            self._failure("password_reset_request", command.request_id, "dependency")
            raise PasswordRecoveryDependencyUnavailableError(
                "Password recovery dependency is unavailable"
            ) from error
        except AuthenticationCryptographyError:
            self._failure("password_reset_request", command.request_id, "cryptography")
            raise
        except PasswordRecoveryError:
            raise
        except Exception as error:
            self._failure("password_reset_request", command.request_id, "internal")
            _raise_internal("Password recovery failed", error)
        self._event_logger.emit(
            event="authentication_operation", operation="password_reset_request",
            outcome="accepted", request_id=command.request_id,
        )
        return PasswordResetAcceptedResult()

    def confirm(self, command: ConfirmPasswordResetCommand) -> None:
        try:
            digest = self._material.lookup_digest(command.token)
        except ValueError as error:
            raise ResetTokenInvalidError("Reset token is invalid") from error
        self._require_password(command.new_password)
        replacement = self._password_hasher.hash(command.new_password)
        now = self._clock()
        try:
            with self._unit_of_work_factory(command.request_id) as unit:
                challenge = unit.password_reset_challenges.get_by_digest_for_update(digest)
                if challenge is None or challenge.consumed_at is not None:
                    raise ResetTokenInvalidError("Reset token is invalid")
                if now >= challenge.expires_at:
                    raise ResetTokenExpiredError("Reset token expired")
                account = unit.accounts.get_for_update(challenge.user_account_id)
                if account is None or account.status is not AccountStatus.ACTIVE:
                    raise ResetTokenInvalidError("Reset token is invalid")
                if not unit.accounts.replace_password_and_increment_epoch(
                    account.id, replacement, now
                ):
                    raise PasswordRecoveryInternalError("Password reset failed")
                if not unit.password_reset_challenges.consume(challenge.id, now):
                    raise ResetTokenInvalidError("Reset token is invalid")
                unit.refresh_sessions.revoke_all_for_account(
                    account.id, now, "password_reset"
                )
                unit.security_events.append(
                    self._security_event("password_reset_succeeded", command, now, account.id)
                )
                unit.commit()
        except AuthenticationPersistenceUnavailableError as error:
            self._failure("password_reset_confirm", command.request_id, "dependency")
            raise PasswordRecoveryDependencyUnavailableError(
                "Password recovery dependency is unavailable"
            ) from error
        except PasswordRecoveryError:
            raise
        except AuthenticationCryptographyError:
            raise
        except Exception as error:
            self._failure("password_reset_confirm", command.request_id, "internal")
            _raise_internal("Password reset failed", error)
        self._event_logger.emit(
            event="authentication_operation", operation="password_reset_confirm",
            outcome="succeeded", request_id=command.request_id,
        )

    def _require_password(self, password: str) -> None:
        try:
            require_password_length(password)
        except PasswordPolicyError as error:
            raise ResetPasswordRejectedError("Password was rejected") from error
        if self._compromised_passwords.is_compromised(password):
            raise ResetPasswordRejectedError("Password was rejected")

    def _failure(self, operation: str, request_id: str, category: str) -> None:
        self._event_logger.emit(
            event="authentication_operation", operation=operation, outcome="failed",
            request_id=request_id, error_category=category,
        )

    def _security_event(
        self, event_type: str, command: object, now: datetime, account_id: AccountId
    ) -> SecurityEventRecord:
        return SecurityEventRecord(
            id=self._identifier_factory(), event_type=event_type,
            occurred_at=now, user_account_id=account_id,
            request_id=str(getattr(command, "request_id")),
        )
