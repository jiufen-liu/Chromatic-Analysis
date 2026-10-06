# Hotfix45 — Pearl Rendering / Taskbar 3D Window

- Rendering profile translated from the approved visual prompt into real-time Qt Quick 3D parameters.
- 3D display colours only: lower saturation, higher midtone brightness, warm-white pearl lift. Measured Lab/spectra/calculations are unchanged.
- Pearl-white/light-gray studio background; grid and axes are visually de-emphasized.
- Four-direction soft studio fill and broader restrained specular highlight.
- 3D dialog is now parentless top-level Qt.Window so minimizing creates a visible Windows taskbar entry. Main-window destruction closes it automatically.
- No coordinate warping is introduced: actual Lab geometry remains true to the data; a forced spindle shape would distort analytical coordinates and is deliberately not used.
