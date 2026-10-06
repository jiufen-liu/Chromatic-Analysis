# Hotfix38 — Quick3D ownership fix

## Root cause
`QQuick3DGeometry` may only be parented by a `QQuick3DObject` (or `None`). Hotfix37 incorrectly passed `Lab3DDialog` (a QWidget/QDialog) as the parent for `GamutGeometry`, `GridGeometry`, and `AxisGeometry`. PySide6 therefore raised a constructor type error before QML/Quick3D rendering began.

## Fix
- All custom `QQuick3DGeometry` objects are created with `parent=None`.
- Python dialog attributes keep the objects alive while QML consumes them as context properties.
- Renderer API query now uses `QQuickView.rendererInterface()`.
- Added initialization-stage diagnostics and HF38 visible marker.
- Legacy black 3D remains disabled.

## Business logic unchanged
No changes to qtx_core, library storage, QTX/CPX/Excel, Lab/XYZ, CMC, DE2000, MI or 555.
