"""Centralized framework concerns shared by application composition."""

from app.core.cors import configure_cors
from app.core.request_id import (
    REQUEST_ID_HEADER,
    configure_request_ids,
    get_request_id,
)

__all__ = [
    "REQUEST_ID_HEADER",
    "configure_cors",
    "configure_request_ids",
    "get_request_id",
]
