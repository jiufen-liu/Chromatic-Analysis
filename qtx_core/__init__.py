"""Public API for the QTX colour calculation core."""

from .analysis import (
    analyse_pair,
    average_reflectance,
    average_spectral_standard,
    spectral_rms_distance, spectral_feature_summary, spectral_order,
)
from .colorimetry import (
    SUPPORTED_ILLUMINANTS,
    cie_tint_d65_10,
    cie_whiteness_d65_10,
    cam16ucs_from_xyz,
    munsell_hue_order_from_xyz,
    delta_e,
    delta_e_many,
    delta_h_cielab_signed,
    normalize_illuminant,
    display_illuminant,
    illuminant_note,
    reflectance_to_xyz_lab,
    reflectances_to_xyz_lab,
    xyz_to_lab,
)
from .metamerism import metamerism_index_multiplicative
from .models import ColorResult, PairAnalysis, Sample, Workbench
from .cpx_io import CpxProject, parse_cpx_file, export_cpx_file
from .shade_sort import Shade555Result, shade_555, symmetric_ranges
from .qtx_parser import parse_qtx_file, parse_qtx_text, export_qtx_file
from .palette_arrangement import HUE_FAMILY_NAMES, visual_palette_order_v1, visual_palette_layout_v2, visual_palette_layout_v21, visual_palette_layout_v22, visual_palette_layout_v23, visual_palette_layout_v24, visual_palette_layout_v241, visual_palette_layout_v242
from .palette_continuity_v21 import appearance_continuity_layout_v21

__all__ = [
    "Workbench",
    "CpxProject", "parse_cpx_file", "export_cpx_file",
    "Shade555Result", "shade_555", "symmetric_ranges",
    "ColorResult",
    "PairAnalysis",
    "Sample",
    "SUPPORTED_ILLUMINANTS",
    "analyse_pair",
    "average_reflectance",
    "average_spectral_standard",
    "spectral_rms_distance", "spectral_feature_summary", "spectral_order",
    "cie_tint_d65_10",
    "cie_whiteness_d65_10",
    "cam16ucs_from_xyz",
    "munsell_hue_order_from_xyz",
    "delta_e",
    "delta_e_many",
    "delta_h_cielab_signed",
    "metamerism_index_multiplicative",
    "normalize_illuminant",
    "display_illuminant",
    "illuminant_note",
    "parse_qtx_file",
    "export_qtx_file",
    "parse_qtx_text",
    "reflectance_to_xyz_lab",
    "reflectances_to_xyz_lab",
    "xyz_to_lab",
    "HUE_FAMILY_NAMES",
    "visual_palette_order_v1",
    "visual_palette_layout_v2",
    "visual_palette_layout_v21",
    "visual_palette_layout_v22",
    "visual_palette_layout_v23",
    "visual_palette_layout_v24",
    "visual_palette_layout_v241",
    "visual_palette_layout_v242",
    "appearance_continuity_layout_v21",
    "appearance_continuity_layout_v24",
    "appearance_continuity_layout_v241_science",
    "appearance_continuity_layout_v25",
]


from .palette_continuity_v22 import appearance_continuity_layout_v22

from .palette_continuity_v23 import appearance_continuity_layout_v23

from .palette_continuity_v24 import appearance_continuity_layout_v24

from .palette_continuity_v241_science import appearance_continuity_layout_v241_science

from .palette_continuity_v25 import appearance_continuity_layout_v25

# Hotfix97: simple broad colour-family sorting.
from .palette_family_sort import (
    FAMILY_ORDER,
    FAMILY_LABELS_ZH,
    FamilySortRecord,
    classify_color_family,
    sort_lab_rows,
    family_counts,
)
