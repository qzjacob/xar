"""Mount the vendored siliconomics (`slx`) FastAPI app into the XAR app as XAR Andy.

Same "mount first, merge later" shape as fenny_mount: bridge XAR config → the env
keys the vendored connectors read, make sure the dedicated `slx` Postgres schema
exists, then return the sub-app for `app.mount("/api/andy", ...)`.

The vendored routes surface as `/api/andy/{health,metrics,registry/*,overclaims*}`.
XAR-native 勾稽 (crosswalk) routes live in xar.api.andy_links and are registered on
the host app BEFORE this mount so they shadow `/api/andy/link/*`.
"""
from __future__ import annotations

import os

from ..config import get_settings
from ..logging import get_logger

log = get_logger("xar.andy")


def get_andy_app():
    """Return the slx FastAPI app wired to XAR (env keys + shared-DB `slx` schema)."""
    s = get_settings()
    bridged = (
        ("SLX_DATABASE_URL", s.database_url),
        ("SEC_EDGAR_USER_AGENT", s.edgar_identity),
        ("FRED_API_KEY", s.fred_api_key),
        ("BEA_API_KEY", s.bea_api_key),
        ("EIA_API_KEY", s.eia_api_key),
        ("EMBER_API_KEY", s.ember_api_key),
        ("ACLED_API_KEY", s.acled_api_key),
        ("ACLED_EMAIL", s.acled_email),
        ("TICKETMASTER_API_KEY", s.ticketmaster_api_key),
        ("SLACK_WEBHOOK_URL", s.slx_slack_webhook),
    )
    for key, val in bridged:
        if val:  # existing env wins
            os.environ.setdefault(key, val)

    from slx import db as slx_db

    try:  # a fresh container should boot serving /api/andy/health without manual steps
        slx_db.init_schema()
    except Exception as e:  # noqa: BLE001
        log.warning("slx schema init skipped: %s", e)

    import slx.api.main as smain

    keyed = [k for k, v in bridged[2:] if v]
    log.info("andy (slx) mounted — schema=slx, keyed connectors: %s", ", ".join(keyed) or "none")
    return smain.app
