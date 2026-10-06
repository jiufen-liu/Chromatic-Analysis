from __future__ import annotations

import math
from typing import Sequence, TypeVar

T = TypeVar("T")


def active_geometry(slots: Sequence[T | None], source_cols: int) -> tuple[int, int]:
    """Return the non-destructive CPX presentation rectangle.

    Only *fully empty trailing columns on the right* and fully empty tail rows
    are folded. Leading/internal blanks are retained because they are part of
    the palette composition. The source CPX TileCount is never changed.
    """
    cols=max(1,int(source_cols or 1))
    occupied=[i for i,v in enumerate(slots) if v is not None]
    if not occupied:
        return 1,1
    active_rows=max(i//cols for i in occupied)+1
    active_cols=max(i%cols for i in occupied)+1
    return max(1,active_cols),max(1,active_rows)


def project_active_slots(slots: Sequence[T | None], source_cols: int, active_cols: int, active_rows: int) -> list[T | None]:
    """Project a full source CPX canvas into its presentation rectangle."""
    source_cols=max(1,int(source_cols or 1)); active_cols=max(1,min(source_cols,int(active_cols or 1))); active_rows=max(1,int(active_rows or 1))
    out=[]
    for r in range(active_rows):
        base=r*source_cols
        for c in range(active_cols):
            i=base+c
            out.append(slots[i] if i<len(slots) else None)
    return out


def expand_to_source_grid(visible_slots: Sequence[T | None], display_cols: int, source_cols: int, source_rows: int) -> tuple[list[T | None], int]:
    """Expand a CPX presentation grid back into the original fixed source grid.

    The display may fold unused right columns / bottom rows, but export CPX must
    reconstruct them. If edits need more rows than the source had, rows are
    extended without changing the original column count.
    """
    display_cols=max(1,int(display_cols or 1)); source_cols=max(display_cols,int(source_cols or display_cols or 1)); source_rows=max(1,int(source_rows or 1))
    visible_rows=max(1,int(math.ceil(len(visible_slots)/display_cols))) if visible_slots else 1
    rows=max(source_rows,visible_rows)
    out=[None]*(source_cols*rows)
    for i,value in enumerate(visible_slots):
        r,c=divmod(i,display_cols)
        if r>=rows: break
        if c<source_cols:
            out[r*source_cols+c]=value
    return out,rows


def crop_trailing_empty_edges(slots: Sequence[T | None], cols: int) -> tuple[list[T | None], int, int]:
    """Crop only empty right-edge columns / bottom rows from a presentation grid."""
    cols=max(1,int(cols or 1))
    rows=max(1,int(math.ceil(len(slots)/cols))) if slots else 1
    padded=list(slots)+[None]*max(0,cols*rows-len(slots))
    active_cols,active_rows=active_geometry(padded,cols)
    return project_active_slots(padded,cols,active_cols,active_rows),active_cols,active_rows
