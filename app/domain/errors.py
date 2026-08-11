"""Domain errors raised by the current local resource behavior."""


class UnknownResourceError(Exception):
    """Raised when a requested mock resource does not exist."""


class ResourceMutationNotAllowedError(Exception):
    """Raised when a resource does not support the requested mutation."""


class ItemNotFoundError(Exception):
    """Raised when an item cannot be found in a list resource."""
