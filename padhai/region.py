"""G4 — Region awareness.

Small module that lets every other piece of code know which region
it's running in. Used by:
- Observability tags (so we can split metrics per region in Sentry/
  PostHog)
- DB connection routing (Singapore reads from the local Neon replica)
- Failover-aware response headers (`X-Padhai-Region: mumbai`)

Single env var: `PADHAI_REGION` ∈ {'mumbai', 'singapore', 'eu',
'dev'}. Default 'dev' for local sandboxes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

VALID_REGIONS = frozenset({"mumbai", "singapore", "eu", "dev"})


@dataclass(frozen=True)
class RegionInfo:
    region: str
    is_primary: bool
    label: str          # human-readable for log lines + status pages


def current() -> RegionInfo:
    raw = (os.environ.get("PADHAI_REGION") or "dev").lower().strip()
    if raw not in VALID_REGIONS:
        raw = "dev"
    label = {
        "mumbai": "Mumbai, India",
        "singapore": "Singapore",
        "eu": "Frankfurt, EU",
        "dev": "local dev",
    }[raw]
    # Mumbai is primary in our default topology. When ops promotes
    # Singapore (Mumbai outage), they flip `PADHAI_REGION_PROMOTED=1`
    # in the env so this returns is_primary=True.
    promoted = os.environ.get("PADHAI_REGION_PROMOTED") == "1"
    is_primary = raw == "mumbai" or promoted
    return RegionInfo(region=raw, is_primary=is_primary, label=label)


def description() -> str:
    info = current()
    role = "primary" if info.is_primary else "standby"
    return f"{info.label} ({role})"
