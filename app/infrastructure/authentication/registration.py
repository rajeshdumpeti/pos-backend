"""Cryptography, password, email, and safe diagnostics for registration."""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import re
from uuid import UUID
from typing import Never

from argon2 import PasswordHasher
from argon2.exceptions import HashingError, InvalidHashError, VerificationError
from argon2.low_level import Type
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from email_validator import EmailNotValidError, validate_email

from app.application.email_registration import (
    AuthenticationCryptographyError,
    EncryptedOutboxPayload,
    FlowTokenLookup,
    IssuedFlowToken,
    IssuedVerificationMaterial,
    VerificationEmailMessage,
)
from app.application.password_recovery import (
    IssuedPasswordResetMaterial,
    PasswordResetEmailMessage,
)
from app.domain.authentication import ChallengePurpose, EmailVerificationChallengeRecord
from app.domain.registration import EmailPolicyError, NormalizedEmail


LOGGER = logging.getLogger("pos.authentication.registration")


class RedactedAuthenticationCause(RuntimeError):
    """Safe public chain node for sensitive authentication internals."""


def _raise_crypto_error(message: str, internal_cause: BaseException) -> Never:
    error = AuthenticationCryptographyError(message)
    error._internal_cause = internal_cause  # type: ignore[attr-defined]
    raise error from RedactedAuthenticationCause(
        f"{type(internal_cause).__name__} details redacted"
    )


class Argon2idPasswordHasher:
    """Maintained Argon2id adapter with explicit, benchmarkable parameters."""

    def __init__(
        self,
        *,
        time_cost: int,
        memory_cost_kib: int,
        parallelism: int,
    ) -> None:
        self._hasher = PasswordHasher(
            time_cost=time_cost,
            memory_cost=memory_cost_kib,
            parallelism=parallelism,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )

    def hash(self, password: str) -> str:
        if not 1 <= len(password) <= 1024:
            raise AuthenticationCryptographyError("Password length is invalid")
        try:
            return self._hasher.hash(password)
        except HashingError as error:
            _raise_crypto_error("Password hashing failed", error)

    def verify(self, password_hash: str, password: str) -> bool:
        if not 1 <= len(password) <= 1024:
            return False
        try:
            return self._hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError as error:
            _raise_crypto_error("Password hash is invalid", error)


class LocalCompromisedPasswordChecker:
    """Offline replaceable blocklist; production corpus integration is deferred."""

    _BLOCKED = frozenset(
        {
            "123456789012345",
            "correct horse battery staple",
            "letmeinletmeinletmein",
            "passwordpassword",
            "qwertyqwertyqwerty",
            "thisisnotsecure",
        }
    )

    def is_compromised(self, password: str) -> bool:
        return password.casefold() in self._BLOCKED


class EmailValidatorNormalizer:
    """Central POS email syntax, IDNA, and case-insensitive comparison policy."""

    def normalize(self, email: str) -> NormalizedEmail:
        try:
            result = validate_email(
                email,
                check_deliverability=False,
                allow_smtputf8=True,
            )
        except EmailNotValidError:
            raise EmailPolicyError("Email address is invalid") from None

        display = result.normalized
        local = result.local_part
        ascii_domain = result.ascii_domain
        if ascii_domain is None:
            raise EmailPolicyError("Email address is invalid")
        comparison = f"{local.casefold()}@{ascii_domain.casefold()}"
        if len(display) > 320 or len(comparison) > 320:
            raise EmailPolicyError("Email address is invalid")
        return NormalizedEmail(display=display, comparison=comparison)


