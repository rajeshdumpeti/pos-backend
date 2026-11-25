"""Feature flags decided from startup configuration only.

No database, no per-account storage: a flag is a pure function of the configured mode, the
configured allowlist and the authenticated account.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

FEATURE_FLAGS_CONTRACT_VERSION = "1.0"


class VoiceEntryMode(StrEnum):
    OFF = "off"
    ALLOWLIST = "allowlist"
    ON = "on"


def voice_entry_enabled(
    mode: VoiceEntryMode, allowlist: frozenset[UUID], account_id: UUID
) -> bool:
    """off -> nobody; on -> everybody; allowlist -> only listed accounts."""
    if mode is VoiceEntryMode.ON:
        return True
    if mode is VoiceEntryMode.ALLOWLIST:
        return account_id in allowlist
    return False


@dataclass(frozen=True, slots=True)
class FeatureFlags:
    contract_version: str
    voice_entry_enabled: bool


class FeatureFlagService:
    def __init__(self, voice_entry_mode: VoiceEntryMode, voice_entry_allowlist: frozenset[UUID]):
        self._mode = voice_entry_mode
        self._allowlist = voice_entry_allowlist

    def flags_for(self, account_id: UUID) -> FeatureFlags:
        return FeatureFlags(
            contract_version=FEATURE_FLAGS_CONTRACT_VERSION,
            voice_entry_enabled=voice_entry_enabled(self._mode, self._allowlist, account_id),
        )
