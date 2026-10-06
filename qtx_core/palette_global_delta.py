"""Global open-path optimisation for palette Running ΔE (Hotfix110).

The previous implementation was a single-start greedy nearest-neighbour walk.
That is fast, but it is vulnerable to two well-known failures:

* local optimum: a smooth beginning can leave one isolated sample for the end;
* start bias: an arbitrary selected sample is forced to be an endpoint.

This module keeps the tool deterministic but upgrades it to an approximate
open-TSP path:

1. build one exact pairwise distance matrix for the active colour-difference
   formula (CIE94/CMC are symmetrised for an undirected continuity path),
2. generate several nearest-neighbour candidate paths from deterministic,
   geometry-derived starts,
3. choose the best candidate by the lexicographic objective
      max adjacent ΔE -> P95 adjacent ΔE -> total adjacent ΔE,
4. improve the best candidates with bottleneck-aware 2-opt moves.

The goal is not to claim a mathematically exact TSP optimum.  It is a much more
robust, globally-aware palette path while remaining practical for interactive
use and deterministic for the same input/version/formula.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Callable, Iterable, Sequence

import numpy as np


@dataclass(frozen=True)
class PathMetrics:
    maximum: float
    p95: float
    total: float
    mean: float

    def score(self) -> tuple[float, float, float]:
        # Tiny rounding prevents meaningless floating noise changing a
        # deterministic tie-break between otherwise identical paths.
        return (round(float(self.maximum), 10), round(float(self.p95), 10), round(float(self.total), 10))


@dataclass(frozen=True)
class GlobalPathResult:
    order: tuple[int, ...]
    initial_metrics: PathMetrics
    final_metrics: PathMetrics
    candidate_starts: tuple[int, ...]
    two_opt_moves: int


def _p95_nearest(values: np.ndarray) -> float:
    if values.size == 0:
        return 0.0
    # A nearest-rank P95 is easier to explain in diagnostics than interpolated
    # percentiles and is stable for small palettes.
    k = max(0, min(values.size - 1, int(np.ceil(0.95 * values.size)) - 1))
    return float(np.partition(values, k)[k])


def path_metrics(path: Sequence[int], matrix: np.ndarray) -> PathMetrics:
    if len(path) < 2:
        return PathMetrics(0.0, 0.0, 0.0, 0.0)
    p = np.asarray(path, dtype=np.int32)
    edges = np.asarray(matrix[p[:-1], p[1:]], dtype=np.float64)
    total = float(edges.sum())
    return PathMetrics(float(edges.max(initial=0.0)), _p95_nearest(edges), total, total / max(1, edges.size))


def build_symmetric_distance_matrix(
    labs: Sequence[Sequence[float]],
    distance: Callable[[Sequence[float], Sequence[float]], float],
    *,
    directional: bool = False,
) -> np.ndarray:
    """Return an NxN float32 symmetric distance matrix.

    For asymmetric formulas such as textile CIE94/CMC, ``directional=True``
    asks us to average d(A,B) and d(B,A).  A palette continuity path has no
    standard/sample semantics for each adjacent pair, so the symmetric mean is
    the appropriate undirected similarity used by the open-path optimiser.
    """
    n = len(labs)
    matrix = np.zeros((n, n), dtype=np.float32)
    for i in range(n - 1):
        a = labs[i]
        for j in range(i + 1, n):
            b = labs[j]
            d1 = float(distance(a, b))
            if directional:
                d2 = float(distance(b, a))
                value = 0.5 * (d1 + d2)
            else:
                value = d1
            if not np.isfinite(value):
                value = 1.0e9
            matrix[i, j] = matrix[j, i] = max(0.0, value)
    return matrix


def _candidate_starts(labs: np.ndarray, max_starts: int) -> list[int]:
    """Deterministic starts spanning the geometry of the measured set."""
    n = len(labs)
    if n <= 1:
        return [0] if n else []
    C = np.hypot(labs[:, 1], labs[:, 2])
    columns = [labs[:, 0], labs[:, 1], labs[:, 2], C]
    candidates: list[int] = []
    for col in columns:
        candidates.extend([int(np.argmin(col)), int(np.argmax(col))])

    centroid = labs.mean(axis=0)
    sq = np.sum((labs - centroid) ** 2, axis=1)
    far = int(np.argmax(sq)); candidates.append(far)
    sq2 = np.sum((labs - labs[far]) ** 2, axis=1)
    candidates.append(int(np.argmax(sq2)))

    # Stable first/last are useful for degenerate or duplicate-heavy datasets.
    candidates.extend([0, n - 1])
    out: list[int] = []
    seen: set[int] = set()
    for idx in candidates:
        if idx not in seen:
            seen.add(idx); out.append(idx)
        if len(out) >= max_starts:
            break
    return out


def _nearest_neighbour(start: int, matrix: np.ndarray) -> list[int]:
    n = int(matrix.shape[0])
    remaining = np.ones(n, dtype=bool)
    remaining[start] = False
    path = [int(start)]
    current = int(start)
    stable = np.arange(n, dtype=np.int32)
    for _ in range(n - 1):
        row = matrix[current]
        available = np.flatnonzero(remaining)
        # np.argmin is stable on the available array, which is ascending index;
        # therefore equal-distance ties are deterministic by original order.
        nxt = int(available[int(np.argmin(row[available]))])
        path.append(nxt)
        remaining[nxt] = False
        current = nxt
    return path


def _top_edge_indices(edges: np.ndarray, count: int) -> list[int]:
    if edges.size == 0:
        return []
    count = max(1, min(int(count), int(edges.size)))
    if count == edges.size:
        idx = np.arange(edges.size)
    else:
        idx = np.argpartition(edges, edges.size - count)[-count:]
    return [int(i) for i in idx[np.argsort(edges[idx])[::-1]]]


def _two_opt_global(path: list[int], matrix: np.ndarray, max_passes: int = 10) -> tuple[list[int], int]:
    """Bottleneck-aware deterministic 2-opt for an open path.

    Candidate moves are focused around the worst current edges.  This directly
    attacks the classic greedy "one huge final jump" failure while keeping the
    computation practical for hundreds to low-thousands of samples.
    """
    n = len(path)
    if n < 4:
        return path, 0
    path = list(path)
    moves = 0

    for _pass in range(max(1, int(max_passes))):
        p = np.asarray(path, dtype=np.int32)
        edges = np.asarray(matrix[p[:-1], p[1:]], dtype=np.float64)
        current_metrics = path_metrics(path, matrix)
        current_score = current_metrics.score()
        total = current_metrics.total

        # Focus on the worst ~8% of edges, bounded for predictable runtime.
        focus_count = min(48, max(12, int(np.ceil(edges.size * 0.08))))
        focus = _top_edge_indices(edges, focus_count)

        # Top three are sufficient for max-other because one 2-opt move removes
        # at most two boundary edges.
        top3 = _top_edge_indices(edges, min(3, edges.size))

        def max_other(e1: int | None, e2: int | None) -> float:
            for ei in top3:
                if ei != e1 and ei != e2:
                    return float(edges[ei])
            # Tiny paths / all top edges removed: fall back to a safe scan.
            mask = np.ones(edges.size, dtype=bool)
            if e1 is not None: mask[e1] = False
            if e2 is not None: mask[e2] = False
            vals = edges[mask]
            return float(vals.max(initial=0.0))

        # Generate unique segment reversals that replace at least one focused
        # bad edge.  Keep a shortlist before doing exact P95 evaluation.
        candidate_set: set[tuple[int, int]] = set()
        for e in focus:
            # e is boundary between positions e and e+1.
            i = e + 1
            for j in range(i + 1, n):
                if not (i == 0 and j == n - 1):
                    candidate_set.add((i, j))
            j = e
            for i2 in range(0, j):
                if not (i2 == 0 and j == n - 1):
                    candidate_set.add((i2, j))

        shortlist: list[tuple[tuple[float, float], int, int, float, float, int | None, int | None]] = []
        for i, j in candidate_set:
            if i >= j:
                continue
            e1 = i - 1 if i > 0 else None
            e2 = j if j < n - 1 else None
            old_sum = (float(edges[e1]) if e1 is not None else 0.0) + (float(edges[e2]) if e2 is not None else 0.0)
            new_vals: list[float] = []
            if e1 is not None:
                new_vals.append(float(matrix[path[i - 1], path[j]]))
            if e2 is not None:
                new_vals.append(float(matrix[path[i], path[j + 1]]))
            new_max = max([max_other(e1, e2), *new_vals]) if new_vals else max_other(e1, e2)
            new_total = total - old_sum + sum(new_vals)
            # Only moves that cannot worsen the primary bottleneck objective are
            # worth exact P95 evaluation.
            if new_max > current_metrics.maximum + 1e-10:
                continue
            key = (round(new_max, 10), round(new_total, 10))
            shortlist.append((key, i, j, new_max, new_total, e1, e2))

        if not shortlist:
            break
        shortlist.sort(key=lambda x: (x[0], x[1], x[2]))
        shortlist = shortlist[:64]

        best_score = current_score
        best_move: tuple[int, int] | None = None
        # Exact score on the shortlist: only two boundary values change; the
        # internal reversed segment retains the same undirected edge lengths.
        for _key, i, j, _mx, _tot, e1, e2 in shortlist:
            test_edges = edges.copy()
            if e1 is not None:
                test_edges[e1] = float(matrix[path[i - 1], path[j]])
            if e2 is not None:
                test_edges[e2] = float(matrix[path[i], path[j + 1]])
            score = (
                round(float(test_edges.max(initial=0.0)), 10),
                round(_p95_nearest(test_edges), 10),
                round(float(test_edges.sum()), 10),
            )
            if score < best_score:
                best_score = score
                best_move = (i, j)

        if best_move is None:
            break
        i, j = best_move
        path[i : j + 1] = reversed(path[i : j + 1])
        moves += 1

    return path, moves


def optimise_global_open_path(
    labs: Sequence[Sequence[float]],
    distance: Callable[[Sequence[float], Sequence[float]], float],
    *,
    directional_formula: bool = False,
    max_starts: int = 12,
    optimise_candidates: int = 3,
    two_opt_passes: int = 10,
) -> GlobalPathResult:
    """Return a deterministic globally-improved open colour-continuity path."""
    n = len(labs)
    if n == 0:
        zero = PathMetrics(0.0, 0.0, 0.0, 0.0)
        return GlobalPathResult((), zero, zero, (), 0)
    if n == 1:
        zero = PathMetrics(0.0, 0.0, 0.0, 0.0)
        return GlobalPathResult((0,), zero, zero, (0,), 0)

    lab_arr = np.asarray(labs, dtype=float)
    matrix = build_symmetric_distance_matrix(labs, distance, directional=directional_formula)
    starts = _candidate_starts(lab_arr, max_starts=max_starts)

    candidates: list[tuple[tuple[float, float, float], list[int], PathMetrics]] = []
    for s in starts:
        path = _nearest_neighbour(s, matrix)
        metrics = path_metrics(path, matrix)
        # A path and its reverse are equivalent under our symmetric continuity
        # distance.  Canonical orientation makes output deterministic.
        rev = list(reversed(path))
        if tuple(rev) < tuple(path):
            path = rev
        candidates.append((metrics.score(), path, metrics))
    candidates.sort(key=lambda x: (x[0], tuple(x[1])))

    best_final: tuple[tuple[float, float, float], list[int], PathMetrics, PathMetrics, int] | None = None
    for _score, initial_path, initial_metrics in candidates[: max(1, int(optimise_candidates))]:
        improved, moves = _two_opt_global(initial_path, matrix, max_passes=two_opt_passes)
        final_metrics = path_metrics(improved, matrix)
        rev = list(reversed(improved))
        if tuple(rev) < tuple(improved):
            improved = rev
        record = (final_metrics.score(), improved, initial_metrics, final_metrics, moves)
        if best_final is None or (record[0], tuple(record[1])) < (best_final[0], tuple(best_final[1])):
            best_final = record

    assert best_final is not None
    _, path, initial_metrics, final_metrics, moves = best_final
    return GlobalPathResult(tuple(path), initial_metrics, final_metrics, tuple(starts), int(moves))
