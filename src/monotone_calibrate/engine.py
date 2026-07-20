"""Deterministic finite-registry fitting for one and two monotone segments.

The public interface deliberately hides solver starts, conditional linear
algebra and certificate construction. Solver proposals are projected onto a
thousandth coefficient grid before scoring and certification; P2 branches may
differ at the raw-x breakpoint by no more than one direction-preserving grid
step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterable, Literal

import numpy as np
from scipy.optimize import lsq_linear, minimize, nnls

from .model_runtime import COEFFICIENT_DECIMAL_PLACES, FittedModel, SegmentModel
from .registry import (
    FAMILY_IDS,
    Direction,
    amplitude_bounds,
    certify_family,
    family_spec,
    feature_columns,
    parameters_from_parts,
)


@dataclass(frozen=True, slots=True)
class SegmentMetrics:
    segment_id: str
    n: int
    n_unique_x: int
    share: float
    r2: float | None
    rmse: float
    mae: float


def _quantize_parameters(parameters: dict[str, float]) -> dict[str, float]:
    quantized: dict[str, float] = {}
    for name, value in parameters.items():
        rounded = round(float(value), COEFFICIENT_DECIMAL_PLACES)
        quantized[name] = 0.0 if rounded == 0.0 else rounded
    return quantized


def _align_quantized_join(
    left: SegmentModel,
    right: SegmentModel,
    direction: Direction,
) -> SegmentModel:
    """Shift the right intercept onto the nearest direction-safe join cell."""

    join = left.x_upper
    left_value = float(left.predict_unchecked(join))
    right_value = float(right.predict_unchecked(join))
    delta = right_value - left_value
    quantum = 10.0**-COEFFICIENT_DECIMAL_PLACES
    arithmetic = 1e-12
    if direction == "increasing":
        steps = math.ceil((-delta - arithmetic) / quantum)
    elif direction == "decreasing":
        steps = math.floor((-delta + arithmetic) / quantum)
    else:
        steps = round(-delta / quantum)
    if steps == 0:
        return right
    parameters = dict(right.parameters)
    parameters["a"] = round(
        parameters["a"] + steps * quantum,
        COEFFICIENT_DECIMAL_PLACES,
    )
    if parameters["a"] == 0.0:
        parameters["a"] = 0.0
    return SegmentModel(
        right.family_id,
        right.x_lower,
        right.x_upper,
        parameters,
        right.segment_id,
    )


def _quantized_model(
    segments: tuple[SegmentModel, ...],
    direction: Direction,
) -> FittedModel:
    return FittedModel(
        segments,
        direction,
        coefficient_decimal_places=COEFFICIENT_DECIMAL_PLACES,
    )


@dataclass(frozen=True, slots=True)
class FitCandidate:
    structure: Literal["P1", "P2"]
    status: str
    model: FittedModel
    sse: float
    r2_refit: float | None
    rmse_refit: float
    mae_refit: float
    segment_metrics: tuple[SegmentMetrics, ...]
    predictions: np.ndarray

    @property
    def breakpoint(self) -> float | None:
        return self.model.breakpoint

    @property
    def segment_shares(self) -> tuple[float, ...]:
        return tuple(segment.share for segment in self.segment_metrics)


@dataclass(frozen=True, slots=True)
class CandidateSet:
    one: FitCandidate
    two: FitCandidate | None
    two_status: str
    search_trace: SearchTrace


SearchProfile = Literal["fast", "balanced", "quality", "exhaustive"]
SEARCH_POLICY_ID = "candidate-search-v1"


@dataclass(frozen=True, slots=True)
class SearchPolicy:
    """Named deterministic budget for the internal candidate search."""

    profile: SearchProfile = "fast"

    def __post_init__(self) -> None:
        if self.profile not in {"fast", "balanced", "quality", "exhaustive"}:
            raise ValueError("unknown search profile")


@dataclass(frozen=True, slots=True)
class SearchTrace:
    profile: SearchProfile
    approximate: bool
    variant_count: int
    min_segment_share: float
    max_elementary_starts: int | None
    eligible_cells: int
    coarse_cells: int
    evaluated_cells: int
    evaluated_candidates: int
    refinement_pairs: int
    termination: str
    policy_id: str = SEARCH_POLICY_ID


@dataclass(frozen=True, slots=True)
class StartOverride:
    """One locally validated replacement for a predeclared P1 solver start."""

    slot_id: str
    parameter_vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class StartSlot:
    """Public descriptor disclosed to an optional start advisor."""

    slot_id: str
    family_id: str
    direction: Direction
    default_vector: tuple[float, ...]
    bounds: tuple[tuple[float, float], ...]


def _p1_slot_id(
    family_id: str,
    region_index: int,
    direction: Direction,
    start_index: int,
) -> str:
    return f"p1:{family_id}:r{region_index:02d}:{direction}:s{start_index:02d}"


def p1_start_slots() -> tuple[StartSlot, ...]:
    """Return the finite replaceable-slot ledger for P1, never anchor starts."""

    slots: list[StartSlot] = []
    for family_id in FAMILY_IDS:
        spec = family_spec(family_id)
        for region_index, region in enumerate(spec.shape_regions):
            for start_index in region.replaceable_start_indices:
                for direction in ("increasing", "decreasing"):
                    slots.append(
                        StartSlot(
                            _p1_slot_id(family_id, region_index, direction, start_index),
                            family_id,
                            direction,
                            tuple(map(float, region.starts[start_index])),
                            region.bounds,
                        )
                    )
    return tuple(slots)


@dataclass(frozen=True, slots=True)
class FitOptions:
    min_segment_share: float = 0.40
    max_elementary_starts: int | None = None
    start_overrides: tuple[StartOverride, ...] = ()
    search_policy: SearchPolicy = field(default_factory=SearchPolicy)

    def __post_init__(self) -> None:
        if not np.isfinite(self.min_segment_share) or not 0.0 < self.min_segment_share <= 0.5:
            raise ValueError("min_segment_share must lie in (0, 0.5]")
        if self.max_elementary_starts is not None and (
            isinstance(self.max_elementary_starts, bool)
            or not isinstance(self.max_elementary_starts, int)
            or self.max_elementary_starts < 0
        ):
            raise ValueError("max_elementary_starts must be nonnegative or None")
        if not isinstance(self.search_policy, SearchPolicy):
            raise ValueError("search_policy must be a SearchPolicy")
        known_slots = {slot.slot_id: slot for slot in p1_start_slots()}
        normalized: list[StartOverride] = []
        seen: set[str] = set()
        for raw_override in self.start_overrides:
            try:
                slot_id = str(raw_override.slot_id)
                vector = tuple(float(value) for value in raw_override.parameter_vector)
                slot = known_slots[slot_id]
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid or unknown start_overrides slot") from exc
            if slot_id in seen:
                raise ValueError("start_overrides contains a duplicate slot")
            if (
                len(vector) != len(slot.bounds)
                or not all(np.isfinite(value) for value in vector)
                or not all(
                    lower <= value <= upper
                    for value, (lower, upper) in zip(vector, slot.bounds, strict=True)
                )
            ):
                raise ValueError("start_overrides parameter vector is outside its registry slot")
            seen.add(slot_id)
            normalized.append(StartOverride(slot_id, vector))
        object.__setattr__(self, "start_overrides", tuple(normalized))


@dataclass(frozen=True, slots=True)
class _Variant:
    family_id: str
    shape: tuple[float, ...]


def _as_problem(x: Iterable[float], y: Iterable[float]) -> tuple[np.ndarray, np.ndarray]:
    xv = np.asarray(tuple(x), dtype=np.float64)
    yv = np.asarray(tuple(y), dtype=np.float64)
    if xv.ndim != 1 or yv.ndim != 1 or xv.size != yv.size:
        raise ValueError("x and y must be equally sized one-dimensional arrays")
    if xv.size < 4 or not np.all(np.isfinite(xv)) or not np.all(np.isfinite(yv)):
        raise ValueError("fitting requires at least four finite observations")
    xv = np.where(xv == 0.0, 0.0, xv)
    if np.unique(xv).size < 3 or not float(np.max(xv)) > float(np.min(xv)):
        raise ValueError("fitting requires at least three distinct x values")
    order = np.lexsort((yv, xv))
    return xv[order], yv[order]


def _metric_values(y: np.ndarray, prediction: np.ndarray) -> tuple[float, float | None, float, float]:
    residual = y - prediction
    sse = float(np.dot(residual, residual))
    rmse = float(math.sqrt(sse / y.size))
    mae = float(np.mean(np.abs(residual)))
    centered = y - float(np.mean(y))
    denominator = float(np.dot(centered, centered))
    r2 = None if denominator == 0.0 else float(1.0 - sse / denominator)
    return sse, r2, rmse, mae


def _bernstein(t: np.ndarray, degree: int) -> np.ndarray:
    if degree == 0:
        return np.ones((t.size, 1), dtype=np.float64)
    columns = []
    for index in range(degree + 1):
        columns.append(
            math.comb(degree, index)
            * np.power(t, index)
            * np.power(1.0 - t, degree - index)
        )
    return np.column_stack(columns)


def _controls_to_parameters(family_id: str, controls: np.ndarray) -> dict[str, float]:
    c = np.asarray(controls, dtype=np.float64)
    if family_id == "constant_v1":
        return {"a": float(c[0])}
    if family_id == "poly1_v1":
        return {"a": float(c[0]), "b": float(c[1] - c[0])}
    if family_id == "poly2_v1":
        return {
            "a": float(c[0]),
            "b": float(2.0 * (c[1] - c[0])),
            "c": float(c[0] - 2.0 * c[1] + c[2]),
        }
    if family_id == "poly3_v1":
        return {
            "a": float(c[0]),
            "b": float(3.0 * (c[1] - c[0])),
            "c": float(3.0 * (c[0] - 2.0 * c[1] + c[2])),
            "d": float(-c[0] + 3.0 * c[1] - 3.0 * c[2] + c[3]),
        }
    raise ValueError(f"not a polynomial family: {family_id}")


def _polynomial_design(t: np.ndarray, degree: int, direction: Direction) -> np.ndarray:
    """Intercept plus nonnegative Bernstein control-point increments."""

    sign = 1.0 if direction == "increasing" else -1.0
    basis = _bernstein(t, degree)
    increments = [sign * np.sum(basis[:, index:], axis=1) for index in range(1, degree + 1)]
    return np.column_stack((np.ones(t.size), *increments))


def _fit_linear_bounded(
    design: np.ndarray,
    y: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
) -> tuple[float, np.ndarray, np.ndarray] | None:
    try:
        result = lsq_linear(
            design,
            y,
            bounds=(lower, upper),
            method="trf",
            tol=1e-11,
            lsmr_tol=None,
            max_iter=250,
        )
    except (ValueError, np.linalg.LinAlgError):
        return None
    prediction = design @ result.x
    if not result.success or not np.all(np.isfinite(prediction)):
        return None
    residual = y - prediction
    return float(np.dot(residual, residual)), np.asarray(result.x), prediction


def _fit_join_nonnegative(
    design: np.ndarray,
    y: np.ndarray,
) -> tuple[float, np.ndarray, np.ndarray] | None:
    """Solve an unbounded join plus nonnegative branch increments efficiently."""

    features = design[:, 1:]
    if features.shape[1] == 0:
        return None
    feature_mean = np.mean(features, axis=0)
    y_mean = float(np.mean(y))
    centered_features = features - feature_mean
    centered_y = y - y_mean
    try:
        increments, _ = nnls(centered_features, centered_y, maxiter=250)
    except (RuntimeError, ValueError, np.linalg.LinAlgError):
        return _fit_linear_bounded(
            design,
            y,
            np.r_[-np.inf, np.zeros(features.shape[1])],
            np.full(1 + features.shape[1], np.inf),
        )
    join = y_mean - float(np.dot(feature_mean, increments))
    coefficients = np.r_[join, increments]
    prediction = design @ coefficients
    if not np.all(np.isfinite(prediction)):
        return None
    residual = y - prediction
    return float(np.dot(residual, residual)), coefficients, prediction


def _elementary_profile(
    family_id: str,
    t: np.ndarray,
    y: np.ndarray,
    direction: Direction,
    shape: tuple[float, ...],
) -> tuple[float, dict[str, float], np.ndarray] | None:
    feature = feature_columns(family_id, t, shape)
    design = np.column_stack((np.ones(t.size), feature))
    amplitude_lower, amplitude_upper = amplitude_bounds(family_id, shape, direction)
    solved = _fit_linear_bounded(
        design,
        y,
        np.asarray([-np.inf, amplitude_lower]),
        np.asarray([np.inf, amplitude_upper]),
    )
    if solved is None:
        return None
    sse, linear, prediction = solved
    parameters = parameters_from_parts(family_id, linear[0], linear[1:], shape)
    if not certify_family(family_id, parameters, direction).valid:
        return None
    return sse, parameters, prediction


def _shape_variants(
    family_id: str,
    maximum: int | None = None,
) -> tuple[tuple[float, ...], ...]:
    spec = family_spec(family_id)
    if not spec.shape_parameter_names:
        return ((),)
    starts: list[tuple[float, ...]] = []
    for region in spec.shape_regions:
        starts.extend(tuple(map(float, start)) for start in region.starts)
    unique = tuple(dict.fromkeys(starts))
    return unique if maximum is None else unique[:maximum]


def _fit_one(x: np.ndarray, y: np.ndarray, options: FitOptions) -> FitCandidate:
    lower_x = float(x[0])
    upper_x = float(x[-1])
    t = (x - lower_x) / (upper_x - lower_x)
    contenders: list[tuple[float, int, int, FittedModel, np.ndarray]] = []
    start_overrides = {override.slot_id: override.parameter_vector for override in options.start_overrides}

    constant = round(float(np.mean(y)), COEFFICIENT_DECIMAL_PLACES)
    constant_model = _quantized_model(
        (SegmentModel("constant_v1", lower_x, upper_x, {"a": constant}),),
        "flat",
    )
    constant_prediction = np.asarray(constant_model.predict(x))
    constant_sse = float(np.sum((y - constant_prediction) ** 2))
    contenders.append((constant_sse, 0, 0, constant_model, constant_prediction))

    for direction in ("increasing", "decreasing"):
        for registry_index, family_id in enumerate(FAMILY_IDS):
            spec = family_spec(family_id)
            if family_id == "constant_v1":
                continue
            if x.size < spec.free_parameter_count + 1 or np.unique(x).size < spec.free_parameter_count:
                continue
            if spec.kind == "polynomial":
                design = _polynomial_design(t, spec.polynomial_degree, direction)
                solved = _fit_linear_bounded(
                    design,
                    y,
                    np.r_[-np.inf, np.zeros(spec.polynomial_degree)],
                    np.full(spec.polynomial_degree + 1, np.inf),
                )
                if solved is None:
                    continue
                sse, coefficients, _ = solved
                sign = 1.0 if direction == "increasing" else -1.0
                controls = coefficients[0] + sign * np.r_[0.0, np.cumsum(coefficients[1:])]
                parameters = _quantize_parameters(
                    _controls_to_parameters(family_id, controls)
                )
                if not certify_family(family_id, parameters, direction).valid:
                    continue
                try:
                    model = _quantized_model(
                        (SegmentModel(family_id, lower_x, upper_x, parameters),),
                        direction,
                    )
                except ValueError:
                    continue
                prediction = np.asarray(model.predict(x))
                sse = float(np.sum((y - prediction) ** 2))
                contenders.append((sse, spec.polynomial_degree, registry_index, model, prediction))
                continue

            for region_index, region in enumerate(spec.shape_regions):
                for start_index, default_start in enumerate(region.starts):
                    slot_id = _p1_slot_id(family_id, region_index, direction, start_index)
                    start = start_overrides.get(slot_id, tuple(map(float, default_start)))
                    bounds = region.bounds

                    def objective(vector: np.ndarray) -> float:
                        fitted = _elementary_profile(
                            family_id,
                            t,
                            y,
                            direction,
                            tuple(map(float, vector)),
                        )
                        return np.inf if fitted is None else fitted[0]

                    optimized = minimize(
                        objective,
                        np.asarray(start, dtype=np.float64),
                        method="L-BFGS-B",
                        bounds=bounds,
                        options={"maxiter": 120, "ftol": 1e-12},
                    )
                    shapes = [tuple(map(float, start))]
                    if optimized.success and np.all(np.isfinite(optimized.x)):
                        shapes.append(tuple(map(float, optimized.x)))
                    for shape in shapes:
                        fitted = _elementary_profile(family_id, t, y, direction, shape)
                        if fitted is None:
                            continue
                        _, parameters, _ = fitted
                        parameters = _quantize_parameters(parameters)
                        if not certify_family(family_id, parameters, direction).valid:
                            continue
                        try:
                            model = _quantized_model(
                                (SegmentModel(family_id, lower_x, upper_x, parameters),),
                                direction,
                            )
                        except ValueError:
                            continue
                        prediction = np.asarray(model.predict(x))
                        sse = float(np.sum((y - prediction) ** 2))
                        contenders.append((sse, 4, registry_index, model, prediction))

    sse, _, _, model, prediction = min(
        contenders,
        key=lambda item: (round(item[0], 12), item[1], item[2], item[3].model_instance_hash),
    )
    sse, r2, rmse, mae = _metric_values(y, prediction)
    metrics = SegmentMetrics("single", y.size, np.unique(x).size, 1.0, r2, rmse, mae)
    return FitCandidate("P1", "VALID", model, sse, r2, rmse, mae, (metrics,), prediction)


def _eligible_cells(x: np.ndarray, minimum_share: float) -> tuple[tuple[int, float], ...]:
    unique, counts = np.unique(x, return_counts=True)
    cumulative = np.cumsum(counts)
    cells: list[tuple[int, float]] = []
    for index in range(unique.size - 1):
        left_n = int(cumulative[index])
        share = left_n / x.size
        right_n = x.size - left_n
        if (
            minimum_share <= share <= 1.0 - minimum_share
            and left_n >= 3
            and right_n >= 3
            and index + 1 >= 2
            and unique.size - index - 1 >= 2
        ):
            lo = float(unique[index])
            hi = float(unique[index + 1])
            breakpoint = round(
                lo + (hi - lo) / 2.0,
                COEFFICIENT_DECIMAL_PLACES,
            )
            if breakpoint == 0.0:
                breakpoint = 0.0
            if lo <= breakpoint < hi:
                cells.append((left_n, breakpoint))
    return tuple(cells)


def _normalized_atom(variant: _Variant, t: np.ndarray) -> np.ndarray:
    raw = feature_columns(variant.family_id, t, variant.shape)[:, 0]
    endpoints = feature_columns(variant.family_id, np.asarray([0.0, 1.0]), variant.shape)[:, 0]
    span = float(endpoints[1] - endpoints[0])
    if not np.isfinite(span) or abs(span) <= 1e-14:
        raise ValueError("non-identifiable elementary atom")
    return (raw - endpoints[0]) / span


def _variants(options: FitOptions) -> tuple[_Variant, ...]:
    values: list[_Variant] = []
    for family_id in FAMILY_IDS:
        spec = family_spec(family_id)
        if spec.kind == "polynomial" or family_id == "constant_v1":
            values.append(_Variant(family_id, ()))
        else:
            values.extend(
                _Variant(family_id, shape)
                # P2 profiles have fixed nonlinear shapes and no shape
                # optimizer.  Advice is therefore deliberately excluded:
                # it may initialize P1's optimizer, never expand P2's model
                # grid or the OOF procedure.
                for shape in _shape_variants(family_id, options.max_elementary_starts)
            )
    return tuple(values)


def _branch_columns(variant: _Variant, t: np.ndarray, side: str, sign: float) -> np.ndarray:
    spec = family_spec(variant.family_id)
    if variant.family_id == "constant_v1":
        return np.empty((t.size, 0), dtype=np.float64)
    if spec.kind == "polynomial":
        basis = _bernstein(t, spec.polynomial_degree)
        if side == "left":
            columns = [-sign * np.sum(basis[:, :index], axis=1) for index in range(1, spec.polynomial_degree + 1)]
        else:
            columns = [sign * np.sum(basis[:, index:], axis=1) for index in range(1, spec.polynomial_degree + 1)]
        return np.column_stack(columns)
    atom = _normalized_atom(variant, t)
    column = sign * (atom - 1.0) if side == "left" else sign * atom
    return column.reshape(-1, 1)


def _branch_parameters(
    variant: _Variant,
    side: str,
    join: float,
    increments: np.ndarray,
    sign: float,
) -> dict[str, float]:
    spec = family_spec(variant.family_id)
    if variant.family_id == "constant_v1":
        return {"a": float(join)}
    if spec.kind == "polynomial":
        if side == "left":
            controls = np.asarray(
                [join - sign * float(np.sum(increments[index:])) for index in range(spec.polynomial_degree)]
                + [join]
            )
        else:
            controls = np.asarray(
                [join] + [join + sign * float(np.sum(increments[:index])) for index in range(1, spec.polynomial_degree + 1)]
            )
        return _controls_to_parameters(variant.family_id, controls)
    endpoint_t = np.asarray([0.0, 1.0])
    raw_endpoints = feature_columns(variant.family_id, endpoint_t, variant.shape)[:, 0]
    raw_span = float(raw_endpoints[1] - raw_endpoints[0])
    q = float(increments[0])
    amplitude = sign * q / raw_span
    intercept = join - amplitude * (raw_endpoints[1] if side == "left" else raw_endpoints[0])
    return parameters_from_parts(variant.family_id, intercept, (amplitude,), variant.shape)


@dataclass(frozen=True, slots=True)
class _SearchBudget:
    coarse_cells: int
    refinement_pairs: int
    refinement_grid: int
    final_span: int


@dataclass(frozen=True, slots=True)
class _PairBest:
    key: tuple[float, int, int]
    cell_index: int


@dataclass(frozen=True, slots=True)
class _TwoIncumbent:
    sse: float
    complexity: int
    registry_order: int
    model: FittedModel
    prediction: np.ndarray
    left_n: int

    @property
    def key(self) -> tuple[float, int, int, str]:
        return (
            round(self.sse, 12),
            self.complexity,
            self.registry_order,
            self.model.model_instance_hash,
        )


_SEARCH_BUDGETS: dict[SearchProfile, _SearchBudget] = {
    "fast": _SearchBudget(9, 2, 9, 8),
    "balanced": _SearchBudget(33, 8, 9, 6),
    "quality": _SearchBudget(65, 12, 11, 4),
    "exhaustive": _SearchBudget(2**63 - 1, 0, 0, 0),
}


def _uniform_cell_indices(total: int, maximum: int) -> tuple[int, ...]:
    if total <= maximum:
        return tuple(range(total))
    return tuple(
        map(
            int,
            np.unique(np.linspace(0, total - 1, maximum, dtype=np.int64)),
        )
    )


def _fit_two(
    x: np.ndarray,
    y: np.ndarray,
    options: FitOptions,
) -> tuple[FitCandidate | None, str, SearchTrace]:
    profile = options.search_policy.profile
    variants = _variants(options)
    cells = _eligible_cells(x, options.min_segment_share)
    if not cells:
        return (
            None,
            "NO_BALANCED_SPLIT",
            SearchTrace(
                profile,
                profile != "exhaustive",
                len(variants),
                options.min_segment_share,
                options.max_elementary_starts,
                0,
                0,
                0,
                0,
                0,
                "NO_BALANCED_SPLIT",
            ),
        )
    all_pairs = tuple(
        (direction, left_variant, right_variant)
        for direction in ("increasing", "decreasing")
        for left_variant in variants
        for right_variant in variants
        if not (left_variant.family_id == right_variant.family_id == "constant_v1")
    )
    lower_x = float(x[0])
    upper_x = float(x[-1])
    best: _TwoIncumbent | None = None
    pair_scores: dict[tuple[Direction, _Variant, _Variant], _PairBest] = {}
    evaluated_pair_cells: dict[tuple[Direction, _Variant, _Variant], set[int]] = {}
    evaluated_candidates = 0
    evaluated_cells: set[int] = set()

    def evaluate_cells(
        cell_indices: Iterable[int],
        pairs: tuple[tuple[Direction, _Variant, _Variant], ...],
    ) -> None:
        nonlocal best, evaluated_candidates
        pairs_by_direction = {
            direction: tuple(pair for pair in pairs if pair[0] == direction)
            for direction in ("increasing", "decreasing")
        }
        for cell_index in cell_indices:
            evaluated_cells.add(cell_index)
            left_n, breakpoint = cells[cell_index]
            left_x, right_x = x[:left_n], x[left_n:]
            left_t = (left_x - lower_x) / (breakpoint - lower_x)
            right_t = (right_x - breakpoint) / (upper_x - breakpoint)
            for direction in ("increasing", "decreasing"):
                sign = 1.0 if direction == "increasing" else -1.0
                left_cache: dict[_Variant, np.ndarray | None] = {}
                right_cache: dict[_Variant, np.ndarray | None] = {}
                for _, left_variant, right_variant in pairs_by_direction[direction]:
                    pair = (direction, left_variant, right_variant)
                    pair_cells = evaluated_pair_cells.setdefault(pair, set())
                    if cell_index in pair_cells:
                        continue
                    pair_cells.add(cell_index)
                    left_spec = family_spec(left_variant.family_id)
                    right_spec = family_spec(right_variant.family_id)
                    if left_x.size < max(3, left_spec.free_parameter_count + 1):
                        continue
                    if right_x.size < max(3, right_spec.free_parameter_count + 1):
                        continue
                    if left_variant not in left_cache:
                        try:
                            left_cache[left_variant] = _branch_columns(
                                left_variant,
                                left_t,
                                "left",
                                sign,
                            )
                        except ValueError:
                            left_cache[left_variant] = None
                    if right_variant not in right_cache:
                        try:
                            right_cache[right_variant] = _branch_columns(
                                right_variant,
                                right_t,
                                "right",
                                sign,
                            )
                        except ValueError:
                            right_cache[right_variant] = None
                    left_columns = left_cache[left_variant]
                    right_columns = right_cache[right_variant]
                    if left_columns is None or right_columns is None:
                        continue
                    left_width = left_columns.shape[1]
                    right_width = right_columns.shape[1]
                    design = np.zeros((x.size, 1 + left_width + right_width), dtype=np.float64)
                    design[:, 0] = 1.0
                    design[:left_n, 1 : 1 + left_width] = left_columns
                    design[left_n:, 1 + left_width :] = right_columns
                    evaluated_candidates += 1
                    solved = _fit_join_nonnegative(design, y)
                    if solved is None:
                        continue
                    _, coefficients, _ = solved
                    complexity = left_spec.free_parameter_count + right_spec.free_parameter_count + 1
                    registry_order = FAMILY_IDS.index(left_variant.family_id) * len(FAMILY_IDS) + FAMILY_IDS.index(
                        right_variant.family_id
                    )
                    join = float(coefficients[0])
                    left_parameters = _quantize_parameters(
                        _branch_parameters(
                            left_variant,
                            "left",
                            join,
                            coefficients[1 : 1 + left_width],
                            sign,
                        )
                    )
                    right_parameters = _quantize_parameters(
                        _branch_parameters(
                            right_variant,
                            "right",
                            join,
                            coefficients[1 + left_width :],
                            sign,
                        )
                    )
                    try:
                        left_segment = SegmentModel(
                            left_variant.family_id,
                            lower_x,
                            breakpoint,
                            left_parameters,
                            "left",
                        )
                        right_segment = SegmentModel(
                            right_variant.family_id,
                            breakpoint,
                            upper_x,
                            right_parameters,
                            "right",
                        )
                        right_segment = _align_quantized_join(
                            left_segment,
                            right_segment,
                            direction,
                        )
                        model = _quantized_model(
                            (
                                left_segment,
                                right_segment,
                            ),
                            direction,
                        )
                    except ValueError:
                        continue
                    prediction = np.asarray(model.predict(x))
                    if not np.all(np.isfinite(prediction)):
                        continue
                    sse = float(np.sum((y - prediction) ** 2))
                    pair_score = (round(sse, 12), complexity, registry_order)
                    if pair not in pair_scores or pair_score < pair_scores[pair].key:
                        pair_scores[pair] = _PairBest(pair_score, cell_index)
                    if best is not None and pair_score > best.key[:3]:
                        continue
                    contender = _TwoIncumbent(
                        sse,
                        complexity,
                        registry_order,
                        model,
                        prediction,
                        left_n,
                    )
                    if best is None or contender.key < best.key:
                        best = contender

    budget = _SEARCH_BUDGETS[profile]
    coarse_indices = _uniform_cell_indices(len(cells), budget.coarse_cells)
    evaluate_cells(coarse_indices, all_pairs)
    refinement_pairs = 0
    if profile != "exhaustive" and len(coarse_indices) < len(cells) and pair_scores:
        ranked_pairs = tuple(
            (pair, pair_best.cell_index)
            for pair, pair_best in sorted(
                pair_scores.items(),
                key=lambda item: (
                    item[1].key,
                    item[0][0],
                    item[0][1].family_id,
                    item[0][1].shape,
                    item[0][2].family_id,
                    item[0][2].shape,
                ),
            )[: budget.refinement_pairs]
        )
        refinement_pairs = len(ranked_pairs)
        for pair, anchor in ranked_pairs:
            anchor_position = coarse_indices.index(anchor)
            lower = coarse_indices[max(0, anchor_position - 1)]
            upper = coarse_indices[min(len(coarse_indices) - 1, anchor_position + 1)]
            while upper - lower > budget.final_span:
                probes = tuple(
                    lower + offset
                    for offset in _uniform_cell_indices(
                        upper - lower + 1,
                        budget.refinement_grid,
                    )
                )
                evaluate_cells(probes, (pair,))
                best_cell = pair_scores[pair].cell_index
                best_position = min(
                    range(len(probes)),
                    key=lambda index: (abs(probes[index] - best_cell), index),
                )
                lower = probes[max(0, best_position - 1)]
                upper = probes[min(len(probes) - 1, best_position + 1)]
            evaluate_cells(range(lower, upper + 1), (pair,))
    if best is None:
        return (
            None,
            "NO_VALID_TWO_MODEL",
            SearchTrace(
                profile,
                profile != "exhaustive",
                len(variants),
                options.min_segment_share,
                options.max_elementary_starts,
                len(cells),
                len(coarse_indices),
                len(evaluated_cells),
                evaluated_candidates,
                refinement_pairs,
                "NO_VALID_TWO_MODEL",
            ),
        )
    sse = best.sse
    model = best.model
    prediction = best.prediction
    left_n = best.left_n
    sse, r2, rmse, mae = _metric_values(y, prediction)
    metrics: list[SegmentMetrics] = []
    for segment_id, subset in (("left", slice(0, left_n)), ("right", slice(left_n, x.size))):
        _, local_r2, local_rmse, local_mae = _metric_values(y[subset], prediction[subset])
        metrics.append(
            SegmentMetrics(
                segment_id,
                y[subset].size,
                np.unique(x[subset]).size,
                float(y[subset].size / y.size),
                local_r2,
                local_rmse,
                local_mae,
            )
        )
    trace = SearchTrace(
        profile,
        profile != "exhaustive",
        len(variants),
        options.min_segment_share,
        options.max_elementary_starts,
        len(cells),
        len(coarse_indices),
        len(evaluated_cells),
        evaluated_candidates,
        refinement_pairs,
        "EXHAUSTIVE_SPACE" if profile == "exhaustive" else "BUDGET_COMPLETE",
    )
    return (
        FitCandidate("P2", "VALID", model, sse, r2, rmse, mae, tuple(metrics), prediction),
        "VALID",
        trace,
    )


def fit_candidates(
    x: Iterable[float],
    y: Iterable[float],
    options: FitOptions | None = None,
) -> CandidateSet:
    """Fit the minimum one-function model first, then an optional balanced P2."""

    resolved = FitOptions() if options is None else options
    xv, yv = _as_problem(x, y)
    one = _fit_one(xv, yv, resolved)
    two, two_status, search_trace = _fit_two(xv, yv, resolved)
    if two is None and one.model.direction == "flat" and two_status == "NO_VALID_TWO_MODEL":
        two_status = "COLLAPSED_TO_P1"
    if two is not None:
        grid = np.linspace(float(xv[0]), float(xv[-1]), 1001)
        difference = np.max(
            np.abs(np.asarray(two.model.predict(grid)) - np.asarray(one.model.predict(grid)))
        )
        scale = max(1.0, float(np.max(yv) - np.min(yv)), float(np.max(np.abs(yv))))
        if difference <= 1e-10 * scale:
            two = None
            two_status = "COLLAPSED_TO_P1"
    return CandidateSet(
        one=one,
        two=two,
        two_status=two_status,
        search_trace=search_trace,
    )
