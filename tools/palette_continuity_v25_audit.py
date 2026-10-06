from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qtx_core import (
    parse_qtx_file,
    appearance_continuity_layout_v23,
    appearance_continuity_layout_v241_science,
    appearance_continuity_layout_v25,
)
from qtx_core.colorimetry import delta_e
from qtx_app.excel_exchange import _lab_to_hex
from tools.pac_batch_utils import (
    BatchCaseResult,
    choose_batch_dir,
    footer,
    write_manifest,
    write_summary,
)

OUT = ROOT / "diagnostics_reports"


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, max(0, int(round((len(values) - 1) * q))))] if values else 0.0


def stats(values):
    values = [float(x) for x in values if math.isfinite(float(x))]
    return {
        "count": len(values),
        "mean": statistics.fmean(values) if values else 0.0,
        "p95": pct(values, .95),
        "max": max(values) if values else 0.0,
    }


def row_metrics(grid):
    vals = []
    over10 = over20 = 0
    for row in grid:
        ss = [s for s in row if s is not None]
        for a, b in zip(ss, ss[1:]):
            d = float(delta_e(a.lab_d65_10, b.lab_d65_10, "CIE 2000"))
            vals.append(d)
            over10 += int(d > 10)
            over20 += int(d > 20)
    r = stats(vals)
    r["over10"] = over10
    r["over20"] = over20
    return r


def linear_grid(order, columns):
    out = []
    for i in range(0, len(order), columns):
        row = list(order[i:i + columns])
        row += [None] * (columns - len(row))
        out.append(row)
    return out


def safe_hex(lab):
    try:
        return _lab_to_hex(tuple(float(x) for x in lab)).replace("#", "")
    except Exception:
        return "D9D9D9"


