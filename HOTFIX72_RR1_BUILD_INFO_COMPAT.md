# Hotfix72 · Release Readiness R1 · Build Info Compatibility Fix

## Root cause
HF70/HF71 accidentally rewrote `qtx_app/build_info.py` with `APP_VERSION` only, while `main.py` and `qtx_app/release_runtime.py` still import `PRODUCT_VERSION`. This caused startup to stop immediately with:

`ImportError: cannot import name 'PRODUCT_VERSION' from 'qtx_app.build_info'`

## Fix
- Restore `PRODUCT_NAME = "Chromatic Analysis"`.
- Restore `PRODUCT_VERSION = "0.14.6.4"`.
- Keep `APP_VERSION = PRODUCT_VERSION` as a backwards-compatible alias.
- Bump build metadata to HF72.
- Add `START_RELEASE_R1_HF72.bat`.

## Frozen scope
No changes to `main_window.py`, Munsell sorting/cache reuse, QTX/CPX parsing, colorimetry, 555, library data, Excel exchange, RBAC, audit, 3D, or palette business behavior.
