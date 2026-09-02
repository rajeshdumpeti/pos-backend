"""Email registration and verification use-case orchestration."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Protocol
from uuid import UUID, uuid4

from app.application.authentication_persistence import (
    AuthenticationPersistenceUnavailableError,
    AuthenticationUnitOfWork,
    ConcurrentPersistenceUpdateError,
    DuplicateNormalizedIdentityError,
)
from app.domain.authentication import (
    AccountId,
    AccountStatus,
    AuthenticationEmailOutboxRecord,
    ChallengeCompletionAction,
    ChallengeId,
    ChallengePurpose,
    EmailIdentityRecord,
    EmailVerificationChallengeRecord,
    IdentityId,
    OutboxMessageKind,
    OutboxStatus,
    SecurityEventRecord,
    UserAccountRecord,
)
from app.domain.registration import (
    EmailPolicyError,
    NormalizedEmail,
    PasswordPolicyError,
    mask_email,
    require_password_length,
)


class RegistrationVerificationError(RuntimeError):
    """Base safe use-case failure."""


class RegistrationInputRejectedError(RegistrationVerificationError):
    pass


class PasswordRejectedError(RegistrationVerificationError):
    pass


class VerificationFlowInvalidError(RegistrationVerificationError):
    pass


class VerificationFlowExpiredError(RegistrationVerificationError):
    pass


class VerificationCodeInvalidError(RegistrationVerificationError):
    pass


class VerificationCodeExpiredError(RegistrationVerificationError):
    pass


class VerificationAttemptsExceededError(RegistrationVerificationError):
    pass


class AccountAlreadyExistsError(RegistrationVerificationError):
    pass


class RegistrationDependencyUnavailableError(RegistrationVerificationError):
    pass


class RegistrationInternalError(RegistrationVerificationError):
    pass


class AuthenticationCryptographyError(RegistrationVerificationError):
    pass


class ResendCooldownActiveError(RegistrationVerificationError):
    def __init__(self, retry_at: datetime, retry_after_seconds: int) -> None:
        super().__init__("Verification resend cooldown is active")
        self.retry_at = retry_at
        self.retry_after_seconds = retry_after_seconds


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...
    def verify(self, password_hash: str, password: str) -> bool: ...
    def needs_rehash(self, password_hash: str) -> bool: ...


class CompromisedPasswordChecker(Protocol):
    def is_compromised(self, password: str) -> bool: ...


class EmailNormalizer(Protocol):
    def normalize(self, email: str) -> NormalizedEmail: ...


@dataclass(frozen=True, slots=True)
class FlowTokenLookup:
    digest: str = field(repr=False)
    key_id: str
    key_version: int


@dataclass(frozen=True, slots=True)
class IssuedVerificationMaterial:
    flow_token: str = field(repr=False)
    flow_token_digest: str = field(repr=False)
    code: str = field(repr=False)
    code_digest: str = field(repr=False)
    key_id: str
    key_version: int


class VerificationMaterialProvider(Protocol):
    def issue(
        self, challenge_id: UUID, purpose: ChallengePurpose
    ) -> IssuedVerificationMaterial: ...
    def flow_lookup(self, flow_token: str) -> FlowTokenLookup: ...
    def verify_code(
        self, challenge: EmailVerificationChallengeRecord, code: str
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class VerificationEmailMessage:
    code: str = field(repr=False)
    expires_at: datetime
    purpose: ChallengePurpose


@dataclass(frozen=True, slots=True)
class EncryptedOutboxPayload:
    ciphertext: bytes = field(repr=False)
    key_id: str
    key_version: int


class OutboxPayloadCipher(Protocol):
    def encrypt(
        self, message: VerificationEmailMessage, *, context: bytes
    ) -> EncryptedOutboxPayload: ...


class AuthenticationEventLogger(Protocol):
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
    ) -> None: ...


class AuthenticationAvailabilityDiagnostic(Protocol):
    def emit_configuration_incomplete(
        self, *, operation: str, request_id: str
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class RegistrationPolicy:
    code_lifetime: timedelta = timedelta(minutes=10)
    flow_lifetime: timedelta = timedelta(minutes=30)
    resend_cooldown: timedelta = timedelta(seconds=60)
    maximum_attempts: int = 5
    terms_version: str = ""
    privacy_version: str = ""


@dataclass(frozen=True, slots=True)
class RegisterAccountCommand:
    display_name: str
    email: str = field(repr=False)
    password: str = field(repr=False)
    terms_accepted: bool
    privacy_accepted: bool
    terms_version: str
    privacy_version: str
    request_id: str


@dataclass(frozen=True, slots=True)
class ConfirmEmailCommand:
    flow_token: str = field(repr=False)
    code: str = field(repr=False)
    request_id: str


@dataclass(frozen=True, slots=True)
class ResendVerificationCommand:
    flow_token: str = field(repr=False)
    request_id: str


@dataclass(frozen=True, slots=True)
class ChangeUnverifiedEmailCommand:
    flow_token: str = field(repr=False)
    new_email: str = field(repr=False)
    request_id: str


@dataclass(frozen=True, slots=True)
class PendingVerificationResult:
    status: str
    flow_token: str = field(repr=False)
    masked_email: str
    verification_expires_at: datetime
    flow_expires_at: datetime
    resend_available_at: datetime


@dataclass(frozen=True, slots=True)
class EmailVerifiedResult:
    status: str
    account_id: UUID
    onboarding_required: bool
    session: None
    next_action: str


class EmailRegistrationService:
    """Own registration/verification transactions; routes only map HTTP."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[str], AuthenticationUnitOfWork],
        password_hasher: PasswordHasher,
        compromised_passwords: CompromisedPasswordChecker,
        email_normalizer: EmailNormalizer,
        verification_material: VerificationMaterialProvider,
        outbox_cipher: OutboxPayloadCipher,
        event_logger: AuthenticationEventLogger,
        policy: RegistrationPolicy,
        *,
        clock: Callable[[], datetime] | None = None,
        identifier_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._password_hasher = password_hasher
        self._compromised_passwords = compromised_passwords
        self._email_normalizer = email_normalizer
        self._verification_material = verification_material
        self._outbox_cipher = outbox_cipher
        self._event_logger = event_logger
        self._policy = policy
        self._clock = clock or (lambda: datetime.now(UTC))
        self._identifier_factory = identifier_factory

    def register(self, command: RegisterAccountCommand) -> PendingVerificationResult:
        normalized = self._normalize_email(command.email)
        display_name = command.display_name.strip()
        if not display_name or len(display_name) > 160:
            raise RegistrationInputRejectedError("Registration input was rejected")
        if (
            not command.terms_accepted
            or not command.privacy_accepted
            or command.terms_version != self._policy.terms_version
            or command.privacy_version != self._policy.privacy_version
        ):
            raise RegistrationInputRejectedError("Legal acceptance is required")
        self._require_password(command.password)
        password_hash = self._password_hasher.hash(command.password)

        try:
            for attempt in range(2):
                try:
                    return self._register_once(
                        command, normalized, display_name, password_hash
                    )
                except DuplicateNormalizedIdentityError as error:
                    if attempt == 1:
                        raise RegistrationInternalError(
                            "Registration state changed concurrently"
                        ) from error
            raise AssertionError("registration retry loop exhausted")
        except AuthenticationPersistenceUnavailableError as error:
            self._emit_failure("registration", command.request_id, "dependency")
            raise RegistrationDependencyUnavailableError(
                "Registration dependency is unavailable"
            ) from error
        except ConcurrentPersistenceUpdateError as error:
            self._emit_failure("registration", command.request_id, "concurrency")
            raise RegistrationInternalError(
                "Registration state changed concurrently"
            ) from error
        except RegistrationVerificationError as error:
            self._emit_failure(
                "registration", command.request_id, type(error).__name__
            )
            if isinstance(error, AuthenticationCryptographyError):
                self._emit_rollback(
                    "registration", command.request_id, "outbox_encryption"
                )
            raise

    def confirm(self, command: ConfirmEmailCommand) -> EmailVerifiedResult:
        lookup = self._lookup_flow(command.flow_token)
        now = self._clock()
        deferred_error: RegistrationVerificationError | None = None
        result: EmailVerifiedResult | None = None

        try:
            with self._unit_of_work_factory(command.request_id) as unit:
                challenge = unit.verification_challenges.get_by_flow_digest_for_update(
                    lookup.digest
                )
                self._require_current_flow(challenge, lookup, now)
                assert challenge is not None
                account = unit.accounts.get_for_update(challenge.user_account_id)
                identity = unit.email_identities.get_primary_for_account_for_update(
                    challenge.user_account_id
                )
                if account is None or identity is None:
                    raise VerificationFlowInvalidError("Verification flow is invalid")
                if challenge.attempts >= challenge.max_attempts:
                    raise VerificationAttemptsExceededError(
                        "Verification attempts were exceeded"
                    )
                if now >= challenge.expires_at:
                    raise VerificationCodeExpiredError("Verification code expired")
                if (
                    not self._valid_code_shape(command.code)
                    or not self._verification_material.verify_code(
                        challenge, command.code
                    )
                ):
                    attempts = unit.verification_challenges.increment_attempts(
                        challenge.id, challenge.status_version
                    )
                    if attempts is None:
                        raise VerificationFlowInvalidError(
                            "Verification flow is invalid"
                        )
                    unit.security_events.append(
                        self._security_event(
                            "email_verification_rejected",
                            command.request_id,
                            now,
                            account.id,
                            {"category": "invalid_code"},
                        )
                    )
                    unit.commit()
                    if attempts >= challenge.max_attempts:
                        deferred_error = VerificationAttemptsExceededError(
                            "Verification attempts were exceeded"
                        )
                    else:
                        deferred_error = VerificationCodeInvalidError(
                            "Verification code is invalid"
                        )
                elif not unit.verification_challenges.consume(
                    challenge.id, now, challenge.status_version
                ):
                    raise VerificationFlowInvalidError("Verification flow is invalid")
                elif (
                    challenge.completion_action
                    is ChallengeCompletionAction.ACCOUNT_EXISTS_NOTICE
                ):
                    unit.security_events.append(
                        self._security_event(
                            "existing_account_ownership_proved",
                            command.request_id,
                            now,
                            account.id,
                        )
                    )
                    unit.commit()
                    deferred_error = AccountAlreadyExistsError(
                        "An account already exists for this email"
                    )
                else:
                    if (
                        account.status is not AccountStatus.PENDING_VERIFICATION
                        or identity.normalized_email
                        != challenge.target_normalized_email
                        or not unit.email_identities.mark_verified(identity.id, now)
                        or not unit.accounts.activate_pending(account.id, now)
                    ):
                        raise VerificationFlowInvalidError(
                            "Verification flow is invalid"
                        )
                    unit.security_events.append(
                        self._security_event(
                            "email_verification_succeeded",
                            command.request_id,
                            now,
                            account.id,
                        )
                    )
                    unit.commit()
                    result = EmailVerifiedResult(
                        status="email_verified",
                        account_id=account.id,
                        onboarding_required=account.onboarding_required,
                        session=None,
                        next_action="await_session_establishment",
                    )
        except AuthenticationPersistenceUnavailableError as error:
            self._emit_failure("email_verification", command.request_id, "dependency")
            raise RegistrationDependencyUnavailableError(
                "Verification dependency is unavailable"
            ) from error
        except ConcurrentPersistenceUpdateError as error:
            self._emit_failure(
                "email_verification", command.request_id, "concurrency"
            )
            raise RegistrationInternalError(
                "Verification state changed concurrently"
            ) from error
        except RegistrationVerificationError as error:
            self._emit_failure(
                "email_verification", command.request_id, type(error).__name__
            )
            raise

        if deferred_error is not None:
            category = type(deferred_error).__name__
            self._emit_failure("email_verification", command.request_id, category)
            raise deferred_error
        assert result is not None
        self._event_logger.emit(
            event="authentication_operation",
            operation="email_verification",
            outcome="succeeded",
            request_id=command.request_id,
            account_id=result.account_id,
        )
        return result

    def resend(
        self, command: ResendVerificationCommand
    ) -> PendingVerificationResult:
        lookup = self._lookup_flow(command.flow_token)
        now = self._clock()
        try:
            with self._unit_of_work_factory(command.request_id) as unit:
                challenge = unit.verification_challenges.get_by_flow_digest_for_update(
                    lookup.digest
                )
                self._require_current_flow(challenge, lookup, now)
                assert challenge is not None
                if now < challenge.resend_available_at:
                    raise ResendCooldownActiveError(
                        challenge.resend_available_at,
                        max(1, ceil((challenge.resend_available_at - now).total_seconds())),
                    )
                identity = unit.email_identities.get_primary_for_account_for_update(
                    challenge.user_account_id
                )
                if identity is None:
                    raise VerificationFlowInvalidError("Verification flow is invalid")
                result = self._replace_challenge(
                    unit,
                    challenge,
                    challenge.user_account_id,
                    challenge.purpose,
                    challenge.completion_action,
                    identity.display_email,
                    challenge.target_normalized_email,
                    command.request_id,
                    now,
                )
                unit.security_events.append(
                    self._security_event(
                        "email_verification_resend_accepted",
                        command.request_id,
                        now,
                        challenge.user_account_id,
                    )
                )
                unit.commit()
        except AuthenticationPersistenceUnavailableError as error:
            self._emit_failure("verification_resend", command.request_id, "dependency")
            raise RegistrationDependencyUnavailableError(
                "Verification dependency is unavailable"
            ) from error
        except ConcurrentPersistenceUpdateError as error:
            self._emit_failure(
                "verification_resend", command.request_id, "concurrency"
            )
            raise RegistrationInternalError(
                "Verification state changed concurrently"
            ) from error
        except RegistrationVerificationError as error:
            self._emit_failure(
                "verification_resend", command.request_id, type(error).__name__
            )
            if isinstance(error, AuthenticationCryptographyError):
                self._emit_rollback(
                    "verification_resend", command.request_id, "outbox_encryption"
                )
            raise
        self._event_logger.emit(
            event="authentication_operation",
            operation="verification_resend",
            outcome="accepted",
            request_id=command.request_id,
        )
        return result

    def change_email(
        self, command: ChangeUnverifiedEmailCommand
    ) -> PendingVerificationResult:
        normalized = self._normalize_email(command.new_email)
        try:
            for attempt in range(2):
                try:
                    return self._change_email_once(command, normalized)
                except DuplicateNormalizedIdentityError as error:
                    if attempt == 1:
                        raise RegistrationInternalError(
                            "Email identity changed concurrently"
                        ) from error
            raise AssertionError("email-change retry loop exhausted")
        except AuthenticationPersistenceUnavailableError as error:
            self._emit_failure("change_unverified_email", command.request_id, "dependency")
            raise RegistrationDependencyUnavailableError(
                "Verification dependency is unavailable"
            ) from error
        except ConcurrentPersistenceUpdateError as error:
            self._emit_failure(
                "change_unverified_email", command.request_id, "concurrency"
            )
            raise RegistrationInternalError(
                "Email verification state changed concurrently"
            ) from error
        except RegistrationVerificationError as error:
            self._emit_failure(
                "change_unverified_email", command.request_id, type(error).__name__
            )
            if isinstance(error, AuthenticationCryptographyError):
                self._emit_rollback(
                    "change_unverified_email",
                    command.request_id,
                    "outbox_encryption",
                )
            raise

    def _register_once(
        self,
        command: RegisterAccountCommand,
        normalized: NormalizedEmail,
        display_name: str,
        password_hash: str,
    ) -> PendingVerificationResult:
        now = self._clock()
        with self._unit_of_work_factory(command.request_id) as unit:
            identity = unit.email_identities.get_by_normalized_email_for_update(
                normalized.comparison
            )
            if identity is None:
                account_id = AccountId(self._identifier_factory())
                identity = EmailIdentityRecord(
                    id=IdentityId(self._identifier_factory()),
                    user_account_id=account_id,
                    display_email=normalized.display,
                    normalized_email=normalized.comparison,
                    is_verified=False,
                    is_primary=True,
                    created_at=now,
                    updated_at=now,
                )
                account = UserAccountRecord(
                    id=account_id,
                    status=AccountStatus.PENDING_VERIFICATION,
                    display_name=display_name,
                    password_hash=password_hash,
                    email_verified_at=None,
                    onboarding_required=True,
                    authentication_epoch=0,
                    terms_version=command.terms_version,
                    terms_accepted_at=now,
                    privacy_version=command.privacy_version,
                    privacy_accepted_at=now,
                    created_at=now,
                    updated_at=now,
                )
                unit.accounts.add(account)
                unit.email_identities.add(identity)
            else:
                account = unit.accounts.get_for_update(identity.user_account_id)
                if account is None:
                    raise RegistrationInternalError("Registration state is invalid")

            completion_action = (
                ChallengeCompletionAction.ACCOUNT_EXISTS_NOTICE
                if account.status is AccountStatus.ACTIVE
                else ChallengeCompletionAction.ACTIVATE_ACCOUNT
            )
            current = unit.verification_challenges.get_current_for_update(
                account.id, ChallengePurpose.REGISTRATION
            )
            result = self._replace_challenge(
                unit,
                current,
                account.id,
                ChallengePurpose.REGISTRATION,
                completion_action,
                identity.display_email,
                identity.normalized_email,
                command.request_id,
                now,
            )
            unit.security_events.append(
                self._security_event(
                    "registration_accepted", command.request_id, now, account.id
                )
            )
            unit.commit()
        self._event_logger.emit(
            event="authentication_operation",
            operation="registration",
            outcome="accepted",
            request_id=command.request_id,
        )
        return result

    def _change_email_once(
        self, command: ChangeUnverifiedEmailCommand, normalized: NormalizedEmail
    ) -> PendingVerificationResult:
        lookup = self._lookup_flow(command.flow_token)
        now = self._clock()
        with self._unit_of_work_factory(command.request_id) as unit:
            origin = unit.verification_challenges.get_by_flow_digest_for_update(
                lookup.digest
            )
            self._require_current_flow(origin, lookup, now)
            assert origin is not None
            origin_account = unit.accounts.get_for_update(origin.user_account_id)
            origin_identity = unit.email_identities.get_primary_for_account_for_update(
                origin.user_account_id
            )
            if (
                origin_account is None
                or origin_identity is None
                or origin_account.status is not AccountStatus.PENDING_VERIFICATION
                or origin.completion_action
                is not ChallengeCompletionAction.ACTIVATE_ACCOUNT
            ):
                raise VerificationFlowInvalidError("Verification flow is invalid")

            collision = unit.email_identities.get_by_normalized_email_for_update(
                normalized.comparison
            )
            if collision is not None and collision.id != origin_identity.id:
                target_account = unit.accounts.get_for_update(collision.user_account_id)
                if target_account is None:
                    raise RegistrationInternalError("Registration state is invalid")
                target_identity = collision
                completion_action = ChallengeCompletionAction.ACCOUNT_EXISTS_NOTICE
            else:
                replacing_identity = (
                    normalized.comparison != origin_identity.normalized_email
                )
                if replacing_identity and not (
                    unit.email_identities.replace_unverified_email(
                        origin_identity.id,
                        normalized.display,
                        normalized.comparison,
                        now,
                    )
                ):
                    raise VerificationFlowInvalidError("Verification flow is invalid")
                target_account = origin_account
                target_identity = EmailIdentityRecord(
                    id=origin_identity.id,
                    user_account_id=origin_identity.user_account_id,
                    display_email=normalized.display,
                    normalized_email=normalized.comparison,
                    is_verified=False,
                    is_primary=True,
                    created_at=origin_identity.created_at,
                    updated_at=now,
                )
                completion_action = ChallengeCompletionAction.ACTIVATE_ACCOUNT

            target_current = unit.verification_challenges.get_current_for_update(
                target_account.id, ChallengePurpose.EMAIL_CHANGE
            )
            replacement_id = ChallengeId(self._identifier_factory())
            current_challenges = {
                item.id: item
                for item in (origin, target_current)
                if item is not None
            }
            for current in current_challenges.values():
                if not unit.verification_challenges.supersede(
                    current.id, replacement_id, current.status_version
                ):
                    raise ConcurrentPersistenceUpdateError(
                        "Verification challenge changed concurrently"
                    )
            result = self._add_challenge(
                unit,
                replacement_id,
                target_account.id,
                ChallengePurpose.EMAIL_CHANGE,
                completion_action,
                target_identity.display_email,
                target_identity.normalized_email,
                now,
            )
            unit.security_events.append(
                self._security_event(
                    "unverified_email_change_accepted",
                    command.request_id,
                    now,
                    origin_account.id,
                )
            )
            unit.commit()
        self._event_logger.emit(
            event="authentication_operation",
            operation="change_unverified_email",
            outcome="accepted",
            request_id=command.request_id,
        )
        return result

    def _replace_challenge(
        self,
        unit: AuthenticationUnitOfWork,
        current: EmailVerificationChallengeRecord | None,
        account_id: AccountId,
        purpose: ChallengePurpose,
        completion_action: ChallengeCompletionAction,
        display_email: str,
        normalized_email: str,
        request_id: str,
        now: datetime,
    ) -> PendingVerificationResult:
        replacement_id = ChallengeId(self._identifier_factory())
        if current is not None and not unit.verification_challenges.supersede(
            current.id, replacement_id, current.status_version
        ):
            raise ConcurrentPersistenceUpdateError(
                "Verification challenge changed concurrently"
            )
        return self._add_challenge(
            unit,
            replacement_id,
            account_id,
            purpose,
            completion_action,
            display_email,
            normalized_email,
            now,
        )

    def _add_challenge(
        self,
        unit: AuthenticationUnitOfWork,
        challenge_id: ChallengeId,
        account_id: AccountId,
        purpose: ChallengePurpose,
        completion_action: ChallengeCompletionAction,
        display_email: str,
        normalized_email: str,
        now: datetime,
    ) -> PendingVerificationResult:
        material = self._verification_material.issue(challenge_id, purpose)
        expires_at = now + self._policy.code_lifetime
        flow_expires_at = now + self._policy.flow_lifetime
        resend_available_at = now + self._policy.resend_cooldown
        challenge = EmailVerificationChallengeRecord(
            id=challenge_id,
            user_account_id=account_id,
            purpose=purpose,
            flow_token_digest=material.flow_token_digest,
            code_digest=material.code_digest,
            target_normalized_email=normalized_email,
            issued_at=now,
            expires_at=expires_at,
            flow_expires_at=flow_expires_at,
            resend_available_at=resend_available_at,
            digest_key_id=material.key_id,
            digest_key_version=material.key_version,
            completion_action=completion_action,
            attempts=0,
            max_attempts=self._policy.maximum_attempts,
        )
        payload = self._outbox_cipher.encrypt(
            VerificationEmailMessage(
                code=material.code,
                expires_at=expires_at,
                purpose=purpose,
            ),
            context=f"email-verification:{challenge_id}".encode("ascii"),
        )
        unit.verification_challenges.add(challenge)
        unit.email_outbox.add(
            AuthenticationEmailOutboxRecord(
                id=self._identifier_factory(),
                user_account_id=account_id,
                message_kind=OutboxMessageKind.EMAIL_VERIFICATION,
                recipient_normalized_email=normalized_email,
                payload_ciphertext=payload.ciphertext,
                encryption_key_id=payload.key_id,
                encryption_key_version=payload.key_version,
                deduplication_key=f"email-verification:{challenge_id}",
                status=OutboxStatus.PENDING,
                created_at=now,
                available_at=now,
            )
        )
        return PendingVerificationResult(
            status="registration_pending",
            flow_token=material.flow_token,
            masked_email=mask_email(display_email),
            verification_expires_at=expires_at,
            flow_expires_at=flow_expires_at,
            resend_available_at=resend_available_at,
        )

    def _require_password(self, password: str) -> None:
        try:
            require_password_length(password)
        except PasswordPolicyError as error:
            raise PasswordRejectedError("Password was rejected") from error
        if self._compromised_passwords.is_compromised(password):
            raise PasswordRejectedError("Password was rejected")

    def _normalize_email(self, email: str) -> NormalizedEmail:
        try:
            return self._email_normalizer.normalize(email)
        except EmailPolicyError as error:
            raise RegistrationInputRejectedError(
                "Registration input was rejected"
            ) from error

    def _lookup_flow(self, flow_token: str) -> FlowTokenLookup:
        try:
            return self._verification_material.flow_lookup(flow_token)
        except ValueError as error:
            raise VerificationFlowInvalidError("Verification flow is invalid") from error

    def _require_current_flow(
        self,
        challenge: EmailVerificationChallengeRecord | None,
        lookup: FlowTokenLookup,
        now: datetime,
    ) -> None:
        if (
            challenge is None
            or challenge.digest_key_id != lookup.key_id
            or challenge.digest_key_version != lookup.key_version
            or challenge.consumed_at is not None
            or challenge.superseded_by_id is not None
        ):
            raise VerificationFlowInvalidError("Verification flow is invalid")
        if now >= challenge.flow_expires_at:
            raise VerificationFlowExpiredError("Verification flow expired")

    @staticmethod
    def _valid_code_shape(code: str) -> bool:
        return len(code) == 6 and code.isascii() and code.isdigit()

    def _security_event(
        self,
        event_type: str,
        request_id: str,
        occurred_at: datetime,
        account_id: AccountId,
        metadata: dict[str, str] | None = None,
    ) -> SecurityEventRecord:
        return SecurityEventRecord(
            id=self._identifier_factory(),
            event_type=event_type,
            occurred_at=occurred_at,
            user_account_id=account_id,
            request_id=request_id,
            sanitized_metadata=metadata or {},
        )

    def _emit_failure(
        self, operation: str, request_id: str, category: str
    ) -> None:
        self._event_logger.emit(
            event="authentication_operation",
            operation=operation,
            outcome="rejected",
            request_id=request_id,
            error_category=category,
        )

    def _emit_rollback(
        self, operation: str, request_id: str, category: str
    ) -> None:
        self._event_logger.emit(
            event="authentication_transaction",
            operation=operation,
            outcome="rolled_back",
            request_id=request_id,
            error_category=category,
        )
