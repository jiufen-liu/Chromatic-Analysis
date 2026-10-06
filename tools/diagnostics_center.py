from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import traceback
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "diagnostics_reports"
PERF_DIR = ROOT / "performance_reports"


def _build_info() -> dict:
    info = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "python": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "processor": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown"),
        "machine": platform.machine(),
        "cwd": str(Path.cwd()),
        "root": str(ROOT),
    }
    version_file = ROOT / "VERSION"
    if version_file.exists():
        info["version_file"] = version_file.read_text(encoding="utf-8", errors="replace").strip()
    try:
        from qtx_app import build_info
        info["product_name"] = getattr(build_info, "PRODUCT_NAME", "")
        info["product_version"] = getattr(build_info, "PRODUCT_VERSION", "")
        info["build_id"] = getattr(build_info, "BUILD_ID", "")
        info["build_label"] = getattr(build_info, "BUILD_LABEL", "")
    except Exception as exc:
        info["build_info_error"] = repr(exc)
    return info


def _appdata_logs_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "QTX 色彩分析" / "logs"


def _latest_files(folder: Path, pattern: str, limit: int = 10) -> list[Path]:
    if not folder.exists():
        return []
    items = [p for p in folder.glob(pattern) if p.is_file()]
    items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return items[:limit]


def system_report() -> tuple[Path, Path]:
    REPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    info = _build_info()

    try:
        import numpy
        info["numpy"] = numpy.__version__
    except Exception as exc:
        info["numpy"] = f"unavailable: {exc}"
    try:
        import PySide6
        info["PySide6"] = PySide6.__version__
    except Exception as exc:
        info["PySide6"] = f"unavailable: {exc}"
    try:
        import colour
        info["colour"] = getattr(colour, "__version__", "unknown")
    except Exception as exc:
        info["colour"] = f"unavailable: {exc}"

    logs_dir = _appdata_logs_dir()
    info["logs_dir"] = str(logs_dir) if logs_dir else ""
    if logs_dir:
        info["latest_crash_logs"] = [str(p) for p in _latest_files(logs_dir, "crash_*.log", 10)]

    info["latest_performance_reports"] = [
        str(p) for p in _latest_files(PERF_DIR, "*.txt", 15)
    ]

    json_path = REPORT_DIR / f"SYSTEM_DIAGNOSTICS_{stamp}.json"
    txt_path = REPORT_DIR / f"SYSTEM_DIAGNOSTICS_{stamp}.txt"
    json_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "Chromatic Analysis · 系统诊断报告",
        "=" * 60,
        f"时间: {info.get('captured_at','')}",
        f"版本: {info.get('version_file','')}",
        f"Build: {info.get('build_id','')}",
        f"Windows: {info.get('platform','')}",
        f"CPU: {info.get('processor','')}",
        f"Python: {info.get('python','')}",
        f"PySide6: {info.get('PySide6','')}",
        f"NumPy: {info.get('numpy','')}",
        f"colour-science: {info.get('colour','')}",
        f"程序目录: {info.get('root','')}",
        f"崩溃日志目录: {info.get('logs_dir','')}",
        "",
        "最近崩溃日志:",
    ]
    logs = info.get("latest_crash_logs", [])
    lines.extend([f"  {x}" for x in logs] if logs else ["  无"])
    lines += ["", "最近性能报告:"]
    perfs = info.get("latest_performance_reports", [])
    lines.extend([f"  {x}" for x in perfs] if perfs else ["  无"])
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    return txt_path, json_path


