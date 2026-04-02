"""ByteRover long-term project memory integration.

This package provides the ByteRover memory layer for Hermes Agent.
All ``brv`` CLI interactions are deferred — the package is safe to import
even when ByteRover is not installed.

Named ``byterover_integration`` (not ``byterover``) to avoid shadowing
the ``byterover-cli`` npm package.

Modules:
    client       — brv binary resolution, subprocess runner
    recall       — recall_mode config, auto-enrich, auto-flush
    onboarding   — first-run onboarding prompt & marker
"""

from byterover_integration.client import check_requirements
from byterover_integration.recall import (
    get_brv_recall_mode,
    brv_auto_enrich,
    brv_auto_curate_turn,
    brv_flush_on_compress,
    BRV_REFRESH_INTERVAL,
)
from byterover_integration.onboarding import (
    is_brv_onboarded,
    mark_brv_onboarded,
    get_brv_onboarding_prompt,
    get_setup_step,
    set_setup_step,
    clear_setup_step,
    parse_provider_choice,
    parse_storage_choice,
)

__all__ = [
    "check_requirements",
    "get_brv_recall_mode",
    "brv_auto_enrich",
    "brv_auto_curate_turn",
    "brv_flush_on_compress",
    "BRV_REFRESH_INTERVAL",
    "is_brv_onboarded",
    "mark_brv_onboarded",
    "get_brv_onboarding_prompt",
    "get_setup_step",
    "set_setup_step",
    "clear_setup_step",
    "parse_provider_choice",
    "parse_storage_choice",
]
