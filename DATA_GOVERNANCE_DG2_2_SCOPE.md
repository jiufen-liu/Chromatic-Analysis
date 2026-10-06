# Data Governance DG-2.2 / Hotfix58

## Goal
Simplify administrator-facing RBAC while keeping the DG-2.1 security model intact, and make Find use the same authorised resource tree as the library.

## Permission UX
- Remove permission templates from the foreground UI.
- One user list + one in-place permission editor.
- Function capability and data access remain the only two concepts shown to administrators.
- Official and formal libraries share the same data-resource tree.
- Keep final decision rule: feature capability AND data access; export additionally requires global export + resource export.
- Add “copy another user's permissions” as the lightweight way to provision similar users without introducing template complexity.
- Admin account remains fixed full access.

## Find scope
- Replace single customer combo with a unified multi-select resource picker.
- Support one source, multiple sources, all official, all formal, or all authorised data.
- Only data already allowed by RBAC is shown.
- Official and formal resources use the same tree presentation.
- Scope selection is stored per Find task; changing scope invalidates the cached result and requires re-query.
- LibraryStore ACL remains the security boundary; the picker cannot expose or query denied resources.

## Frozen
- qtx_core colour science, MI, 555, CMC/DE2000
- P3-1..P3-6 performance paths
- Quick3D/QML
- Excel exchange
