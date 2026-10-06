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
    spectral_order,
    appearance_continuity_layout_v23,
    appearance_continuity_layout_v24,
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


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(round((len(v) - 1) * q))))] if v else 0.0


def stats(v):
    v = [float(x) for x in v if math.isfinite(float(x))]
    return {"count": len(v), "mean": statistics.fmean(v) if v else 0.0, "p95": pct(v, .95), "max": max(v) if v else 0.0}


def row_metrics(grid):
    vals = []
    over10 = over20 = 0
    for row in grid:
        ss = [s for s in row if s is not None]
        for a, b in zip(ss, ss[1:]):
            d = float(delta_e(a.lab_d65_10, b.lab_d65_10, "CIE 2000"))
            vals.append(d)
            over10 += d > 10
            over20 += d > 20
    r = stats(vals)
    r["over10"] = over10
    r["over20"] = over20
    return r


def linear_metrics(order, columns):
    grid = []
    for i in range(0, len(order), columns):
        row = list(order[i:i + columns])
        row += [None] * (columns - len(row))
        grid.append(row)
    return row_metrics(grid)


def safe_hex(lab):
    try:
        return _lab_to_hex(tuple(float(x) for x in lab)).replace("#", "")
    except Exception:
        return "D9D9D9"


def preview(path: Path, result, title: str):
    from PIL import Image, ImageDraw, ImageFont
    grid = result["grid"]
    cols = result["columns"]
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
            dr.text((4, y + 7), f"{ii['family']}\n{ii['tone']} / {ii['chroma']}", fill=(55, 65, 78), font=font)
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
    t = perf_counter(); legacy = spectral_order(samples, hybrid=True); legacy_s = perf_counter() - t
    t = perf_counter(); v23 = appearance_continuity_layout_v23(samples, columns); v23_s = perf_counter() - t
    t = perf_counter(); v24 = appearance_continuity_layout_v24(samples, columns); v24_s = perf_counter() - t

    lm = linear_metrics(legacy, columns)
    m23 = row_metrics(v23["grid"])
    m24 = row_metrics(v24["grid"])

    p23 = out / f"{case_id}_V23.png"
    p24 = out / f"{case_id}_V24.png"
    txt = out / f"{case_id}_AUDIT.txt"
    js = out / f"{case_id}_AUDIT.json"
    preview(p23, v23, "PAC V23 baseline")
    preview(p24, v24, "PAC V24 Neutral Confidence + Hue Confidence")

    payload = {
        "file": str(qtx), "samples": len(samples), "parse_s": parse_s,
        "legacy_s": legacy_s, "v23_s": v23_s, "v24_s": v24_s,
        "legacy": lm, "v23": m23, "v24": m24,
        "v23_groups": len(v23["groups"]), "v24_groups": len(v24["groups"]),
        "v23_breaks": len(v23["breaks"]), "v24_breaks": len(v24["breaks"]),
        "v23_compactness": v23.get("compactness", 0.0), "v24_compactness": v24.get("compactness", 0.0),
        "low_chroma_hue_risk": v24.get("low_chroma_hue_risk", 0),
        "deep_hue_risk": v24.get("deep_hue_risk", 0),
        "near_white_hue_risk": v24.get("near_white_hue_risk", 0),
        "v23_preview": p23.name, "v24_preview": p24.name,
    }
    js.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "Chromatic Analysis · PAC V24 Neutral Confidence + Hue Confidence A/B",
        "=" * 76,
        f"文件: {qtx}", f"色样: {len(samples)}",
        f"耗时: parse={parse_s:.3f}s legacy={legacy_s:.3f}s V23={v23_s:.3f}s V24={v24_s:.3f}s", "",
        f"Legacy ΔE00: mean={lm['mean']:.3f} p95={lm['p95']:.3f} max={lm['max']:.3f} >10={lm['over10']} >20={lm['over20']}",
        f"V23    ΔE00: mean={m23['mean']:.3f} p95={m23['p95']:.3f} max={m23['max']:.3f} >10={m23['over10']} >20={m23['over20']}",
        f"V24    ΔE00: mean={m24['mean']:.3f} p95={m24['p95']:.3f} max={m24['max']:.3f} >10={m24['over10']} >20={m24['over20']}", "",
        f"V23: groups={len(v23['groups'])} breaks={len(v23['breaks'])} compactness={v23.get('compactness',0.0):.3f}",
        f"V24: groups={len(v24['groups'])} breaks={len(v24['breaks'])} compactness={v24.get('compactness',0.0):.3f}",
        f"V24风险指标: LOW_CHROMA_HUE_RISK={payload['low_chroma_hue_risk']}  DEEP_HUE_RISK={payload['deep_hue_risk']}  NEAR_WHITE_HUE_RISK={payload['near_white_hue_risk']}", "",
        f"V23预览: {p23}", f"V24预览: {p24}",
        "重点目视比较：近白/浅灰是否不再被强行塞进 Green/Blue/Orange-Brown，同时确认真正的浅蓝、浅绿、粉色没有被过度中性化。",
    ]
    txt.write_text("\n".join(lines), encoding="utf-8")
    return payload


