# Hotfix66 · Release Readiness R1 · Palette Direct Drop

## Scope
- Remove the duplicate visible `方案管理 / 搜索` command and Ctrl+M entry from Palette Studio.
- Keep `打开已有方案 -> 查看全部方案` as the single route for large saved-scheme lists/search.
- Add direct Windows Explorer file drop into Palette Studio / palette schemes for QTX, CPX and Excel.
- Dropping onto an empty Palette Studio canvas creates a transient unsaved draft automatically.
- A draft remains unsaved after a drop; closing it does not pollute the saved-scheme list.
- Existing saved schemes retain the former auto-save-after-import behaviour.
- No colour science, 555, QTX parser, library core, Excel parser/export semantics, or 3D rendering logic changed.
