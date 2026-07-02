"""Mount the vendored Fenny (`fcn`) FastAPI app into the XAR app.

"Mount first, merge later": the Fenny sub-app keeps its own routes + exception handlers
(it has a catch-all `@app.exception_handler(Exception)` that must NOT leak onto XAR's app,
which is why we mount the sub-app rather than folding its routes into ours). This shim
bridges XAR config → the env keys Fenny reads (Massive/Finnhub/FMP) and injects the
Postgres-backed blotter, then returns the sub-app for `app.mount("/api/fenny", ...)`.

External paths become `/api/fenny/api/v1/*` (Fenny's internal `/api/v1` prefix under the
`/api/fenny` mount) — kept under `/api` so the XAR SPA catch-all leaves it alone and the
`/fenny` client route stays free for the React UI.
"""
from __future__ import annotations

import os

from ..config import get_settings
from ..logging import get_logger

log = get_logger("xar.fenny")


def get_fenny_app():
    """Return the Fenny FastAPI app wired to XAR (env keys + Postgres blotter)."""
    s = get_settings()
    for key, val in (("MASSIVE_API_KEY", s.massive_api_key),
                     ("FINNHUB_API_KEY", s.finnhub_api_key),
                     ("FMP_API_KEY", s.fmp_api_key)):
        if val:                       # existing env wins (Fenny's load_dotenv respects it)
            os.environ.setdefault(key, val)
    import fcn.api.main as fmain
    import fcn.service.llm as flm
    from ..fenny.blotter_pg import PgBlotterStore
    flm.route_via_xar = True           # route Fenny's LLM prose through XAR's task manager
    fmain.blotter_factory = PgBlotterStore
    fmain._get_blotter.cache_clear()  # rebuild the singleton against the injected factory
    log.info("fenny mounted (blotter=postgres, massive=%s)", "on" if s.massive_api_key else "off")
    return fmain.app