def make_bundle() -> Path:
    REPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sys_txt, sys_json = system_report()
    bundle = REPORT_DIR / f"DIAGNOSTIC_BUNDLE_{stamp}.zip"
    logs_dir = _appdata_logs_dir()

    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(sys_txt, arcname=f"system/{sys_txt.name}")
        zf.write(sys_json, arcname=f"system/{sys_json.name}")
        for p in _latest_files(PERF_DIR, "*.txt", 20):
            zf.write(p, arcname=f"performance/{p.name}")
        for p in _latest_files(PERF_DIR, "*.json", 20):
            zf.write(p, arcname=f"performance/{p.name}")
        for pattern in ("PALETTE_SORT_SCIENCE_AUDIT_*.txt", "PALETTE_SORT_SCIENCE_AUDIT_*.json", "PALETTE_SORT_SCIENCE_AUDIT_*.xlsx"):
            for p in _latest_files(REPORT_DIR, pattern, 10):
                zf.write(p, arcname=f"sort_audit/{p.name}")
        for pattern in ("VPA_V2_AUDIT_*.txt", "VPA_V2_AUDIT_*.json", "VPA_V2_AUDIT_*.xlsx", "VPA_V2_PREVIEW_*.png"):
            for p in _latest_files(REPORT_DIR, pattern, 10):
                zf.write(p, arcname=f"visual_palette_v2/{p.name}")
        for pattern in ("VPA_V21_AUDIT_*.txt", "VPA_V21_AUDIT_*.json", "VPA_V21_AUDIT_*.xlsx", "VPA_V21_PREVIEW_*.png"):
            for p in _latest_files(REPORT_DIR, pattern, 10):
                zf.write(p, arcname=f"visual_palette_v21/{p.name}")
        for pattern in ("VPA_V22_*_AUDIT.txt", "VPA_V22_*_AUDIT.json", "VPA_V22_*_AUDIT.xlsx", "VPA_V22_*_PREVIEW.png", "VPA_V22_BATCH_SUMMARY_*.xlsx", "VPA_V22_BATCH_SUMMARY_*.json"):
            for p in _latest_files(REPORT_DIR, pattern, 20):
                zf.write(p, arcname=f"visual_palette_v22/{p.name}")
        for pattern in ("VPA_V23_*_AUDIT.txt", "VPA_V23_*_AUDIT.json", "VPA_V23_*_AUDIT.xlsx", "VPA_V23_*_PREVIEW.png", "VPA_V23_BATCH_SUMMARY_*.xlsx", "VPA_V23_BATCH_SUMMARY_*.json"):
            for p in _latest_files(REPORT_DIR, pattern, 20):
                zf.write(p, arcname=f"visual_palette_v23/{p.name}")
        for pattern in ("VPA_V24_*_AUDIT.txt", "VPA_V24_*_AUDIT.json", "VPA_V24_*_AUDIT.xlsx", "VPA_V24_*_PREVIEW.png", "VPA_V24_BATCH_SUMMARY_*.xlsx", "VPA_V24_BATCH_SUMMARY_*.json"):
            for p in _latest_files(REPORT_DIR, pattern, 20):
                zf.write(p, arcname=f"visual_palette_v24/{p.name}")
        for pattern in ("VPA_V241_*_AUDIT.txt", "VPA_V241_*_AUDIT.json", "VPA_V241_*_AUDIT.xlsx", "VPA_V241_*_PREVIEW.png", "VPA_V241_BATCH_SUMMARY_*.xlsx", "VPA_V241_BATCH_SUMMARY_*.json"):
            for p in _latest_files(REPORT_DIR, pattern, 20):
                zf.write(p, arcname=f"visual_palette_v241/{p.name}")
        for pattern in ("VPA_V242_*_AUDIT.txt", "VPA_V242_*_AUDIT.json", "VPA_V242_*_AUDIT.xlsx", "VPA_V242_*_PREVIEW.png", "VPA_V242_BATCH_SUMMARY_*.xlsx", "VPA_V242_BATCH_SUMMARY_*.json"):
            for p in _latest_files(REPORT_DIR, pattern, 20):
                zf.write(p, arcname=f"visual_palette_v242/{p.name}")
        # HF94: include the newest short-path PAC batch outputs.
        pac_dirs = [p for p in REPORT_DIR.iterdir() if p.is_dir() and (p.name.startswith("P23_1_") or p.name.startswith("P24_") or p.name.startswith("P241_") or p.name.startswith("P25_"))]
        pac_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for d in pac_dirs[:4]:
            for p in d.rglob("*"):
                if p.is_file():
                    zf.write(p, arcname=f"pac_batches/{d.name}/{p.relative_to(d)}")
        # HF117: include newest full-app responsiveness sessions.
        if PERF_DIR.exists():
            sessions=[p for p in PERF_DIR.glob("FULL_APP_SESSION_*") if p.is_dir()]
            sessions.sort(key=lambda p:p.stat().st_mtime, reverse=True)
            for d in sessions[:3]:
                for p in d.rglob("*"):
                    if p.is_file():
                        zf.write(p, arcname=f"full_app_performance/{d.name}/{p.relative_to(d)}")
        if logs_dir:
            for p in _latest_files(logs_dir, "crash_*.log", 20):
                zf.write(p, arcname=f"crash_logs/{p.name}")
    return bundle