class HmacVerificationMaterialProvider:
    """Generate and verify domain-separated challenge material."""

    def __init__(self, key: bytes, *, key_id: str, key_version: int) -> None:
        if len(key) != 32:
            raise ValueError("Verification digest key must be 256 bits")
        self._key = key
        self._key_id = key_id
        self._key_version = key_version

    def issue(
        self, challenge_id: object, purpose: ChallengePurpose
    ) -> IssuedVerificationMaterial:
        random_token = secrets.token_urlsafe(32)
        flow_token = f"v{self._key_version}.{random_token}"
        code = f"{secrets.randbelow(1_000_000):06d}"
        return IssuedVerificationMaterial(
            flow_token=flow_token,
            flow_token_digest=self._digest("flow", flow_token),
            code=code,
            code_digest=self._code_digest(str(challenge_id), purpose, code),
            key_id=self._key_id,
            key_version=self._key_version,
        )

    def issue_flow_token(self) -> IssuedFlowToken:
        random_token = secrets.token_urlsafe(32)
        flow_token = f"v{self._key_version}.{random_token}"
        return IssuedFlowToken(
            token=flow_token,
            digest=self._digest("flow", flow_token),
            key_id=self._key_id,
            key_version=self._key_version,
        )

    def flow_lookup(self, flow_token: str) -> FlowTokenLookup:
        prefix, separator, random_value = flow_token.partition(".")
        if (
            separator != "."
            or prefix != f"v{self._key_version}"
            or len(random_value) < 43
        ):
            raise ValueError("Invalid flow token")
        return FlowTokenLookup(
            digest=self._digest("flow", flow_token),
            key_id=self._key_id,
            key_version=self._key_version,
        )

    def verify_code(
        self, challenge: EmailVerificationChallengeRecord, code: str
    ) -> bool:
        candidate = self._code_digest(str(challenge.id), challenge.purpose, code)
        return hmac.compare_digest(candidate, challenge.code_digest)

    def _code_digest(
        self, challenge_id: str, purpose: ChallengePurpose, code: str
    ) -> str:
        return self._digest(
            "code", f"{challenge_id}:{purpose.value}:{code}"
        )

    def _digest(self, purpose: str, value: str) -> str:
        payload = f"pos-auth:{purpose}:v1:{value}".encode("utf-8")
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()


