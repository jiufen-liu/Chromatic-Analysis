# Hotfix76 · Shutdown Lifecycle Stability

## Problem
Directly closing the main program could produce a very long PySide6/Shiboken traceback during Qt teardown. The failure was reproducible across several earlier hotfixes because application-wide and viewport event filters remained attached while child C++ widgets were being destroyed.

## Root cause
- `MainWindow` installs itself as an application-wide event filter.
- `PaletteMdiArea`, `StudioMdiArea`, and workspace card views also install viewport event filters.
- During application shutdown Qt destroys child viewports/scroll areas and emits `destroyed`/deferred-delete events.
- Existing Python callbacks still called `viewport()`, `deleteLater()`, or `refresh_empty_state()` on wrappers whose C++ objects were already gone.
- This caused recursive `eventFilter()` error chains ending in `libshiboken: Internal C++ object ... already deleted`.

## HF76 changes
1. Main `closeEvent()` sets the shutdown flag first, detaches the application-wide filter and known viewport filters while widgets are still valid, and stops UI rebuild timers.
2. MainWindow's application-wide `eventFilter()` exits immediately after shutdown begins.
3. MDI event filters cache their viewport references and avoid calling `viewport()` during teardown.
4. WorkspaceDocument uses a cached list viewport and bypasses resize/filter work during shutdown.
5. `destroyed` callbacks for workspace windows/taskbar buttons do no UI cleanup once application shutdown has started; Qt owns final child destruction.
6. Existing import, drag/drop, palette sorting, QTX/CPX, colour science, 555, 3D, Excel and RBAC behaviour is unchanged.

## Validation
- Open no internal windows and close the program.
- Open Library + Compare + Palette windows and close the main program directly.
- Open a QTX workspace document, minimize/restore it, then close the program.
- Start a Munsell background sort and immediately close the program.
- Repeat 5 times. Expected: clean exit, no crash dialog, no new crash log, exit code 0.
