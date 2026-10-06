# Hotfix42 — Orbit / Pan / Zoom

3D interaction is fixed to Scheme A:

- Left-button drag: orbit / rotate the CIELAB scene.
- Right-button drag: pan / slide the whole 3D view in screen space.
- Mouse wheel: zoom.
- Left-button double click: reset orbit, pan, and zoom.
- Left click sample: select/highlight.
- Left click empty area: clear selection.

Pan speed is perspective/zoom aware so the scene follows the cursor consistently at different zoom levels.

This hotfix changes only the Quick3D interaction layer and user-facing build label. Color calculations, Lab coordinates, QTX/CPX, SQLite, 555, MI, and core colorimetry are unchanged.
