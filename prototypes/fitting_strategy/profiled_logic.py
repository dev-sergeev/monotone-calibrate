"""Pure throwaway logic for the fitting-strategy decision.

The module deliberately implements only representative hard parameterizations:
poly1, positive/negative-rate exponential and logistic.  It is not registry_v1
and it does not perform validation or choose the product recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from typing import Any, Literal

import numpy as np
from scipy.optimize import differential_evolution, lsq_linear, minimize, minimize_scalar
from scipy.special import expit


FamilyId = Literal["poly1_v1", "exp_pos_v1", "exp_neg_v1", "logistic_v1"]
StrategyId = Literal["profile_cells", "joint_cells", "global_profiled_reference"]


@dataclass(frozen=True)
class Scale:
    x_min: float
    x_span: float
    y_offset: float
    y_scale: float

    def x_to_t(self, x: np.ndarray) -> np.ndarray:
        return (np.asarray(x, dtype=float) - self.x_min) / self.x_span

    def y_to_scaled(self, y: np.ndarray) -> np.ndarray:
        return (np.asarray(y, dtype=float) - self.y_offset) / self.y_scale


@dataclass(frozen=True)
class XScaleDiagnostics:
    unique_x: int
    unique_t: int
    max_ulp_over_span: float
    normalized_collision: bool
    warning_codes: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        return {
            "unique_x": self.unique_x,
            "unique_t": self.unique_t,
            "max_ulp_over_span": _finite(self.max_ulp_over_span),
            "normalized_collision": self.normalized_collision,
            "warning_codes": list(self.warning_codes),
        }


@dataclass(frozen=True)
class Problem:
    name: str
    x: np.ndarray
    y: np.ndarray
    families: tuple[FamilyId, FamilyId]
    direction: Literal[-1, 1]
    truth_c: float | None
    expected_state: str


@dataclass(frozen=True)
class TieCell:
    index: int
    lo_x: float
    hi_x: float
    lo_t: float
    hi_t: float
    left_n: int
    right_n: int
    left_unique: int
    right_unique: int


@dataclass(frozen=True)
class Certificate:
    valid: bool
    finite: bool
    balance_ok: bool
    ties_atomic: bool
    support_ok: bool
    domain_ok: bool
    shape_ok: bool
    evaluator_match: bool
    join_error_scaled: float | None
    signed_derivative_margin: float | None
    design_rank: int | None
    design_condition: float | None
    reason_codes: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "finite": self.finite,
            "balance_ok": self.balance_ok,
            "ties_atomic": self.ties_atomic,
            "support_ok": self.support_ok,
            "domain_ok": self.domain_ok,
            "shape_ok": self.shape_ok,
            "evaluator_match": self.evaluator_match,
            "join_error_scaled": _finite(self.join_error_scaled),
            "signed_derivative_margin": _finite(self.signed_derivative_margin),
            "design_rank": self.design_rank,
            "design_condition": _finite(self.design_condition),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class Candidate:
    strategy: StrategyId
    families: tuple[FamilyId, FamilyId]
    direction: int
    c_t: float
    linear: tuple[float, float, float]
    shape: tuple[float, ...]
    sse_scaled: float
    prediction_scaled: np.ndarray
    certificate: Certificate
    status: str
    warnings: tuple[str, ...]

    @property
    def canonical_replacement(self) -> dict[str, Any] | None:
        if self.status != "COLLAPSED_SEGMENTS":
            return None
        start = float(self.prediction_scaled[0])
        end = float(self.prediction_scaled[-1])
        if abs(end - start) <= 1e-8:
            return {"family": "constant_v1", "a_scaled": 0.5 * (start + end)}
        return {"family": "poly1_v1", "a_scaled": start, "b_scaled": end - start}

    @property
    def model_hash(self) -> str:
        # Runtime, traversal order, diagnostics and formatted strings are not
        # properties of the mathematical model and therefore are excluded.
        replacement = self.canonical_replacement
        if replacement is not None:
            payload = {
                "registry_version": "profiled_subset_v2",
                "canonical_replacement": {
                    key: round(value, 9) if isinstance(value, float) else value
                    for key, value in replacement.items()
                },
                "direction": "flat" if replacement["family"] == "constant_v1" else self.direction,
            }
        else:
            payload = {
                "registry_version": "profiled_subset_v2",
                "canonical_ast": [f"centered:{family}" for family in self.families],
                "families": self.families,
                "direction": self.direction,
                "c_t": round(self.c_t, 9),
                "linear": [round(value, 9) for value in self.linear],
                "shape": [round(value, 9) for value in self.shape],
            }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode()).hexdigest()[:16]

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "families": list(self.families),
            "direction": self.direction,
            "c_t": self.c_t,
            "linear_scaled": list(self.linear),
            "shape": list(self.shape),
            "sse_scaled": self.sse_scaled,
            "prediction_scaled": self.prediction_scaled.tolist(),
            "certificate": self.certificate.public(),
            "warnings": list(self.warnings),
            "canonical_replacement": self.canonical_replacement,
            "model_hash": self.model_hash,
        }


@dataclass(frozen=True)
class Attempt:
    cell_index: int
    start_id: str
    success: bool
    sse_scaled: float | None
    outer_evaluations: int
    inner_lsq_solves: int
    reason_codes: tuple[str, ...]
    c_t: float | None = None
    linear: tuple[float, ...] = ()
    shape: tuple[float, ...] = ()
    model_hash: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "cell_index": self.cell_index,
            "start_id": self.start_id,
            "success": self.success,
            "sse_scaled": _finite(self.sse_scaled),
            "outer_evaluations": self.outer_evaluations,
            "inner_lsq_solves": self.inner_lsq_solves,
            "reason_codes": list(self.reason_codes),
            "c_t": _finite(self.c_t),
            "linear": list(self.linear),
            "shape": list(self.shape),
            "model_hash": self.model_hash,
        }


@dataclass(frozen=True)
class FitTrace:
    strategy: StrategyId
    status: str
    best: Candidate | None
    cells: tuple[TieCell, ...]
    attempts: tuple[Attempt, ...]
    warnings: tuple[str, ...] = ()
    x_scale: XScaleDiagnostics | None = None

    def public(self, include_attempts: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "strategy": self.strategy,
            "status": self.status,
            "best": None if self.best is None else self.best.public(),
            "eligible_cells": [cell.__dict__ for cell in self.cells],
            "attempt_count": len(self.attempts),
            "successful_attempts": sum(attempt.success for attempt in self.attempts),
            "outer_evaluations": sum(attempt.outer_evaluations for attempt in self.attempts),
            "inner_lsq_solves": sum(attempt.inner_lsq_solves for attempt in self.attempts),
            "warnings": list(self.warnings),
            "x_scale": None if self.x_scale is None else self.x_scale.public(),
        }
        if include_attempts:
            data["attempts"] = [attempt.public() for attempt in self.attempts]
        return data


FAMILY_P: dict[FamilyId, int] = {
    "poly1_v1": 2,
    "exp_pos_v1": 3,
    "exp_neg_v1": 3,
    "logistic_v1": 4,
}


def scale_problem(problem: Problem) -> tuple[Scale, np.ndarray, np.ndarray]:
    x = np.asarray(problem.x, dtype=float)
    y = np.asarray(problem.y, dtype=float)
    if x.ndim != 1 or y.ndim != 1 or len(x) != len(y) or len(x) == 0:
        raise ValueError("INVALID_SHAPE")
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise ValueError("NONFINITE_INPUT")
    x_min = float(np.min(x))
    x_span = float(np.max(x) - x_min)
    if not np.isfinite(x_span) or x_span <= 0.0:
        raise ValueError("CONSTANT_X")
    # Stable traversal is part of the numerical manifest: rows remain separate,
    # but reduction order must not depend on their input permutation.
    y_offset = float(np.mean(np.sort(y, kind="stable")))
    raw_y_scale = float(np.max(y) - np.min(y))
    y_resolution = 64.0 * np.finfo(float).eps * max(1.0, float(np.max(np.abs(y))))
    y_scale = raw_y_scale if raw_y_scale > y_resolution else 1.0
    scale = Scale(x_min, x_span, y_offset, y_scale)
    return scale, scale.x_to_t(x), scale.y_to_scaled(y)


def x_scale_diagnostics(problem: Problem) -> XScaleDiagnostics:
    scale, t, _ = scale_problem(problem)
    x = np.asarray(problem.x, dtype=float)
    unique_x = np.unique(x)
    unique_t = int(np.unique(t).size)
    ulp_ratio = float(np.max(np.abs(np.spacing(unique_x))) / scale.x_span)
    collision = unique_t < int(unique_x.size)
    warnings: list[str] = []
    if collision or ulp_ratio > 1e-6:
        warnings.append("LOW_X_RESOLUTION")
    if collision:
        warnings.append("NORMALIZED_X_COLLISION")
    return XScaleDiagnostics(
        int(unique_x.size),
        unique_t,
        ulp_ratio,
        collision,
        tuple(warnings),
    )


def admissible_cells(problem: Problem) -> tuple[TieCell, ...]:
    return _enumerate_cells(problem, require_sufficiency=True)


def balanced_cells(problem: Problem) -> tuple[TieCell, ...]:
    return _enumerate_cells(problem, require_sufficiency=False)


def _enumerate_cells(problem: Problem, *, require_sufficiency: bool) -> tuple[TieCell, ...]:
    scale, _, _ = scale_problem(problem)
    x = np.asarray(problem.x, dtype=float)
    unique_x, counts = np.unique(x, return_counts=True)
    unique_t = scale.x_to_t(unique_x)
    n = len(x)
    lower, upper = int(np.ceil(0.40 * n)), int(np.floor(0.60 * n))
    cumulative = np.cumsum(counts)
    cells: list[TieCell] = []
    left_p, right_p = FAMILY_P[problem.families[0]], FAMILY_P[problem.families[1]]
    for index in range(len(unique_x) - 1):
        left_n = int(cumulative[index])
        right_n = n - left_n
        left_unique = index + 1
        right_unique = len(unique_x) - left_unique
        balanced = lower <= left_n <= upper
        sufficient = (
            left_n >= max(3, left_p + 2)
            and right_n >= max(3, right_p + 2)
            and left_unique >= max(2, left_p + 1)
            and right_unique >= max(2, right_p + 1)
        )
        if balanced and (sufficient or not require_sufficiency):
            cells.append(
                TieCell(
                    index=len(cells),
                    lo_x=float(unique_x[index]),
                    hi_x=float(unique_x[index + 1]),
                    lo_t=float(unique_t[index]),
                    hi_t=(
                        float(np.nextafter(unique_t[index + 1], unique_t[index]))
                        if unique_t[index + 1] > unique_t[index]
                        else float(unique_t[index])
                    ),
                    left_n=left_n,
                    right_n=right_n,
                    left_unique=left_unique,
                    right_unique=right_unique,
                )
            )
    return tuple(cells)


def fit_problem(
    problem: Problem,
    strategy: StrategyId,
    *,
    seed: int = 20260716,
    start_order: Literal["normal", "reversed"] = "normal",
    budget: Literal["small", "normal", "forced_failure"] = "normal",
) -> FitTrace:
    diagnostics = x_scale_diagnostics(problem)
    balanced = balanced_cells(problem)
    cells = admissible_cells(problem)
    if diagnostics.normalized_collision:
        return FitTrace(
            strategy,
            "NONINVERTIBLE_X_SCALE",
            None,
            cells,
            (),
            diagnostics.warning_codes,
            diagnostics,
        )
    if not balanced:
        return FitTrace(strategy, "NO_BALANCED_SPLIT", None, cells, (), diagnostics.warning_codes, diagnostics)
    if not cells:
        return FitTrace(
            strategy,
            "INSUFFICIENT_DATA_FOR_P2",
            None,
            cells,
            (),
            diagnostics.warning_codes,
            diagnostics,
        )
    if strategy == "profile_cells":
        trace = _fit_local(problem, cells, True, seed, start_order, budget)
    elif strategy == "joint_cells":
        trace = _fit_local(problem, cells, False, seed, start_order, budget)
    elif strategy == "global_profiled_reference":
        trace = _fit_global(problem, cells, seed, budget)
    else:
        raise ValueError(strategy)
    return replace(trace, warnings=diagnostics.warning_codes, x_scale=diagnostics)


def fit_p1_profiled(
    x: np.ndarray,
    y: np.ndarray,
    family: FamilyId,
    direction: Literal[-1, 1],
    *,
    seed: int = 20260716,
    global_reference: bool = False,
) -> dict[str, Any]:
    """Small P1 seam probe: nonlinear shapes outside, `[a,q>=0]` profiled."""
    dummy = Problem("p1_probe", np.asarray(x), np.asarray(y), (family, family), direction, None, "OK")
    diagnostics = x_scale_diagnostics(dummy)
    if diagnostics.normalized_collision:
        return {
            "status": "NONINVERTIBLE_X_SCALE",
            "family": family,
            "direction": direction,
            "warnings": list(diagnostics.warning_codes),
            "x_scale": diagnostics.public(),
        }
    scale, t, ys = scale_problem(dummy)
    order = np.lexsort((ys, t))
    t, ys = t[order], ys[order]
    p = FAMILY_P[family]
    if len(t) < max(4, p + 2) or np.unique(np.asarray(x, dtype=float)).size < max(3, p + 1):
        return {
            "status": "INSUFFICIENT_DATA_FOR_P1",
            "family": family,
            "direction": direction,
            "warnings": list(diagnostics.warning_codes),
            "x_scale": diagnostics.public(),
        }
    bounds = _shape_bounds(family)
    evaluations = 0
    inner_solves = 0

    def solve(shape: np.ndarray) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
        nonlocal inner_solves
        inner_solves += 1
        atom = _atom(family, t, shape)
        design = np.column_stack([np.ones_like(t), direction * atom])
        fitted = lsq_linear(design, ys, bounds=([-np.inf, 0.0], [np.inf, np.inf]), tol=1e-12, max_iter=300)
        prediction = design @ fitted.x
        return float(np.sum((ys - prediction) ** 2)), np.asarray(fitted.x), prediction, design

    if not bounds:
        best_shape = np.empty(0)
        best = solve(best_shape)
        optimizer_status = "OK"
    elif global_reference:
        def objective(shape: np.ndarray) -> float:
            nonlocal evaluations
            evaluations += 1
            return solve(shape)[0]

        optimized = differential_evolution(
            objective,
            bounds,
            seed=_derived_seed(seed, 701),
            maxiter=100,
            popsize=10,
            tol=1e-10,
            atol=1e-12,
            polish=True,
            workers=1,
            updating="deferred",
        )
        best_shape = np.asarray(optimized.x)
        best = solve(best_shape)
        optimizer_status = "OK" if optimized.success else "REFERENCE_BUDGET_EXHAUSTED"
    else:
        candidates: list[tuple[float, np.ndarray, tuple[float, np.ndarray, np.ndarray, np.ndarray]]] = []
        for _, start in _manifest_starts(bounds, 12, seed, 0):
            def objective(shape: np.ndarray) -> float:
                nonlocal evaluations
                evaluations += 1
                return solve(shape)[0]

            optimized = minimize(
                objective,
                start,
                method="L-BFGS-B",
                bounds=bounds,
                options={"maxiter": 400, "ftol": 1e-13, "gtol": 1e-8, "maxls": 50},
            )
            if optimized.success and np.isfinite(optimized.fun):
                shape = np.asarray(optimized.x)
                candidates.append((float(optimized.fun), shape, solve(shape)))
        if not candidates:
            return {
                "status": "OPTIMIZER_FAILURE",
                "family": family,
                "direction": direction,
                "warnings": list(diagnostics.warning_codes),
                "x_scale": diagnostics.public(),
            }
        candidates.sort(key=lambda row: (round(row[0], 12), tuple(np.round(row[1], 9))))
        _, best_shape, best = candidates[0]
        optimizer_status = "OK"

    sse, linear, prediction, design = best
    singular = np.linalg.svd(design, compute_uv=False)
    rank = int(np.linalg.matrix_rank(design))
    condition = float(np.inf if singular[-1] == 0 else singular[0] / singular[-1])
    margin = float(linear[1] * _atom_derivative_min(family, best_shape))
    reasons: list[str] = []
    if not np.all(np.isfinite(prediction)):
        reasons.append("NONFINITE_FAILURE")
    if not _shape_in_bounds(family, best_shape):
        reasons.append("DOMAIN_FAILURE")
    if linear[1] < -1e-12 or margin < -1e-10:
        reasons.append("SHAPE_FAILURE")
    if rank < 2:
        reasons.append("RANK_FAILURE")
    if condition > 1e12:
        reasons.append("WEAK_IDENTIFIABILITY")
    status = "COLLAPSED_TO_CONSTANT" if linear[1] <= 1e-8 and not reasons else optimizer_status
    if reasons:
        status = "CERTIFICATE_FAILURE"
    payload = {
        "family": family,
        "direction": direction,
        "linear": [round(float(value), 9) for value in linear],
        "shape": [round(float(value), 9) for value in best_shape],
    }
    model_hash = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    return {
        "status": status,
        "family": family,
        "direction": direction,
        "sse_scaled": sse,
        "linear_scaled": linear.tolist(),
        "shape": best_shape.tolist(),
        "prediction_scaled": prediction.tolist(),
        "signed_derivative_margin": margin,
        "design_rank": rank,
        "design_condition": _finite(condition),
        "reason_codes": reasons,
        "model_hash": model_hash,
        "outer_evaluations": evaluations,
        "inner_lsq_solves": inner_solves,
        "scale": scale.__dict__,
        "warnings": list(diagnostics.warning_codes),
        "x_scale": diagnostics.public(),
    }


def _fit_local(
    problem: Problem,
    cells: tuple[TieCell, ...],
    profiled: bool,
    seed: int,
    start_order: str,
    budget: str,
) -> FitTrace:
    scale, t, ys = scale_problem(problem)
    order = np.lexsort((ys, t))
    t, ys = t[order], ys[order]
    strategy: StrategyId = "profile_cells" if profiled else "joint_cells"
    attempts: list[Attempt] = []
    winners: list[Candidate] = []
    start_count = 1 if budget == "forced_failure" else (6 if budget == "small" else 12)
    maxiter = 0 if budget == "forced_failure" else (160 if budget == "small" else 400)

    for cell in cells:
        bounds = _outer_bounds(cell, problem.families)
        if profiled and len(bounds) == 1:
            counters = {"outer": 0, "inner": 0}

            def scalar_objective(value: float) -> float:
                counters["outer"] += 1
                solved = _profiled_at(problem, t, ys, np.array([value]))
                counters["inner"] += 1
                return solved[0]

            result = minimize_scalar(
                scalar_objective,
                bounds=bounds[0],
                method="bounded",
                options={"xatol": 1e-12, "maxiter": maxiter},
            )
            final_outer = np.array([float(result.x)])
            sse, linear, prediction, design = _profiled_at(problem, t, ys, final_outer)
            counters["inner"] += 1
            candidate = _candidate(problem, scale, t, strategy, final_outer, linear, prediction, sse)
            success = bool(result.success and np.isfinite(sse) and candidate.certificate.valid)
            reasons = tuple(sorted(set((*candidate.certificate.reason_codes, *(("OPTIMIZER_FAILURE",) if not result.success else ())))))
            attempts.append(Attempt(
                cell.index,
                f"cell{cell.index}:scalar",
                success,
                sse,
                counters["outer"],
                counters["inner"],
                reasons,
                candidate.c_t,
                candidate.linear,
                candidate.shape,
                candidate.model_hash,
            ))
            if success:
                winners.append(candidate)
            continue
        starts = _manifest_starts(bounds, start_count, seed, cell.index)
        if start_order == "reversed":
            starts = list(reversed(starts))
        for start_index, outer_start in starts:
            counters = {"outer": 0, "inner": 0}
            start_id = f"cell{cell.index}:start{start_index:02d}"
            if profiled:
                def objective(vector: np.ndarray) -> float:
                    counters["outer"] += 1
                    solved = _profiled_at(problem, t, ys, vector)
                    counters["inner"] += 1
                    return solved[0]

                result = minimize(
                    objective,
                    outer_start,
                    method="L-BFGS-B",
                    bounds=bounds,
                    options={"maxiter": maxiter, "ftol": 1e-13, "gtol": 1e-8, "maxls": 50},
                )
                final_outer = np.asarray(result.x, dtype=float)
                sse, linear, prediction, design = _profiled_at(problem, t, ys, final_outer)
                counters["inner"] += 1
            else:
                initial_sse, initial_linear, _, _ = _profiled_at(problem, t, ys, outer_start)
                counters["inner"] += 1
                joint_start = np.r_[outer_start[0], initial_linear, outer_start[1:]]
                joint_bounds = [bounds[0], (None, None), (0.0, None), (0.0, None), *bounds[1:]]

                def objective(vector: np.ndarray) -> float:
                    counters["outer"] += 1
                    prediction = _predict_from_joint(problem, t, vector)
                    return float(np.sum((ys - prediction) ** 2))

                result = minimize(
                    objective,
                    joint_start,
                    method="L-BFGS-B",
                    bounds=joint_bounds,
                    options={"maxiter": maxiter, "ftol": 1e-13, "gtol": 1e-8, "maxls": 50},
                )
                final_outer = np.r_[result.x[0], result.x[4:]]
                linear = np.asarray(result.x[1:4], dtype=float)
                prediction = _predict(problem, t, final_outer, linear)
                sse = float(np.sum((ys - prediction) ** 2))
                design = _design(problem, t, final_outer)

            candidate = _candidate(problem, scale, t, strategy, final_outer, linear, prediction, sse)
            success = bool(result.success and np.isfinite(sse) and candidate.certificate.valid)
            reasons = tuple(sorted(set((*candidate.certificate.reason_codes, *(("OPTIMIZER_FAILURE",) if not result.success else ())))))
            attempts.append(Attempt(
                cell.index,
                start_id,
                success,
                sse,
                counters["outer"],
                counters["inner"],
                reasons,
                candidate.c_t,
                candidate.linear,
                candidate.shape,
                candidate.model_hash,
            ))
            if success:
                winners.append(candidate)

    return _finish_trace(strategy, cells, attempts, winners)


def _fit_global(problem: Problem, cells: tuple[TieCell, ...], seed: int, budget: str) -> FitTrace:
    scale, t, ys = scale_problem(problem)
    order = np.lexsort((ys, t))
    t, ys = t[order], ys[order]
    attempts: list[Attempt] = []
    winners: list[Candidate] = []
    exhausted = False
    maxiter = 35 if budget == "small" else 90
    popsize = 6 if budget == "small" else 10
    for cell in cells:
        bounds = _outer_bounds(cell, problem.families)
        counters = {"outer": 0, "inner": 0}

        def objective(vector: np.ndarray) -> float:
            counters["outer"] += 1
            solved = _profiled_at(problem, t, ys, vector)
            counters["inner"] += 1
            return solved[0]

        result = differential_evolution(
            objective,
            bounds,
            seed=_derived_seed(seed, cell.index, 991),
            maxiter=maxiter,
            popsize=popsize,
            tol=1e-10,
            atol=1e-12,
            polish=True,
            workers=1,
            updating="deferred",
        )
        outer = np.asarray(result.x, dtype=float)
        sse, linear, prediction, design = _profiled_at(problem, t, ys, outer)
        counters["inner"] += 1
        candidate = _candidate(problem, scale, t, "global_profiled_reference", outer, linear, prediction, sse)
        # DE is a stochastic best-known reference, not a deployable candidate.
        # A finite certified incumbent remains useful when its convergence
        # tolerance was not reached within the explicitly pinned audit budget.
        success = bool(np.isfinite(sse) and candidate.certificate.valid)
        exhausted = exhausted or not result.success
        reasons = tuple(sorted(set((*candidate.certificate.reason_codes, *(("REFERENCE_BUDGET_EXHAUSTED",) if not result.success else ())))))
        attempts.append(
            Attempt(
                cell.index,
                f"cell{cell.index}:de",
                success,
                sse,
                counters["outer"],
                counters["inner"],
                reasons,
                candidate.c_t,
                candidate.linear,
                candidate.shape,
                candidate.model_hash,
            )
        )
        if success:
            winners.append(candidate)
    trace = _finish_trace("global_profiled_reference", cells, attempts, winners)
    if trace.best is not None and exhausted:
        return FitTrace(trace.strategy, "REFERENCE_BUDGET_EXHAUSTED", trace.best, trace.cells, trace.attempts)
    return trace


def _finish_trace(
    strategy: StrategyId,
    cells: tuple[TieCell, ...],
    attempts: list[Attempt],
    winners: list[Candidate],
) -> FitTrace:
    if not winners:
        optimizer_only = bool(attempts) and all("OPTIMIZER_FAILURE" in attempt.reason_codes for attempt in attempts)
        status = "OPTIMIZER_FAILURE" if optimizer_only else "OPTIMIZER_OR_CERTIFICATE_FAILURE"
        return FitTrace(strategy, status, None, cells, tuple(attempts))
    best_loss = min(item.sse_scaled for item in winners)
    objective_tolerance = max(1e-12, 1e-10 * max(1.0, abs(best_loss)))
    tied = [item for item in winners if item.sse_scaled - best_loss <= objective_tolerance]
    best = min(tied, key=lambda item: item.model_hash)
    near_tolerance = max(1e-8, 0.01 * max(best.sse_scaled, 1e-8))
    near = [item for item in winners if item.sse_scaled - best.sse_scaled <= near_tolerance]
    if near and max(item.c_t for item in near) - min(item.c_t for item in near) > 0.15:
        flag = "FLAT_PROFILE" if best.status == "COLLAPSED_SEGMENTS" else "MULTIPLE_NEAR_OPTIMA"
        status = best.status if best.status == "COLLAPSED_SEGMENTS" else "MULTIPLE_NEAR_OPTIMA"
        best = replace(best, status=status, warnings=tuple(sorted(set((*best.warnings, flag)))))
    status = best.status
    return FitTrace(strategy, status, best, cells, tuple(attempts))


def _candidate(
    problem: Problem,
    scale: Scale,
    t: np.ndarray,
    strategy: StrategyId,
    outer: np.ndarray,
    linear: np.ndarray,
    prediction: np.ndarray,
    sse: float,
) -> Candidate:
    certificate = _certify(problem, t, outer, linear, prediction)
    warnings: list[str] = []
    status = "OK"
    c = float(outer[0])
    q_left, q_right = float(linear[1]), float(linear[2])
    if problem.families == ("poly1_v1", "poly1_v1"):
        left_slope = q_left / max(c, 1e-15)
        right_slope = q_right / max(1.0 - c, 1e-15)
        if abs(left_slope - right_slope) <= 1e-6 * max(1.0, left_slope, right_slope):
            status = "COLLAPSED_SEGMENTS"
            warnings.append("BREAKPOINT_NOT_IDENTIFIED")
    if max(q_left, q_right) <= 1e-8:
        status = "COLLAPSED_SEGMENTS"
        warnings.append("CONSTANT_LIMIT")
    if certificate.design_condition is not None and certificate.design_condition > 1e8:
        warnings.append("WEAK_IDENTIFIABILITY")
        if certificate.design_condition > 1e12 and status == "OK":
            status = "WEAK_IDENTIFIABILITY"
    shape_values = np.asarray(outer[1:], dtype=float)
    shape_bounds = [*_shape_bounds(problem.families[0]), *_shape_bounds(problem.families[1])]
    bound_hit = any(
        min(abs(value - low), abs(value - high)) <= 1e-6 * max(1.0, high - low)
        for value, (low, high) in zip(shape_values, shape_bounds)
    )
    if bound_hit:
        warnings.append("PARAMETER_BOUND_HIT")
    if "logistic_v1" in problem.families and (
        bound_hit
        or (
            certificate.signed_derivative_margin is not None
            and certificate.signed_derivative_margin < 1e-6
        )
    ):
        warnings.append("WEAK_IDENTIFIABILITY")
        status = "WEAK_IDENTIFIABILITY"
    selected_cell = next(
        (cell for cell in admissible_cells(problem) if cell.lo_t - 1e-15 <= c <= cell.hi_t + 1e-15),
        None,
    )
    if selected_cell is not None and min(abs(c - selected_cell.lo_t), abs(c - selected_cell.hi_t)) <= 1e-6:
        warnings.append("BOUNDARY_HIT")
    return Candidate(
        strategy,
        problem.families,
        problem.direction,
        c,
        tuple(float(value) for value in linear),
        tuple(float(value) for value in outer[1:]),
        float(sse),
        np.asarray(prediction, dtype=float),
        certificate,
        status,
        tuple(sorted(set(warnings))),
    )


def _certify(
    problem: Problem,
    t: np.ndarray,
    outer: np.ndarray,
    linear: np.ndarray,
    fit_prediction: np.ndarray,
    *,
    right_join_offset: float = 0.0,
    rank_column_fault: bool = False,
) -> Certificate:
    c = float(outer[0])
    _, source_t, source_ys = scale_problem(problem)
    source_order = np.lexsort((source_ys, source_t))
    canonical_x = np.asarray(problem.x, dtype=float)[source_order]
    left_shape, right_shape = _cert_split_shapes(problem.families, outer[1:])
    mask = t <= c
    left_n, right_n = int(np.sum(mask)), int(np.sum(~mask))
    n = len(t)
    balance_ok = int(np.ceil(0.4 * n)) <= left_n <= int(np.floor(0.6 * n))
    ties_atomic = all(
        bool(np.all(mask[canonical_x == value] == mask[canonical_x == value][0]))
        for value in np.unique(canonical_x)
    )
    left_p, right_p = FAMILY_P[problem.families[0]], FAMILY_P[problem.families[1]]
    support_ok = (
        left_n >= max(3, left_p + 2)
        and right_n >= max(3, right_p + 2)
        and np.unique(canonical_x[mask]).size >= max(2, left_p + 1)
        and np.unique(canonical_x[~mask]).size >= max(2, right_p + 1)
    )
    domain_ok = _cert_shape_in_bounds(problem.families[0], left_shape) and _cert_shape_in_bounds(problem.families[1], right_shape)
    q_left, q_right = float(linear[1]), float(linear[2])
    left_margin = q_left * _cert_derivative_min(problem.families[0], left_shape) / max(c, 1e-15)
    right_margin = q_right * _cert_derivative_min(problem.families[1], right_shape) / max(1.0 - c, 1e-15)
    derivative_margin = min(left_margin, right_margin)
    shape_ok = bool(q_left >= -1e-12 and q_right >= -1e-12 and derivative_margin >= -1e-10)
    mu = float(linear[0])
    cert_prediction, cert_design = _certificate_evaluate(
        problem,
        t,
        c,
        linear,
        left_shape,
        right_shape,
        right_join_offset=right_join_offset,
        rank_column_fault=rank_column_fault,
    )
    finite = bool(
        np.all(np.isfinite(fit_prediction))
        and np.all(np.isfinite(cert_prediction))
        and np.all(np.isfinite(outer))
        and np.all(np.isfinite(linear))
    )
    comparison_scale = max(1.0, float(np.max(np.abs(cert_prediction)))) if np.all(np.isfinite(cert_prediction)) else 1.0
    evaluator_match = bool(
        finite and np.max(np.abs(np.asarray(fit_prediction) - cert_prediction)) <= 1e-10 * comparison_scale
    )
    left_join = mu + problem.direction * q_left * (
        _cert_atom(problem.families[0], np.array([1.0]), left_shape)[0]
        - _cert_atom(problem.families[0], np.array([1.0]), left_shape)[0]
    )
    right_join = mu + right_join_offset + problem.direction * q_right * (
        _cert_atom(problem.families[1], np.array([0.0]), right_shape)[0]
        - _cert_atom(problem.families[1], np.array([0.0]), right_shape)[0]
    )
    join_error = abs(float(left_join - right_join))
    singular = np.linalg.svd(cert_design, compute_uv=False)
    rank = int(np.linalg.matrix_rank(cert_design))
    condition = float(np.inf if singular[-1] == 0 else singular[0] / singular[-1])
    reasons: list[str] = []
    if not finite:
        reasons.append("NONFINITE_FAILURE")
    if not balance_ok:
        reasons.append("BALANCE_FAILURE")
    if not ties_atomic:
        reasons.append("TIE_ATOMICITY_FAILURE")
    if not support_ok:
        reasons.append("INSUFFICIENT_DATA_FOR_P2")
    if not domain_ok:
        reasons.append("DOMAIN_FAILURE")
    if not shape_ok:
        reasons.append("SHAPE_FAILURE")
    if join_error > 1e-10 * max(1.0, abs(mu)):
        reasons.append("JOIN_FAILURE")
    if not evaluator_match:
        reasons.append("EVALUATOR_MISMATCH")
    if rank < 3:
        reasons.append("RANK_FAILURE")
    valid = not reasons
    return Certificate(
        valid,
        finite,
        balance_ok,
        ties_atomic,
        support_ok,
        domain_ok,
        shape_ok,
        evaluator_match,
        join_error,
        derivative_margin,
        rank,
        condition,
        tuple(reasons),
    )


def _certificate_evaluate(
    problem: Problem,
    t: np.ndarray,
    c: float,
    linear: np.ndarray,
    left_shape: np.ndarray,
    right_shape: np.ndarray,
    *,
    right_join_offset: float,
    rank_column_fault: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """Independent evaluator used only by the certifier."""
    mask = t <= c
    left_column = np.zeros_like(t)
    right_column = np.zeros_like(t)
    left_local = t[mask] / c
    right_local = (t[~mask] - c) / (1.0 - c)
    left_anchor = _cert_atom(problem.families[0], np.array([1.0]), left_shape)[0]
    right_anchor = _cert_atom(problem.families[1], np.array([0.0]), right_shape)[0]
    left_column[mask] = problem.direction * (
        _cert_atom(problem.families[0], left_local, left_shape) - left_anchor
    )
    right_column[~mask] = problem.direction * (
        _cert_atom(problem.families[1], right_local, right_shape) - right_anchor
    )
    if rank_column_fault:
        right_column[:] = 0.0
    design = np.column_stack([np.ones_like(t), left_column, right_column])
    prediction = design @ np.asarray(linear)
    prediction[~mask] += right_join_offset
    return prediction, design


def _cert_split_shapes(
    families: tuple[FamilyId, FamilyId], vector: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    sizes = {"poly1_v1": 0, "exp_pos_v1": 1, "exp_neg_v1": 1, "logistic_v1": 2}
    left_size = sizes[families[0]]
    return np.asarray(vector[:left_size], dtype=float), np.asarray(vector[left_size:], dtype=float)


def _cert_shape_in_bounds(family: FamilyId, params: np.ndarray) -> bool:
    explicit = {
        "poly1_v1": (),
        "exp_pos_v1": ((0.10, 12.0),),
        "exp_neg_v1": ((-12.0, -0.10),),
        "logistic_v1": ((0.25, 20.0), (0.0, 1.0)),
    }[family]
    return len(params) == len(explicit) and all(
        low - 1e-12 <= float(value) <= high + 1e-12
        for value, (low, high) in zip(params, explicit)
    )


def _cert_atom(family: FamilyId, t: np.ndarray, params: np.ndarray) -> np.ndarray:
    values = np.asarray(t, dtype=float)
    if family == "poly1_v1":
        return values
    if family == "exp_pos_v1":
        return np.exp(float(params[0]) * values)
    if family == "exp_neg_v1":
        return -np.exp(float(params[0]) * values)
    if family == "logistic_v1":
        z = float(params[0]) * (values - float(params[1]))
        result = np.empty_like(z)
        positive = z >= 0.0
        result[positive] = 1.0 / (1.0 + np.exp(-z[positive]))
        exp_z = np.exp(z[~positive])
        result[~positive] = exp_z / (1.0 + exp_z)
        return result
    raise ValueError(family)


def _cert_derivative_min(family: FamilyId, params: np.ndarray) -> float:
    if family == "poly1_v1":
        return 1.0
    if family == "exp_pos_v1":
        k = float(params[0])
        return min(k, k * float(np.exp(k)))
    if family == "exp_neg_v1":
        k = float(params[0])
        return min(-k, -k * float(np.exp(k)))
    if family == "logistic_v1":
        k, m = (float(value) for value in params)
        endpoints = _cert_atom(family, np.array([0.0, 1.0]), params)
        return float(k * np.min(endpoints * (1.0 - endpoints)))
    raise ValueError(family)


def _profiled_at(
    problem: Problem,
    t: np.ndarray,
    ys: np.ndarray,
    outer: np.ndarray,
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    design = _design(problem, t, outer)
    solved = lsq_linear(
        design,
        ys,
        bounds=([-np.inf, 0.0, 0.0], [np.inf, np.inf, np.inf]),
        tol=1e-12,
        lsmr_tol="auto",
        max_iter=300,
    )
    linear = np.asarray(solved.x, dtype=float)
    prediction = design @ linear
    return float(np.sum((ys - prediction) ** 2)), linear, prediction, design


def _design(problem: Problem, t: np.ndarray, outer: np.ndarray) -> np.ndarray:
    c = float(outer[0])
    left_shape, right_shape = _split_shapes(problem.families, outer[1:])
    mask = t <= c
    left_column = np.zeros_like(t)
    right_column = np.zeros_like(t)
    left_local = t[mask] / max(c, 1e-15)
    right_local = (t[~mask] - c) / max(1.0 - c, 1e-15)
    left_column[mask] = problem.direction * (
        _atom(problem.families[0], left_local, left_shape)
        - _atom(problem.families[0], np.ones_like(left_local), left_shape)
    )
    right_column[~mask] = problem.direction * (
        _atom(problem.families[1], right_local, right_shape)
        - _atom(problem.families[1], np.zeros_like(right_local), right_shape)
    )
    return np.column_stack([np.ones_like(t), left_column, right_column])


def _predict(problem: Problem, t: np.ndarray, outer: np.ndarray, linear: np.ndarray) -> np.ndarray:
    return _design(problem, t, outer) @ np.asarray(linear)


def _predict_from_joint(problem: Problem, t: np.ndarray, joint: np.ndarray) -> np.ndarray:
    outer = np.r_[joint[0], joint[4:]]
    return _predict(problem, t, outer, joint[1:4])


def _outer_bounds(cell: TieCell, families: tuple[FamilyId, FamilyId]) -> list[tuple[float, float]]:
    return [(cell.lo_t, cell.hi_t), *_shape_bounds(families[0]), *_shape_bounds(families[1])]


def _shape_bounds(family: FamilyId) -> list[tuple[float, float]]:
    if family == "poly1_v1":
        return []
    if family == "exp_pos_v1":
        return [(0.10, 12.0)]
    if family == "exp_neg_v1":
        return [(-12.0, -0.10)]
    if family == "logistic_v1":
        return [(0.25, 20.0), (0.0, 1.0)]
    raise ValueError(family)


def _shape_in_bounds(family: FamilyId, params: np.ndarray) -> bool:
    bounds = _shape_bounds(family)
    return len(params) == len(bounds) and all(low - 1e-12 <= value <= high + 1e-12 for value, (low, high) in zip(params, bounds))


def _split_shapes(families: tuple[FamilyId, FamilyId], vector: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    left_size = len(_shape_bounds(families[0]))
    return np.asarray(vector[:left_size], dtype=float), np.asarray(vector[left_size:], dtype=float)


def _atom(family: FamilyId, t: np.ndarray, params: np.ndarray) -> np.ndarray:
    if family == "poly1_v1":
        return np.asarray(t, dtype=float)
    if family == "exp_pos_v1":
        return np.exp(np.clip(params[0] * t, -700.0, 700.0))
    if family == "exp_neg_v1":
        return -np.exp(np.clip(params[0] * t, -700.0, 700.0))
    if family == "logistic_v1":
        return expit(params[0] * (t - params[1]))
    raise ValueError(family)


def _atom_derivative_min(family: FamilyId, params: np.ndarray) -> float:
    if family == "poly1_v1":
        return 1.0
    if family == "exp_pos_v1":
        k = float(params[0])
        return k * min(1.0, float(np.exp(k)))
    if family == "exp_neg_v1":
        k = float(params[0])
        return -k * min(1.0, float(np.exp(k)))
    if family == "logistic_v1":
        k, m = (float(value) for value in params)
        endpoints = expit(k * (np.array([0.0, 1.0]) - m))
        return float(k * np.min(endpoints * (1.0 - endpoints)))
    raise ValueError(family)


def _manifest_starts(
    bounds: list[tuple[float, float]], count: int, seed: int, cell_index: int
) -> list[tuple[int, np.ndarray]]:
    starts: list[tuple[int, np.ndarray]] = []
    primes = (2, 3, 5, 7, 11, 13, 17)
    offset = _derived_seed(seed, cell_index, 17) % 97
    for start_id in range(count):
        values: list[float] = []
        for dim, (low, high) in enumerate(bounds):
            fraction = 0.5 if start_id == 0 else _van_der_corput(start_id + 1 + offset, primes[dim])
            fraction = min(0.995, max(0.005, fraction))
            values.append(low + fraction * (high - low))
        starts.append((start_id, np.asarray(values, dtype=float)))
    return starts


def _van_der_corput(index: int, base: int) -> float:
    result, denominator = 0.0, 1.0
    while index:
        index, remainder = divmod(index, base)
        denominator *= base
        result += remainder / denominator
    return result


def _derived_seed(seed: int, *parts: int) -> int:
    value = int(seed) & 0xFFFFFFFF
    for part in parts:
        value = (1664525 * (value ^ int(part)) + 1013904223) & 0xFFFFFFFF
    return value


def make_scenarios() -> tuple[Problem, ...]:
    x = np.linspace(0.0, 1.0, 31)
    hinge = 0.2 + 0.35 * x + 1.25 * np.maximum(x - 0.52, 0.0)

    def p2_truth(
        c: float,
        families: tuple[FamilyId, FamilyId],
        shapes: tuple[np.ndarray, np.ndarray],
        q: tuple[float, float],
        mu: float,
    ) -> np.ndarray:
        mask = x <= c
        y = np.empty_like(x)
        left_t = x[mask] / c
        right_t = (x[~mask] - c) / (1.0 - c)
        y[mask] = mu + q[0] * (_atom(families[0], left_t, shapes[0]) - _atom(families[0], np.ones_like(left_t), shapes[0]))
        y[~mask] = mu + q[1] * (_atom(families[1], right_t, shapes[1]) - _atom(families[1], np.zeros_like(right_t), shapes[1]))
        return y

    exp_families: tuple[FamilyId, FamilyId] = ("exp_pos_v1", "exp_pos_v1")
    exp_y = p2_truth(0.48, exp_families, (np.array([2.2]), np.array([1.3])), (0.15, 0.30), 0.8)
    exp_neg_families: tuple[FamilyId, FamilyId] = ("exp_neg_v1", "exp_neg_v1")
    exp_neg_y = p2_truth(0.51, exp_neg_families, (np.array([-2.0]), np.array([-1.1])), (0.7, 0.9), 0.8)
    logistic_families: tuple[FamilyId, FamilyId] = ("logistic_v1", "logistic_v1")
    logistic_y = p2_truth(0.55, logistic_families, (np.array([7.0, 0.35]), np.array([9.0, 0.65])), (1.1, 0.9), 1.0)
    saturated_y = p2_truth(0.50, logistic_families, (np.array([20.0, 0.0]), np.array([20.0, 1.0])), (0.3, 0.3), 1.0)

    tie_x = np.repeat(np.arange(9, dtype=float) / 8.0, [2, 2, 2, 1, 1, 1, 2, 2, 2])
    tie_y = 0.4 + 0.5 * tie_x + 0.6 * np.maximum(tie_x - 0.4, 0.0)
    no_x = np.repeat(np.array([0.0, 0.5, 1.0]), [3, 14, 3])
    no_y = 1.0 + no_x
    insufficient_x = np.linspace(0.0, 1.0, 10)
    insufficient_y = 0.5 + expit(5.0 * (insufficient_x - 0.5))
    basin_x = np.linspace(0.0, 1.0, 41)
    basin_y = (
        0.2
        + 0.2 * basin_x
        + 1.0 * np.maximum(basin_x - 0.30, 0.0)
        - 1.0 * np.maximum(basin_x - 0.50, 0.0)
        + 1.0 * np.maximum(basin_x - 0.70, 0.0)
    )
    return (
        Problem("hinge_identified", x, hinge, ("poly1_v1", "poly1_v1"), 1, 0.52, "OK"),
        Problem("exp_identified", x, exp_y, exp_families, 1, 0.48, "OK"),
        Problem("exp_negative_rate", x, exp_neg_y, exp_neg_families, 1, 0.51, "OK"),
        Problem("logistic_identified", x, logistic_y, logistic_families, 1, 0.55, "OK"),
        Problem("line_collapse", x, 0.3 + 0.8 * x, ("poly1_v1", "poly1_v1"), 1, None, "COLLAPSED_SEGMENTS"),
        Problem("constant_collapse", x, np.full_like(x, 2.75), ("poly1_v1", "poly1_v1"), 1, None, "COLLAPSED_SEGMENTS"),
        Problem("break_outside_balance", x, 0.2 + 0.2 * x + 1.2 * np.maximum(x - 0.20, 0.0), ("poly1_v1", "poly1_v1"), 1, 0.20, "BOUNDARY_HIT"),
        Problem("ties_exact_40_60", tie_x, tie_y, ("poly1_v1", "poly1_v1"), 1, 0.4, "OK"),
        Problem("ties_no_split", no_x, no_y, ("poly1_v1", "poly1_v1"), 1, None, "NO_BALANCED_SPLIT"),
        Problem("balanced_but_insufficient", insufficient_x, insufficient_y, logistic_families, 1, None, "INSUFFICIENT_DATA_FOR_P2"),
        Problem("multiple_near_optima", basin_x, basin_y, ("poly1_v1", "poly1_v1"), 1, None, "MULTIPLE_NEAR_OPTIMA"),
        Problem("logistic_saturation", x, saturated_y, logistic_families, 1, 0.5, "WEAK_IDENTIFIABILITY"),
    )


def cubic_grid_trap() -> dict[str, Any]:
    center, epsilon = 0.503, 1e-6
    coefficients = np.array([0.0, center**2 - epsilon, -center, 1.0 / 3.0])
    grid = np.linspace(0.0, 1.0, 101)
    derivative = coefficients[1] + 2.0 * coefficients[2] * grid + 3.0 * coefficients[3] * grid**2
    vertex = -coefficients[2] / (3.0 * coefficients[3])
    exact = coefficients[1] + 2.0 * coefficients[2] * vertex + 3.0 * coefficients[3] * vertex**2
    return {
        "coarse_grid_passes": bool(np.all(derivative >= 0.0)),
        "analytic_certificate_passes": bool(exact >= 0.0),
        "exact_min_signed_derivative": float(exact),
        "status": "SHAPE_FAILURE" if exact < 0.0 else "OK",
    }


def certificate_fault_probes() -> dict[str, Any]:
    problem = next(item for item in make_scenarios() if item.name == "hinge_identified")
    trace = fit_problem(problem, "profile_cells", budget="small")
    if trace.best is None:
        return {"pass": False, "status": "BASE_FIT_UNAVAILABLE"}
    candidate = trace.best
    _, t, ys = scale_problem(problem)
    order = np.lexsort((ys, t))
    t = t[order]
    outer = np.r_[candidate.c_t, candidate.shape]
    linear = np.asarray(candidate.linear)
    base_prediction = np.asarray(candidate.prediction_scaled)
    base = _certify(problem, t, outer, linear, base_prediction)

    tampered_prediction = base_prediction.copy()
    tampered_prediction[len(tampered_prediction) // 2] += 1e-4
    evaluator_fault = _certify(problem, t, outer, linear, tampered_prediction)
    join_fault = _certify(
        problem, t, outer, linear, base_prediction, right_join_offset=1e-4
    )

    negative_linear = linear.copy()
    negative_linear[1] = -max(1e-3, abs(negative_linear[1]))
    left_shape, right_shape = _cert_split_shapes(problem.families, outer[1:])
    negative_prediction, _ = _certificate_evaluate(
        problem,
        t,
        float(outer[0]),
        negative_linear,
        left_shape,
        right_shape,
        right_join_offset=0.0,
        rank_column_fault=False,
    )
    shape_fault = _certify(problem, t, outer, negative_linear, negative_prediction)

    nonfinite_prediction = base_prediction.copy()
    nonfinite_prediction[0] = np.nan
    nonfinite_fault = _certify(problem, t, outer, linear, nonfinite_prediction)

    rank_prediction, _ = _certificate_evaluate(
        problem,
        t,
        float(outer[0]),
        linear,
        left_shape,
        right_shape,
        right_join_offset=0.0,
        rank_column_fault=True,
    )
    rank_fault = _certify(
        problem, t, outer, linear, rank_prediction, rank_column_fault=True
    )

    exp_problem = next(item for item in make_scenarios() if item.name == "exp_identified")
    exp_trace = fit_problem(exp_problem, "profile_cells", budget="small")
    if exp_trace.best is None:
        return {"pass": False, "status": "EXP_BASE_FIT_UNAVAILABLE"}
    exp_best = exp_trace.best
    _, exp_t, exp_ys = scale_problem(exp_problem)
    exp_order = np.lexsort((exp_ys, exp_t))
    exp_t = exp_t[exp_order]
    exp_outer = np.r_[exp_best.c_t, exp_best.shape]
    exp_outer[1] = 13.0
    exp_linear = np.asarray(exp_best.linear)
    exp_left, exp_right = _cert_split_shapes(exp_problem.families, exp_outer[1:])
    exp_prediction, _ = _certificate_evaluate(
        exp_problem,
        exp_t,
        float(exp_outer[0]),
        exp_linear,
        exp_left,
        exp_right,
        right_join_offset=0.0,
        rank_column_fault=False,
    )
    domain_fault = _certify(exp_problem, exp_t, exp_outer, exp_linear, exp_prediction)
    probes = {
        "base_valid": base.valid,
        "evaluator_fault_codes": list(evaluator_fault.reason_codes),
        "join_fault_codes": list(join_fault.reason_codes),
        "shape_fault_codes": list(shape_fault.reason_codes),
        "nonfinite_fault_codes": list(nonfinite_fault.reason_codes),
        "rank_fault_codes": list(rank_fault.reason_codes),
        "domain_fault_codes": list(domain_fault.reason_codes),
    }
    probes["pass"] = bool(
        base.valid
        and "EVALUATOR_MISMATCH" in evaluator_fault.reason_codes
        and "JOIN_FAILURE" in join_fault.reason_codes
        and "SHAPE_FAILURE" in shape_fault.reason_codes
        and "NONFINITE_FAILURE" in nonfinite_fault.reason_codes
        and "RANK_FAILURE" in rank_fault.reason_codes
        and "DOMAIN_FAILURE" in domain_fault.reason_codes
    )
    return probes


def _finite(value: float | None) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return float(value)