class HmacPasswordResetMaterialProvider:
    """Issue opaque 256-bit tokens and retain only domain-separated digests."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("Password-reset digest key must be 256 bits")
        self._key = key

    def issue(
        self, challenge_id: object, request_id: str
    ) -> IssuedPasswordResetMaterial:
        token = secrets.token_urlsafe(32)
        return IssuedPasswordResetMaterial(
            token=token,
            token_digest=self.lookup_digest(token),
            request_security_digest=self._digest(
                "request", f"{challenge_id}:{request_id}"
            ),
        )

    def lookup_digest(self, token: str) -> str:
        if len(token) < 43 or len(token) > 128:
            raise ValueError("Invalid reset token")
        return self._digest("token", token)

    def _digest(self, purpose: str, value: str) -> str:
        payload = f"pos-auth:password-reset:{purpose}:v1:{value}".encode("utf-8")
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()


class AesGcmOutboxCipher:
    """AES-256-GCM envelope for authentication email payloads."""

    _NONCE_BYTES = 12

    def __init__(self, key: bytes, *, key_id: str, key_version: int) -> None:
        if len(key) != 32:
            raise ValueError("Outbox encryption key must be 256 bits")
        self._cipher = AESGCM(key)
        self._key_id = key_id
        self._key_version = key_version

    def encrypt(
        self, message: VerificationEmailMessage, *, context: bytes
    ) -> EncryptedOutboxPayload:
        plaintext = json.dumps(
            {
                "code": message.code,
                "expires_at": message.expires_at.isoformat(),
                "purpose": message.purpose.value,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        nonce = secrets.token_bytes(self._NONCE_BYTES)
        try:
            encrypted = self._cipher.encrypt(nonce, plaintext, context)
        except Exception as error:
            _raise_crypto_error("Authentication email encryption failed", error)
        return EncryptedOutboxPayload(
            ciphertext=nonce + encrypted,
            key_id=self._key_id,
            key_version=self._key_version,
        )

    def decrypt(
        self, payload: EncryptedOutboxPayload, *, context: bytes
    ) -> VerificationEmailMessage:
        if (
            payload.key_id != self._key_id
            or payload.key_version != self._key_version
            or len(payload.ciphertext) <= self._NONCE_BYTES
        ):
            raise AuthenticationCryptographyError(
                "Authentication email key version is unavailable"
            )
        nonce = payload.ciphertext[: self._NONCE_BYTES]
        ciphertext = payload.ciphertext[self._NONCE_BYTES :]
        try:
            plaintext = self._cipher.decrypt(nonce, ciphertext, context)
            decoded = json.loads(plaintext)
            return VerificationEmailMessage(
                code=str(decoded["code"]),
                expires_at=_parse_datetime(str(decoded["expires_at"])),
                purpose=ChallengePurpose(str(decoded["purpose"])),
            )
        except (InvalidTag, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            _raise_crypto_error("Authentication email decryption failed", error)

    def encrypt_password_reset(
        self, message: PasswordResetEmailMessage, *, context: bytes
    ) -> EncryptedOutboxPayload:
        plaintext = json.dumps(
            {
                "kind": "password_reset",
                "token": message.token,
                "reset_url": message.reset_url,
                "expires_at": message.expires_at.isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        nonce = secrets.token_bytes(self._NONCE_BYTES)
        try:
            encrypted = self._cipher.encrypt(nonce, plaintext, context)
        except Exception as error:
            _raise_crypto_error("Authentication email encryption failed", error)
        return EncryptedOutboxPayload(
            ciphertext=nonce + encrypted,
            key_id=self._key_id,
            key_version=self._key_version,
        )

    def decrypt_password_reset(
        self, payload: EncryptedOutboxPayload, *, context: bytes
    ) -> PasswordResetEmailMessage:
        if (
            payload.key_id != self._key_id
            or payload.key_version != self._key_version
            or len(payload.ciphertext) <= self._NONCE_BYTES
        ):
            raise AuthenticationCryptographyError(
                "Authentication email key version is unavailable"
            )
        nonce = payload.ciphertext[: self._NONCE_BYTES]
        try:
            plaintext = self._cipher.decrypt(
                nonce, payload.ciphertext[self._NONCE_BYTES :], context
            )
            decoded = json.loads(plaintext)
            if decoded.get("kind") != "password_reset":
                raise ValueError("Unexpected email payload")
            return PasswordResetEmailMessage(
                token=str(decoded["token"]),
                reset_url=str(decoded["reset_url"]),
                expires_at=_parse_datetime(str(decoded["expires_at"])),
            )
        except (InvalidTag, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            _raise_crypto_error("Authentication email decryption failed", error)


class SafeAuthenticationEventLogger:
    """Allow-listed structured authentication diagnostics."""

    def emit(
        self,
        *,
        event: str,
        operation: str,
        outcome: str,
        request_id: str,
        error_category: str | None = None,
        account_id: object | None = None,
        challenge_id: object | None = None,
        session_id: object | None = None,
    ) -> None:
        def label(value):
            return value if value in _SAFE_DIAGNOSTIC_LABELS else "redacted"
        def identifier(value):
            return str(value) if isinstance(value, UUID) else None
        safe_request_id = request_id if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", request_id) else "redacted"
        try:
            LOGGER.log(logging.WARNING if event == "authentication_configuration_unavailable" else logging.INFO, label(event), extra={
                "operation": label(operation), "outcome": label(outcome),
                "request_id": safe_request_id,
                ("category" if event == "authentication_configuration_unavailable" else "error_category"): None if error_category is None else label(error_category),
                **({} if event == "authentication_configuration_unavailable" else {
                    "account_id": identifier(account_id), "challenge_id": identifier(challenge_id),
                    "session_id": identifier(session_id),
                }),
            })
        except Exception:
            # Post-commit/rejection diagnostics cannot change the public outcome.
            pass



class SafeAuthenticationAvailabilityDiagnostic:
    """Emit only allow-listed fields when authentication is unconfigured."""

    def emit_configuration_incomplete(
        self, *, operation: str, request_id: str
    ) -> None:
        SafeAuthenticationEventLogger().emit(
            event="authentication_configuration_unavailable", operation=operation,
            outcome="unavailable", error_category="configuration_incomplete", request_id=request_id)



def encode_key_for_local_generation(key: bytes) -> str:
    """Return URL-safe base64 for an explicitly generated local key."""
    return base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")


def _parse_datetime(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)


_SAFE_DIAGNOSTIC_LABELS = frozenset({
    'AccessTokenExpiredError',
    'AccessTokenInvalidError',
    'AccountAlreadyExistsError',
    'AccountUnavailableError',
    'AuthenticationCryptographyError',
    'AuthenticationRequiredError',
    'EmailNotVerifiedError',
    'InvalidCredentialsError',
    'PasswordRecoveryDependencyUnavailableError',
    'PasswordRecoveryError',
    'PasswordRecoveryInternalError',
    'PasswordRejectedError',
    'RefreshSessionExpiredError',
    'RefreshSessionInvalidError',
    'RefreshSessionReplayedError',
    'RegistrationDependencyUnavailableError',
    'RegistrationInputRejectedError',
    'RegistrationInternalError',
    'RegistrationVerificationError',
    'ResendCooldownActiveError',
    'ResetPasswordRejectedError',
    'ResetTokenExpiredError',
    'ResetTokenInvalidError',
    'SessionDependencyUnavailableError',
    'SessionInternalError',
    'SessionLifecycleError',
    'VerificationAttemptsExceededError',
    'VerificationCodeExpiredError',
    'VerificationCodeInvalidError',
    'VerificationFlowExpiredError',
    'VerificationFlowInvalidError',

    'accepted',
    'access_token_rejected',
    'account_unavailable',
    'all',
    'all_sessions_logged_out',
    'already_revoked',
    'approved_retry',
    'ascii',
    'authentication_admission_rejected',
    'authentication_configuration_unavailable',
    'authentication_internal_failure',
    'authentication_operation',
    'authentication_required',
    'authentication_transaction',
    'authorization',
    'await_session_establishment',
    'body',
    'category',
    'change_email',
    'change_unverified_email',
    'concurrency',
    'configuration_incomplete',
    'cryptography',
    'current_session',
    'dependency',
    'dependency_unavailable',
    'email',
    'email_not_verified',
    'email_sign_in',
    'email_sign_in_failed',
    'email_sign_in_succeeded',
    'email_verification',
    'email_verification_rejected',
    'email_verification_resend_accepted',
    'email_verification_succeeded',
    'email_verified',
    'existing_account_ownership_proved',
    'failed',
    'flow_token',
    'forwarded',
    'global',
    'headers',
    'http',
    'identity',
    'instructions_if_eligible',
    'internal',
    'internal_error',
    'internal_server_error',
    'invalid_access_token',
    'invalid_code',
    'invalid_credentials',
    'logout',
    'logout_all',
    'logout_current',
    'method',
    'more_body',
    'new_email',
    'outbox_encryption',
    'password_reset',
    'password_reset_confirm',
    'password_reset_request',
    'password_reset_requested',
    'password_reset_succeeded',
    'path',
    'query_string',
    'rate_limited',
    'redacted',
    'refresh',
    'refresh_replay',
    'refresh_retry_succeeded',
    'refresh_session',
    'refresh_token',
    'refresh_token_replayed',
    'refresh_token_rotated',
    'registration',
    'registration_accepted',
    'registration_pending',
    'rejected',
    'replay',
    'request_id',
    'request_too_large',
    'request_validation_failed',
    'restore',
    'revoked',
    'rolled_back',
    'search',
    'session_logged_out',
    'session_restored',
    'sign_in',
    'status',
    'succeeded',
    'suppressed',
    'token',
    'type',
    'unavailable',
    'unsupported_media_type',
    'unverified_email_change_accepted',
    'verification_confirm',
    'verification_resend',
})
