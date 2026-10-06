from __future__ import annotations

import csv
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class BatchArtifacts:
    case_id: str
    preview: Path
    audit_txt: Path
    audit_json: Path


@dataclass
class BatchCaseResult:
    case_id: str
    qtx_name: str
    qtx_path: str
    status: str = "PENDING"
    failed_stage: str = ""
    error: str = ""
    preview: str = ""
    audit_txt: str = ""
    audit_json: str = ""
    preview_exists: bool = False
    audit_txt_exists: bool = False
    audit_json_exists: bool = False


def choose_batch_dir(preferred_root: Path, tag: str, stamp: str) -> Path:
    """Choose a short, writable batch directory.

    The normal location remains the project's diagnostics_reports folder.  On
    Windows, if the representative output path is already close to MAX_PATH,
    fall back to LOCALAPPDATA/TEMP so Pillow and other libraries do not depend
    on machine-specific long-path policy.
    """
    preferred_root = Path(preferred_root)
    preferred = preferred_root / f"{tag}_{stamp}"
    representative = preferred / "999_PREVIEW.png"

    use_fallback = os.name == "nt" and len(str(representative.resolve())) >= 235
    if use_fallback:
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or os.environ.get("TMP")
        if base:
            preferred = Path(base) / "ChromaticAnalysis" / "diagnostics" / f"{tag}_{stamp}"

    preferred.mkdir(parents=True, exist_ok=True)
    return preferred


def build_artifacts(batch_dir: Path, index: int, total: int) -> BatchArtifacts:
    """Short artifact names; original QTX names live in MANIFEST.csv."""
    width = max(3, len(str(max(1, int(total)))))
    case_id = f"{int(index):0{width}d}"
    batch_dir = Path(batch_dir)
    return BatchArtifacts(
        case_id=case_id,
        preview=batch_dir / f"{case_id}_PREVIEW.png",
        audit_txt=batch_dir / f"{case_id}_AUDIT.txt",
        audit_json=batch_dir / f"{case_id}_AUDIT.json",
    )


def finalize_case(result: BatchCaseResult, artifacts: BatchArtifacts) -> BatchCaseResult:
    result.preview = artifacts.preview.name
    result.audit_txt = artifacts.audit_txt.name
    result.audit_json = artifacts.audit_json.name
    result.preview_exists = artifacts.preview.is_file()
    result.audit_txt_exists = artifacts.audit_txt.is_file()
    result.audit_json_exists = artifacts.audit_json.is_file()

    required_ok = result.preview_exists and result.audit_txt_exists and result.audit_json_exists
    if result.status == "PASS" and not required_ok:
        missing = []
        if not result.preview_exists:
            missing.append("PREVIEW")
        if not result.audit_txt_exists:
            missing.append("AUDIT.txt")
        if not result.audit_json_exists:
            missing.append("AUDIT.json")
        result.status = "FAIL"
        result.failed_stage = result.failed_stage or "FINALIZE"
        result.error = "Missing artifact(s): " + ", ".join(missing)
    return result


def write_manifest(batch_dir: Path, rows: Iterable[BatchCaseResult]) -> Path:
    path = Path(batch_dir) / "MANIFEST.csv"
    fields = list(BatchCaseResult.__dataclass_fields__.keys())
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow(asdict(row))
    return path


def write_summary(batch_dir: Path, rows: Iterable[BatchCaseResult], extra: dict[str, Any] | None = None) -> Path:
    rows = list(rows)
    total = len(rows)
    passed = sum(r.status == "PASS" for r in rows)
    preview_ok = sum(r.preview_exists for r in rows)
    txt_ok = sum(r.audit_txt_exists for r in rows)
    json_ok = sum(r.audit_json_exists for r in rows)
    values: list[tuple[str, Any]] = [
        ("total", total),
        ("passed", passed),
        ("failed", total - passed),
        ("preview_ok", preview_ok),
        ("audit_txt_ok", txt_ok),
        ("audit_json_ok", json_ok),
    ]
    if extra:
        values.extend(extra.items())
    path = Path(batch_dir) / "SUMMARY.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        w.writerows(values)
    return path


def footer(rows: Iterable[BatchCaseResult], batch_dir: Path) -> str:
    rows = list(rows)
    total = len(rows)
    passed = sum(r.status == "PASS" for r in rows)
    preview_ok = sum(r.preview_exists for r in rows)
    txt_ok = sum(r.audit_txt_exists for r in rows)
    json_ok = sum(r.audit_json_exists for r in rows)
    lines = [
        "",
        "批量验证完成。",
        f"结果目录: {batch_dir}",
        f"Total: {total}",
        f"PASS: {passed}",
        f"FAIL: {total - passed}",
        f"PREVIEW OK: {preview_ok}/{total}",
        f"AUDIT TXT OK: {txt_ok}/{total}",
        f"AUDIT JSON OK: {json_ok}/{total}",
    ]
    if total and passed == total and preview_ok == total and txt_ok == total and json_ok == total:
        lines.append("所有 QTX 均已生成 PREVIEW + AUDIT。")
    else:
        lines.append("存在失败项；请查看 MANIFEST.csv 的 failed_stage / error。")
    return "\n".join(lines)
