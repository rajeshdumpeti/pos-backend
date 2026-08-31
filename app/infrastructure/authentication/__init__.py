"""SQLAlchemy authentication persistence implementation."""

from app.infrastructure.authentication.models import (
    AuthenticationAttemptStateModel,
    AuthenticationEmailOutboxModel,
    EmailIdentityModel,
    EmailVerificationChallengeModel,
    ExternalProviderIdentityModel,
    PasswordResetChallengeModel,
    RefreshRotationRetryReceiptModel,
    RefreshSessionModel,
    SecurityEventModel,
    UserAccountModel,
)
from app.infrastructure.authentication.unit_of_work import SqlAlchemyAuthenticationUnitOfWork

__all__ = [
    "AuthenticationAttemptStateModel",
    "AuthenticationEmailOutboxModel",
    "EmailIdentityModel",
    "EmailVerificationChallengeModel",
    "ExternalProviderIdentityModel",
    "PasswordResetChallengeModel",
    "RefreshRotationRetryReceiptModel",
    "RefreshSessionModel",
    "SecurityEventModel",
    "SqlAlchemyAuthenticationUnitOfWork",
    "UserAccountModel",
]