def batch(folder: Path, columns: int):
    files = sorted(p for p in folder.rglob("*.qtx") if p.is_file())
    if not files:
        print(f"文件夹中没有QTX: {folder}")
        return 2
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = choose_batch_dir(OUT, "P24", stamp)
    rows = []
    cases: list[BatchCaseResult] = []
    print(f"PAC V24 A/B 批量验证 {len(files)} 个 QTX；结果目录: {out}")
    for i, qtx in enumerate(files, 1):
        case_id = f"{i:03d}"
        c = BatchCaseResult(case_id=case_id, qtx_name=qtx.name, qtx_path=str(qtx))
        # For the common manifest schema, PREVIEW means the V24 preview.
        c.preview = f"{case_id}_V24.png"
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
            c.status = "FAIL"; c.failed_stage = "FINALIZE"; c.error = "Missing V24 artifact(s)"
        cases.append(c)

    metrics = out / "PAC_V24_AB_SUMMARY.csv"
    headers = [
        "file", "samples", "legacy_mean", "v23_mean", "v24_mean", "legacy_p95", "v23_p95", "v24_p95",
        "legacy_max", "v23_max", "v24_max", "v23_groups", "v24_groups", "v23_breaks", "v24_breaks",
        "v23_compactness", "v24_compactness", "low_chroma_hue_risk", "deep_hue_risk", "near_white_hue_risk", "status",
    ]
    with metrics.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f); w.writerow(headers)
        for x in rows:
            if "error" in x:
                w.writerow([x["file"]] + [""] * (len(headers) - 2) + [x["error"]])
                continue
            w.writerow([
                x["file"], x["samples"], x["legacy"]["mean"], x["v23"]["mean"], x["v24"]["mean"],
                x["legacy"]["p95"], x["v23"]["p95"], x["v24"]["p95"], x["legacy"]["max"], x["v23"]["max"], x["v24"]["max"],
                x["v23_groups"], x["v24_groups"], x["v23_breaks"], x["v24_breaks"], x["v23_compactness"], x["v24_compactness"],
                x["low_chroma_hue_risk"], x["deep_hue_risk"], x["near_white_hue_risk"], "PASS",
            ])
    write_manifest(out, cases)
    write_summary(out, cases, {"ab_metrics": metrics.name})
    print(footer(cases, out))
    print("A/B 额外预览：每个 case 同时生成 001_V23.png 与 001_V24.png。")
    return 0 if all(c.status == "PASS" for c in cases) else 1


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("path", type=Path); ap.add_argument("--columns", type=int, default=6)
    a = ap.parse_args(); p = a.path.expanduser().resolve(); OUT.mkdir(exist_ok=True)
    print("Chromatic Analysis · PAC V24 Neutral Confidence + Hue Confidence A/B")
    print("=" * 76)
    print("V23排序作为冻结基线；V24只做离线候选验证，不替换正式色卡编排。")
    if not p.exists(): print("路径不存在:", p); return 2
    if p.is_dir(): return batch(p, a.columns)
    if p.suffix.lower() != ".qtx": print("请选择QTX或包含QTX的文件夹"); return 2
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S"); out = choose_batch_dir(OUT, "P24_SINGLE", stamp)
    r = audit_one(p, out, "001", a.columns)
    print(f"完成。\nV23预览: {out / r['v23_preview']}\nV24预览: {out / r['v24_preview']}\n报告: {out / '001_AUDIT.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
