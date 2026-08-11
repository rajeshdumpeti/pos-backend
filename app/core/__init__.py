"""Centralized framework concerns shared by application composition."""

from app.core.cors import configure_cors

__all__ = ["configure_cors"]
