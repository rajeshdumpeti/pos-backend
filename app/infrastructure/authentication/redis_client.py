"""Bounded redis-py composition with private representations and verified TLS."""

from typing import TYPE_CHECKING, Never
from urllib.parse import urlsplit

from redis import BlockingConnectionPool, Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.application.authentication_security import LimiterUnavailableError

if TYPE_CHECKING:
    from app.config import Settings


class _PrivateConnectionPool(BlockingConnectionPool):
    def __repr__(self) -> str:
        # redis-py's default pool repr renders every connection kwarg, including password.
        return "<POS authentication Redis pool>"


def raise_safe_redis_error(cause: Exception) -> Never:
    error = LimiterUnavailableError("Authentication admission unavailable")
    error._internal_cause = cause
    raise error from RuntimeError("Redis details redacted")


def create_redis_client(settings: "Settings") -> Redis:
    """Construct lazily; URL options cannot override security or timeout settings."""
    assert settings.redis_url is not None
    pool = None
    try:
        raw = settings.redis_url.get_secret_value()
        target = urlsplit(raw)
        if target.query or target.fragment:
            raise ValueError("Redis connection overrides are forbidden")
        timeout = settings.auth_redis_timeout_ms / 1000
        tls = (
            {"ssl_cert_reqs": "required", "ssl_check_hostname": True}
            if target.scheme == "rediss"
            else {}
        )
        pool = _PrivateConnectionPool.from_url(
            raw,
            timeout=timeout,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
            max_connections=20,
            retry=Retry(NoBackoff(), 0),
            retry_on_timeout=False,
            **tls,
        )
        return Redis.from_pool(pool)
    except Exception as error:
        if pool is not None:
            try:
                pool.disconnect()
            except Exception:
                pass
        raise_safe_redis_error(error)


def close_redis_client(client: Redis) -> None:
    try:
        client.close()
    except Exception as error:
        raise_safe_redis_error(error)
