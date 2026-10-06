# Hotfix131 · True Adaptive MDI Fit

Scope is UI-shell only.

- Compact/small workspaces no longer auto-call `QMdiSubWindow.showMaximized()`.
- Primary tool windows stay in normal MDI state and are fitted to the live MDI viewport with an 8 px inset.
- The fitted window is therefore fully visible and immediately draggable on first open.
- The fit recalculates after sidebar/layout changes using the current viewport size.
- Large-screen mode restores the previous normal geometry/minimum size.
- User-triggered manual maximization is still respected.
- No search, library, palette, compare, spectrum, QTX/CPX/Excel, export, RBAC, colour-science, sorting or performance algorithm was changed.