def preview(path: Path, result, title: str, *, software_linear: bool = False):
    from PIL import Image, ImageDraw, ImageFont

    cols = int(result["columns"])
    grid = linear_grid(result["order"], cols) if software_linear else result["grid"]
    info = result["info"]
    cw, ch, sw, label = 205, 105, 60, 135
    w = label + cols * cw
    h = max(1, len(grid)) * ch + 28
    im = Image.new("RGB", (w, h), "white")
    dr = ImageDraw.Draw(im)
    font = ImageFont.load_default()
    dr.text((4, 4), title, fill=(20, 25, 32), font=font)
    oy = 28
    for r, row in enumerate(grid):
        y = oy + r * ch
        first = next((s for s in row if s is not None), None)
        if first is not None:
            ii = info[id(first)]
            fam = ii.get("family", "")
            tone = ii.get("tone", "")
            chroma = ii.get("chroma", "")
            dr.text((4, y + 7), f"{fam}\n{tone} / {chroma}", fill=(55, 65, 78), font=font)
        for c, sm in enumerate(row):
            if sm is None:
                continue
            x = label + c * cw
            hx = safe_hex(sm.lab_d65_10)
            rgb = tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))
            dr.rounded_rectangle((x + 4, y + 4, x + cw - 8, y + sw), radius=7, fill=rgb, outline=(215, 220, 228))
            name = sm.display_name or "(unnamed)"
            name = name if len(name) <= 25 else name[:24] + "…"
            L, a, b = (float(x) for x in sm.lab_d65_10)
            C = math.hypot(a, b)
            hue = math.degrees(math.atan2(b, a)) % 360 if C > 1e-12 else 0
            dr.text((x + 7, y + sw + 5), name, fill=(20, 25, 32), font=font)
            dr.text((x + 7, y + sw + 21), f"L {L:.1f}  C {C:.1f}  h {hue:.0f}°", fill=(85, 95, 110), font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def audit_one(qtx: Path, out: Path, case_id: str, columns=6):
    t = perf_counter(); samples = list(parse_qtx_file(qtx)); parse_s = perf_counter() - t
    t = perf_counter(); v23 = appearance_continuity_layout_v23(samples, columns); v23_s = perf_counter() - t
    t = perf_counter(); v241 = appearance_continuity_layout_v241_science(samples, columns); v241_s = perf_counter() - t
    t = perf_counter(); v25 = appearance_continuity_layout_v25(samples, columns); v25_s = perf_counter() - t

    m23 = row_metrics(v23["grid"])
    m241 = row_metrics(v241["grid"])
    m25_struct = row_metrics(v25["grid"])
    m25_linear = row_metrics(linear_grid(v25["order"], columns))

    p23 = out / f"{case_id}_V23.png"
    p241 = out / f"{case_id}_V241.png"
    p25 = out / f"{case_id}_V25.png"
    p25s = out / f"{case_id}_V25_STRUCTURAL.png"
    txt = out / f"{case_id}_AUDIT.txt"
    js = out / f"{case_id}_AUDIT.json"

    preview(p23, v23, "PAC V23 baseline")
    preview(p241, v241, "PAC V24.1 Science baseline")
    preview(p25, v25, "PAC V25 HVC Surface Path — SOFTWARE ORDER", software_linear=True)
    preview(p25s, v25, "PAC V25 structural groups / break view")

    boundaries = v25.get("family_boundaries") or []
    payload = {
        "file": str(qtx), "samples": len(samples), "parse_s": parse_s,
        "v23_s": v23_s, "v241_s": v241_s, "v25_s": v25_s,
        "v23": m23, "v241": m241, "v25_structural": m25_struct, "v25_linear": m25_linear,
        "v23_groups": len(v23.get("groups") or []),
        "v241_groups": len(v241.get("groups") or []),
        "v25_groups": len(v25.get("groups") or []),
        "v25_breaks": len(v25.get("breaks") or []),
        "v25_compactness": v25.get("compactness", 0.0),
        "cross_family_anchor_risk": v25.get("cross_family_anchor_risk", 0),
        "warm_cool_inversion_risk": v25.get("warm_cool_inversion_risk", 0),
        "neutral_split_count": v25.get("neutral_split_count", 0),
        "core_neutral_count": v25.get("core_neutral_count", 0),
        "tinted_neutral_count": v25.get("tinted_neutral_count", 0),
        "pale_boundary_island_changes": v25.get("pale_boundary_island_changes", 0),
        "family_boundary_max_de": v25.get("family_boundary_max_de", 0.0),
        "family_boundary_max_dL": v25.get("family_boundary_max_dL", 0.0),
        "family_boundary_max_regret": v25.get("family_boundary_max_regret", 0.0),
        "catastrophic_boundary_count": v25.get("catastrophic_boundary_count", 0),
        "avoidable_catastrophic_boundary_count": v25.get("avoidable_catastrophic_boundary_count", 0),
        "within_family_max_de00": v25.get("within_family_max_de00", 0.0),
        "lightness_reversal_count": v25.get("lightness_reversal_count", 0),
        "isolated_chromatic_island": v25.get("isolated_chromatic_island", 0),
        "family_boundaries": boundaries,
        "surface_orientation": v25.get("surface_orientation") or {},
        "previews": {"v23": p23.name, "v241": p241.name, "v25": p25.name, "v25_structural": p25s.name},
    }
    js.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "Chromatic Analysis · PAC V25 HVC Surface Path / Neutral Axis A/B",
        "=" * 82,
        f"文件: {qtx}", f"色样: {len(samples)}",
        f"耗时: parse={parse_s:.3f}s V23={v23_s:.3f}s V24.1={v241_s:.3f}s V25={v25_s:.3f}s", "",
        f"V23 row ΔE00: mean={m23['mean']:.3f} p95={m23['p95']:.3f} max={m23['max']:.3f}",
        f"V24.1 row ΔE00: mean={m241['mean']:.3f} p95={m241['p95']:.3f} max={m241['max']:.3f}",
        f"V25 structural row ΔE00: mean={m25_struct['mean']:.3f} p95={m25_struct['p95']:.3f} max={m25_struct['max']:.3f}",
        f"V25 SOFTWARE linear ΔE00: mean={m25_linear['mean']:.3f} p95={m25_linear['p95']:.3f} max={m25_linear['max']:.3f}", "",
        f"Neutral Axis: core={payload['core_neutral_count']} tinted={payload['tinted_neutral_count']} split={payload['neutral_split_count']}",
        f"Hue guards: CROSS_FAMILY={payload['cross_family_anchor_risk']} WARM_COOL={payload['warm_cool_inversion_risk']} PALE_BOUNDARY_MOVES={payload['pale_boundary_island_changes']}",
        f"Family boundary: maxΔE00={payload['family_boundary_max_de']:.3f} maxΔL*={payload['family_boundary_max_dL']:.3f} max_regret={payload['family_boundary_max_regret']:.3f}",
        f"Boundary flags: catastrophic={payload['catastrophic_boundary_count']} avoidable_catastrophic={payload['avoidable_catastrophic_boundary_count']}",
        f"Within-family maxΔE00={payload['within_family_max_de00']:.3f}  lightness_reversals={payload['lightness_reversal_count']}  isolated_islands={payload['isolated_chromatic_island']}", "",
        "Family boundaries:",
    ]
    for b in boundaries:
        lines.append(
            f"  {b['from_family']} -> {b['to_family']}: ΔE00={b['de00']:.3f} "
            f"best_available={b.get('min_possible_de00',0):.3f} regret={b.get('boundary_regret_de00',0):.3f} "
            f"ΔL*={b['dL']:.3f} avoidable={int(bool(b.get('avoidable_catastrophic')))}"
        )
    lines += [
        "",
        f"V23: {p23}", f"V24.1: {p241}",
        f"V25 SOFTWARE ORDER: {p25}", f"V25 structural: {p25s}",
        "重点检查：浅灰不得首尾分裂；卡其/米色不得进入Blue；Near-White边界小岛不得与同色相深色形成灾难跳跃；深蓝→紫→黑应连续。",
    ]
    txt.write_text("\n".join(lines), encoding="utf-8")
    return payload


