# Hotfix43 · ChromaShare Render / Fast 3D / Library Switch

## 1. 3D visual profile
- Reference: the ChromaShare Lab-space video supplied by the user.
- Display-only Lab-aware colour mapping: cleaner hue separation, lifted dark colours, controlled saturation.
- Neutral mid-gray background and three soft fill lights.
- AO disabled and MSAA reduced to Medium to avoid dirty shadows and reduce GPU cost.
- Grid/axis visual weight reduced.
- Measured Lab/spectrum and library swatches are not changed.

## 2. 3D performance
- New low-poly smooth sphere geometry for instanced points instead of Qt's high-detail built-in sphere.
- 3,500 points remain complete; no sampling or coordinate changes.
- Gamut surface geometry is built lazily only when Surface/Surface+Points is requested.
- Formal/official-library 3D at D65/10° loads only P3-2 scalar index fields (name/path/Lab) instead of deserializing thousands of spectral JSON payloads.
- Non-D65/10° keeps the full spectral path because recalculation requires reflectance.

## 3. Library opening/switching
- Formal library no longer auto-opens “全部客户”; it opens as a navigation container, matching official library behavior.
- Switching formal/official library is atomic and brings the existing library window forward immediately.
- Official shortcuts defer customer opening one event cycle after scope reset to remove the occasional first-click/no-response race.

## Protected areas
No intended change to qtx_core colour science, QTX/CPX/Excel parsing, CMC/DE2000/MI/555, RBAC, or saved payload format.
