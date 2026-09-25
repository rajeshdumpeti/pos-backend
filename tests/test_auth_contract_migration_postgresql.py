"""Guarded live coverage for the legacy receipt cleanup in revision 0004."""

from datetime import UTC, datetime, timedelta
import os
from uuid import UUID

from alembic import command
from alembic.config import Config
from pydantic import SecretStr
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.infrastructure.authentication.models import (
    RefreshRotationRetryReceiptModel,
    RefreshSessionModel,
    UserAccountModel,
)
from app.infrastructure.database import create_database_engine, dispose_database_engine
from tests.postgresql_support import bind_alembic_to_test_database


TEST_URL = os.environ.get("POS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    TEST_URL is None, reason="authorized PostgreSQL test URL not configured"
)
NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)


def _receipt_values(
    receipt_id: UUID,
    session_id: UUID,
    family_id: UUID,
    request_id: str,
    digest: str,
    created_at: datetime,
) -> dict[str, object]:
    return {
        "id": receipt_id,
        "refresh_session_id": session_id,
        "token_family_id": family_id,
        "previous_token_digest": digest,
        "refresh_request_id": request_id,
        "response_ciphertext": b"migration-fixture",
        "encryption_key_id": "migration-test-key",
        "encryption_key_version": 1,
        "created_at": created_at,
        "expires_at": created_at + timedelta(minutes=5),
        "consumed_at": None,
    }


def test_0004_deduplicates_legacy_receipts_before_enforcing_uniqueness():
    assert TEST_URL is not None
    engine = create_database_engine(SecretStr(TEST_URL))
    config = Config("alembic.ini")
    account_id = UUID(int=100)
    session_id = UUID(int=101)
    family_id = UUID(int=102)
    canonical_id = UUID(int=103)
    later_same_time_id = UUID(int=104)
    later_timestamp_id = UUID(int=105)
    unrelated_id = UUID(int=106)
    request_id = "migration-canonical-request"
    unrelated_request_id = "migration-unrelated-request"
    fixture_receipt_ids = {
        canonical_id,
        later_same_time_id,
        later_timestamp_id,
        unrelated_id,
        UUID(int=107),
        UUID(int=108),
    }

    try:
        with bind_alembic_to_test_database(TEST_URL):
            command.upgrade(config, "head")
            command.downgrade(config, "0003_registration_flow")

        with engine.begin() as connection:
            connection.execute(
                RefreshRotationRetryReceiptModel.__table__.delete().where(
                    RefreshRotationRetryReceiptModel.id.in_(fixture_receipt_ids)
                )
            )
            connection.execute(
                RefreshSessionModel.__table__.delete().where(
                    RefreshSessionModel.id == session_id
                )
            )
            connection.execute(
                UserAccountModel.__table__.delete().where(
                    UserAccountModel.id == account_id
                )
            )
            connection.execute(
                UserAccountModel.__table__.insert().values(
                    id=account_id,
                    status="active",
                    display_name="Migration Test",
                    password_hash=None,
                    email_verified_at=NOW,
                    onboarding_required=True,
                    authentication_epoch=0,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            connection.execute(
                RefreshSessionModel.__table__.insert().values(
                    id=session_id,
                    user_account_id=account_id,
                    token_family_id=family_id,
                    current_token_digest="migration-current-digest",
                    remember_me=False,
                    authentication_epoch=0,
                    device_metadata={},
                    created_at=NOW,
                    last_used_at=NOW,
                    expires_at=NOW + timedelta(days=1),
                    revoked_at=None,
                    revocation_reason=None,
                )
            )
            connection.execute(
                RefreshRotationRetryReceiptModel.__table__.insert(),
                [
                    _receipt_values(
                        canonical_id,
                        session_id,
                        family_id,
                        request_id,
                        "migration-previous-a",
                        NOW,
                    ),
                    _receipt_values(
                        later_same_time_id,
                        session_id,
                        family_id,
                        request_id,
                        "migration-previous-b",
                        NOW,
                    ),
                    _receipt_values(
                        later_timestamp_id,
                        session_id,
                        family_id,
                        request_id,
                        "migration-previous-c",
                        NOW + timedelta(seconds=1),
                    ),
                    _receipt_values(
                        unrelated_id,
                        session_id,
                        family_id,
                        unrelated_request_id,
                        "migration-previous-unrelated",
                        NOW,
                    ),
                ],
            )

        with bind_alembic_to_test_database(TEST_URL):
            command.upgrade(config, "0004_auth_contract_v1")

        with engine.begin() as connection:
            remaining_ids = set(
                connection.scalars(
                    select(RefreshRotationRetryReceiptModel.id).where(
                        RefreshRotationRetryReceiptModel.refresh_request_id.in_(
                            {request_id, unrelated_request_id}
                        )
                    )
                ).all()
            )
            assert remaining_ids == {canonical_id, unrelated_id}
            unrelated = connection.execute(
                select(
                    RefreshRotationRetryReceiptModel.previous_token_digest,
                    RefreshRotationRetryReceiptModel.created_at,
                    RefreshRotationRetryReceiptModel.response_ciphertext,
                ).where(RefreshRotationRetryReceiptModel.id == unrelated_id)
            ).one()
            assert unrelated == (
                "migration-previous-unrelated",
                NOW,
                b"migration-fixture",
            )
            constraints = {
                item["name"]
                for item in inspect(connection).get_unique_constraints(
                    "refresh_rotation_retry_receipts"
                )
            }
            assert (
                "uq_refresh_rotation_retry_receipts__refresh_request_id" in constraints
            )
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(
                        RefreshRotationRetryReceiptModel.__table__.insert().values(
                            **_receipt_values(
                                UUID(int=107),
                                session_id,
                                family_id,
                                request_id,
                                "migration-previous-rejected",
                                NOW + timedelta(seconds=2),
                            )
                        )
                    )

        with bind_alembic_to_test_database(TEST_URL):
            command.downgrade(config, "0003_registration_flow")

        with engine.begin() as connection:
            remaining_ids = set(
                connection.scalars(
                    select(RefreshRotationRetryReceiptModel.id).where(
                        RefreshRotationRetryReceiptModel.refresh_request_id.in_(
                            {request_id, unrelated_request_id}
                        )
                    )
                ).all()
            )
            assert remaining_ids == {canonical_id, unrelated_id}
            connection.execute(
                RefreshRotationRetryReceiptModel.__table__.insert().values(
                    **_receipt_values(
                        UUID(int=108),
                        session_id,
                        family_id,
                        request_id,
                        "migration-previous-after-downgrade",
                        NOW + timedelta(seconds=3),
                    )
                )
            )
    finally:
        try:
            with engine.begin() as connection:
                connection.execute(
                    RefreshRotationRetryReceiptModel.__table__.delete().where(
                        RefreshRotationRetryReceiptModel.id.in_(fixture_receipt_ids)
                    )
                )
                connection.execute(
                    RefreshSessionModel.__table__.delete().where(
                        RefreshSessionModel.id == session_id
                    )
                )
                connection.execute(
                    UserAccountModel.__table__.delete().where(
                        UserAccountModel.id == account_id
                    )
                )
            with bind_alembic_to_test_database(TEST_URL):
                command.upgrade(config, "head")
        finally:
            dispose_database_engine(engine)
