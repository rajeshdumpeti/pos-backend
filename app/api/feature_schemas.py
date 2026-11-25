"""Closed HTTP contract for feature flags v1."""

from typing import Literal

from app.api.planning_schemas import ClosedSchema


class VoiceEntryFlagResponse(ClosedSchema):
    enabled: bool


class FeatureFlagsResponse(ClosedSchema):
    contract_version: Literal["1.0"]
    voice_entry: VoiceEntryFlagResponse
