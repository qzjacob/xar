"""XAR-side glue for the vendored Fenny (`fcn`) structured-note / options desk.

Fenny is vendored under `src/fcn` (see FENNY_UPSTREAM.md) and mounted into the XAR
FastAPI app by `xar.api.fenny_mount`. This package holds the host-side adapters that
re-home Fenny's file/in-memory state onto XAR infrastructure without `fcn` importing
`xar` — the dependency points one way (xar → fcn), so `fcn` stays runnable standalone.
"""