def batch(folder: Path, columns: int):
    files = sorted(p for p in folder.rglob("*.qtx") if p.is_file())
    if not files:
        print(f"文件夹中没有QTX: {folder}")
        return 2
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = choose_batch_dir(OUT, "P25_HVC", stamp)
    rows = []
    cases: list[BatchCaseResult] = []
    print(f"PAC V25 HVC Surface Path 批量验证 {len(files)} 个 QTX；结果目录: {out}")
    for i, qtx in enumerate(files, 1):
        case_id = f"{i:03d}"
        c = BatchCaseResult(case_id=case_id, qtx_name=qtx.name, qtx_path=str(qtx))
        c.preview = f"{case_id}_V25.png"
        c.audit_txt = f"{case_id}_AUDIT.txt"
        c.audit_json = f"{case_id}_AUDIT.json"
        print(f"[{i}/{len(files)}] {qtx.name}", flush=True)
        try:
            payload = audit_one(qtx, out, case_id, columns)
            rows.append(payload)
            c.status = "PASS"
        except Exception as exc:
            c.status = "FAIL"
            c.failed_stage = "RUN_CASE"
            c.error = f"{type(exc).__name__}: {exc}"
            rows.append({"file": str(qtx), "error": c.error})
            print("  失败:", exc)
        c.preview_exists = (out / c.preview).is_file()
        c.audit_txt_exists = (out / c.audit_txt).is_file()
        c.audit_json_exists = (out / c.audit_json).is_file()
        if c.status == "PASS" and not (c.preview_exists and c.audit_txt_exists and c.audit_json_exists):
            c.status = "FAIL"; c.failed_stage = "FINALIZE"; c.error = "Missing V25 artifact(s)"
        cases.append(c)

    metrics = out / "PAC_V25_HVC_AB_SUMMARY.csv"
    headers = [
        "file", "samples", "v23_mean", "v241_mean", "v25_row_mean", "v25_linear_mean",
        "v23_p95", "v241_p95", "v25_row_p95", "v25_linear_p95",
        "v23_max", "v241_max", "v25_row_max", "v25_linear_max",
        "neutral_split_count", "cross_family_anchor_risk", "warm_cool_inversion_risk",
        "pale_boundary_island_changes", "family_boundary_max_de", "family_boundary_max_regret",
        "catastrophic_boundary_count", "avoidable_catastrophic_boundary_count",
        "within_family_max_de00", "lightness_reversal_count", "isolated_chromatic_island", "status",
    ]
    with metrics.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f); w.writerow(headers)
        for x in rows:
            if "error" in x:
                w.writerow([x["file"]] + [""] * (len(headers) - 2) + [x["error"]])
                continue
            w.writerow([
                x["file"], x["samples"], x["v23"]["mean"], x["v241"]["mean"], x["v25_structural"]["mean"], x["v25_linear"]["mean"],
                x["v23"]["p95"], x["v241"]["p95"], x["v25_structural"]["p95"], x["v25_linear"]["p95"],
                x["v23"]["max"], x["v241"]["max"], x["v25_structural"]["max"], x["v25_linear"]["max"],
                x["neutral_split_count"], x["cross_family_anchor_risk"], x["warm_cool_inversion_risk"], x["pale_boundary_island_changes"],
                x["family_boundary_max_de"], x["family_boundary_max_regret"], x["catastrophic_boundary_count"], x["avoidable_catastrophic_boundary_count"],
                x["within_family_max_de00"], x["lightness_reversal_count"], x["isolated_chromatic_island"], "PASS",
            ])

    write_manifest(out, cases)
    write_summary(out, cases, {"ab_metrics": metrics.name})
    print(footer(cases, out))
    print("每个case生成 V23 / V24.1 / V25 SOFTWARE / V25 STRUCTURAL 四张预览。")
    return 0 if all(c.status == "PASS" for c in cases) else 1


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("path", type=Path); ap.add_argument("--columns", type=int, default=6)
    a = ap.parse_args(); p = a.path.expanduser().resolve(); OUT.mkdir(exist_ok=True)
    print("Chromatic Analysis · PAC V25 HVC Surface Path / Neutral Axis A/B")
    print("=" * 82)
    print("V23 / V24.1作为基线；V25与软件主按钮使用同一 HVC曲面+中性轴+端点优化核心。")
    if not p.exists(): print("路径不存在:", p); return 2
    if p.is_dir(): return batch(p, a.columns)
    if p.suffix.lower() != ".qtx": print("请选择QTX或包含QTX的文件夹"); return 2
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = choose_batch_dir(OUT, "P25_HVC_SINGLE", stamp)
    r = audit_one(p, out, "001", a.columns)
    print(f"完成。\nV25软件顺序预览: {out / r['previews']['v25']}\n报告: {out / '001_AUDIT.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
