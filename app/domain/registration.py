"""Framework-independent registration and verification policies."""

from dataclasses import dataclass, field


PASSWORD_MIN_CHARACTERS = 15
PASSWORD_MAX_CHARACTERS = 1024


class PasswordPolicyError(ValueError):
    """Safe password-policy rejection without retaining submitted input."""


class EmailPolicyError(ValueError):
    """Safe email-policy rejection without retaining submitted input."""


@dataclass(frozen=True, slots=True)
class NormalizedEmail:
    display: str = field(repr=False)
    comparison: str = field(repr=False)


def require_password_length(password: str) -> None:
    length = len(password)
    if length < PASSWORD_MIN_CHARACTERS or length > PASSWORD_MAX_CHARACTERS:
        raise PasswordPolicyError("Password does not meet the approved length policy")


def mask_email(email: str) -> str:
    """Return a stable destination hint without exposing the complete address."""
    local, separator, domain = email.rpartition("@")
    if not separator or not local or not domain:
        raise EmailPolicyError("Email address is invalid")
    visible = local[0]
    return f"{visible}{'*' * max(3, min(len(local) - 1, 8))}@{domain}"
