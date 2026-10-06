# Hotfix37 Runtime Path Guard

Purpose: prove that the running application really comes from this build.

- Main window title contains `Hotfix37 · Quick3D Unified`.
- Quick3D dialog title contains the same build label.
- QML viewport has a visible `HF37 · Qt Quick 3D / RHI` badge.
- The historical black `Lab3DDialog` class has been renamed to `LegacyLab3DDialog_DISABLED`; there are no runtime call sites to it.
- `START_HF37.bat` always uses this folder's `.venv` and prints the actual imported source paths before starting.
- If Quick3D import fails, the application shows the actual import error instead of silently launching the old 3D window.
