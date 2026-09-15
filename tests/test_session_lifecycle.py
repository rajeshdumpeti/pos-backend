"""Application transaction tests for sign-in, refresh rotation, and replay."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.application.session_lifecycle import (
    EmailSessionService,
    InvalidCredentialsError,
    EmailNotVerifiedError,
    AccountUnavailableError,
    AccessTokenInvalidError,
    RefreshSessionInvalidError,
    RefreshSessionExpiredError,
    SessionInternalError,
    RefreshCommand,
    RefreshSessionReplayedError,
    SignInCommand,
)
from app.domain.authentication import (
    AccountId,
    AccountStatus,
    EmailIdentityRecord,
    IdentityId,
    UserAccountRecord,
)
from app.domain.registration import NormalizedEmail
from app.infrastructure.authentication.session_security import (
    AesGcmRefreshReceiptCipher,
    JwtAccessTokenCodec,
    OpaqueRefreshTokenGenerator,
)
from app.application.session_lifecycle import SessionPolicy
from app.application.email_registration import VerificationContinuationResult


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


class Passwords:
    def hash(self, password):
        return "hash:" + password

    def verify(self, password_hash, password):
        return password_hash == "hash:" + password

    def needs_rehash(self, password_hash):
        return False


class Emails:
    def normalize(self, email):
        value = email.strip().lower()
        return NormalizedEmail(email.strip(), value)


class Accounts:
    def __init__(self, account):
        self.account = account

    def get(self, account_id):
        return self.account if self.account.id == account_id else None

    def get_for_update(self, account_id):
        return self.get(account_id)

    def update_password_hash(self, account_id, password_hash, updated_at):
        return True


class Identities:
    def __init__(self, identity):
        self.identity = identity

    def get_by_normalized_email(self, normalized):
        return self.identity if normalized == self.identity.normalized_email else None


class Sessions:
    def __init__(self):
        self.records = {}

    def add(self, record):
        self.records[record.id] = record

    def get_by_token_digest_for_update(self, digest):
        return next((record for record in self.records.values() if record.current_token_digest == digest), None)

    def get_for_update(self, session_id):
        return self.records.get(session_id)

    def replace_token(self, session_id, expected_digest, replacement_digest, used_at):
        from dataclasses import replace

        record = self.records.get(session_id)
        if record is None or record.current_token_digest != expected_digest or record.revoked_at:
            return False
        self.records[session_id] = replace(record, current_token_digest=replacement_digest, last_used_at=used_at)
        return True

    def revoke(self, session_id, revoked_at, reason):
        from dataclasses import replace

        record = self.records.get(session_id)
        if record is None or record.revoked_at:
            return False
        self.records[session_id] = replace(record, revoked_at=revoked_at, revocation_reason=reason)
        return True

    def revoke_family(self, family_id, revoked_at, reason):
        return sum(self.revoke(record.id, revoked_at, reason) for record in list(self.records.values()) if record.token_family_id == family_id)

    def revoke_all_for_account(self, account_id, revoked_at, reason):
        return sum(self.revoke(record.id, revoked_at, reason) for record in list(self.records.values()) if record.user_account_id == account_id)


class Receipts:
    def __init__(self):
        self.records = []

    def add(self, record):
        self.records.append(record)

    def get_by_previous_digest_for_update(self, digest):
        return next((record for record in self.records if record.previous_token_digest == digest), None)

    def get_by_request_id_for_update(self, request_id):
        return next(
            (
                record
                for record in self.records
                if record.refresh_request_id == request_id
            ),
            None,
        )


class Events:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)


class Unit:
    def __init__(self, state):
        self.accounts = state.accounts
        self.email_identities = state.identities
        self.refresh_sessions = state.sessions
        self.refresh_receipts = state.receipts
        self.security_events = state.events
        self.commits = state.commits
        self.state = state

    def __enter__(self):
        self.state.enters += 1
        return self

    def __exit__(self, exception_type, *_args):
        self.state.exits += 1
        self.state.exit_exceptions.append(exception_type)
        return None

    def commit(self):
        self.state.commits += 1


class State:
    def __init__(self):
        account_id = AccountId(uuid4())
        self.account = UserAccountRecord(
            account_id, AccountStatus.ACTIVE, "POS Tester", "hash:correct", NOW,
            True, 0, "terms-v1", NOW, "privacy-v1", NOW, NOW, NOW
        )
        self.identity = EmailIdentityRecord(
            IdentityId(uuid4()), account_id, "person@example.test", "person@example.test",
            True, True, NOW, NOW, NOW
        )
        self.accounts = Accounts(self.account)
        self.identities = Identities(self.identity)
        self.sessions = Sessions()
        self.receipts = Receipts()
        self.events = Events()
        self.commits = 0
        self.enters = 0
        self.exits = 0
        self.exit_exceptions = []


def service(
    state,
    event_logger=None,
    receipt_cipher=None,
    verification_continuation=None,
):
    policy = SessionPolicy()
    return EmailSessionService(
        lambda request_id: Unit(state),
        Passwords(),
        Emails(),
        JwtAccessTokenCodec("fake-signing-key-that-is-at-least-32-characters", policy),
        OpaqueRefreshTokenGenerator(),
        receipt_cipher
        or AesGcmRefreshReceiptCipher(b"r" * 32, key_id="receipt", key_version=1),
        "hash:dummy",
        policy,
        verification_continuation=verification_continuation,
        event_logger=event_logger,
        clock=lambda: NOW,
    )


def test_sign_in_is_enumeration_safe_and_refresh_is_seven_days():
    state = State()
    sessions = service(state)
    with pytest.raises(InvalidCredentialsError):
        sessions.sign_in(SignInCommand("unknown@example.test", "wrong", "request-1"))

    result = sessions.sign_in(SignInCommand("PERSON@example.test", "correct", "request-2"))
    assert (result.refresh_expires_at - NOW).days == 7
    assert (result.access_expires_at - NOW).seconds == 600
    assert state.commits == 1
    assert len(state.sessions.records) == 1


def test_consecutive_unknown_sign_ins_remain_equivalent_and_release_each_unit_of_work():
    state = State()
    sessions = service(state)

    for request_id in ("invalid-attempt-1", "invalid-attempt-2"):
        with pytest.raises(InvalidCredentialsError, match="Email or password is invalid"):
            sessions.sign_in(SignInCommand("unknown@example.test", "wrong", request_id))

    assert state.enters == 2
    assert state.exits == 2
    assert state.exit_exceptions == [InvalidCredentialsError, InvalidCredentialsError]
    assert state.commits == 0
    assert state.sessions.records == {}


def test_unverified_and_disabled_conditions_require_a_correct_password():
    state = State()
    state.account = replace(
        state.account,
        status=AccountStatus.PENDING_VERIFICATION,
        email_verified_at=None,
    )
    state.accounts.account = state.account
    state.identity = replace(state.identity, is_verified=False, verified_at=None)
    state.identities.identity = state.identity

    class Continuation:
        def issue_sign_in_continuation(self, *_args):
            return VerificationContinuationResult(
                "v1." + "f" * 43,
                "p***@example.test",
                NOW.replace(hour=12, minute=30),
                NOW.replace(hour=12, minute=1),
            )

    with pytest.raises(EmailNotVerifiedError):
        service(state, verification_continuation=Continuation()).sign_in(
            SignInCommand("person@example.test", "correct", "unverified")
        )
    with pytest.raises(InvalidCredentialsError):
        service(state).sign_in(SignInCommand("person@example.test", "wrong", "wrong"))

    state.account = replace(state.account, status=AccountStatus.DISABLED)
    state.accounts.account = state.account
    with pytest.raises(AccountUnavailableError):
        service(state).sign_in(SignInCommand("person@example.test", "correct", "disabled"))


def test_refresh_retry_returns_exact_response_and_replay_revokes_family():
    state = State()
    sessions = service(state)
    signed_in = sessions.sign_in(SignInCommand("person@example.test", "correct", "sign-in"))
    rotated = sessions.refresh(RefreshCommand(signed_in.refresh_token, "retry-one", "rotate"))
    retried = sessions.refresh(RefreshCommand(signed_in.refresh_token, "retry-one", "retry"))
    assert retried == rotated

    with pytest.raises(RefreshSessionReplayedError):
        sessions.refresh(RefreshCommand(signed_in.refresh_token, "different-key", "replay"))
    assert next(iter(state.sessions.records.values())).revocation_reason == "refresh_replay"
    assert any(event.event_type == "refresh_token_replayed" for event in state.events.records)


def test_logout_current_and_all_are_idempotent():
    state = State()
    sessions = service(state)
    signed_in = sessions.sign_in(
        SignInCommand("person@example.test", "correct", "sign-in")
    )

    sessions.logout_current(signed_in.access_token, "logout-current")
    sessions.logout_current(signed_in.access_token, "logout-current-retry")
    sessions.logout_all(signed_in.access_token, "logout-all")
    sessions.logout_all(signed_in.access_token, "logout-all-retry")

    events = [event.event_type for event in state.events.records]
    assert events.count("session_logged_out") == 1
    assert events.count("all_sessions_logged_out") == 0


def test_access_and_refresh_credentials_are_not_interchangeable():
    state = State()
    sessions = service(state)
    signed_in = sessions.sign_in(
        SignInCommand("person@example.test", "correct", "sign-in")
    )
    with pytest.raises(AccessTokenInvalidError):
        sessions.current(signed_in.refresh_token, "wrong-bearer")
    with pytest.raises(RefreshSessionInvalidError):
        sessions.refresh(RefreshCommand(signed_in.access_token, "retry-id", "wrong-refresh"))


def test_current_session_restores_minimum_identity_and_expired_refresh_is_distinct():
    state = State()
    sessions = service(state)
    signed_in = sessions.sign_in(
        SignInCommand("person@example.test", "correct", "sign-in")
    )
    current = sessions.current(signed_in.access_token, "restore")
    assert current.account_id == state.account.id
    assert current.display_name == "POS Tester"
    assert current.onboarding_required is True

    record = next(iter(state.sessions.records.values()))
    state.sessions.records[record.id] = replace(record, expires_at=NOW)
    with pytest.raises(RefreshSessionExpiredError):
        sessions.refresh(RefreshCommand(signed_in.refresh_token, "expired", "expired"))


def test_rotation_cipher_failure_does_not_commit_partial_operation():
    class FailingCipher:
        def encrypt(self, response, *, context):
            raise SessionInternalError("Retry receipt encryption failed")

        def decrypt(self, receipt, *, context):
            raise AssertionError("not used")

    state = State()
    sessions = service(state, receipt_cipher=FailingCipher())
    signed_in = sessions.sign_in(
        SignInCommand("person@example.test", "correct", "sign-in")
    )
    with pytest.raises(SessionInternalError):
        sessions.refresh(RefreshCommand(signed_in.refresh_token, "rotation", "rotation"))
    assert state.commits == 1
    assert state.receipts.records == []


def test_diagnostics_are_allow_listed_and_emit_once_without_credentials():
    class Logger:
        def __init__(self):
            self.events = []

        def emit(self, **fields):
            self.events.append(fields)

    logger = Logger()
    state = State()
    with pytest.raises(InvalidCredentialsError):
        service(state, logger).sign_in(
            SignInCommand("must_never_appear@example.test", "must_never_appear", "safe-request")
        )

    assert logger.events == [
        {
            "event": "email_sign_in_failed",
            "operation": "email_sign_in",
            "outcome": "rejected",
            "request_id": "safe-request",
            "error_category": "invalid_credentials",
            "account_id": None,
            "session_id": None,
        }
    ]
    assert "must_never_appear" not in repr(logger.events)
