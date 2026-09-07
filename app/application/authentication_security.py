"""Provider-neutral authentication admission policy; no account lookup occurs here."""
from dataclasses import dataclass, field
from typing import Protocol


class LimiterUnavailableError(RuntimeError):
    """Safe dependency failure, including exhausted bounded storage."""


@dataclass(frozen=True)
class AbusePolicy:
    source_limit: int
    identity_limit: int
    window_seconds: int


DEFAULT_POLICIES = {
    "registration": AbusePolicy(20, 5, 900),
    "verification_confirm": AbusePolicy(60, 10, 600),
    "verification_resend": AbusePolicy(30, 5, 600),
    "change_email": AbusePolicy(20, 5, 900),
    "sign_in": AbusePolicy(60, 10, 900),
    "password_reset_request": AbusePolicy(20, 3, 900),
    "password_reset_confirm": AbusePolicy(30, 5, 900),
    "refresh": AbusePolicy(120, 30, 60),
    "restore": AbusePolicy(120, 0, 60),
    "logout": AbusePolicy(60, 0, 60),
}


@dataclass(frozen=True)
class Bucket:
    key: str = field(repr=False)
    limit: int
    window_seconds: int


class Limiter(Protocol):
    def consume(self, buckets: tuple[Bucket, ...]) -> int:
        """Consume in order, return zero or bounded seconds until retry."""


class SecurityKeyDeriver(Protocol):
    def derive(self, domain: str, value: str) -> str: ...
    def identity(self, value: str) -> str: ...


class AuthenticationAdmission:
    """Count every attempt, independent of credential validity or identity existence."""

    def __init__(self, limiter: Limiter, keys: SecurityKeyDeriver,
                 policies: dict[str, AbusePolicy], global_limit: int = 10000) -> None:
        self._limiter = limiter
        self._keys = keys
        self._policies = dict(policies)
        self._global_limit = global_limit

    def check(self, operation: str, source: str, identity: str | None = None,
              *, email: bool = False) -> int:
        policy = self._policies[operation]
        buckets = [
            Bucket(self._keys.derive("global", "all"), self._global_limit, 60),
            Bucket(self._keys.derive(operation + ":source", source),
                   policy.source_limit, policy.window_seconds),
        ]
        if identity is not None and policy.identity_limit:
            value = self._keys.identity(identity) if email else identity
            buckets.append(Bucket(self._keys.derive(operation + ":identity", value),
                                  policy.identity_limit, policy.window_seconds))
        return self._limiter.consume(tuple(buckets))

    def check_identity(self, operation: str, identity: str, *, email: bool = False) -> int:
        policy = self._policies[operation]
        if not policy.identity_limit:
            return 0
        value = self._keys.identity(identity) if email else identity
        return self._limiter.consume((Bucket(
            self._keys.derive(operation + ":identity", value),
            policy.identity_limit, policy.window_seconds),))
