# Fenny (`src/fcn`) — vendored upstream

`src/fcn` is a **plain vendored copy** (not a git subtree/submodule) of the Fenny
FCN / structured-note quoter + options desk, absorbed into XAR under the
"mount first, merge later" plan.

- **Upstream:** https://github.com/qzjacob/fenny.git
- **Vendored commit:** `8858acfbb8a0c0d1f4df312d827d5fe33a9208ed`
- **Vendored on:** 2026-07-02
- **Tests:** copied to `tests/fenny/` (run alongside the XAR suite).
- **Docs:** upstream `docs/` copied to `docs/fenny/`; `UI_reference.md` → `docs/FENNY_UI_reference.md` (the Phase 7 UI-port spec).

## Local modifications (kept current)

1. **`src/fcn/service/llm.py`** — `generate()`/`is_available()` route through XAR's task
   manager (`xar.models.llm.complete`, task=`adhoc_strong`) when no explicit `poster`/
   `api_key` is given; the Anthropic-format path + the `poster` injection point + the
   return-`None`-on-failure contract are preserved, so `fcn` stays runnable standalone and
   all upstream tests pass unchanged.
2. **`src/fcn/api/main.py`** — added a module-level `blotter_factory` hook consulted by
   `_get_blotter()` (default `None` = the original file store). XAR injects
   `xar.fenny.blotter_pg.PgBlotterStore` via `xar.api.fenny_mount`, moving the blotter onto
   the `fenny_blotter` Postgres table. `@functools.lru_cache` is kept so upstream tests that
   call `_get_blotter.cache_clear()` still work.

Nothing else in `src/fcn` is modified. The dependency points one way (`xar` → `fcn`);
`fcn` never imports `xar`, so the vendored package remains standalone-runnable and a future
upstream re-sync stays mechanical.

## ⚠️ Security note
Upstream's repo `.env` (NOT vendored here) contained a **real `MASSIVE_API_KEY`**. That key
should be **rotated**. XAR reads the Massive key from its own config (`XAR` env /
`MASSIVE_API_KEY`); no key is committed in this repo.
