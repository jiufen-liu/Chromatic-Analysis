# Hotfix52 · P3-4 Import First-Paint Stabilization

Scope: only the large-file import progress surface and its worker launch timing.

- The import dialog is shown, polished, laid out, and painted before CPU-heavy parsing starts.
- Worker submission is deferred to a later Qt event-loop turn (80 ms) so Windows does not show an empty white dialog.
- The parser still runs in the existing executor; parsing/data semantics are unchanged.
- Worker startup failures are rendered in the same dialog instead of leaving a blank surface.
- P3-1/P3-2/P3-3/P3-4 virtualization, Quick3D, qtx_core algorithms, SQLite schema, RBAC, MI/555/CMC/DE2000 are unchanged.
