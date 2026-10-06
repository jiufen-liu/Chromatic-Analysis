from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence


Triplet = tuple[float, float, float]


@dataclass(frozen=True)
class Sample:
    sample_id: str
    display_name: str
    kind: str
    xyz_d65_10: Triplet
    lab_d65_10: Triplet
    reflectance: tuple[float, ...]
    wavelengths: tuple[int, ...] = tuple(range(360, 701, 10))
    source_file: str = ""
    viewing: str = ""
    raw: Mapping[str, str] = field(default_factory=dict, repr=False)

    def has_spectrum(self) -> bool:
        return len(self.reflectance) == len(self.wavelengths) and bool(self.reflectance)


@dataclass(frozen=True)
class ColorResult:
    illuminant: str
    xyz: Triplet
    lab: Triplet
    observer_degrees: int = 10
    coordinate_source: str = "unspecified"
    illuminant_note: str = ""


@dataclass(frozen=True)
class PairAnalysis:
    illuminant: str
    standard: ColorResult
    batch: ColorResult
    delta_e76: float
    delta_e94: float
    delta_e00: float
    cmc21: float
    cmc11: float
    metamerism_index: float | None
    observer_degrees: int = 10
    reference_illuminant: str = "D65"


@dataclass
class Workbench:
    """一个比色工作台：一组样本 + 一个当前标准"""
    workbench_id: str
    name: str
    sample_keys: list[str] = field(default_factory=list)   # 色样 key 列表
    standard_key: str | None = None                         # 当前标准的 sample_key
    created_at: float = 0.0
    is_collapsed: bool = False                              # 是否被"收起"
