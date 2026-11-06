"""Service-level rotation/reset proofs through the existing disposable DB guard."""
from concurrent.futures import ThreadPoolExecutor
import os

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.session_lifecycle import (
    EmailSessionService, SessionPolicy, SignInCommand, RefreshCommand,
    AccessTokenInvalidError, RefreshSessionInvalidError, RefreshSessionReplayedError,
    RefreshIdempotencyConflictError,
)
from app.application.password_recovery import RequestPasswordResetCommand, ConfirmPasswordResetCommand
from app.infrastructure.authentication.session_security import JwtAccessTokenCodec, OpaqueRefreshTokenGenerator, AesGcmRefreshReceiptCipher
from app.infrastructure.authentication.registration import EmailValidatorNormalizer
from app.infrastructure.authentication.unit_of_work import SqlAlchemyAuthenticationUnitOfWork
from app.infrastructure.authentication.models import RefreshSessionModel, RefreshRotationRetryReceiptModel
from tests.test_password_recovery_postgresql import (
    engine, empty_tables, recovery, _create_account, _latest_token, FastHasher, NOW,
)

pytestmark = pytest.mark.skipif(not os.environ.get('POS_TEST_DATABASE_URL'), reason='guarded PostgreSQL configuration unavailable')


def session_service(factory, cipher=None):
    return EmailSessionService(
        lambda rid: SqlAlchemyAuthenticationUnitOfWork(factory, request_id=rid),
        FastHasher(), EmailValidatorNormalizer(), JwtAccessTokenCodec('k'*64,SessionPolicy()),
        OpaqueRefreshTokenGenerator(), cipher or AesGcmRefreshReceiptCipher(bytes(range(32)),key_id='test',key_version=1),
        FastHasher().hash('dummy'), clock=lambda: NOW)


def setup_session(recovery):
    _, _, factory = recovery
    account = _create_account(factory)
    with SqlAlchemyAuthenticationUnitOfWork(factory, request_id='setup') as unit:
        unit.accounts.update_password_hash(account, FastHasher().hash('test-password'), NOW)
        unit.commit()
    service = session_service(factory)
    return service, service.sign_in(SignInCommand('person@example.com','test-password','signin'))


def test_service_concurrent_rotation_single_winner_and_family_containment(engine, recovery):
    service, tokens = setup_session(recovery)
    def rotate(i):
        try:
            service.refresh(RefreshCommand(tokens.refresh_token, 'rotation-'+str(i), 'race'))
            return 'success'
        except (RefreshSessionInvalidError, RefreshSessionReplayedError):
            return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(rotate, range(2)))
    assert outcomes.count('success') == 1
    with Session(engine) as session:
        assert session.get(RefreshSessionModel,tokens.session_id).revoked_at is not None
        assert len(session.scalars(select(RefreshRotationRetryReceiptModel)).all()) == 1


def test_password_reset_invalidates_actual_access_refresh_and_retry(engine, recovery):
    sessions, old = setup_session(recovery)
    rotated = sessions.refresh(RefreshCommand(old.refresh_token,'rotation','request'))
    reset, cipher, _ = recovery
    reset.request(RequestPasswordResetCommand('person@example.com','request'))
    reset.confirm(ConfirmPasswordResetCommand(_latest_token(engine,cipher),'a strong replacement password','confirm'))
    with pytest.raises(AccessTokenInvalidError):
        sessions.current(old.access_token,'restore')
    with pytest.raises(RefreshSessionInvalidError):
        sessions.refresh(RefreshCommand(rotated.refresh_token,'next','refresh'))
    with pytest.raises(RefreshSessionReplayedError):
        sessions.refresh(RefreshCommand(old.refresh_token,'rotation','retry'))


def test_rotation_crypto_failure_rolls_back_token_and_receipt(engine, recovery):
    sessions, old = setup_session(recovery)
    class BrokenCipher:
        def encrypt(self, *args, **kwargs):
            raise RuntimeError('safe test failure')
    broken = session_service(recovery[2], BrokenCipher())
    with pytest.raises(RuntimeError):
        broken.refresh(RefreshCommand(old.refresh_token,'first','failure'))
    with Session(engine) as session:
        assert session.scalars(select(RefreshRotationRetryReceiptModel)).all() == []
    assert sessions.refresh(RefreshCommand(old.refresh_token,'second','success')).session_id == old.session_id


def test_same_idempotency_key_cannot_rotate_different_material(engine, recovery):
    sessions, original = setup_session(recovery)
    rotated = sessions.refresh(
        RefreshCommand(original.refresh_token, "one-logical-operation", "first")
    )
    with pytest.raises(RefreshIdempotencyConflictError):
        sessions.refresh(
            RefreshCommand(rotated.refresh_token, "one-logical-operation", "second")
        )
