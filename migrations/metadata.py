"""Canonical Alembic target metadata without runtime application imports."""

from app.infrastructure.relational import RELATIONAL_METADATA
from app.infrastructure.authentication import models as authentication_models


_ = authentication_models


target_metadata = RELATIONAL_METADATA