def run_script(script: str, extra_args: list[str] | None = None) -> int:
    cmd = [sys.executable, str(ROOT / "tools" / script)] + (extra_args or [])
    return subprocess.call(cmd, cwd=str(ROOT))


def ask_path(prompt: str) -> str:
    """Accept normal paste plus Windows Terminal/PowerShell drag-and-drop quoting."""
    raw = input(prompt).strip()
    if raw.startswith("&"):
        raw = raw[1:].strip()
    # Dragging a path into different Windows terminals may wrap it in single
    # or double quotes. Strip one matching outer pair only.
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"\"", "'"}:
        raw = raw[1:-1].strip()
    return raw


def menu() -> int:
    while True:
        print("\n" + "=" * 64)
        print("Chromatic Analysis · 诊断与性能测试中心")
        print("=" * 64)
        print("    [1] 系统 / 版本 / 依赖诊断（很快）")
        print("    [2] QTX 解析与颜色计算性能基线（P3-5/P3-6 工具）")
        print("    [3] 色卡排序专项性能测试（Munsell / 光谱 / 混合）")
        print("    [4] 色卡排序科学 / 逻辑验证（含视觉色貌编排 V1）")
        print("    [5] 打包诊断资料（崩溃日志 + 性能报告 + 排序验证 + 系统信息）")
        print("    [6] 打开 diagnostics_reports 文件夹")
        print("    [7] 视觉色卡编排 V2 离线验证（HF77基线）")
        print("    [8] 视觉色卡编排 V2.1 离线验证（边界/明度层修正）")
        print("    [9] 视觉色卡编排 V2.2 离线验证（HF79基线；支持QTX或文件夹）")
        print("    [10] 视觉色卡编排 V2.3 离线验证（Neutral Field + 自适应C*步进；支持QTX或文件夹）")
        print("    [11] 视觉色卡编排 V2.4 离线验证（高L*白色包络 + Neutral Tint分道；支持QTX或文件夹）")
        print("    [12] 视觉色卡编排 V2.4.1 离线验证（自适应 Near-White 桥接吸收；支持QTX或文件夹）")
        print("    [13] 视觉色卡编排 V2.4.2 离线验证（受控 Near-White 二跳连续桥接；支持QTX或文件夹）")
        print("    [14] 色貌连续排序专项验证（历史光谱+感知 vs HF88；卡其/米色断点定位）")
        print("    [15] Munsell 生产路径 / 持久缓存专项测试（定位为什么仍然很慢）")
        print("    [16] 色貌连续排序 V2.1 结构性离线验证（支持QTX或文件夹）")
        print("    [17] 色貌连续排序 V2.2 自适应连续带验证（减少过度分组；支持QTX或文件夹）")
        print("    [18] 色貌连续排序 V2.3/P23.1 验证（HF94修复阈值报告/长路径/批量状态）")
        print("    [19] 色貌连续排序 PAC V24 A/B（Neutral Confidence + Hue Confidence；支持QTX或文件夹）")
        print("    [20] 色貌连续排序 PAC V24.1 Science A/B（色相拓扑 + 中性色 + 暖冷保护；旧基线）")
        print("    [21] 色貌连续排序 PAC V25 HVC Surface A/B（中性轴 + Near-White边界岛 + 家族端点优化；与软件主按钮同核心）")
        print("    [22] 全程序流畅度 / 交互性能回归测试（最终发布前人工走一遍）")
        print("    [23] 一键自动核心性能验证（HF122 Performance Final；只选一次QTX）")
        print("    [0] 退出")
        choice = input("\n请选择: ").strip()

        try:
            if choice == "1":
                txt, js = system_report()
                print(f"\n已生成:\n{txt}\n{js}")
            elif choice == "2":
                path = ask_path("把要测试的 QTX 文件拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到文件: {path}")
                    continue
                run_script("performance_baseline.py", [path])
            elif choice == "3":
                path = ask_path("把色卡编排中实际使用的 QTX 文件拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到文件: {path}")
                    continue
                run_script("palette_sort_benchmark.py", [path])
            elif choice == "4":
                path = ask_path("把色卡编排中实际使用的 QTX 文件拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到文件: {path}")
                    continue
                run_script("palette_sort_correctness_audit.py", [path])
            elif choice == "5":
                bundle = make_bundle()
                print(f"\n诊断包已生成:\n{bundle}\n以后程序异常时，把这个 ZIP 发给我即可。")
            elif choice == "6":
                REPORT_DIR.mkdir(exist_ok=True)
                if os.name == "nt":
                    os.startfile(str(REPORT_DIR))  # type: ignore[attr-defined]
                else:
                    print(REPORT_DIR)
            elif choice == "7":
                path = ask_path("把要验证的 QTX 文件拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到文件: {path}")
                    continue
                run_script("visual_palette_v2_audit.py", [path])
            elif choice == "8":
                path = ask_path("把要验证的 QTX 文件拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到文件: {path}")
                    continue
                run_script("visual_palette_v21_audit.py", [path])
            elif choice == "9":
                path = ask_path("把要验证的 QTX 文件，或包含多个 QTX 的文件夹拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到路径: {path}")
                    continue
                run_script("visual_palette_v22_audit.py", [path])
            elif choice == "10":
                path = ask_path("把要验证的 QTX 文件，或包含多个 QTX 的文件夹拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到路径: {path}")
                    continue
                run_script("visual_palette_v23_audit.py", [path])
            elif choice == "11":
                path = ask_path("把要验证的 QTX 文件，或包含多个 QTX 的文件夹拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到路径: {path}")
                    continue
                run_script("visual_palette_v24_audit.py", [path])
            elif choice == "12":
                path = ask_path("把要验证的 QTX 文件，或包含多个 QTX 的文件夹拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到路径: {path}")
                    continue
                run_script("visual_palette_v241_audit.py", [path])
            elif choice == "13":
                path = ask_path("把要验证的 QTX 文件，或包含多个 QTX 的文件夹拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到路径: {path}")
                    continue
                run_script("visual_palette_v242_audit.py", [path])
            elif choice == "14":
                path = ask_path("把出现卡其/米色/棕黄色断开的原始 QTX 文件拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到文件: {path}")
                    continue
                run_script("palette_continuity_audit.py", [path])
            elif choice == "15":
                path = ask_path("把 Munsell 排序很慢的原始 QTX 文件拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到文件: {path}")
                    continue
                run_script("munsell_production_benchmark.py", [path])
            elif choice == "16":
                path = ask_path("把要验证的 QTX 文件，或包含多个 QTX 的文件夹拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到路径: {path}")
                    continue
                run_script("palette_continuity_v21_audit.py", [path])
            elif choice == "17":
                path = ask_path("把要验证的 QTX 文件，或包含多个 QTX 的文件夹拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到路径: {path}")
                    continue
                run_script("palette_continuity_v22_audit.py", [path])
            elif choice == "18":
                path = ask_path("把要验证的 QTX 文件，或包含多个 QTX 的文件夹拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到路径: {path}")
                    continue
                run_script("palette_continuity_v23_audit.py", [path])
            elif choice == "19":
                path = ask_path("把要验证的 QTX 文件，或包含多个 QTX 的文件夹拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到路径: {path}")
                    continue
                run_script("palette_continuity_v24_audit.py", [path])
            elif choice == "20":
                path = ask_path("把要验证的 QTX 文件，或包含多个 QTX 的文件夹拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到路径: {path}")
                    continue
                run_script("palette_continuity_v241_science_audit.py", [path])
            elif choice == "21":
                path = ask_path("把要验证的 QTX 文件，或包含多个 QTX 的文件夹拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到路径: {path}")
                    continue
                run_script("palette_continuity_v25_audit.py", [path])
            elif choice == "22":
                run_script("full_app_performance_test.py")
            elif choice == "23":
                path = ask_path("把一个代表性的大 QTX（建议 600~3500 色）拖到窗口，然后按 Enter:\n> ")
                if not Path(path).exists():
                    print(f"找不到文件: {path}")
                    continue
                run_script("auto_performance_suite.py", [path])
            elif choice == "0":
                return 0
            else:
                print("请输入 0-23。")
        except KeyboardInterrupt:
            print("\n已取消。")
        except Exception:
            print("\n诊断工具自身出现异常：")
            traceback.print_exc()


if __name__ == "__main__":
    raise SystemExit(menu())
