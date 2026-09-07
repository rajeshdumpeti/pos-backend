"""Cryptography, password, email, and safe diagnostics for registration."""

import base64
import hashlib
import hmac
import json
import logging
import secrets
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
    IssuedVerificationMaterial,
    VerificationEmailMessage,
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
        try:
            return self._hasher.hash(password)
        except HashingError as error:
            _raise_crypto_error("Password hashing failed", error)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except VerificationError:
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
        LOGGER.info(
            event,
            extra={
                "operation": operation,
                "outcome": outcome,
                "request_id": request_id,
                "error_category": error_category,
                "account_id": None if account_id is None else str(account_id),
                "challenge_id": None
                if challenge_id is None
                else str(challenge_id),
                "session_id": None if session_id is None else str(session_id),
            },
        )


class SafeAuthenticationAvailabilityDiagnostic:
    """Emit only allow-listed fields when authentication is unconfigured."""

    def emit_configuration_incomplete(
        self, *, operation: str, request_id: str
    ) -> None:
        LOGGER.warning(
            "authentication_configuration_unavailable",
            extra={
                "operation": operation,
                "outcome": "unavailable",
                "category": "configuration_incomplete",
                "request_id": request_id,
            },
        )


def encode_key_for_local_generation(key: bytes) -> str:
    """Return URL-safe base64 for an explicitly generated local key."""
    return base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")


def _parse_datetime(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)
