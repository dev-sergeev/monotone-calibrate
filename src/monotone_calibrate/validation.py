"""Grouped out-of-fold comparison and conservative P1/P2 decision policy."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import math
import struct
from typing import Iterable

import numpy as np

from .engine import CandidateSet, FitOptions, SearchPolicy, fit_candidates
from .model_runtime import FittedModel


@dataclass(frozen=True, slots=True)
class ValidationOptions:
    repetitions: int = 10
    bootstrap_resamples: int = 200
    base_seed: int = 20260716


@dataclass(frozen=True, slots=True)
class ProcedureMetrics:
    r2_oos: float | None
    rmse_oos: float | None
    mae_oos: float | None
    mse_oos: float | None
    r2_status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "r2_oos": self.r2_oos,
            "r2_status": self.r2_status,
            "rmse_oos": self.rmse_oos,
            "mae_oos": self.mae_oos,
            "mse_oos": self.mse_oos,
        }


@dataclass(frozen=True, slots=True)
class UpliftMetrics:
    relative_mse: float | None
    delta_r2: float | None
    delta_rmse: float | None
    delta_mae: float | None
    repetition_effects: tuple[float | None, ...]
    positive_repetition_share: float
    p10_repetition: float | None
    bootstrap_lower: float | None
    bootstrap_upper: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "relative_mse": self.relative_mse,
            "delta_r2": self.delta_r2,
            "delta_rmse": self.delta_rmse,
            "delta_mae": self.delta_mae,
            "repetition_effects": list(self.repetition_effects),
            "positive_repetition_share": self.positive_repetition_share,
            "p10_repetition": self.p10_repetition,
            "bootstrap_interval": {
                "lower": self.bootstrap_lower,
                "upper": self.bootstrap_upper,
            },
        }


@dataclass(frozen=True, slots=True)
class StabilityMetrics:
    p2_success_rate: float
    direction_agreement: float
    dominant_pair_share: float
    breakpoint_width_fraction: float | None
    edge_frequency: float
    selected_pairs: tuple[str, ...]
    selected_breakpoints: tuple[float, ...]
    passed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "p2_success_rate": self.p2_success_rate,
            "direction_agreement": self.direction_agreement,
            "dominant_pair_share": self.dominant_pair_share,
            "breakpoint_width_fraction": self.breakpoint_width_fraction,
            "edge_frequency": self.edge_frequency,
            "selected_pairs": list(self.selected_pairs),
            "selected_breakpoints": list(self.selected_breakpoints),
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class OOFRecord:
    repetition: int
    fold: int
    canonical_index: int
    x: float
    y: float
    prediction_one: float
    prediction_two: float
    null_prediction: float
    two_fallback: bool


@dataclass(frozen=True, slots=True)
class ValidationResult:
    status: str
    decision_state: str
    recommended_structure: str | None
    one: ProcedureMetrics
    two: ProcedureMetrics
    uplift: UpliftMetrics
    stability: StabilityMetrics
    warning_codes: tuple[str, ...]
    folds: int
    repetitions: int
    oof: tuple[OOFRecord, ...]

    @property
    def oof_appearances(self) -> int:
        return len(self.oof)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "decision_state": self.decision_state,
            "recommended_structure": self.recommended_structure,
            "one": self.one.to_dict(),
            "two": self.two.to_dict(),
            "uplift": self.uplift.to_dict(),
            "stability": self.stability.to_dict(),
            "warning_codes": list(self.warning_codes),
            "folds": self.folds,
            "repetitions": self.repetitions,
            "oof_appearances": self.oof_appearances,
        }


def _canonical_problem(x: Iterable[float], y: Iterable[float]) -> tuple[np.ndarray, np.ndarray]:
    xv = np.asarray(tuple(x), dtype=np.float64)
    yv = np.asarray(tuple(y), dtype=np.float64)
    if xv.ndim != 1 or yv.ndim != 1 or xv.size != yv.size:
        raise ValueError("x and y must be equally sized one-dimensional arrays")
    if not np.all(np.isfinite(xv)) or not np.all(np.isfinite(yv)):
        raise ValueError("validation requires finite coordinates")
    order = np.lexsort((yv, xv))
    return xv[order], yv[order]


def _grouped_assignment(x: np.ndarray, folds: int, repetition: int, seed: int) -> np.ndarray:
    unique, inverse, counts = np.unique(x, return_inverse=True, return_counts=True)
    priorities: list[tuple[bytes, int]] = []
    for group_index, value in enumerate(unique):
        preimage = (
            b"monotone-grouped-fold-v1\0"
            + struct.pack(">QII", seed, repetition, group_index)
            + float(value).hex().encode("ascii")
        )
        priorities.append((hashlib.sha256(preimage).digest(), group_index))
    order = [group for _, group in sorted(priorities)]
    loads = [0] * folds
    group_fold = np.empty(unique.size, dtype=np.int64)
    rotation = repetition % folds
    for group_index in order:
        selected = min(range(folds), key=lambda fold: (loads[fold], (fold - rotation) % folds))
        group_fold[group_index] = selected
        loads[selected] += int(counts[group_index])
    return group_fold[inverse]


def _validation_predict(model: FittedModel, x: np.ndarray) -> np.ndarray:
    """Evaluate a fold model for OOF comparison, including held-out edge groups."""

    values = np.asarray(x, dtype=np.float64)
    with np.errstate(all="ignore"):
        if model.segment_count == 1:
            result = np.asarray(model.segments[0].predict_unchecked(values), dtype=np.float64)
        else:
            left, right = model.segments
            result = np.empty(values.shape, dtype=np.float64)
            mask = values <= float(model.breakpoint)
            result[mask] = left.predict_unchecked(values[mask])
            result[~mask] = right.predict_unchecked(values[~mask])
    return result


def _procedure_metrics(y: np.ndarray, prediction: np.ndarray, null_prediction: np.ndarray) -> ProcedureMetrics:
    residual = y - prediction
    sse = float(np.dot(residual, residual))
    mse = float(sse / y.size)
    rmse = float(math.sqrt(mse))
    mae = float(np.mean(np.abs(residual)))
    null_residual = y - null_prediction
    null_sse = float(np.dot(null_residual, null_residual))
    if null_sse == 0.0:
        r2 = None
        status = "UNDEFINED_R2"
    else:
        r2 = float(1.0 - sse / null_sse)
        status = "DEFINED"
    return ProcedureMetrics(r2, rmse, mae, mse, status)


def _empty_metrics() -> ProcedureMetrics:
    return ProcedureMetrics(None, None, None, None, "NOT_AVAILABLE")


def _empty_uplift() -> UpliftMetrics:
    return UpliftMetrics(None, None, None, None, (), 0.0, None, None, None)


def _empty_stability() -> StabilityMetrics:
    return StabilityMetrics(0.0, 0.0, 0.0, None, 0.0, (), (), False)


def _bootstrap_interval(
    x: np.ndarray,
    loss_one: np.ndarray,
    loss_two: np.ndarray,
    resamples: int,
    seed: int,
) -> tuple[float | None, float | None]:
    if resamples <= 0:
        return None, None
    unique, inverse = np.unique(x, return_inverse=True)
    group_one = np.asarray([float(np.sum(loss_one[inverse == index])) for index in range(unique.size)])
    group_two = np.asarray([float(np.sum(loss_two[inverse == index])) for index in range(unique.size)])
    effects: list[float] = []
    for resample in range(resamples):
        sampled_one = 0.0
        sampled_two = 0.0
        for draw in range(unique.size):
            digest = hashlib.sha256(
                b"paired-fixed-oof-bootstrap-v1\0"
                + struct.pack(">QII", seed, resample, draw)
            ).digest()
            group_index = (int.from_bytes(digest, "big") * unique.size) >> 256
            sampled_one += float(group_one[group_index])
            sampled_two += float(group_two[group_index])
        if sampled_one > 0.0 and np.isfinite(sampled_one) and np.isfinite(sampled_two):
            effects.append(float(1.0 - sampled_two / sampled_one))
    if len(effects) < math.ceil(0.80 * resamples):
        return None, None
    return (
        float(np.quantile(effects, 0.05, method="linear")),
        float(np.quantile(effects, 0.95, method="linear")),
    )


def validate_candidates(
    x: Iterable[float],
    y: Iterable[float],
    full_candidates: CandidateSet,
    options: ValidationOptions | None = None,
) -> ValidationResult:
    """Compare complete P1/P2 procedures using repeated grouped outer folds."""

    resolved = ValidationOptions() if options is None else options
    if resolved.repetitions < 1 or resolved.bootstrap_resamples < 0:
        raise ValueError("validation repetition/bootstrap counts are invalid")
    xv, yv = _canonical_problem(x, y)
    groups = np.unique(xv).size
    if groups < 8:
        warnings = ("VALIDATION_UNAVAILABLE",)
        return ValidationResult(
            "DESCRIPTIVE_ONLY",
            "DESCRIPTIVE_ONLY",
            None,
            _empty_metrics(),
            _empty_metrics(),
            _empty_uplift(),
            _empty_stability(),
            warnings,
            0,
            0,
            (),
        )
    folds = 5 if groups >= 10 else 4
    records: list[OOFRecord] = []
    selected_pairs: list[str] = []
    selected_directions: list[str] = []
    selected_breakpoints: list[float] = []
    selected_edge: list[bool] = []
    success_count = 0
    attempted_fits = resolved.repetitions * folds
    fold_options = FitOptions(
        min_segment_share=full_candidates.search_trace.min_segment_share,
        max_elementary_starts=full_candidates.search_trace.max_elementary_starts,
        search_policy=SearchPolicy(full_candidates.search_trace.profile),
        hypothesis_space=full_candidates.hypothesis_space,
    )

    for repetition in range(resolved.repetitions):
        assignment = _grouped_assignment(xv, folds, repetition, resolved.base_seed)
        for fold in range(folds):
            test_mask = assignment == fold
            train_mask = ~test_mask
            train_x, train_y = xv[train_mask], yv[train_mask]
            test_x, test_y = xv[test_mask], yv[test_mask]
            fitted = fit_candidates(train_x, train_y, fold_options)
            prediction_one = _validation_predict(fitted.one.model, test_x)
            train_mean = float(np.mean(train_y))
            if not np.all(np.isfinite(prediction_one)):
                prediction_one = np.full(test_y.shape, train_mean)
            fallback = fitted.two is None
            if fallback:
                prediction_two = prediction_one.copy()
            else:
                prediction_two = _validation_predict(fitted.two.model, test_x)
                if not np.all(np.isfinite(prediction_two)):
                    prediction_two = prediction_one.copy()
                    fallback = True
            if not fallback and fitted.two is not None:
                success_count += 1
                selected_directions.append(fitted.two.model.direction)
                selected_pairs.append("/".join(fitted.two.model.family_ids))
                assert fitted.two.breakpoint is not None
                selected_breakpoints.append(float(fitted.two.breakpoint))
                selected_edge.append(
                    any(abs(share - 0.40) <= 1e-12 or abs(share - 0.60) <= 1e-12 for share in fitted.two.segment_shares)
                )
            test_indices = np.flatnonzero(test_mask)
            for local, canonical_index in enumerate(test_indices):
                records.append(
                    OOFRecord(
                        repetition,
                        fold,
                        int(canonical_index),
                        float(test_x[local]),
                        float(test_y[local]),
                        float(prediction_one[local]),
                        float(prediction_two[local]),
                        train_mean,
                        fallback,
                    )
                )

    records.sort(key=lambda row: (row.repetition, row.canonical_index))
    observed = np.asarray([row.y for row in records])
    prediction_one = np.asarray([row.prediction_one for row in records])
    prediction_two = np.asarray([row.prediction_two for row in records])
    null_prediction = np.asarray([row.null_prediction for row in records])
    one_metrics = _procedure_metrics(observed, prediction_one, null_prediction)
    two_metrics = _procedure_metrics(observed, prediction_two, null_prediction)

    repetition_effects: list[float | None] = []
    for repetition in range(resolved.repetitions):
        mask = np.asarray([row.repetition == repetition for row in records])
        loss_one = float(np.sum((observed[mask] - prediction_one[mask]) ** 2))
        loss_two = float(np.sum((observed[mask] - prediction_two[mask]) ** 2))
        repetition_effects.append(None if loss_one == 0.0 else float(1.0 - loss_two / loss_one))
    defined_effects = np.asarray([effect for effect in repetition_effects if effect is not None])
    positive_share = (
        float(np.mean(defined_effects > 0.0)) if defined_effects.size else 0.0
    )
    p10 = float(np.quantile(defined_effects, 0.10, method="linear")) if defined_effects.size else None
    relative = (
        None
        if one_metrics.mse_oos in {None, 0.0}
        else float(1.0 - float(two_metrics.mse_oos) / float(one_metrics.mse_oos))
    )
    expanded_x = np.asarray([row.x for row in records])
    loss_one_array = (observed - prediction_one) ** 2
    loss_two_array = (observed - prediction_two) ** 2
    bootstrap_lower, bootstrap_upper = _bootstrap_interval(
        expanded_x,
        loss_one_array,
        loss_two_array,
        resolved.bootstrap_resamples,
        resolved.base_seed,
    )
    uplift = UpliftMetrics(
        relative,
        None
        if one_metrics.r2_oos is None or two_metrics.r2_oos is None
        else float(two_metrics.r2_oos - one_metrics.r2_oos),
        None
        if one_metrics.rmse_oos is None or two_metrics.rmse_oos is None
        else float(one_metrics.rmse_oos - two_metrics.rmse_oos),
        None
        if one_metrics.mae_oos is None or two_metrics.mae_oos is None
        else float(one_metrics.mae_oos - two_metrics.mae_oos),
        tuple(repetition_effects),
        positive_share,
        p10,
        bootstrap_lower,
        bootstrap_upper,
    )

    p2_success_rate = success_count / attempted_fits
    direction_counts = Counter(selected_directions)
    pair_counts = Counter(selected_pairs)
    direction_agreement = max(direction_counts.values(), default=0) / max(1, len(selected_directions))
    dominant_pair_share = max(pair_counts.values(), default=0) / max(1, len(selected_pairs))
    if selected_breakpoints:
        width = float(
            np.quantile(selected_breakpoints, 0.90, method="linear")
            - np.quantile(selected_breakpoints, 0.10, method="linear")
        )
        breakpoint_width_fraction = width / float(xv[-1] - xv[0])
    else:
        breakpoint_width_fraction = None
    edge_frequency = float(np.mean(selected_edge)) if selected_edge else 0.0
    stability_passed = bool(
        p2_success_rate >= 0.90
        and direction_agreement >= 0.80
        and dominant_pair_share >= 0.60
        and breakpoint_width_fraction is not None
        and breakpoint_width_fraction <= 0.25
        and edge_frequency <= 0.20
    )
    stability = StabilityMetrics(
        p2_success_rate,
        direction_agreement,
        dominant_pair_share,
        breakpoint_width_fraction,
        edge_frequency,
        tuple(selected_pairs),
        tuple(selected_breakpoints),
        stability_passed,
    )

    clear = bool(
        full_candidates.two is not None
        and relative is not None
        and relative >= 0.10
        and positive_share >= 0.90
        and p10 is not None
        and p10 >= 0.0
        and bootstrap_lower is not None
        and bootstrap_lower >= 0.05
        and uplift.delta_rmse is not None
        and uplift.delta_rmse > 0.0
        and two_metrics.mae_oos is not None
        and one_metrics.mae_oos is not None
        and two_metrics.mae_oos <= 1.02 * one_metrics.mae_oos
        and stability_passed
    )
    if clear:
        decision_state = "CLEAR_PRACTICAL_UPLIFT"
        recommended = "P2"
    elif full_candidates.two is None:
        decision_state = "NO_VALID_TWO_SEGMENT"
        recommended = "P1"
    elif relative is None or relative <= 0.0:
        decision_state = "NO_UPLIFT_OR_HARM"
        recommended = "P1"
    elif relative < 0.10:
        decision_state = "STATISTICAL_ONLY_SMALL"
        recommended = "P1"
    elif bootstrap_lower is None:
        decision_state = "PRACTICALLY_PROMISING_UNCERTAIN"
        recommended = "P1"
    else:
        decision_state = "UNSTABLE_SELECTION"
        recommended = "P1"

    warnings: set[str] = set()
    recommended_metrics = two_metrics if recommended == "P2" else one_metrics
    if recommended_metrics.r2_oos is None:
        warnings.add("UNDEFINED_R2")
    elif recommended_metrics.r2_oos < 0.60:
        warnings.add("BELOW_PRODUCT_R2")
    if decision_state != "CLEAR_PRACTICAL_UPLIFT" and full_candidates.two is not None:
        warnings.add("P2_NOT_RECOMMENDED")
    return ValidationResult(
        "VALIDATED",
        decision_state,
        recommended,
        one_metrics,
        two_metrics,
        uplift,
        stability,
        tuple(sorted(warnings)),
        folds,
        resolved.repetitions,
        tuple(records),
    )
