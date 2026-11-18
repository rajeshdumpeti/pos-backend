"""Repository-level Upstash URL/TLS proof through real redis-py, without sockets."""

import base64
import logging
from queue import Empty
import ssl
import traceback
from unittest.mock import Mock, patch

import pytest
from redis import Redis, BlockingConnectionPool
from redis.connection import SSLConnection
from redis.exceptions import ConnectionError

from app.application.authentication_security import Bucket, LimiterUnavailableError
from app.config import load_settings
from app.infrastructure.authentication.abuse_control import (
    RedisLimiter,
    compose_admission,
)
from app.infrastructure.authentication.redis_client import (
    create_redis_client,
    close_redis_client,
)
from app.infrastructure.database_diagnostics import format_safe_database_traceback


URL = "rediss://sentinel-user:sentinel-redis-secret@private.example.invalid:6379"


@pytest.fixture
def settings():
    return load_settings(
        {
            "APP_ENV": "test",
            "REDIS_URL": URL,
            "AUTH_RATE_LIMIT_BACKEND": "redis",
            "AUTH_RATE_LIMIT_KEY": base64.urlsafe_b64encode(bytes(range(32))).decode(),
        }
    )


@pytest.fixture(autouse=True)
def no_sockets(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Live Redis access forbidden")

    monkeypatch.setattr("socket.socket.connect", forbidden)


def test_actual_admission_composition_passes_unchanged_rediss_to_redis_py(settings):
    with patch(
        "app.infrastructure.authentication.redis_client._PrivateConnectionPool.from_url",
        wraps=BlockingConnectionPool.from_url,
    ) as factory:
        # Use the private pool's real classmethod, preserving its safe repr.
        from app.infrastructure.authentication.redis_client import (
            _PrivateConnectionPool,
        )

        factory.side_effect = (
            lambda *args, **kwargs: BlockingConnectionPool.from_url.__func__(
                _PrivateConnectionPool, *args, **kwargs
            )
        )
        admission, close = compose_admission(settings)
        assert factory.call_args.args == (URL,)
        assert admission is not None
        close()


def test_real_client_pool_tls_timeouts_and_private_representations(settings):
    client = create_redis_client(settings)
    try:
        assert isinstance(client, Redis)
        pool = client.connection_pool
        assert isinstance(pool, BlockingConnectionPool)
        assert pool.timeout == 0.2 and pool.max_connections == 20
        kwargs = pool.connection_kwargs
        assert kwargs["socket_timeout"] == kwargs["socket_connect_timeout"] == 0.2
        assert kwargs["username"] == "sentinel-user"
        assert kwargs["password"] == "sentinel-redis-secret"
        assert kwargs["retry"].get_retries() == 0
        assert issubclass(pool.connection_class, SSLConnection)
        connection = pool.make_connection()  # lazy construction only, no connect()
        assert connection.cert_reqs == ssl.CERT_REQUIRED
        assert connection.check_hostname is True
        with patch("redis.connection.ssl.create_default_context") as create_context:
            connection._wrap_socket_with_ssl(Mock())
        context = create_context.return_value
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert context.wrap_socket.call_args.kwargs["server_hostname"] == kwargs["host"]
        rendered = (
            repr(settings) + settings.model_dump_json() + repr(client) + repr(pool)
        )
        assert all(
            value not in rendered
            for value in (URL, "sentinel-user", "sentinel-redis-secret")
        )
        assert client.auto_close_connection_pool is True
    finally:
        close_redis_client(client)


def test_exhausted_pool_wait_is_bounded(settings):
    client = create_redis_client(settings)
    pool = client.connection_pool
    try:
        with patch.object(pool.pool, "get", side_effect=Empty) as get:
            with pytest.raises(ConnectionError):
                pool.get_connection()
        get.assert_called_once_with(block=True, timeout=0.2)
    finally:
        close_redis_client(client)


@pytest.mark.parametrize(
    "query",
    [
        "?ssl_cert_reqs=none",
        "?ssl_check_hostname=false",
        "?socket_timeout=0",
        "#private-fragment",
    ],
)
def test_tls_and_timeout_url_overrides_rejected_by_configuration(query):
    with pytest.raises(ValueError) as captured:
        load_settings(
            {
                "APP_ENV": "test",
                "REDIS_URL": URL + query,
                "AUTH_RATE_LIMIT_BACKEND": "redis",
                "AUTH_RATE_LIMIT_KEY": base64.urlsafe_b64encode(
                    bytes(range(32))
                ).decode(),
            }
        )
    assert "sentinel-redis-secret" not in str(captured.value)


def test_redis_pool_repr_safe_in_actual_api_composition(settings):
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_eval_and_close_failures_have_safe_private_cause(settings, caplog):
    private = RuntimeError(URL)
    client = Mock()
    client.eval.side_effect = private
    client.close.side_effect = private
    with caplog.at_level(logging.DEBUG):
        for operation in (
            lambda: RedisLimiter(client).consume((Bucket("digest", 1, 60),)),
            lambda: close_redis_client(client),
        ):
            with pytest.raises(LimiterUnavailableError) as captured:
                operation()
            assert captured.value._internal_cause is private
            rendered = (
                str(captured.value)
                + repr(captured.value)
                + "".join(traceback.format_exception(captured.value))
                + format_safe_database_traceback(captured.value)
                + caplog.text
            )
            assert all(
                value not in rendered
                for value in (URL, "sentinel-user", "sentinel-redis-secret")
            )


def test_composition_failure_and_pool_cleanup_are_safe(settings):
    with patch(
        "app.infrastructure.authentication.redis_client.Redis.from_pool",
        side_effect=RuntimeError(URL),
    ), patch(
        "app.infrastructure.authentication.redis_client._PrivateConnectionPool.disconnect"
    ) as disconnect:
        with pytest.raises(LimiterUnavailableError) as captured:
            create_redis_client(settings)
    disconnect.assert_called_once()
    assert URL not in "".join(traceback.format_exception(captured.value))


def test_existing_local_plain_redis_and_absent_configuration_remain_supported():
    settings = load_settings({"APP_ENV": "test"})
    with patch(
        "app.infrastructure.authentication.redis_client.create_redis_client"
    ) as create:
        _, close = compose_admission(settings)
        close()
    create.assert_not_called()
    settings = load_settings(
        {"APP_ENV": "test", "REDIS_URL": "redis://localhost:6379/0"}
    )
    client = create_redis_client(settings)
    try:
        assert not issubclass(client.connection_pool.connection_class, SSLConnection)
    finally:
        close_redis_client(client)
