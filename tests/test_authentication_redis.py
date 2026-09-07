"""Redis adapter tests; live tests require an explicit disposable local DB 15."""
from concurrent.futures import ThreadPoolExecutor
import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from app.application.authentication_security import Bucket
from app.infrastructure.authentication.abuse_control import RedisLimiter, HmacSecurityKeys


def require_safe_redis_test_target(value):
    try:
        parsed = urlsplit(value)
        parsed.port
        valid = (parsed.scheme == 'redis' and parsed.hostname in {'localhost','127.0.0.1','::1'}
                 and parsed.path == '/15' and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeError('Redis tests require disposable local database 15') from None


@pytest.mark.parametrize('value', ['redis://remote/15', 'redis://localhost/0', 'redis://localhost/15?db=0', 'invalid'])
def test_redis_isolation_guard(value):
    with pytest.raises(RuntimeError):
        require_safe_redis_test_target(value)


def test_redis_adapter_uses_one_atomic_call_with_private_members():
    class Client:
        calls = []
        def eval(self, *args):
            self.calls.append(args)
            return 0
    client = Client()
    keys = HmacSecurityKeys(bytes(range(32)))
    bucket = Bucket(keys.derive('sign_in:identity', 'person@example.com'), 2, 60)
    assert RedisLimiter(client).consume((bucket,)) == 0
    assert len(client.calls) == 1
    assert client.calls[0][1] == 2
    assert 'person@example.com' not in repr(client.calls)


@pytest.fixture
def live_limiter():
    target = os.environ.get('POS_TEST_REDIS_URL')
    if not target or os.environ.get('POS_TEST_REDIS_DISPOSABLE') != '1':
        pytest.skip('guarded disposable Redis test configuration unavailable')
    require_safe_redis_test_target(target)
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    client = Redis.from_url(target, socket_timeout=.2, socket_connect_timeout=.2,
                            retry=Retry(NoBackoff(), 0))
    namespace = 'pos-test:{' + str(uuid4()) + '}'
    limiter = RedisLimiter(client, namespace=namespace, max_entries=2)
    try:
        yield limiter, client, namespace
    finally:
        # Only keys created by this fixture. No FLUSHDB/FLUSHALL.
        try:
            client.delete(namespace + ':counts', namespace + ':expiry')
        except Exception:
            pytest.fail('Disposable Redis cleanup unavailable', pytrace=False)
        finally:
            client.close()


def test_live_redis_atomic_expiring_bounded_admission(live_limiter):
    limiter, client, namespace = live_limiter
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: limiter.consume((Bucket('a'*64,5,60),)), range(30)))
    assert results.count(0) == 5
    assert 0 < client.ttl(namespace + ':counts') <= 3601
    assert client.zcard(namespace + ':expiry') == 1
    # Controlled server-side expiry, no sleep or destructive shared cleanup.
    client.zadd(namespace + ':expiry', {'a'*64: 0})
    assert limiter.consume((Bucket('a'*64,5,60),)) == 0
    limiter.consume((Bucket('b'*64,5,60),))
    from app.application.authentication_security import LimiterUnavailableError
    with pytest.raises(LimiterUnavailableError):
        limiter.consume((Bucket('c'*64,5,60),))
