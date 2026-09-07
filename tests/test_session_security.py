"""Focused cryptographic and secret-handling tests for session lifecycle."""

from datetime import UTC, datetime, timedelta
import traceback
from uuid import UUID

import jwt
import pytest

from app.application.session_lifecycle import (
    AccessTokenClaims,
    AccessTokenExpiredError,
    AccessTokenInvalidError,
    SessionPolicy,
    SessionTokenResult,
)
from app.domain.authentication import AccountId, RefreshSessionId
from app.infrastructure.authentication.session_security import (
    AesGcmRefreshReceiptCipher,
    JwtAccessTokenCodec,
    OpaqueRefreshTokenGenerator,
)


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
ACCOUNT_ID = AccountId(UUID("00000000-0000-4000-8000-000000000001"))
SESSION_ID = RefreshSessionId(UUID("00000000-0000-4000-8000-000000000002"))


def test_access_token_has_frozen_lifetime_minimal_claims_and_explicit_validation():
    codec = JwtAccessTokenCodec("fake-signing-key-that-is-at-least-32-characters", SessionPolicy())
    token, expires_at = codec.issue(AccessTokenClaims(ACCOUNT_ID, SESSION_ID, 3), now=NOW)

    assert expires_at == NOW + timedelta(minutes=10)
    assert codec.verify(token, now=NOW + timedelta(minutes=9)).authentication_epoch == 3
    with pytest.raises(AccessTokenExpiredError):
        codec.verify(token, now=expires_at)
    with pytest.raises(AccessTokenInvalidError):
        codec.verify(token + "tampered", now=NOW)

    claims = jwt.decode(
        token,
        options={"verify_signature": False, "verify_exp": False},
        algorithms=["HS256"],
    )
    assert set(claims) == {
        "iss", "aud", "sub", "sid", "aep", "typ", "iat", "nbf", "exp", "jti"
    }


def test_access_token_rejects_wrong_signature_algorithm_issuer_audience_type_and_claims():
    key = "k" * 64
    codec = JwtAccessTokenCodec(key, SessionPolicy())
    token, _ = codec.issue(AccessTokenClaims(ACCOUNT_ID, SESSION_ID, 0), now=NOW)

    with pytest.raises(AccessTokenInvalidError):
        JwtAccessTokenCodec("d" * 64, SessionPolicy()).verify(token, now=NOW)
    with pytest.raises(AccessTokenInvalidError):
        JwtAccessTokenCodec(key, SessionPolicy(), issuer="wrong").verify(token, now=NOW)
    with pytest.raises(AccessTokenInvalidError):
        JwtAccessTokenCodec(key, SessionPolicy(), audience="wrong").verify(token, now=NOW)

    base = jwt.decode(token, options={"verify_signature": False}, algorithms=["HS256"])
    for mutation in (
        {**base, "typ": "refresh"},
        {name: value for name, value in base.items() if name != "sid"},
        {**base, "nbf": int(NOW.timestamp()) + 1},
    ):
        altered = jwt.encode(mutation, key, algorithm="HS256")
        with pytest.raises(AccessTokenInvalidError):
            codec.verify(altered, now=NOW)
    wrong_algorithm = jwt.encode(base, key, algorithm="HS384")
    with pytest.raises(AccessTokenInvalidError):
        codec.verify(wrong_algorithm, now=NOW)


def test_refresh_values_are_high_entropy_and_only_digest_is_stable():
    generator = OpaqueRefreshTokenGenerator()
    first = generator.issue()
    second = generator.issue()

    assert first.startswith("v1.") and len(first) >= 46
    assert first != second
    assert generator.digest(first) != first
    assert generator.digest(first) == generator.digest(first)


def test_retry_receipt_round_trip_and_failure_never_expose_tokens(caplog):
    sentinel = "must_never_appear"
    cipher = AesGcmRefreshReceiptCipher(b"r" * 32, key_id="receipt-key", key_version=1)
    result = SessionTokenResult(
        sentinel + "-access",
        sentinel + "-refresh",
        "Bearer",
        NOW + timedelta(minutes=10),
        NOW + timedelta(days=7),
        ACCOUNT_ID,
        SESSION_ID,
        True,
    )
    encrypted = cipher.encrypt(result, context=b"context")
    from app.domain.authentication import RefreshRotationReceiptRecord

    receipt = RefreshRotationReceiptRecord(
        id=UUID("00000000-0000-4000-8000-000000000003"),
        refresh_session_id=SESSION_ID,
        token_family_id=UUID("00000000-0000-4000-8000-000000000004"),
        previous_token_digest="digest",
        refresh_request_id="retry-1",
        response_ciphertext=encrypted.ciphertext,
        encryption_key_id=encrypted.key_id,
        encryption_key_version=encrypted.key_version,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    restored = cipher.decrypt(receipt, context=b"context")
    assert restored == result
    assert sentinel.encode() not in encrypted.ciphertext

    with pytest.raises(Exception) as caught:
        cipher.decrypt(receipt, context=b"wrong-context")
    rendered = "".join(traceback.format_exception(caught.value)) + caplog.text
    assert sentinel not in rendered
    assert result.access_token not in repr(result)
    assert result.refresh_token not in repr(result)


def test_invalid_access_token_traceback_never_echoes_bearer_value():
    sentinel = "must_never_appear"
    codec = JwtAccessTokenCodec("k" * 64, SessionPolicy())
    with pytest.raises(AccessTokenInvalidError) as caught:
        codec.verify(sentinel, now=NOW)
    assert sentinel not in "".join(traceback.format_exception(caught.value))
