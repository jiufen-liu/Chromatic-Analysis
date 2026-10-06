# Hotfix82 · Visual Palette V2.4.1 Offline Audit

- Keeps V2.4 unchanged as the baseline.
- Adds audit-only V2.4.1.
- Adds a data-adaptive near-white bridge rule: a chromatic swatch is promoted into Neutral Field only when L* >= 90, C* <= 18, and it is within CIEDE2000 ΔE00 < 3 of an *initial* Tinted White in the same dataset.
- Promotion is one-pass from initial Tinted White seeds; promoted samples do not recursively recruit further colours.
- This targets the YKK/Walmart blue-white boundary without globally widening the white envelope.
- Adds NearWhite bridge details to audit XLSX.
- Still offline-only; not wired into Palette Studio.
- main_window.py / QTX parser / 555 / 3D / Excel business exchange remain unchanged from HF81.
