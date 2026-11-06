"""Password, email, challenge, encryption, and diagnostic security tests."""

from datetime import UTC, datetime
import logging

import pytest

from app.application.email_registration import (
    AuthenticationCryptographyError,
    EncryptedOutboxPayload,
    VerificationEmailMessage,
)
from app.domain.authentication import (
    AccountId,
    ChallengeCompletionAction,
    ChallengeId,
    ChallengePurpose,
    EmailVerificationChallengeRecord,
)
from app.domain.registration import PasswordPolicyError, require_password_length
from app.infrastructure.authentication.registration import (
    AesGcmOutboxCipher,
    Argon2idPasswordHasher,
    EmailValidatorNormalizer,
    HmacVerificationMaterialProvider,
    LocalCompromisedPasswordChecker,
    SafeAuthenticationEventLogger,
)


NOW = datetime(2026, 9, 1, tzinfo=UTC)


def test_password_policy_accepts_spaces_unicode_and_at_least_64_characters():
    require_password_length("x" * 15)
    require_password_length("long password with spaces")
    require_password_length("安全なパスワードです" * 8)
    require_password_length("x" * 64)
    with pytest.raises(PasswordPolicyError):
        require_password_length("too short")
    with pytest.raises(PasswordPolicyError):
        require_password_length("x" * 1025)


def test_argon2id_hash_never_contains_plaintext_and_supports_rehash_detection():
    password = "a private passphrase with spaces"
    hasher = Argon2idPasswordHasher(
        time_cost=2, memory_cost_kib=19456, parallelism=1
    )
    encoded = hasher.hash(password)

    assert encoded.startswith("$argon2id$")
    assert password not in encoded
    assert hasher.verify(encoded, password)
    assert not hasher.verify(encoded, "a different private passphrase")
    assert not hasher.needs_rehash(encoded)


def test_local_compromised_password_checker_is_offline_and_deterministic():
    checker = LocalCompromisedPasswordChecker()
    assert checker.is_compromised("passwordpassword")
    assert not checker.is_compromised("a unique local test phrase 9472")


def test_email_normalization_preserves_plus_and_dots_with_casefolded_idna_domain():
    normalized = EmailValidatorNormalizer().normalize(
        "User.Name+tag@BÜCHER.de"
    )

    assert normalized.display.endswith("@bücher.de")
    assert normalized.comparison == "user.name+tag@xn--bcher-kva.de"
    assert "User.Name+tag" not in repr(normalized)


def test_email_normalization_rejects_invalid_and_overlong_addresses():
    normalizer = EmailValidatorNormalizer()
    with pytest.raises(Exception, match="Email address is invalid"):
        normalizer.normalize("not-an-email")
    with pytest.raises(Exception, match="Email address is invalid"):
        normalizer.normalize(f"{'x' * 300}@example.com")


def test_hmac_material_uses_six_digits_leading_zero_and_constant_time_path(monkeypatch):
    monkeypatch.setattr("secrets.randbelow", lambda _limit: 42)
    monkeypatch.setattr("secrets.token_urlsafe", lambda _bytes: "t" * 43)
    provider = HmacVerificationMaterialProvider(
        b"d" * 32, key_id="digest-test", key_version=7
    )
    challenge_id = ChallengeId(__import__("uuid").uuid4())
    issued = provider.issue(challenge_id, ChallengePurpose.REGISTRATION)
    challenge = EmailVerificationChallengeRecord(
        id=challenge_id,
        user_account_id=AccountId(__import__("uuid").uuid4()),
        purpose=ChallengePurpose.REGISTRATION,
        flow_token_digest=issued.flow_token_digest,
        code_digest=issued.code_digest,
        target_normalized_email="private@example.test",
        issued_at=NOW,
        expires_at=NOW,
        flow_expires_at=NOW,
        resend_available_at=NOW,
        digest_key_id=issued.key_id,
        digest_key_version=issued.key_version,
        completion_action=ChallengeCompletionAction.ACTIVATE_ACCOUNT,
    )

    assert issued.code == "000042"
    assert provider.verify_code(challenge, "000042")
    assert not provider.verify_code(challenge, "000043")
    assert issued.code not in repr(issued)
    assert issued.flow_token not in repr(issued)


def test_aes_gcm_round_trip_unique_nonce_tamper_and_wrong_version():
    cipher = AesGcmOutboxCipher(b"e" * 32, key_id="email-test", key_version=3)
    message = VerificationEmailMessage(
        code="012345",
        expires_at=NOW,
        purpose=ChallengePurpose.REGISTRATION,
    )
    first = cipher.encrypt(message, context=b"email-verification:first")
    second = cipher.encrypt(message, context=b"email-verification:first")

    assert first.ciphertext != second.ciphertext
    assert b"012345" not in first.ciphertext
    assert cipher.decrypt(first, context=b"email-verification:first") == message

    tampered = EncryptedOutboxPayload(
        ciphertext=first.ciphertext[:-1] + bytes([first.ciphertext[-1] ^ 1]),
        key_id=first.key_id,
        key_version=first.key_version,
    )
    with pytest.raises(AuthenticationCryptographyError) as captured:
        cipher.decrypt(tampered, context=b"email-verification:first")
    assert "012345" not in str(captured.value)

    wrong_version = EncryptedOutboxPayload(
        ciphertext=first.ciphertext,
        key_id=first.key_id,
        key_version=99,
    )
    with pytest.raises(AuthenticationCryptographyError):
        cipher.decrypt(wrong_version, context=b"email-verification:first")

    wrong_key = AesGcmOutboxCipher(b"z" * 32, key_id="email-test", key_version=3)
    with pytest.raises(AuthenticationCryptographyError) as captured:
        wrong_key.decrypt(first, context=b"email-verification:first")
    assert "012345" not in str(captured.value)


def test_structured_authentication_logs_are_allow_listed(caplog):
    caplog.set_level(logging.INFO, logger="pos.authentication.registration")
    SafeAuthenticationEventLogger().emit(
        event="authentication_operation",
        operation="registration",
        outcome="accepted",
        request_id="request-safe",
    )

    assert "private@example.test" not in caplog.text
    assert "012345" not in caplog.text
    record = caplog.records[-1]
    assert record.operation == "registration"
    assert record.request_id == "request-safe"
