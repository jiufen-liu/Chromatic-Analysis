# Hotfix137 · MDI Layout + Taskbar Stability

## Scope
UI-shell stability only. No changes to color science, QTX/CPX/Excel parsing/export,
find/search algorithms, library data, palette sorting, 555, tolerance, RBAC, 3D,
spectrum calculation, or frozen performance paths.

## Fixes
1. Studio tool windows are sized from the live MDI viewport before being shown.
2. Removed competing StudioToolSubWindow first-show fit timers; MainWindow is now the
   single geometry owner for responsive MDI sizing.
3. Re-parented mature tool pages have their top-level layouts activated immediately
   after MDI geometry changes, preventing clipped/offset first paint and later self-fix.
4. The custom title-bar minimise button now goes directly to the persistent Studio
   taskbar instead of entering Qt native QMdiSubWindow minimised state first.
5. Studio taskbar visibility is derived from its task-button registry and is re-synced
   after minimise/restore/layout changes.
6. Restoring a taskbar window re-applies compact fit against the current live viewport.

## Static validation
- Full compileall passes.
- No existing top-level function/class method removed versus HF136.
- Only qtx_app/main_window.py and qtx_app/build_info.py changed as program source.
