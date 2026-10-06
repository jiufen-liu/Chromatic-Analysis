"""Lossless application work file, independent of QTX/CPX/Excel exports."""
from __future__ import annotations

import json
from pathlib import Path
from .atomic_json import atomic_write_json

FORMAT = "chromatic-analysis-workfile"
VERSION = 1
SUFFIX = ".chromatic"


def read_workfile(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("文件不是 Chromatic Analysis 工作文件")
    if data.get("version") != VERSION:
        raise ValueError("该工作文件版本暂不支持，请使用对应版本打开")
    if not isinstance(data.get("samples"), list) or not isinstance(data.get("slots"), list):
        raise ValueError("工作文件缺少色样或卡位数据")
    return data


def write_workfile(path: str | Path, payload: dict) -> None:
    destination = Path(path)
    if destination.suffix.lower() != SUFFIX:
        destination = destination.with_suffix(SUFFIX)
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = dict(payload, format=FORMAT, version=VERSION)
    atomic_write_json(destination, content, indent=None)
