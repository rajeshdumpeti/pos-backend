"""Deterministic admission, HTTP, proxy, claim and containment regressions."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import logging
import traceback

import jwt
import pytest
from fastapi.testclient import TestClient

from app.application.authentication_security import (
    AbusePolicy, AuthenticationAdmission, Bucket, DEFAULT_POLICIES, LimiterUnavailableError,
)
from app.application.session_lifecycle import AccessTokenInvalidError, RefreshCommand, RefreshSessionReplayedError, SignInCommand
from app.config import load_settings
from app.core.authentication_security import TrustedClientSource
from app.infrastructure.authentication.abuse_control import HmacSecurityKeys, InMemoryLimiter, RedisLimiter
from app.infrastructure.authentication.registration import SafeAuthenticationEventLogger, Argon2idPasswordHasher
from app.main import create_app
from tests.test_session_api import StubSessionService
from tests.test_session_lifecycle import State, service, NOW
from tests.test_session_security import ACCOUNT_ID, SESSION_ID
from app.application.session_lifecycle import AccessTokenClaims, SessionPolicy
from app.infrastructure.authentication.session_security import JwtAccessTokenCodec


def admission(clock=lambda: 0, max_entries=100):
    limiter = InMemoryLimiter(clock=clock, max_entries=max_entries)
    policies = {name: AbusePolicy(4, 2, 60) for name in DEFAULT_POLICIES}
    return AuthenticationAdmission(limiter, HmacSecurityKeys(bytes(range(32))), policies), limiter


def test_identity_normalization_separation_privacy_and_expiry():
    now = [0]
    gate, limiter = admission(lambda: now[0])
    for email in ['Person@example.com', 'person@EXAMPLE.COM']:
        assert gate.check_identity('sign_in', email, email=True) == 0
    assert gate.check_identity('sign_in', 'PERSON@example.com', email=True) == 60
    assert gate.check_identity('sign_in', 'other@example.com', email=True) == 0
    assert gate.check_identity('registration', 'person@example.com', email=True) == 0
    assert all(len(key) == 64 and '@' not in key for key in limiter._entries)
    now[0] = 59.1
    assert gate.check_identity('sign_in', 'person@example.com', email=True) == 1
    now[0] = 60
    assert gate.check_identity('sign_in', 'person@example.com', email=True) == 0


def test_local_atomic_admission_and_bounded_cardinality():
    limiter = InMemoryLimiter(clock=lambda: 0, max_entries=2)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: limiter.consume((Bucket('same', 5, 60),)), range(30)))
    assert results.count(0) == 5
    limiter.consume((Bucket('second', 5, 60),))
    with pytest.raises(LimiterUnavailableError):
        limiter.consume((Bucket('third', 5, 60),))
    assert len(limiter._entries) == 2


@pytest.mark.parametrize('headers', [['192.0.2.1'], ['192.0.2.2'], ['invalid'], ['a', 'b']])
def test_untrusted_forwarding_is_ignored(headers):
    assert TrustedClientSource(('10.0.0.0/8',)).resolve('198.51.100.5', headers, ['untrusted']) == '198.51.100.5'


def test_trusted_chain_uses_nearest_untrusted_hop_and_normalizes():
    resolver = TrustedClientSource(('10.0.0.0/8',))
    assert resolver.resolve('10.0.0.1', ['192.0.2.9, 198.51.100.1, 10.0.0.2'], []) == '198.51.100.1'
    assert resolver.resolve('::ffff:198.51.100.1', [], []) == '198.51.100.1'
    for values, standard in [(['bad'], []), (['1.2.3.4', '1.2.3.5'], []), (['1.2.3.4'], ['for=1.2.3.4']), (['fe80::1%eth0'], [])]:
        with pytest.raises(ValueError):
            resolver.resolve('10.0.0.1', values, standard)


def test_redis_failure_drops_raw_exception_and_arguments():
    class Broken:
        def eval(self, *args):
            raise RuntimeError('sensitive-sentinel')
    with pytest.raises(LimiterUnavailableError) as caught:
        RedisLimiter(Broken()).consume((Bucket('private', 1, 60),))
    assert 'sensitive-sentinel' not in ''.join(traceback.format_exception(caught.value))


def test_sign_in_throttled_and_spoofed_headers_cannot_reset_source():
    settings = load_settings({'AUTH_LIMIT_SIGN_IN_SOURCE': '2'})
    api = TestClient(create_app(settings, email_session_service=StubSessionService()))
    for i in range(3):
        response = api.post('/api/v1/auth/sessions', json={'email': f'person{i}@example.com', 'password': 'test'},
                            headers={'X-Forwarded-For': f'192.0.2.{i}', 'X-Request-ID': 'safe-id'})
        assert response.status_code == (200 if i < 2 else 429)
    assert response.json()['code'] == 'rate_limited'
    assert 1 <= int(response.headers['Retry-After']) <= 900
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Pragma'] == 'no-cache'
    assert response.json()['request_id'] == response.headers['X-Request-ID'] == 'safe-id'


def test_forgot_suppression_and_dependency_failure_are_generic(monkeypatch):
    from app.application.password_recovery import PasswordResetAcceptedResult
    class Recovery:
        calls = 0
        def request(self, command):
            self.calls += 1
            return PasswordResetAcceptedResult()
    recovery = Recovery()
    api = TestClient(create_app(load_settings({'AUTH_LIMIT_PASSWORD_RESET_REQUEST_IDENTITY': '1'}),
                               password_recovery_service=recovery))
    first = api.post('/api/v1/auth/password-resets', json={'email': 'person@example.com'})
    second = api.post('/api/v1/auth/password-resets', json={'email': 'PERSON@example.com'})
    assert recovery.calls == 1
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert 'Retry-After' not in second.headers
    def unavailable(*args, **kwargs):
        raise LimiterUnavailableError()
    monkeypatch.setattr(InMemoryLimiter, 'consume', unavailable)
    failed = api.post('/api/v1/auth/password-resets', json={'email': 'unknown@example.com'})
    assert failed.status_code == 202 and failed.json() == first.json()
    assert api.get('/api/v1/auth/session').status_code == 503


@pytest.mark.parametrize('headers', [
    [('Authorization', 'Bearer x'), ('Authorization', 'Bearer y')],
    {'Authorization': 'Bearer x, Bearer y'}, {'Authorization': 'Bearer  x'},
    {'Authorization': 'Basic x'}, {'Authorization': 'Bearer ' + 'x' * 4097},
])
def test_ambiguous_authorization_rejected(headers):
    api = TestClient(create_app(load_settings({}), email_session_service=StubSessionService()))
    response = api.get('/api/v1/auth/session', headers=headers)
    assert response.status_code == 401
    assert response.headers['Cache-Control'] == 'no-store'


def test_sensitive_input_bounds_json_and_safe_details():
    api = TestClient(create_app(load_settings({}), email_session_service=StubSessionService()))
    path = '/api/v1/auth/sessions'
    assert api.post(path, content=b'x'*16385, headers={'content-type': 'application/json'}).status_code == 413
    assert api.post(path, content='{}', headers={'content-type': 'text/plain'}).status_code == 415
    assert api.post(path, content='{"email":"a","email":"b"}', headers={'content-type': 'application/json'}).status_code == 422
    response = api.post(path, json={'email':'person@example.com','password':'x'*1025,'sensitive-sentinel':'hidden'})
    assert response.status_code == 422 and 'sensitive-sentinel' not in response.text
    assert api.get('/api/v1/auth/session?access_token=placeholder', headers={'authorization':'Bearer x'}).status_code == 400


def test_claim_confusion_and_unsigned_tokens():
    key = 'k'*64
    codec = JwtAccessTokenCodec(key, SessionPolicy())
    token, _ = codec.issue(AccessTokenClaims(ACCOUNT_ID, SESSION_ID, 0), now=NOW)
    base = jwt.decode(token, options={'verify_signature':False})
    mutations = [{'aep':True}, {'iat':str(base['iat'])}, {'exp':10**99}, {'iat':base['iat']+1},
                 {'jti':'invalid'}, {'sid':None}, {'aud':['pos-mobile']}, {'exp':base['exp']+1}]
    for mutation in mutations:
        with pytest.raises(AccessTokenInvalidError):
            codec.verify(jwt.encode({**base, **mutation},key,algorithm='HS256'),now=NOW)
    with pytest.raises(AccessTokenInvalidError):
        codec.verify(jwt.encode(base, '', algorithm='none'), now=NOW)
    with pytest.raises(AccessTokenInvalidError):
        codec.verify('x'*4097, now=NOW)


def test_retry_receipt_cannot_bypass_epoch_or_disabled_account():
    state = State()
    sessions = service(state)
    original = sessions.sign_in(SignInCommand('person@example.test','correct','request'))
    sessions.refresh(RefreshCommand(original.refresh_token, 'retry', 'request'))
    state.accounts.account = replace(state.accounts.account, authentication_epoch=1)
    with pytest.raises(RefreshSessionReplayedError):
        sessions.refresh(RefreshCommand(original.refresh_token, 'retry', 'request'))
    assert all(s.revoked_at for s in state.sessions.records.values())


def test_revoked_access_cannot_log_out_new_sessions():
    state = State()
    sessions = service(state)
    old = sessions.sign_in(SignInCommand('person@example.test','correct','request'))
    sessions.logout_current(old.access_token, 'request')
    new = sessions.sign_in(SignInCommand('person@example.test','correct','request'))
    sessions.logout_all(old.access_token, 'request')
    assert sessions.current(new.access_token, 'request').session_id == new.session_id


def test_password_adapter_rejects_oversize_before_primitive(monkeypatch):
    hasher = Argon2idPasswordHasher(time_cost=2, memory_cost_kib=19456, parallelism=1)
    def forbidden(*args):
        pytest.fail('Expensive primitive called')
    monkeypatch.setattr(hasher._hasher.__class__, 'hash', forbidden)
    with pytest.raises(Exception):
        hasher.hash('x'*1025)
    assert hasher.verify('unused','x'*1025) is False


def test_log_injection_fields_and_diagnostic_failure(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger='pos.authentication.registration')
    logger = SafeAuthenticationEventLogger()
    logger.emit(event='private\nforged=true', operation='person@example.com', outcome='private',
                request_id='bad\nline', account_id='private', error_category='private')
    record = caplog.records[-1]
    assert record.message == 'redacted'
    assert record.account_id is None and record.request_id == 'redacted'
    assert 'private' not in caplog.text and 'forged' not in caplog.text
    monkeypatch.setattr(logging.Logger, 'log', lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    logger.emit(event='session_restored',operation='current_session',outcome='succeeded',request_id='safe')


def test_persisted_security_events_reject_non_allowlisted_metadata():
    from app.domain.authentication import SecurityEventRecord
    from uuid import uuid4
    for metadata in [{'email':'private'}, {'category':'private'}, {'token':'private'}]:
        with pytest.raises(ValueError, match='metadata'):
            SecurityEventRecord(uuid4(), 'email_sign_in_succeeded', NOW, sanitized_metadata=metadata)
    with pytest.raises(ValueError):
        SecurityEventRecord(uuid4(), 'email_sign_in_succeeded', NOW, request_id='injected\nline')


def test_protected_controls_and_secret_safe_config():
    from app.config import Settings, AppEnvironment
    from pydantic import ValidationError
    for overrides in [{'auth_rate_limit_backend':'unknown'}, {'auth_redis_timeout_ms':0},
                      {'auth_max_body_bytes':1000000}, {'auth_trusted_proxy_networks':('0.0.0.0/0',)},
                      {'auth_rate_limit_backend':'redis'}]:
        with pytest.raises((ValueError, ValidationError)):
            Settings(app_env=AppEnvironment.TEST, cors_origins=('*',), **overrides)
    with pytest.raises(ValueError) as caught:
        load_settings({'AUTH_RATE_LIMIT_KEY':'sensitive-sentinel'})
    assert 'sensitive-sentinel' not in str(caught.value)


def test_failed_signin_existing_and_unknown_have_equal_throttle_behavior():
    from app.application.session_lifecycle import InvalidCredentialsError
    outputs = []
    for email in ['person@example.com', 'unknown@example.com']:
        api = TestClient(create_app(load_settings({'AUTH_LIMIT_SIGN_IN_IDENTITY':'1'}),
                                   email_session_service=StubSessionService(InvalidCredentialsError())))
        responses = [api.post('/api/v1/auth/sessions', json={'email':email,'password':'test'},
                              headers={'X-Request-ID':'privacy'}) for _ in range(2)]
        outputs.append([(r.status_code, r.json(), 'Retry-After' in r.headers) for r in responses])
    assert outputs[0] == outputs[1]
    assert [item[0] for item in outputs[0]] == [401,429]


@pytest.mark.parametrize("unexpected", [False, True])
def test_storage_failure_in_reset_request_is_privacy_safe(unexpected):
    from tests.test_password_recovery import ContextUnit, _service, _account, _identity
    from app.application.authentication_persistence import AuthenticationPersistenceUnavailableError
    from app.application.password_recovery import RequestPasswordResetCommand
    unit = ContextUnit()
    account = _account()
    unit.email_identities.get_by_normalized_email_for_update.return_value = _identity(account.id)
    unit.accounts.get_for_update.return_value = account
    unit.commit.side_effect = RuntimeError() if unexpected else AuthenticationPersistenceUnavailableError()
    recovery = _service(unit)
    assert recovery.request(RequestPasswordResetCommand('person@example.com','safe')).status == 'instructions_if_eligible'


def test_http_chunk_bounds_duplicate_request_id_and_unexpected_failure(caplog):
    class Broken(StubSessionService):
        def sign_in(self, command):
            raise RuntimeError('private-exception-sentinel')
    api = TestClient(create_app(load_settings({}), email_session_service=Broken()))
    response = api.post('/api/v1/auth/sessions', json={'email':'person@example.com','password':'test'},
                        headers=[('X-Request-ID','first'),('X-Request-ID','second')])
    assert response.status_code == 500
    assert response.headers['X-Request-ID'] not in {'first','second'}
    assert 'private-exception-sentinel' not in response.text + caplog.text
    response = api.post('/api/v1/auth/sessions', content=iter([b'x'*9000,b'y'*9000]),
                        headers={'Content-Type':'application/json'})
    assert response.status_code == 413


def test_redis_configuration_cannot_override_timeouts():
    import base64
    for target in ['redis://localhost/0?socket_timeout=0', 'redis://localhost/not-a-db', 'redis://localhost/0#fragment']:
        with pytest.raises(ValueError):
            load_settings({'AUTH_RATE_LIMIT_BACKEND':'redis','REDIS_URL':target,
                           'AUTH_RATE_LIMIT_KEY':base64.urlsafe_b64encode(bytes(range(32))).decode()})


def test_protected_environment_rejects_memory_and_insecure_delivery():
    import base64
    from app.config import Settings
    from pydantic import SecretStr
    secret = SecretStr(base64.urlsafe_b64encode(bytes(range(32))).decode())
    valid = dict(app_env='production', cors_origins=('https://app.example.test',),
                 database_url=SecretStr('postgresql://localhost/pos_test'),
                 jwt_secret=SecretStr('x'*64), auth_challenge_digest_key=secret,
                 auth_outbox_encryption_key=secret, auth_refresh_receipt_encryption_key=secret,
                 current_terms_version='terms-v1', current_terms_url='https://legal.pos.com/terms',
                 current_privacy_version='privacy-v1', current_privacy_url='https://legal.pos.com/privacy',
                 auth_rate_limit_backend='redis', redis_url=SecretStr('redis://localhost/0'),
                 auth_rate_limit_key=secret, auth_public_origin='https://api.example.test',
                 auth_email_provider='resend', resend_api_key=SecretStr('test'),
                 auth_email_from='auth@example.test', auth_password_reset_url='https://app.example.test/reset')
    Settings(**valid)
    for mutation in [{'auth_rate_limit_backend':'memory'}, {'auth_public_origin':None},
                     {'cors_origins':('http://app.example.test',)}, {'auth_email_provider':'local'},
                     {'local_email_inspection_enabled':True}]:
        with pytest.raises(ValueError):
            Settings(**{**valid, **mutation})


def test_failed_rehash_cannot_create_session_or_commit():
    from app.application.email_registration import AuthenticationCryptographyError
    state = State()
    sessions = service(state)
    sessions._password_hasher.needs_rehash = lambda _: True
    def failed_rehash(_):
        raise AuthenticationCryptographyError('Password hashing failed')
    sessions._password_hasher.hash = failed_rehash
    with pytest.raises(AuthenticationCryptographyError):
        sessions.sign_in(SignInCommand('person@example.test','correct','request'))
    assert state.commits == 0 and not state.sessions.records
    assert state.accounts.account == state.account
