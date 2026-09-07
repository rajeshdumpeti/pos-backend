"""Bounded local/Redis admission adapters and domain-separated private keys."""
import hashlib
import hmac
import math
import secrets
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings

from app.application.authentication_security import (
    AuthenticationAdmission, Bucket, LimiterUnavailableError,
)
from app.infrastructure.authentication.registration import EmailValidatorNormalizer


class HmacSecurityKeys:
    def __init__(self, key: bytes) -> None:
        self._key = key
        self._normalizer = EmailValidatorNormalizer()

    def derive(self, domain: str, value: str) -> str:
        return hmac.new(self._key, ("pos:auth-limit:v1:" + domain + "\0" + value).encode(),
                        hashlib.sha256).hexdigest()

    def identity(self, value: str) -> str:
        try:
            return self._normalizer.normalize(value).comparison
        except ValueError:
            return "invalid-identity"


class InMemoryLimiter:
    """Explicitly local; capped storage, atomic admission, non-sliding cooldown."""
    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 max_entries: int = 100000) -> None:
        self._clock = clock
        self._max_entries = max_entries
        self._entries: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def consume(self, buckets: tuple[Bucket, ...]) -> int:
        with self._lock:
            now = self._clock()
            # Bounded by max_entries; local only. No background thread or network.
            self._entries = {k: v for k, v in self._entries.items() if v[1] > now}
            for bucket in buckets:
                count, expiry = self._entries.get(bucket.key, (0, now + bucket.window_seconds))
                if count >= bucket.limit:
                    return max(1, min(3600, math.ceil(expiry - now)))
                if not count and len(self._entries) >= self._max_entries:
                    raise LimiterUnavailableError("Authentication admission unavailable")
                self._entries[bucket.key] = (count + 1, expiry)
            return 0


# Two fixed same-slot keys, bounded hash fields/ZSET members, bounded cleanup.
# Redis TIME prevents app-instance clock skew. Individual logical entries expire
# at first-use + window; rejected requests never extend their expiry.
REDIS_ADMISSION_SCRIPT = """
local now = tonumber(redis.call('TIME')[1])
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now, 'LIMIT', 0, 128)
for _, k in ipairs(expired) do
  redis.call('HDEL', KEYS[1], k)
  redis.call('ZREM', KEYS[2], k)
end
for i = 2, #ARGV, 3 do
  local key, limit, window = ARGV[i], tonumber(ARGV[i+1]), tonumber(ARGV[i+2])
  local expiry = tonumber(redis.call('ZSCORE', KEYS[2], key) or '0')
  local count = tonumber(redis.call('HGET', KEYS[1], key) or '0')
  if expiry <= now then
    if count == 0 and redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[1]) then
      return -1
    end
    count = 0
    expiry = now + window
  end
  if count >= limit then return math.max(1, math.min(3600, expiry-now)) end
  redis.call('HSET', KEYS[1], key, count+1)
  redis.call('ZADD', KEYS[2], expiry, key)
  redis.call('EXPIRE', KEYS[1], 3601)
  redis.call('EXPIRE', KEYS[2], 3601)
end
return 0
"""


class RedisLimiter:
    def __init__(self, client: object, *, max_entries: int = 100000,
                 namespace: str = 'pos:{auth-limit-v1}') -> None:
        self._client = client
        self._max_entries = max_entries
        self._namespace = namespace

    def consume(self, buckets: tuple[Bucket, ...]) -> int:
        args: list[object] = [self._max_entries]
        for bucket in buckets:
            args.extend((bucket.key, bucket.limit, bucket.window_seconds))
        try:
            result = self._client.eval(REDIS_ADMISSION_SCRIPT, 2,
                self._namespace + ':counts', self._namespace + ':expiry', *args)
            if type(result) is not int or not 0 <= result <= 3600:
                raise LimiterUnavailableError("Authentication admission unavailable")
            return result
        except Exception:
            raise LimiterUnavailableError("Authentication admission unavailable") from None


def compose_admission(settings: "Settings") -> tuple[AuthenticationAdmission, Callable[[], None]]:
    from app.config import decode_authentication_key
    key = (secrets.token_bytes(32) if settings.auth_rate_limit_key is None else
           decode_authentication_key(settings.auth_rate_limit_key, 'AUTH_RATE_LIMIT_KEY'))
    close = lambda: None
    if settings.auth_rate_limit_backend == 'redis':
        from redis import Redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry
        assert settings.redis_url is not None
        # URL query options must not override the explicit security settings.
        client = Redis.from_url(settings.redis_url.get_secret_value(),
            socket_connect_timeout=settings.auth_redis_timeout_ms / 1000,
            socket_timeout=settings.auth_redis_timeout_ms / 1000,
            retry=Retry(NoBackoff(), 0), retry_on_timeout=False, max_connections=20)
        limiter = RedisLimiter(client, max_entries=settings.auth_rate_limit_max_entries)
        close = client.close
    else:
        limiter = InMemoryLimiter(max_entries=settings.auth_rate_limit_max_entries)
    return AuthenticationAdmission(limiter, HmacSecurityKeys(key),
        settings.auth_rate_limit_policies, settings.auth_rate_limit_global), close
