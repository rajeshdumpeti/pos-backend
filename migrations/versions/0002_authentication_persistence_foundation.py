"""Create the authentication persistence foundation.

Revision ID: 0002_auth_persistence
Revises: 0001_migration_foundation
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002_auth_persistence"
down_revision: str | None = "0001_migration_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "authentication_attempt_states",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("identity_digest", sa.String(128), nullable=False),
        sa.Column("risk_bucket_digest", sa.String(128), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("window_started_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_attempt_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("progressive_delay_until", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("locked_until", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("failed_attempts >= 0", name=op.f("ck_authentication_attempt_states__failed_attempts_nonnegative")),
        sa.CheckConstraint("last_attempt_at >= window_started_at", name=op.f("ck_authentication_attempt_states__last_attempt_within_window")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authentication_attempt_states")),
        sa.UniqueConstraint("identity_digest", "risk_bucket_digest", name=op.f("uq_authentication_attempt_states__identity_digest__62b111e31b")),
    )
    op.create_index(op.f("ix_authentication_attempt_states__retention_cleanup"), "authentication_attempt_states", ["updated_at"])

    op.create_table(
        "user_accounts",
        sa.Column("id", sa.UUID(), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False), sa.Column("password_hash", sa.String(512), nullable=True),
        sa.Column("email_verified_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("onboarding_required", sa.Boolean(), nullable=False), sa.Column("authentication_epoch", sa.Integer(), nullable=False),
        sa.Column("terms_version", sa.String(64), nullable=True), sa.Column("terms_accepted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("privacy_version", sa.String(64), nullable=True), sa.Column("privacy_accepted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deletion_requested_at", postgresql.TIMESTAMP(timezone=True), nullable=True), sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False), sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('pending_verification', 'active', 'disabled', 'deactivated', 'deletion_pending', 'deleted')", name=op.f("ck_user_accounts__valid_status")),
        sa.CheckConstraint("authentication_epoch >= 0", name=op.f("ck_user_accounts__authentication_epoch_nonnegative")),
        sa.CheckConstraint("(status <> 'active') OR email_verified_at IS NOT NULL", name=op.f("ck_user_accounts__active_account_email_verified")),
        sa.CheckConstraint("(status <> 'deleted') OR deleted_at IS NOT NULL", name=op.f("ck_user_accounts__deleted_account_timestamped")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_accounts")),
    )

    op.create_table(
        "authentication_email_outbox",
        sa.Column("id", sa.UUID(), nullable=False), sa.Column("user_account_id", sa.UUID(), nullable=True),
        sa.Column("message_kind", sa.String(32), nullable=False), sa.Column("recipient_normalized_email", sa.String(320), nullable=False),
        sa.Column("payload_ciphertext", sa.LargeBinary(), nullable=False), sa.Column("encryption_key_id", sa.String(128), nullable=False),
        sa.Column("encryption_key_version", sa.Integer(), nullable=False), sa.Column("deduplication_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("available_at", postgresql.TIMESTAMP(timezone=True), nullable=False), sa.Column("claimed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("claim_id", sa.UUID(), nullable=True), sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("delivered_at", postgresql.TIMESTAMP(timezone=True), nullable=True), sa.Column("failed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True), sa.Column("failure_category", sa.String(64), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'claimed', 'delivered', 'failed')", name=op.f("ck_authentication_email_outbox__valid_status")),
        sa.CheckConstraint("message_kind IN ('email_verification', 'password_reset')", name=op.f("ck_authentication_email_outbox__valid_message_kind")),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_authentication_email_outbox__attempt_count_nonnegative")),
        sa.CheckConstraint("encryption_key_version > 0", name=op.f("ck_authentication_email_outbox__key_version_positive")),
        sa.CheckConstraint("(status <> 'claimed') OR (claimed_at IS NOT NULL AND claim_id IS NOT NULL)", name=op.f("ck_authentication_email_outbox__claim_fields_consistent")),
        sa.CheckConstraint("(status <> 'delivered') OR delivered_at IS NOT NULL", name=op.f("ck_authentication_email_outbox__delivery_timestamp_consistent")),
        sa.ForeignKeyConstraint(["user_account_id"], ["user_accounts.id"], name=op.f("fk_authentication_email_outbox__user_account_id__user_accounts"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authentication_email_outbox")),
        sa.UniqueConstraint("deduplication_key", name=op.f("uq_authentication_email_outbox__deduplication_key")),
    )
    op.create_index(op.f("ix_authentication_email_outbox__delivery_claim"), "authentication_email_outbox", ["status", "available_at", "created_at"])
    op.create_index(op.f("ix_authentication_email_outbox__delivery_cleanup"), "authentication_email_outbox", ["delivered_at", "failed_at"])
    op.create_index(op.f("ix_authentication_email_outbox__user_account_id"), "authentication_email_outbox", ["user_account_id"])

    op.create_table(
        "email_identities",
        sa.Column("id", sa.UUID(), nullable=False), sa.Column("user_account_id", sa.UUID(), nullable=False),
        sa.Column("display_email", sa.String(320), nullable=False), sa.Column("normalized_email", sa.String(320), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False), sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("verified_at", postgresql.TIMESTAMP(timezone=True), nullable=True), sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("(is_verified = false AND verified_at IS NULL) OR (is_verified = true AND verified_at IS NOT NULL)", name=op.f("ck_email_identities__verification_timestamp_consistent")),
        sa.ForeignKeyConstraint(["user_account_id"], ["user_accounts.id"], name=op.f("fk_email_identities__user_account_id__user_accounts"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_identities")), sa.UniqueConstraint("normalized_email", name=op.f("uq_email_identities__normalized_email")),
    )
    op.create_index(op.f("ix_email_identities__one_primary_email_per_account"), "email_identities", ["user_account_id"], unique=True, postgresql_where=sa.text("is_primary IS true"))
    op.create_index(op.f("ix_email_identities__user_account_id"), "email_identities", ["user_account_id"])

    op.create_table(
        "email_verification_challenges",
        sa.Column("id", sa.UUID(), nullable=False), sa.Column("user_account_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False), sa.Column("flow_token_digest", sa.String(128), nullable=False),
        sa.Column("code_digest", sa.String(128), nullable=False), sa.Column("target_normalized_email", sa.String(320), nullable=False),
        sa.Column("issued_at", postgresql.TIMESTAMP(timezone=True), nullable=False), sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("consumed_at", postgresql.TIMESTAMP(timezone=True), nullable=True), sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False), sa.Column("resend_available_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("superseded_by_id", sa.UUID(), nullable=True), sa.Column("status_version", sa.Integer(), nullable=False),
        sa.CheckConstraint("purpose IN ('registration', 'email_change')", name=op.f("ck_email_verification_challenges__valid_purpose")),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_email_verification_challenges__attempts_nonnegative")),
        sa.CheckConstraint("max_attempts > 0", name=op.f("ck_email_verification_challenges__max_attempts_positive")),
        sa.CheckConstraint("attempts <= max_attempts", name=op.f("ck_email_verification_challenges__attempts_within_maximum")),
        sa.CheckConstraint("expires_at > issued_at", name=op.f("ck_email_verification_challenges__expiry_after_issue")),
        sa.CheckConstraint("resend_available_at >= issued_at", name=op.f("ck_email_verification_challenges__resend_after_issue")),
        sa.CheckConstraint("status_version > 0", name=op.f("ck_email_verification_challenges__status_version_positive")),
        sa.ForeignKeyConstraint(["user_account_id"], ["user_accounts.id"], name=op.f("fk_email_verification_challenges__user_account_id__e1b68250d3"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["superseded_by_id"], ["email_verification_challenges.id"], name=op.f("fk_email_verification_challenges__superseded_by_id__c933f65beb"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_verification_challenges")),
        sa.UniqueConstraint("flow_token_digest", name=op.f("uq_email_verification_challenges__flow_token_digest")),
        sa.UniqueConstraint("code_digest", name=op.f("uq_email_verification_challenges__code_digest")),
    )
    op.create_index(op.f("ix_email_verification_challenges__expiry_cleanup"), "email_verification_challenges", ["expires_at"])
    op.create_index(op.f("ix_email_verification_challenges__one_active_challe__457a5906cc"), "email_verification_challenges", ["user_account_id", "purpose"], unique=True, postgresql_where=sa.text("consumed_at IS NULL AND superseded_by_id IS NULL"))
    op.create_index(op.f("ix_email_verification_challenges__user_account_id__114bb69908"), "email_verification_challenges", ["user_account_id", "purpose", "expires_at"])

    op.create_table(
        "external_provider_identities",
        sa.Column("id", sa.UUID(), nullable=False), sa.Column("user_account_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False), sa.Column("provider_subject", sa.String(255), nullable=False),
        sa.Column("email_snapshot", sa.String(320), nullable=True), sa.Column("email_verified", sa.Boolean(), nullable=True),
        sa.Column("is_private_relay", sa.Boolean(), nullable=False), sa.Column("first_authenticated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_authenticated_at", postgresql.TIMESTAMP(timezone=True), nullable=False), sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("provider IN ('google', 'apple')", name=op.f("ck_external_provider_identities__valid_provider")),
        sa.ForeignKeyConstraint(["user_account_id"], ["user_accounts.id"], name=op.f("fk_external_provider_identities__user_account_id__user_accounts"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_provider_identities")),
        sa.UniqueConstraint("provider", "provider_subject", name=op.f("uq_external_provider_identities__provider__provider_subject")),
    )
    op.create_index(op.f("ix_external_provider_identities__user_account_id"), "external_provider_identities", ["user_account_id"])

    op.create_table(
        "password_reset_challenges",
        sa.Column("id", sa.UUID(), nullable=False), sa.Column("user_account_id", sa.UUID(), nullable=False),
        sa.Column("token_digest", sa.String(128), nullable=False), sa.Column("request_security_digest", sa.String(128), nullable=False),
        sa.Column("issued_at", postgresql.TIMESTAMP(timezone=True), nullable=False), sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("consumed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("expires_at > issued_at", name=op.f("ck_password_reset_challenges__expiry_after_issue")),
        sa.ForeignKeyConstraint(["user_account_id"], ["user_accounts.id"], name=op.f("fk_password_reset_challenges__user_account_id__user_accounts"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_reset_challenges")), sa.UniqueConstraint("token_digest", name=op.f("uq_password_reset_challenges__token_digest")),
    )
    op.create_index(op.f("ix_password_reset_challenges__expiry_cleanup"), "password_reset_challenges", ["expires_at"])
    op.create_index(op.f("ix_password_reset_challenges__user_account_id"), "password_reset_challenges", ["user_account_id"])

    op.create_table(
        "refresh_sessions",
        sa.Column("id", sa.UUID(), nullable=False), sa.Column("user_account_id", sa.UUID(), nullable=False),
        sa.Column("token_family_id", sa.UUID(), nullable=False), sa.Column("current_token_digest", sa.String(128), nullable=False),
        sa.Column("remember_me", sa.Boolean(), nullable=False), sa.Column("authentication_epoch", sa.Integer(), nullable=False),
        sa.Column("device_metadata", postgresql.JSONB(), nullable=False), sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_used_at", postgresql.TIMESTAMP(timezone=True), nullable=False), sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revoked_at", postgresql.TIMESTAMP(timezone=True), nullable=True), sa.Column("revocation_reason", sa.String(64), nullable=True),
        sa.CheckConstraint("authentication_epoch >= 0", name=op.f("ck_refresh_sessions__authentication_epoch_nonnegative")),
        sa.CheckConstraint("expires_at > created_at", name=op.f("ck_refresh_sessions__expiry_after_creation")),
        sa.CheckConstraint("last_used_at >= created_at", name=op.f("ck_refresh_sessions__last_use_after_creation")),
        sa.CheckConstraint("(revoked_at IS NULL) = (revocation_reason IS NULL)", name=op.f("ck_refresh_sessions__revocation_fields_consistent")),
        sa.ForeignKeyConstraint(["user_account_id"], ["user_accounts.id"], name=op.f("fk_refresh_sessions__user_account_id__user_accounts"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_sessions")), sa.UniqueConstraint("current_token_digest", name=op.f("uq_refresh_sessions__current_token_digest")),
    )
    op.create_index(op.f("ix_refresh_sessions__expiry_cleanup"), "refresh_sessions", ["expires_at"])
    op.create_index(op.f("ix_refresh_sessions__token_family_id"), "refresh_sessions", ["token_family_id"])
    op.create_index(op.f("ix_refresh_sessions__user_account_id__revoked_at"), "refresh_sessions", ["user_account_id", "revoked_at"])

    op.create_table(
        "refresh_rotation_retry_receipts",
        sa.Column("id", sa.UUID(), nullable=False), sa.Column("refresh_session_id", sa.UUID(), nullable=False),
        sa.Column("token_family_id", sa.UUID(), nullable=False), sa.Column("previous_token_digest", sa.String(128), nullable=False),
        sa.Column("refresh_request_id", sa.String(64), nullable=False), sa.Column("response_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("encryption_key_id", sa.String(128), nullable=False), sa.Column("encryption_key_version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False), sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("consumed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("encryption_key_version > 0", name=op.f("ck_refresh_rotation_retry_receipts__key_version_positive")),
        sa.CheckConstraint("expires_at > created_at", name=op.f("ck_refresh_rotation_retry_receipts__expiry_after_creation")),
        sa.ForeignKeyConstraint(["refresh_session_id"], ["refresh_sessions.id"], name=op.f("fk_refresh_rotation_retry_receipts__refresh_session__cd1d60dc96"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_rotation_retry_receipts")),
        sa.UniqueConstraint("previous_token_digest", "refresh_request_id", name=op.f("uq_refresh_rotation_retry_receipts__previous_token__d4d2f5350e")),
    )
    op.create_index(op.f("ix_refresh_rotation_retry_receipts__expiry_cleanup"), "refresh_rotation_retry_receipts", ["expires_at"])
    op.create_index(op.f("ix_refresh_rotation_retry_receipts__refresh_session_id"), "refresh_rotation_retry_receipts", ["refresh_session_id"])
    op.create_index(op.f("ix_refresh_rotation_retry_receipts__token_family_id"), "refresh_rotation_retry_receipts", ["token_family_id"])

    op.create_table(
        "security_events",
        sa.Column("id", sa.UUID(), nullable=False), sa.Column("event_type", sa.String(96), nullable=False),
        sa.Column("user_account_id", sa.UUID(), nullable=True), sa.Column("refresh_session_id", sa.UUID(), nullable=True),
        sa.Column("provider", sa.String(32), nullable=True), sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("source_digest", sa.String(128), nullable=True), sa.Column("occurred_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("sanitized_metadata", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["user_account_id"], ["user_accounts.id"], name=op.f("fk_security_events__user_account_id__user_accounts"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["refresh_session_id"], ["refresh_sessions.id"], name=op.f("fk_security_events__refresh_session_id__refresh_sessions"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_security_events")),
    )
    op.create_index(op.f("ix_security_events__event_type__occurred_at"), "security_events", ["event_type", "occurred_at"])
    op.create_index(op.f("ix_security_events__request_id"), "security_events", ["request_id"])
    op.create_index(op.f("ix_security_events__retention_cleanup"), "security_events", ["occurred_at"])
    op.create_index(op.f("ix_security_events__user_account_id__occurred_at"), "security_events", ["user_account_id", "occurred_at"])


def downgrade() -> None:
    for table_name in (
        "security_events",
        "refresh_rotation_retry_receipts",
        "refresh_sessions",
        "password_reset_challenges",
        "external_provider_identities",
        "email_verification_challenges",
        "email_identities",
        "authentication_email_outbox",
        "user_accounts",
        "authentication_attempt_states",
    ):
        op.drop_table(table_name)
