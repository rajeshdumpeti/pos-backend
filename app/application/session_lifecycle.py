"""Email sign-in and secure session-lifecycle use cases."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from app.application.authentication_persistence import (
    AuthenticationPersistenceUnavailableError,
    AuthenticationUnitOfWork,
)
from app.application.email_registration import EmailNormalizer, PasswordHasher
from app.domain.authentication import (
    AccountId,
    AccountStatus,
    RefreshRotationReceiptRecord,
    RefreshSessionId,
    RefreshSessionRecord,
    SecurityEventRecord,
)


class SessionLifecycleError(RuntimeError):
    """Base safe session use-case error."""


class InvalidCredentialsError(SessionLifecycleError):
    pass


class EmailNotVerifiedError(SessionLifecycleError):
    pass


class AccountUnavailableError(SessionLifecycleError):
    pass


class AccessTokenInvalidError(SessionLifecycleError):
    pass


class AuthenticationRequiredError(SessionLifecycleError):
    pass


class AccessTokenExpiredError(SessionLifecycleError):
    pass


class RefreshSessionInvalidError(SessionLifecycleError):
    pass


class RefreshSessionExpiredError(SessionLifecycleError):
    pass


class RefreshSessionReplayedError(SessionLifecycleError):
    pass


class SessionDependencyUnavailableError(SessionLifecycleError):
    pass


class SessionInternalError(SessionLifecycleError):
    pass


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    account_id: AccountId
    session_id: RefreshSessionId
    authentication_epoch: int


class AccessTokenCodec(Protocol):
    def issue(self, claims: AccessTokenClaims, *, now: datetime) -> tuple[str, datetime]: ...
    def verify(self, token: str, *, now: datetime) -> AccessTokenClaims: ...


@dataclass(frozen=True, slots=True)
class EncryptedRefreshReceipt:
    ciphertext: bytes = field(repr=False)
    key_id: str
    key_version: int


class RefreshReceiptCipher(Protocol):
    def encrypt(self, response: "SessionTokenResult", *, context: bytes) -> EncryptedRefreshReceipt: ...
    def decrypt(self, receipt: RefreshRotationReceiptRecord, *, context: bytes) -> "SessionTokenResult": ...


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    access_lifetime: timedelta = timedelta(minutes=10)
    refresh_lifetime: timedelta = timedelta(days=7)
    remembered_refresh_lifetime: timedelta = timedelta(days=30)
    retry_receipt_lifetime: timedelta = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class SignInCommand:
    email: str = field(repr=False)
    password: str = field(repr=False)
    remember_me: bool
    request_id: str


@dataclass(frozen=True, slots=True)
class RefreshCommand:
    refresh_token: str = field(repr=False)
    refresh_request_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class SessionTokenResult:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    token_type: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    account_id: UUID
    session_id: UUID
    onboarding_required: bool


@dataclass(frozen=True, slots=True)
class CurrentSessionResult:
    account_id: UUID
    session_id: UUID
    display_name: str
    onboarding_required: bool


class SessionTokenGenerator(Protocol):
    def issue(self) -> str: ...
    def digest(self, token: str) -> str: ...


class SessionEventLogger(Protocol):
    def emit(
        self,
        *,
        event: str,
        operation: str,
        outcome: str,
        request_id: str,
        error_category: str | None = None,
        account_id: UUID | None = None,
        challenge_id: UUID | None = None,
        session_id: UUID | None = None,
    ) -> None: ...


class EmailSessionService:
    """Own sign-in, rotation, validation, and revocation transactions."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[str], AuthenticationUnitOfWork],
        password_hasher: PasswordHasher,
        email_normalizer: EmailNormalizer,
        access_tokens: AccessTokenCodec,
        refresh_tokens: SessionTokenGenerator,
        receipt_cipher: RefreshReceiptCipher,
        dummy_password_hash: str,
        policy: SessionPolicy = SessionPolicy(),
        *,
        event_logger: SessionEventLogger | None = None,
        clock: Callable[[], datetime] | None = None,
        identifier_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._password_hasher = password_hasher
        self._email_normalizer = email_normalizer
        self._access_tokens = access_tokens
        self._refresh_tokens = refresh_tokens
        self._receipt_cipher = receipt_cipher
        self._dummy_password_hash = dummy_password_hash
        self._policy = policy
        self._event_logger = event_logger
        self._clock = clock or (lambda: datetime.now(UTC))
        self._identifier_factory = identifier_factory

    def sign_in(self, command: SignInCommand) -> SessionTokenResult:
        if not 1 <= len(command.password) <= 1024 or len(command.email) > 320:
            raise InvalidCredentialsError("Email or password is invalid")
        try:
            normalized = self._email_normalizer.normalize(command.email).comparison
        except ValueError:
            normalized = "invalid@example.invalid"
        now = self._clock()
        try:
            with self._unit_of_work_factory(command.request_id) as unit:
                identity = unit.email_identities.get_by_normalized_email(normalized)
                account = (
                    None
                    if identity is None
                    else unit.accounts.get_for_update(identity.user_account_id)
                )
                password_hash = (
                    self._dummy_password_hash
                    if account is None or account.password_hash is None
                    else account.password_hash
                )
                password_valid = self._password_hasher.verify(
                    password_hash, command.password
                )
                if account is None or identity is None or not password_valid:
                    self._diagnostic("email_sign_in_failed", "email_sign_in", "rejected", command.request_id, "invalid_credentials")
                    raise InvalidCredentialsError("Email or password is invalid")
                if account.status is AccountStatus.PENDING_VERIFICATION or not identity.is_verified:
                    self._diagnostic("email_sign_in_failed", "email_sign_in", "rejected", command.request_id, "email_not_verified", account.id)
                    raise EmailNotVerifiedError("Email verification is required")
                if account.status is not AccountStatus.ACTIVE:
                    self._diagnostic("email_sign_in_failed", "email_sign_in", "rejected", command.request_id, "account_unavailable", account.id)
                    raise AccountUnavailableError("Account is unavailable")
                if self._password_hasher.needs_rehash(password_hash):
                    replacement = self._password_hasher.hash(command.password)
                    if not unit.accounts.update_password_hash(account.id, replacement, now):
                        raise SessionInternalError("Password hash could not be updated")

                session_id = RefreshSessionId(self._identifier_factory())
                family_id = self._identifier_factory()
                refresh_token = self._refresh_tokens.issue()
                refresh_expires_at = now + (
                    self._policy.remembered_refresh_lifetime
                    if command.remember_me
                    else self._policy.refresh_lifetime
                )
                session = RefreshSessionRecord(
                    id=session_id,
                    user_account_id=account.id,
                    token_family_id=family_id,
                    current_token_digest=self._refresh_tokens.digest(refresh_token),
                    remember_me=command.remember_me,
                    authentication_epoch=account.authentication_epoch,
                    created_at=now,
                    last_used_at=now,
                    expires_at=refresh_expires_at,
                )
                unit.refresh_sessions.add(session)
                unit.security_events.append(
                    self._event("email_sign_in_succeeded", command.request_id, now, account.id, session_id)
                )
                access_token, access_expires_at = self._access_tokens.issue(
                    AccessTokenClaims(account.id, session_id, account.authentication_epoch),
                    now=now,
                )
                unit.commit()
            self._diagnostic("email_sign_in_succeeded", "email_sign_in", "succeeded", command.request_id, account_id=account.id)
            return SessionTokenResult(
                access_token,
                refresh_token,
                "Bearer",
                access_expires_at,
                refresh_expires_at,
                account.id,
                session_id,
                account.onboarding_required,
            )
        except AuthenticationPersistenceUnavailableError as error:
            raise SessionDependencyUnavailableError("Session dependency is unavailable") from error

    def refresh(self, command: RefreshCommand) -> SessionTokenResult:
        now = self._clock()
        digest = self._refresh_tokens.digest(command.refresh_token)
        try:
            with self._unit_of_work_factory(command.request_id) as unit:
                session = unit.refresh_sessions.get_by_token_digest_for_update(digest)
                if session is None:
                    receipt = unit.refresh_receipts.get_by_previous_digest_for_update(digest)
                    if receipt is None:
                        raise RefreshSessionInvalidError("Refresh session is invalid")
                    receipt_session = unit.refresh_sessions.get_for_update(
                        receipt.refresh_session_id
                    )
                    receipt_account = (None if receipt_session is None else
                        unit.accounts.get_for_update(receipt_session.user_account_id))
                    if (
                        receipt.refresh_request_id == command.refresh_request_id
                        and receipt.expires_at > now
                        and receipt_session is not None
                        and self._active(receipt_session, receipt_account, now)
                    ):
                        result = self._receipt_cipher.decrypt(
                            receipt, context=self._receipt_context(receipt.id)
                        )
                        self._diagnostic("refresh_retry_succeeded", "refresh_session", "succeeded", command.request_id, "approved_retry", session_id=receipt.refresh_session_id)
                        return result
                    unit.refresh_sessions.revoke_family(
                        receipt.token_family_id, now, "refresh_replay"
                    )
                    unit.security_events.append(
                        self._event(
                            "refresh_token_replayed",
                            command.request_id,
                            now,
                            session_id=receipt.refresh_session_id,
                        )
                    )
                    unit.commit()
                    self._diagnostic("refresh_token_replayed", "refresh_session", "rejected", command.request_id, "replay", session_id=receipt.refresh_session_id)
                    raise RefreshSessionReplayedError("Refresh token replay was detected")
                account = unit.accounts.get_for_update(session.user_account_id)
                if not self._active(session, account, now):
                    if session.revoked_at is None and now >= session.expires_at:
                        raise RefreshSessionExpiredError("Refresh session expired")
                    raise RefreshSessionInvalidError("Refresh session is invalid")
                replacement = self._refresh_tokens.issue()
                replacement_digest = self._refresh_tokens.digest(replacement)
                if not unit.refresh_sessions.replace_token(session.id, digest, replacement_digest, now):
                    raise RefreshSessionInvalidError("Refresh session is invalid")
                assert account is not None
                access_token, access_expires_at = self._access_tokens.issue(
                    AccessTokenClaims(account.id, session.id, account.authentication_epoch),
                    now=now,
                )
                result = SessionTokenResult(
                    access_token,
                    replacement,
                    "Bearer",
                    access_expires_at,
                    session.expires_at,
                    account.id,
                    session.id,
                    account.onboarding_required,
                )
                receipt_id = self._identifier_factory()
                encrypted = self._receipt_cipher.encrypt(result, context=self._receipt_context(receipt_id))
                unit.refresh_receipts.add(
                    RefreshRotationReceiptRecord(
                        id=receipt_id,
                        refresh_session_id=session.id,
                        token_family_id=session.token_family_id,
                        previous_token_digest=digest,
                        refresh_request_id=command.refresh_request_id,
                        response_ciphertext=encrypted.ciphertext,
                        encryption_key_id=encrypted.key_id,
                        encryption_key_version=encrypted.key_version,
                        created_at=now,
                        expires_at=now + self._policy.retry_receipt_lifetime,
                    )
                )
                unit.security_events.append(
                    self._event("refresh_token_rotated", command.request_id, now, account.id, session.id)
                )
                unit.commit()
                self._diagnostic("refresh_token_rotated", "refresh_session", "succeeded", command.request_id, account_id=account.id, session_id=session.id)
                return result
        except AuthenticationPersistenceUnavailableError as error:
            raise SessionDependencyUnavailableError("Session dependency is unavailable") from error

    def authenticate(self, access_token: str, request_id: str) -> tuple[AccessTokenClaims, object]:
        now = self._clock()
        try:
            claims = self._access_tokens.verify(access_token, now=now)
        except (AccessTokenInvalidError, AccessTokenExpiredError):
            self._diagnostic("access_token_rejected", "current_session", "rejected", request_id, "invalid_access_token")
            raise
        try:
            with self._unit_of_work_factory(request_id) as unit:
                session = unit.refresh_sessions.get_for_update(claims.session_id)
                account = unit.accounts.get(claims.account_id)
                if (
                    session is None
                    or session.user_account_id != claims.account_id
                    or claims.authentication_epoch != session.authentication_epoch
                    or not self._active(session, account, now)
                ):
                    raise AccessTokenInvalidError("Access token is invalid")
                return claims, account
        except AuthenticationPersistenceUnavailableError as error:
            raise SessionDependencyUnavailableError("Session dependency is unavailable") from error

    def current(self, access_token: str, request_id: str) -> CurrentSessionResult:
        claims, account = self.authenticate(access_token, request_id)
        self._diagnostic("session_restored", "current_session", "succeeded", request_id,
                         account_id=claims.account_id, session_id=claims.session_id)
        return CurrentSessionResult(
            account_id=claims.account_id,
            session_id=claims.session_id,
            display_name=account.display_name,
            onboarding_required=account.onboarding_required,
        )

    def logout_current(self, access_token: str, request_id: str) -> None:
        claims = self._access_tokens.verify(access_token, now=self._clock())
        now = self._clock()
        try:
            with self._unit_of_work_factory(request_id) as unit:
                session = unit.refresh_sessions.get_for_update(claims.session_id)
                if session is None or session.user_account_id != claims.account_id:
                    raise AccessTokenInvalidError("Access token is invalid")
                account = unit.accounts.get_for_update(claims.account_id)
                if session.revoked_at is not None:
                    return
                if (claims.authentication_epoch != session.authentication_epoch
                        or not self._active(session, account, now)):
                    raise AccessTokenInvalidError("Access token is invalid")
                changed = unit.refresh_sessions.revoke(
                    claims.session_id, now, "logout_current"
                )
                if changed:
                    unit.security_events.append(self._event("session_logged_out", request_id, now, claims.account_id, claims.session_id))
                unit.commit()
            self._diagnostic("session_logged_out", "logout_current", "succeeded", request_id, "revoked" if changed else "already_revoked", claims.account_id, claims.session_id)
        except AuthenticationPersistenceUnavailableError as error:
            raise SessionDependencyUnavailableError("Session dependency is unavailable") from error

    def logout_all(self, access_token: str, request_id: str) -> None:
        claims = self._access_tokens.verify(access_token, now=self._clock())
        now = self._clock()
        try:
            with self._unit_of_work_factory(request_id) as unit:
                session = unit.refresh_sessions.get_for_update(claims.session_id)
                if session is None or session.user_account_id != claims.account_id:
                    raise AccessTokenInvalidError("Access token is invalid")
                account = unit.accounts.get_for_update(claims.account_id)
                if session.revoked_at is not None:
                    return
                if (claims.authentication_epoch != session.authentication_epoch
                        or not self._active(session, account, now)):
                    raise AccessTokenInvalidError("Access token is invalid")
                changed = unit.refresh_sessions.revoke_all_for_account(
                    claims.account_id, now, "logout_all"
                )
                if changed:
                    unit.security_events.append(self._event("all_sessions_logged_out", request_id, now, claims.account_id, claims.session_id))
                unit.commit()
            self._diagnostic("all_sessions_logged_out", "logout_all", "succeeded", request_id, "revoked" if changed else "already_revoked", claims.account_id, claims.session_id)
        except AuthenticationPersistenceUnavailableError as error:
            raise SessionDependencyUnavailableError("Session dependency is unavailable") from error

    @staticmethod
    def _active(session: RefreshSessionRecord, account: object | None, now: datetime) -> bool:
        return bool(
            account is not None
            and account.status is AccountStatus.ACTIVE
            and account.email_verified_at is not None
            and account.authentication_epoch == session.authentication_epoch
            and session.revoked_at is None
            and now < session.expires_at
        )

    @staticmethod
    def _receipt_context(receipt_id: UUID) -> bytes:
        return f"pos:refresh-receipt:{receipt_id}".encode()

    def _event(
        self,
        event_type: str,
        request_id: str,
        now: datetime,
        account_id: AccountId | None = None,
        session_id: RefreshSessionId | None = None,
    ) -> SecurityEventRecord:
        return SecurityEventRecord(
            id=self._identifier_factory(),
            event_type=event_type,
            occurred_at=now,
            user_account_id=account_id,
            refresh_session_id=session_id,
            request_id=request_id,
        )

    def _diagnostic(
        self,
        event: str,
        operation: str,
        outcome: str,
        request_id: str,
        category: str | None = None,
        account_id: AccountId | None = None,
        session_id: RefreshSessionId | None = None,
    ) -> None:
        if self._event_logger is not None:
            self._event_logger.emit(
                event=event,
                operation=operation,
                outcome=outcome,
                request_id=request_id,
                error_category=category,
                account_id=account_id,
                session_id=session_id,
            )
