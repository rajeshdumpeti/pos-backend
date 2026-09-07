"""Secret-safe access-token, refresh-token, and retry-receipt adapters."""

import hashlib
import json
import secrets
from datetime import datetime, timezone
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import jwt
from jwt import InvalidTokenError

from app.application.session_lifecycle import (
    AccessTokenClaims,
    AccessTokenExpiredError,
    AccessTokenInvalidError,
    EncryptedRefreshReceipt,
    RefreshReceiptCipher,
    SessionInternalError,
    SessionPolicy,
    SessionTokenResult,
)
from app.domain.authentication import AccountId, RefreshRotationReceiptRecord, RefreshSessionId


class JwtAccessTokenCodec:
    """Issue and verify minimal HS256 access JWTs with an algorithm allow-list."""

    _ALGORITHM = "HS256"

    def __init__(self, signing_key: str, policy: SessionPolicy, *, issuer: str = "pos-backend", audience: str = "pos-mobile") -> None:
        self._signing_key = signing_key
        self._policy = policy
        self._issuer = issuer
        self._audience = audience

    def issue(self, claims: AccessTokenClaims, *, now: datetime) -> tuple[str, datetime]:
        issued_at = now.replace(microsecond=0)
        expires_at = issued_at + self._policy.access_lifetime
        payload = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": str(claims.account_id),
            "sid": str(claims.session_id),
            "aep": claims.authentication_epoch,
            "typ": "access",
            "iat": issued_at,
            "nbf": issued_at,
            "exp": expires_at,
            "jti": str(UUID(bytes=secrets.token_bytes(16), version=4)),
        }
        return jwt.encode(payload, self._signing_key, algorithm=self._ALGORITHM), expires_at

    def verify(self, token: str, *, now: datetime) -> AccessTokenClaims:
        if not isinstance(token, str) or len(token) > 4096:
            raise AccessTokenInvalidError("Access token is invalid")
        try:
            # Reject duplicate JSON members before the maintained verifier sees
            # them; JSON last-member-wins semantics are ambiguous for claims.
            def unique(pairs):
                result = {}
                for name, value in pairs:
                    if name in result:
                        raise ValueError("Ambiguous token")
                    result[name] = value
                return result
            parts = token.split('.')
            if len(parts) != 3:
                raise ValueError("Invalid token")
            for part in parts[:2]:
                parsed = json.loads(jwt.utils.base64url_decode(part), object_pairs_hook=unique)
                if not isinstance(parsed, dict):
                    raise ValueError("Invalid token")
            header = jwt.get_unverified_header(token)
            if header.get("typ") != "JWT" or set(header) != {"alg", "typ"}:
                raise ValueError("Invalid token")
            payload = jwt.decode(
                token,
                self._signing_key,
                algorithms=[self._ALGORITHM],
                issuer=self._issuer,
                audience=self._audience,
                options={
                    "require": ["exp", "iat", "nbf", "iss", "aud", "sub", "sid", "jti", "typ", "aep"],
                    "strict_aud": True,
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
            if any(type(payload[name]) is not int for name in ("iat", "nbf", "exp", "aep")):
                raise ValueError("Invalid token")
            if any(type(payload[name]) is not str for name in ("sub", "sid", "jti")):
                raise ValueError("Invalid token")
            for name in ("sub", "sid", "jti"):
                if str(UUID(payload[name])) != payload[name] or UUID(payload[name]).int == 0:
                    raise ValueError("Invalid token")
            issued_at = datetime.fromtimestamp(payload["iat"], timezone.utc)
            not_before = datetime.fromtimestamp(int(payload["nbf"]), timezone.utc)
            expires_at = datetime.fromtimestamp(int(payload["exp"]), timezone.utc)
            if (expires_at <= issued_at or not_before != issued_at
                    or expires_at - issued_at != self._policy.access_lifetime):
                raise ValueError("Invalid token")
            if now >= expires_at:
                raise AccessTokenExpiredError("Access token expired")
            if payload["typ"] != "access" or now < not_before or now < issued_at:
                raise AccessTokenInvalidError("Access token is invalid")
            epoch = payload["aep"]
            if not isinstance(epoch, int) or epoch < 0:
                raise AccessTokenInvalidError("Access token is invalid")
            return AccessTokenClaims(
                AccountId(UUID(str(payload["sub"]))),
                RefreshSessionId(UUID(str(payload["sid"]))),
                epoch,
            )
        except (InvalidTokenError, KeyError, TypeError, ValueError, OverflowError, OSError, RecursionError):
            raise AccessTokenInvalidError("Access token is invalid") from None


class OpaqueRefreshTokenGenerator:
    """Generate high-entropy bearer values and retain only SHA-256 digests."""

    def issue(self) -> str:
        return "v1." + secrets.token_urlsafe(32)

    def digest(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()


class AesGcmRefreshReceiptCipher(RefreshReceiptCipher):
    """Encrypt the exact rotation response for narrowly scoped safe retries."""

    _NONCE_BYTES = 12

    def __init__(self, key: bytes, *, key_id: str, key_version: int) -> None:
        self._cipher = AESGCM(key)
        self._key_id = key_id
        self._key_version = key_version

    def encrypt(self, response: SessionTokenResult, *, context: bytes) -> EncryptedRefreshReceipt:
        nonce = secrets.token_bytes(self._NONCE_BYTES)
        plaintext = json.dumps(
            {
                "access_token": response.access_token,
                "refresh_token": response.refresh_token,
                "token_type": response.token_type,
                "access_expires_at": response.access_expires_at.isoformat(),
                "refresh_expires_at": response.refresh_expires_at.isoformat(),
                "account_id": str(response.account_id),
                "session_id": str(response.session_id),
                "onboarding_required": response.onboarding_required,
            },
            separators=(",", ":"),
        ).encode()
        return EncryptedRefreshReceipt(
            nonce + self._cipher.encrypt(nonce, plaintext, context),
            self._key_id,
            self._key_version,
        )

    def decrypt(self, receipt: RefreshRotationReceiptRecord, *, context: bytes) -> SessionTokenResult:
        if (
            receipt.encryption_key_id != self._key_id
            or receipt.encryption_key_version != self._key_version
            or len(receipt.response_ciphertext) <= self._NONCE_BYTES
        ):
            raise SessionInternalError("Refresh retry receipt is unavailable")
        try:
            nonce = receipt.response_ciphertext[: self._NONCE_BYTES]
            plaintext = self._cipher.decrypt(
                nonce, receipt.response_ciphertext[self._NONCE_BYTES :], context
            )
            data = json.loads(plaintext)
            return SessionTokenResult(
                access_token=str(data["access_token"]),
                refresh_token=str(data["refresh_token"]),
                token_type=str(data["token_type"]),
                access_expires_at=datetime.fromisoformat(data["access_expires_at"]),
                refresh_expires_at=datetime.fromisoformat(data["refresh_expires_at"]),
                account_id=UUID(str(data["account_id"])),
                session_id=UUID(str(data["session_id"])),
                onboarding_required=bool(data["onboarding_required"]),
            )
        except (InvalidTag, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise SessionInternalError("Refresh retry receipt is unavailable") from None
