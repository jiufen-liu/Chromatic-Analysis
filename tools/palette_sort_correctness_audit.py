from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import traceback
from datetime import datetime
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

from qtx_core import (
    parse_qtx_file,
    reflectance_to_xyz_lab,
    munsell_hue_order_from_xyz,
    spectral_order,
    visual_palette_order_v1,
)
from qtx_core.analysis import spectral_rms_distance
from qtx_core.colorimetry import delta_e
from qtx_app.excel_exchange import _lab_to_hex


def _pct(vals, q: float) -> float:
    vals = sorted(float(v) for v in vals)
    if not vals:
        return 0.0
    idx = min(len(vals) - 1, max(0, int(round((len(vals) - 1) * q))))
    return vals[idx]


def _summary(vals) -> dict[str, float]:
    vals = [float(v) for v in vals]
    if not vals:
        return {"count": 0, "mean": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "count": len(vals),
        "mean": statistics.fmean(vals),
        "median": statistics.median(vals),
        "p95": _pct(vals, 0.95),
        "max": max(vals),
    }


def _swatch_fill(lab):
    try:
        return PatternFill("solid", fgColor=_lab_to_hex(tuple(float(x) for x in lab)))
    except Exception:
        return PatternFill("solid", fgColor="D9D9D9")


def _hue_family(idx: float) -> str:
    if not math.isfinite(idx):
        return "N"
    families = ["R", "YR", "Y", "GY", "G", "BG", "B", "PB", "P", "RP"]
    fam = int(idx // 10)
    return families[fam] if 0 <= fam < len(families) else "?"


def _munsell_current_sort_key(rec):
    # Mirrors HF70 production palette-sort semantics exactly.
    info = rec["current_info"]
    idx = float(info[1])
    family = int(idx // 10) if math.isfinite(idx) else 99
    pos = idx % 10 if math.isfinite(idx) else 0.0
    return (family, pos, -float(info[3]), -float(info[4]), rec["name"].casefold())


def _munsell_strict_sort_key(rec):
    # Audit-only proposal: true Munsell chromatic -> true neutral -> fallback last.
    status = rec["strict_bucket"]
    if status == "chromatic":
        idx = rec["munsell_index"]
        return (0, int(idx // 10), idx % 10, -rec["munsell_value"], -rec["munsell_chroma"], rec["name"].casefold())
    if status == "neutral":
        return (1, 99, 0.0, -rec["munsell_value"], -rec["munsell_chroma"], rec["name"].casefold())
    return (2, 99, 0.0, -rec["L"], -rec["C"], rec["name"].casefold())


def _transition_rows(order, id_by_obj):
    rows = []
    for rank in range(1, len(order)):
        prev, cur = order[rank - 1], order[rank]
        try:
            spec = spectral_rms_distance(prev, cur)
        except Exception:
            spec = float("nan")
        try:
            de00 = delta_e(prev.lab_d65_10, cur.lab_d65_10, "CIE 2000")
        except Exception:
            de00 = float("nan")
        rows.append({
            "rank": rank + 1,
            "from_id": id_by_obj[id(prev)],
            "from_name": prev.display_name,
            "to_id": id_by_obj[id(cur)],
            "to_name": cur.display_name,
            "spectral_rms": spec,
            "de00": de00,
            "hybrid_score": spec + 0.18 * de00 if math.isfinite(spec) and math.isfinite(de00) else float("nan"),
        })
    return rows


def _set_header(ws, headers):
    for c, text in enumerate(headers, 1):
        cell = ws.cell(1, c, text)
        cell.font = Font(bold=True, color="1F2937")
        cell.fill = PatternFill("solid", fgColor="E8EEF7")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"


def _thin_border():
    side = Side(style="thin", color="D9E0EA")
    return Border(left=side, right=side, top=side, bottom=side)


def _write_records_sheet(wb, title, rows, headers, swatch_lab_key=None):
    ws = wb.create_sheet(title)
    ws.sheet_view.showGridLines = False
    _set_header(ws, [h[0] for h in headers])
    border = _thin_border()
    for r_idx, row in enumerate(rows, 2):
        for c_idx, (_, key) in enumerate(headers, 1):
            val = row.get(key, "")
            if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                val = "" if math.isnan(val) else "∞"
            cell = ws.cell(r_idx, c_idx, val)
            cell.border = border
            if isinstance(val, float):
                cell.number_format = "0.0000"
        if swatch_lab_key:
            lab = row.get(swatch_lab_key)
            if lab:
                ws.cell(r_idx, 2).fill = _swatch_fill(lab)
                ws.cell(r_idx, 2).value = ""
    for idx, (label, _) in enumerate(headers, 1):
        width = 14
        if "名称" in label or "原因" in label or "说明" in label or "Notation" in label:
            width = 32
        if label == "色块":
            width = 10
        ws.column_dimensions[get_column_letter(idx)].width = width
    return ws


def main() -> int:
    ap = argparse.ArgumentParser(description="Chromatic Analysis palette sort science/logic audit")
    ap.add_argument("qtx", type=Path)
    args = ap.parse_args()
    path = args.qtx.expanduser().resolve()
    if not path.exists():
        print(f"文件不存在: {path}")
        return 2

    print("Chromatic Analysis · 色卡排序科学/逻辑验证 R1")
    print("=" * 68)
    print(f"文件: {path}")
    print("本测试不修改色卡、不修改色库、不修改排序算法；只生成验证报告。")
    print()

    t0 = perf_counter()
    samples = parse_qtx_file(path)
    parse_s = perf_counter() - t0
    id_by_obj = {id(s): f"S{i:04d}" for i, s in enumerate(samples, 1)}
    records = []

    print(f"[1/5] QTX 已解析：{len(samples)} 个色样，{parse_s:.3f} s")
    print("[2/5] 对全部色样计算审计用精确 Munsell（C / 2°）...", flush=True)
    mt0 = perf_counter()
    for i, s in enumerate(samples, 1):
        L, a, b = (float(x) for x in s.lab_d65_10)
        C = math.hypot(a, b)
        h = math.degrees(math.atan2(b, a)) % 360.0
        rec = {
            "id": id_by_obj[id(s)], "name": s.display_name, "lab": (L, a, b),
            "L": L, "a": a, "b": b, "C": C, "h": h,
            "has_spectrum": bool(s.has_spectrum()), "pre_neutral_C_lt_3": C < 3.0,
            "munsell_index": float("nan"), "munsell_notation": "", "munsell_value": float("nan"),
            "munsell_chroma": float("nan"), "munsell_exact_neutral": False,
            "munsell_error": "", "fallback": False,
        }
        if s.has_spectrum():
            try:
                xyz = reflectance_to_xyz_lab(s.reflectance, "C", s.wavelengths, 2)[0]
                idx, notation, value, chroma = munsell_hue_order_from_xyz(xyz)
                rec.update({
                    "munsell_index": float(idx), "munsell_notation": notation,
                    "munsell_value": float(value), "munsell_chroma": float(chroma),
                    "munsell_exact_neutral": (not math.isfinite(float(idx))) or float(chroma) < 1.5,
                })
            except Exception as exc:
                rec["munsell_error"] = f"{type(exc).__name__}: {exc}"
                rec["fallback"] = True
        else:
            rec["munsell_error"] = "无完整光谱"
            rec["fallback"] = True

        # Reconstruct CURRENT HF70 classification/sort info.
        if C < 3.0:
            rec["current_class"] = "近中性色(C*<3；按L*)"
            rec["current_info"] = (True, float("inf"), "近中性色 · C*<3 · 按 L* 排列", L, C)
        elif not rec["fallback"]:
            idx = rec["munsell_index"]
            chroma = rec["munsell_chroma"]
            is_neutral = (not math.isfinite(idx)) or chroma < 1.5
            if is_neutral:
                rec["current_class"] = "近中性色(Munsell C<1.5；按L*)"
                rec["current_info"] = (True, idx, f"Munsell {rec['munsell_notation']} · 近中性(C<1.5)", L, C)
            else:
                rec["current_class"] = "Munsell彩色(C/2°)"
                rec["current_info"] = (False, idx, f"Munsell {rec['munsell_notation']} · C/2°", rec["munsell_value"], chroma)
        else:
            rec["current_class"] = "Munsell无法映射(置后)"
            rec["current_info"] = (False, float("nan"), "Munsell 无法映射 · 按 L* 置后", L, C)

        if rec["fallback"]:
            rec["strict_bucket"] = "fallback"
        elif rec["munsell_exact_neutral"]:
            rec["strict_bucket"] = "neutral"
        else:
            rec["strict_bucket"] = "chromatic"

        rec["potential_neutral_mismatch"] = bool(
            rec["pre_neutral_C_lt_3"] and not rec["fallback"] and not rec["munsell_exact_neutral"]
        )
        rec["munsell_family"] = _hue_family(rec["munsell_index"])
        records.append(rec)
        if i % 25 == 0 or i == len(samples):
            print(f"    Munsell audit: {i}/{len(samples)}", flush=True)
    munsell_s = perf_counter() - mt0

    # C* default visual order in HF68: first selection is descending.
    c_order = sorted(records, key=lambda r: (r["C"], r["name"].casefold()), reverse=True)
    for rank, rec in enumerate(c_order, 1):
        rec["rank_C_desc"] = rank

    # HF74 experimental Visual Palette Arrangement V1: appearance-first, fast
    # and independent from the existing Munsell/spectral algorithms.
    visual_result = visual_palette_order_v1([(r["id"], r["name"], r["lab"]) for r in records])
    visual_order_ids = list(visual_result.get("keys") or [])
    visual_rank = {rid: i for i, rid in enumerate(visual_order_ids, 1)}
    for rec in records:
        rec["rank_visual_v1"] = visual_rank.get(rec["id"], 10**9)
        vinfo = (visual_result.get("info") or {}).get(rec["id"], {})
        rec["visual_family"] = vinfo.get("family", "")
        rec["visual_bucket"] = vinfo.get("bucket", "")
        rec["visual_neutral_kind"] = vinfo.get("neutral_kind", "")

    fallback_current = [r for r in records if r["fallback"]]
    neutral_current = [r for r in records if (not r["fallback"]) and r["current_info"][0]]
    chromatic_current = [r for r in records if (not r["fallback"]) and not r["current_info"][0]]
    chromatic_current.sort(key=_munsell_current_sort_key)
    neutral_current.sort(key=lambda r: (-float(r["L"]), -float(r["C"]), r["name"].casefold()))
    fallback_current.sort(key=lambda r: (-float(r["L"]), -float(r["C"]), r["name"].casefold()))
    current_munsell = chromatic_current + neutral_current + fallback_current
    for rank, rec in enumerate(current_munsell, 1):
        rec["rank_munsell_current"] = rank

    strict_munsell = sorted(records, key=_munsell_strict_sort_key)
    for rank, rec in enumerate(strict_munsell, 1):
        rec["rank_munsell_strict"] = rank

    print(f"    Munsell审计计算完成：{munsell_s:.3f} s")
    print("[3/5] 复算当前光谱相似度路径与光谱+感知路径...", flush=True)
    spectral_samples = [s for s in samples if s.has_spectrum()]
    sp0 = perf_counter(); spectral_path = spectral_order(spectral_samples, hybrid=False); sp_s = perf_counter() - sp0
    hy0 = perf_counter(); hybrid_path = spectral_order(spectral_samples, hybrid=True); hy_s = perf_counter() - hy0
    spectral_transitions = _transition_rows(spectral_path, id_by_obj)
    hybrid_transitions = _transition_rows(hybrid_path, id_by_obj)

    rank_sp = {id_by_obj[id(s)]: i for i, s in enumerate(spectral_path, 1)}
    rank_hy = {id_by_obj[id(s)]: i for i, s in enumerate(hybrid_path, 1)}
    by_id = {r["id"]: r for r in records}
    for rid, rank in rank_sp.items(): by_id[rid]["rank_spectral"] = rank
    for rid, rank in rank_hy.items(): by_id[rid]["rank_hybrid"] = rank

    spec_stats = _summary([r["spectral_rms"] for r in spectral_transitions if math.isfinite(r["spectral_rms"])])
    hy_spec_stats = _summary([r["spectral_rms"] for r in hybrid_transitions if math.isfinite(r["spectral_rms"])])
    hy_de_stats = _summary([r["de00"] for r in hybrid_transitions if math.isfinite(r["de00"])])

    print(f"    光谱相似度路径: {sp_s:.3f} s；光谱+感知路径: {hy_s:.3f} s")
    print("[4/5] 生成 Excel 可视化验证报告...", flush=True)

    out_dir = ROOT / "diagnostics_reports"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    xlsx_path = out_dir / f"PALETTE_SORT_SCIENCE_AUDIT_R1_{stamp}.xlsx"
    txt_path = out_dir / f"PALETTE_SORT_SCIENCE_AUDIT_R1_{stamp}.txt"
    json_path = out_dir / f"PALETTE_SORT_SCIENCE_AUDIT_R1_{stamp}.json"

    wb = Workbook()
    ws = wb.active
    ws.title = "总览与结论"
    ws.sheet_view.showGridLines = False
    summary_rows = [
        ("项目", "结果 / 定义"),
        ("输入文件", str(path)),
        ("色样总数", len(samples)),
        ("完整光谱", len(spectral_samples)),
        ("C*排序", "C*=sqrt(a*²+b*²)；当前首次点击为高→低。它是彩度排序，不保证色相/明度视觉连续。"),
        ("视觉色貌编排 V1", "实验性：8个宽色相带 + 近中性色单独处理；每个色相带内部 L* 单调，色相带方向用边界 ΔE*ab 自动选择。仅用于色卡视觉组织，不是标准色差公式。"),
        ("视觉V1中性色", f"{visual_result.get('neutral_count',0)} 张；白/灰/黑低彩样不使用不稳定的近中性 h° 参与色相排序。"),
        ("Munsell条件", "精确 Munsell Renotation 固定使用 Illuminant C / 2°。"),
        ("HF70近中性色预判", "当前 Lab(D65/10°数据) C*<3 作为近中性色快速路由；近中性色统一按 L* 亮→暗。"),
        ("Munsell近中性阈值", "执行Munsell后：hue index非有限或 Munsell Chroma<1.5 → 近中性色组；统一按 L* 亮→暗。"),
        ("Fallback当前行为", "Munsell失败样本不再伪装为 Munsell Hue；单独置于末组，并按 L* 亮→暗。"),
        ("光谱排列", "确定性最近邻路径；距离=共同波长范围反射率RMS。属于相似度路径，不是标准绝对色序。"),
        ("光谱+感知", "当前启发式 score = 光谱RMS + 0.18×CIEDE2000。0.18为软件工程权重，不是CIE/ISO标准公式。"),
        ("QTX解析时间(s)", round(parse_s, 4)),
        ("审计Munsell计算(s)", round(munsell_s, 4)),
        ("光谱路径时间(s)", round(sp_s, 4)),
        ("混合路径时间(s)", round(hy_s, 4)),
        ("C*<3预判中性色数量", sum(r["pre_neutral_C_lt_3"] for r in records)),
        ("精确Munsell中性色数量", sum((not r["fallback"]) and r["munsell_exact_neutral"] for r in records)),
        ("潜在中性色不一致", sum(r["potential_neutral_mismatch"] for r in records)),
        ("Munsell fallback", sum(r["fallback"] for r in records)),
        ("光谱路径相邻RMS mean / p95 / max", f"{spec_stats['mean']:.4f} / {spec_stats['p95']:.4f} / {spec_stats['max']:.4f}"),
        ("混合路径相邻RMS mean / p95 / max", f"{hy_spec_stats['mean']:.4f} / {hy_spec_stats['p95']:.4f} / {hy_spec_stats['max']:.4f}"),
        ("混合路径相邻ΔE00 mean / p95 / max", f"{hy_de_stats['mean']:.4f} / {hy_de_stats['p95']:.4f} / {hy_de_stats['max']:.4f}"),
    ]
    for r, pair in enumerate(summary_rows, 1):
        for c, val in enumerate(pair, 1):
            ws.cell(r, c, val)
    for c in (1, 2):
        ws.cell(1, c).font = Font(bold=True)
        ws.cell(1, c).fill = PatternFill("solid", fgColor="E8EEF7")
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 105
    ws.freeze_panes = "A2"

    common_headers = [
        ("序号", "rank"), ("色块", "swatch"), ("ID", "id"), ("名称", "name"),
        ("L*", "L"), ("a*", "a"), ("b*", "b"), ("C*", "C"), ("h°", "h")
    ]

    c_rows = []
    for rank, rec in enumerate(c_order, 1):
        row = dict(rec); row["rank"] = rank; row["swatch"] = ""; c_rows.append(row)
    _write_records_sheet(wb, "C彩度_高到低", c_rows, common_headers, "lab")

    visual_headers = common_headers + [
        ("视觉色相带", "visual_family"), ("视觉分类", "visual_bucket"),
        ("中性色类别", "visual_neutral_kind")
    ]
    visual_rows = []
    for rank, rid in enumerate(visual_order_ids, 1):
        rec = dict(next(r for r in records if r["id"] == rid)); rec["rank"] = rank; rec["swatch"] = ""
        visual_rows.append(rec)
    _write_records_sheet(wb, "视觉色貌编排_V1", visual_rows, visual_headers, "lab")

    mun_headers = common_headers + [
        ("当前分类", "current_class"), ("C*<3预判", "pre_neutral_C_lt_3"),
        ("Munsell Notation", "munsell_notation"), ("Hue Family", "munsell_family"),
        ("Hue Index", "munsell_index"), ("Value", "munsell_value"), ("Chroma", "munsell_chroma"),
        ("精确Munsell判中性", "munsell_exact_neutral"), ("Potential mismatch", "potential_neutral_mismatch"),
        ("Fallback", "fallback"), ("错误/原因", "munsell_error"), ("严格建议序号", "rank_munsell_strict")
    ]
    mun_rows = []
    for rank, rec in enumerate(current_munsell, 1):
        row = dict(rec); row["rank"] = rank; row["swatch"] = ""; mun_rows.append(row)
    _write_records_sheet(wb, "Munsell_HF70当前序", mun_rows, mun_headers, "lab")

    strict_rows = []
    for rank, rec in enumerate(strict_munsell, 1):
        row = dict(rec); row["rank"] = rank; row["swatch"] = ""; strict_rows.append(row)
    _write_records_sheet(wb, "Munsell_审计建议序", strict_rows, mun_headers, "lab")

    # Spectral path sheets with transition metrics.
    sp_trans_by_to = {r["to_id"]: r for r in spectral_transitions}
    hy_trans_by_to = {r["to_id"]: r for r in hybrid_transitions}
    path_headers = common_headers + [
        ("上一色样", "from_name"), ("相邻光谱RMS", "spectral_rms"), ("相邻ΔE00", "de00"), ("混合score", "hybrid_score")
    ]
    sp_rows = []
    for rank, s in enumerate(spectral_path, 1):
        rec = dict(by_id[id_by_obj[id(s)]])
        rec.update(sp_trans_by_to.get(rec["id"], {})); rec["rank"] = rank; rec["swatch"] = ""
        sp_rows.append(rec)
    _write_records_sheet(wb, "光谱相似度路径", sp_rows, path_headers, "lab")

    hy_rows = []
    for rank, s in enumerate(hybrid_path, 1):
        rec = dict(by_id[id_by_obj[id(s)]])
        rec.update(hy_trans_by_to.get(rec["id"], {})); rec["rank"] = rank; rec["swatch"] = ""
        hy_rows.append(rec)
    _write_records_sheet(wb, "光谱加感知路径", hy_rows, path_headers, "lab")

    anomalies = []
    for rec in records:
        reasons = []
        if rec["potential_neutral_mismatch"]: reasons.append("C*<3预判中性，但精确Munsell为彩色")
        if rec["fallback"]: reasons.append("Munsell fallback / 无法精确转换")
        if not rec["has_spectrum"]: reasons.append("缺少完整光谱")
        if reasons:
            row = dict(rec); row["rank"] = len(anomalies)+1; row["swatch"] = ""; row["audit_reason"] = "；".join(reasons); anomalies.append(row)
    anomaly_headers = common_headers + [
        ("审计原因", "audit_reason"), ("当前分类", "current_class"), ("Munsell Notation", "munsell_notation"),
        ("Munsell Chroma", "munsell_chroma"), ("Fallback错误", "munsell_error")
    ]
    _write_records_sheet(wb, "异常与重点样本", anomalies, anomaly_headers, "lab")

    # Biggest visual/spectral jumps make path problems immediately inspectable.
    jumps = []
    for method, rows in (("光谱相似度", spectral_transitions), ("光谱+感知", hybrid_transitions)):
        for r in rows:
            rr = dict(r); rr["method"] = method; jumps.append(rr)
    jumps.sort(key=lambda r: (r["spectral_rms"] if math.isfinite(r["spectral_rms"]) else -1), reverse=True)
    jump_headers = [
        ("方法", "method"), ("到达序号", "rank"), ("前一色样", "from_name"), ("下一色样", "to_name"),
        ("光谱RMS", "spectral_rms"), ("ΔE00", "de00"), ("混合score", "hybrid_score")
    ]
    _write_records_sheet(wb, "最大跳跃_TOP50", jumps[:50], jump_headers)

    compare_headers = [
        ("ID", "id"), ("色块", "swatch"), ("名称", "name"), ("C*高→低序号", "rank_C_desc"),
        ("Munsell HF70当前序号", "rank_munsell_current"), ("Munsell审计建议序号", "rank_munsell_strict"),
        ("光谱序号", "rank_spectral"), ("光谱+感知序号", "rank_hybrid"),
        ("C*", "C"), ("h°", "h"), ("Munsell", "munsell_notation"), ("当前分类", "current_class")
    ]
    comp_rows = []
    for rec in records:
        row = dict(rec); row["swatch"] = ""; comp_rows.append(row)
    _write_records_sheet(wb, "四种排序位置对比", comp_rows, compare_headers, "lab")

    wb.save(xlsx_path)

    report = {
        "schema": 1,
        "build": "HF70-SORT-SCIENCE-AUDIT-R2",
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "input": str(path), "platform": platform.platform(), "python": platform.python_version(),
        "sample_count": len(samples), "spectral_count": len(spectral_samples),
        "parse_s": parse_s, "munsell_audit_s": munsell_s, "spectral_path_s": sp_s, "hybrid_path_s": hy_s,
        "pre_neutral_count": sum(r["pre_neutral_C_lt_3"] for r in records),
        "exact_munsell_neutral_count": sum((not r["fallback"]) and r["munsell_exact_neutral"] for r in records),
        "potential_neutral_mismatch_count": sum(r["potential_neutral_mismatch"] for r in records),
        "fallback_count": sum(r["fallback"] for r in records),
        "spectral_transition": spec_stats, "hybrid_spectral_transition": hy_spec_stats, "hybrid_de00_transition": hy_de_stats,
        "anomalies": [{k: r.get(k) for k in ("id","name","current_class","munsell_notation","munsell_chroma","munsell_error","potential_neutral_mismatch")} for r in anomalies],
    }
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    txt_lines = [
        "Chromatic Analysis · 色卡排序科学/逻辑验证 R1",
        "=" * 68,
        f"输入: {path}",
        f"色样: {len(samples)}；完整光谱: {len(spectral_samples)}",
        f"QTX解析: {parse_s:.3f} s；Munsell审计: {munsell_s:.3f} s",
        "",
        "【C*】",
        "定义: C*=sqrt(a*²+b*²)。当前首次排序为高→低；这是彩度排序，不保证色相/明度连续。",
        "",
        "【Munsell】",
        "精确条件: Illuminant C / 2°",
        f"HF68 C*<3 预判中性色: {report['pre_neutral_count']}",
        f"精确Munsell判中性色: {report['exact_munsell_neutral_count']}",
        f"潜在预判不一致: {report['potential_neutral_mismatch_count']}",
        f"Fallback: {report['fallback_count']}（当前HF68会用Lab h°混入主序列；报告中已单独列出）",
        "",
        "【光谱相似度路径】",
        "算法: greedy nearest-neighbour；距离=反射率RMS。不是全局最优色序。",
        f"相邻RMS mean/p95/max: {spec_stats['mean']:.4f} / {spec_stats['p95']:.4f} / {spec_stats['max']:.4f}",
        "",
        "【光谱+感知】",
        "当前HF68启发式: score = 光谱RMS + 0.18 × ΔE00；0.18不是标准公式权重。",
        f"相邻RMS mean/p95/max: {hy_spec_stats['mean']:.4f} / {hy_spec_stats['p95']:.4f} / {hy_spec_stats['max']:.4f}",
        f"相邻ΔE00 mean/p95/max: {hy_de_stats['mean']:.4f} / {hy_de_stats['p95']:.4f} / {hy_de_stats['max']:.4f}",
        "",
        "下一步请优先查看 Excel：",
        "1) 异常与重点样本",
        "2) Munsell_HF70当前序 vs Munsell_审计建议序",
        "3) 最大跳跃_TOP50",
        "4) 四种排序位置对比",
        "",
        "本版本只做验证，不改变正式排序定义。把 XLSX 或本 TXT 发给 ChatGPT 后再决定最终算法。",
    ]
    txt_path.write_text("\n".join(txt_lines), encoding="utf-8")

    print("[5/5] 完成。")
    print("\n".join(txt_lines[3:]))
    print(f"\n报告已生成:\n{xlsx_path}\n{txt_path}\n{json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
