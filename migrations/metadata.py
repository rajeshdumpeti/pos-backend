"""Canonical Alembic target metadata without runtime application imports."""

from app.infrastructure.relational import RELATIONAL_METADATA
from app.infrastructure.authentication import models as authentication_models
from app.infrastructure import onboarding_models, planning_models, job_models


_ = authentication_models
_ = onboarding_models


target_metadata = RELATIONAL_METADATA


_ = planning_models

_ = job_models
