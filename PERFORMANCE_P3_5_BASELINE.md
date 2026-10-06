# Performance P3-5 — Baseline & Compute Cache

P3-5 closes the Performance P3 programme. It does **not** change colour formulas or UI behaviour.

## What is measured

- QTX cold parse time and time per 1000 samples.
- Cold vs warm spectral XYZ/Lab conversion under an alternate illuminant.
- Cold vs warm pair analysis under F11/TL84.
- Existing profiled SQLite/UI hot paths are aggregated into `performance_summary.json` on normal application exit.

## How to benchmark

Run `RUN_P3_5_BENCHMARK.bat` with no arguments for the bundled reference QTX.
For a meaningful large-file baseline, drag a production QTX onto the BAT file, or run:

`RUN_P3_5_BENCHMARK.bat "D:\path\Coloro 3500.qtx"`

Always compare the same file on the same PC between releases.

## Runtime diagnostics

The application data directory contains:

- `performance.log`: individual operations slower than the configured threshold (default 5 ms).
- `performance_summary.json`: aggregate count / average / max / slow count plus science-cache hit/miss data.

Set `CHROMATIC_PERF_THRESHOLD_MS` before launch to change the slow-call logging threshold.

## P3-5 science cache

`reflectance_to_xyz_lab()` now uses a bounded process cache keyed by the complete reflectance curve, wavelength grid, illuminant and observer. The first call is authoritative ASTM E308. Repeated identical calculations reuse the immutable result.

Cache size is bounded to 8192 entries. It is performance-only and can be cleared at any time without changing data or results.

## Release gate

P3 can be frozen when:

1. P3-1 / P3-2 / P3-3 / P3-4 user workflows remain stable.
2. Repeated spectral calculations show cache hits and materially lower warm latency.
3. Large-file parse/UI timings are recorded on a representative production PC.
4. Closing the app cancels queued background jobs and writes a performance summary without changing user data.
