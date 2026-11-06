"""Shared application errors for account eligibility decisions."""


class AccountUnavailableError(RuntimeError):
    """The requested account is absent or ineligible for the operation."""
