"""PostgreSQL query/acknowledgement boundaries, inspected without a connection."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.infrastructure.authentication.repositories import (
    SqlAlchemyAuthenticationEmailOutboxRepository,
)
from app.infrastructure.jobs import due_query, fence
from tests.test_jobs_infrastructure import job


NOW = datetime(2026, 11, 11, tzinfo=UTC)


def test_email_claim_uses_bounded_skip_locked_and_expired_lease_recovery():
    session = Mock()
    session.scalars.return_value.all.return_value = []
    repository = SqlAlchemyAuthenticationEmailOutboxRepository(session)
    assert repository.claim_available(NOW, uuid4(), 7) == []
    statement = session.scalars.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    contract = str(compiled)
    safe_claim = all(
        fragment in contract
        for fragment in (
            "FOR UPDATE SKIP LOCKED",
            "LIMIT",
            "claimed_at <=",
            "available_at <=",
        )
    )
    assert safe_claim
    assert 7 in compiled.params.values()
    assert NOW - timedelta(minutes=5) in compiled.params.values()
    session.commit.assert_not_called()


def test_email_acknowledgements_and_retries_fence_claim_owner():
    session = Mock()
    session.execute.return_value.rowcount = 0
    repository = SqlAlchemyAuthenticationEmailOutboxRepository(session)
    identifier, owner = uuid4(), uuid4()
    for apply in (
        lambda: repository.mark_delivered(identifier, owner, NOW, "provider-id"),
        lambda: repository.mark_failed(identifier, owner, NOW, "message_expired"),
        lambda: repository.release_for_retry(
            identifier, owner, NOW, "provider_unavailable"
        ),
    ):
        assert apply() is False
        compiled = session.execute.call_args.args[0].compile(
            dialect=postgresql.dialect()
        )
        contract = str(compiled)
        scoped = all(
            fragment in contract for fragment in (".id =", ".claim_id =", ".status =")
        )
        assert scoped
        assert (
            identifier in compiled.params.values() and owner in compiled.params.values()
        )
    session.commit.assert_not_called()


def test_general_jobs_keep_bounded_skip_locked_and_full_lease_fence():
    compiled = due_query(NOW, 9).compile(dialect=postgresql.dialect())
    contract = str(compiled)
    safe_claim = "FOR UPDATE SKIP LOCKED" in contract and "LIMIT" in contract
    assert safe_claim and 9 in compiled.params.values()
    condition = str(fence(job(), NOW).compile(dialect=postgresql.dialect()))
    protected = all(
        fragment in condition
        for fragment in (
            ".id =",
            ".state =",
            ".lease_owner",
            ".attempts =",
            ".lease_expires_at >",
        )
    )
    assert protected
