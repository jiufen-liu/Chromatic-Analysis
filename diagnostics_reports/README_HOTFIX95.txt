Hotfix95 diagnostics output folder.

Use RUN_DIAGNOSTICS.bat -> [20] PAC V24.1 Science A/B.
The run creates a P241_SCIENCE_<timestamp> folder with:
- 001_V23.png / 001_SCIENCE.png
- 001_AUDIT.txt / 001_AUDIT.json
- MANIFEST.csv / SUMMARY.csv
- PAC_V241_SCIENCE_AB_SUMMARY.csv

Do not judge success only by mean ΔE00. Structural acceptance also requires:
CROSS_FAMILY_ANCHOR_RISK=0 and WARM_COOL_INVERSION_RISK=0, plus visual review of khaki/blue, pale/deep blue and near-neutral regions.
