# Hotfix35 — Adaptive 3D Renderer

## Why Hotfix34 could show a blank viewport
Hotfix34 requested an OpenGL 3.3 Core context and GLSL 330 shaders. That path is fast,
but it is too strict for a desktop product that must run on mixed office PCs, old
integrated GPUs, remote desktops, VMs, and different Windows driver stacks.

## Hotfix35 architecture
3D rendering is now treated as a capability layer, not a business requirement.

1. **Automatic GPU attempt**
   - Requests OpenGL 2.0 without a Core-profile requirement.
   - Also supports OpenGL ES 2.0 style shaders.
   - All real sample points are sent in one interleaved VBO and rendered in one
     `glDrawArrays(GL_POINTS)` call.
   - The gamut surface and wireframe are also batched VBO draws.

2. **Health handshake**
   - GPU initialization errors emit an explicit backend-failure signal.
   - The first successful GPU frame emits a readiness signal.
   - If no first frame is produced within 1.8 seconds, the GPU window is considered
     unhealthy even if Qt reports a nominal context.

3. **Automatic CPU fallback**
   - `MainWindow.open_lab3d_window()` listens for the failure signal.
   - The failed GPU window is closed safely outside the OpenGL callback stack.
   - The existing Hotfix33 QPainter 3D renderer is opened automatically.
   - Users never need to know their GPU model or manually switch a setting just to
     make 3D available.

## Why GL_POINTS instead of mandatory instancing
For this product, typical selected sets are around 1,000–10,000 points. A single GPU
point draw is already negligible on old hardware. Mandatory instancing would add a
higher driver/API requirement without meaningful benefit at this scale.

## Unchanged data and color logic
- `qtx_core/*` unchanged
- `library_store.py` unchanged
- Lab coordinates unchanged
- QTX / CPX / Excel unchanged
- CMC / CIEDE2000 / MI / 555 unchanged
- P3-1 / P3-2 SQLite work unchanged

## Long-term renderer policy
The stable contract should be:

`3D ViewModel / Lab data -> Renderer interface -> GPU backend OR compatibility backend`

No future business function should directly depend on one specific graphics API.
