"""Finite, versioned registry of functions allowed by the fitting engine.

The registry is deliberately code, not a symbolic-expression grammar.  A
model can therefore only contain one of the audited evaluators below and a
polynomial of degree greater than three is not representable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

import numpy as np
from scipy.special import expit


Direction = Literal["increasing", "decreasing", "flat"]
FamilyKind = Literal["constant", "polynomial", "elementary"]

REGISTRY_VERSION = "registry-v1"
POLYNOMIAL_DEGREE_MAX = 3


@dataclass(frozen=True, slots=True)
class ShapeRegion:
    """One connected box for nonlinear shape parameters and fixed starts."""

    bounds: tuple[tuple[float, float], ...]
    starts: tuple[tuple[float, ...], ...]
    replaceable_start_indices: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class FamilySpec:
    """Immutable registry entry.

    ``linear_parameter_names`` are fitted conditionally for fixed nonlinear
    shape.  The first linear parameter is always the intercept for P1; P2
    replaces both branch intercepts by one shared join value.
    """

    family_id: str
    canonical_ast_id: str
    kind: FamilyKind
    parameter_names: tuple[str, ...]
    linear_parameter_names: tuple[str, ...]
    shape_parameter_names: tuple[str, ...] = ()
    shape_regions: tuple[ShapeRegion, ...] = ()
    polynomial_degree: int = 0
    supports_p2: bool = True

    @property
    def free_parameter_count(self) -> int:
        return len(self.parameter_names)

    @property
    def nonlinear_parameter_count(self) -> int:
        return len(self.shape_parameter_names)


_FAMILIES = (
    FamilySpec(
        "constant_v1",
        "constant.a.v1",
        "constant",
        ("a",),
        ("a",),
    ),
    FamilySpec(
        "poly1_v1",
        "poly1.a_plus_b_t.v1",
        "polynomial",
        ("a", "b"),
        ("a", "b"),
        polynomial_degree=1,
    ),
    FamilySpec(
        "poly2_v1",
        "poly2.a_plus_b_t_plus_c_t2.v1",
        "polynomial",
        ("a", "b", "c"),
        ("a", "b", "c"),
        polynomial_degree=2,
    ),
    FamilySpec(
        "poly3_v1",
        "poly3.a_plus_b_t_plus_c_t2_plus_d_t3.v1",
        "polynomial",
        ("a", "b", "c", "d"),
        ("a", "b", "c", "d"),
        polynomial_degree=3,
    ),
    FamilySpec(
        "exp_affine_v1",
        "exp_affine.a_plus_b_exp_k_t.v1",
        "elementary",
        ("a", "b", "k"),
        ("a", "b"),
        ("k",),
        (
            ShapeRegion(((-12.0, -0.10),), ((-11.0,), (-1.0,), (-6.05,)), (2,)),
            ShapeRegion(((0.10, 12.0),), ((1.0,), (11.0,), (6.05,)), (2,)),
        ),
    ),
    FamilySpec(
        "log_shift_v1",
        "log_shift.a_plus_b_log_t_plus_d.v1",
        "elementary",
        ("a", "b", "d"),
        ("a", "b"),
        ("d",),
        (ShapeRegion(((0.02, 20.0),), ((0.05,), (15.0,), (1.0,)), (2,)),),
    ),
    FamilySpec(
        "reciprocal_shift_pos_v1",
        "reciprocal_shift_pos.a_plus_b_over_t_plus_d.v1",
        "elementary",
        ("a", "b", "d"),
        ("a", "b"),
        ("d",),
        (ShapeRegion(((0.05, 20.0),), ((0.10,), (15.0,), (1.0,)), (2,)),),
    ),
    FamilySpec(
        "logistic_v1",
        "logistic.a_plus_b_sigma_k_t_minus_m.v1",
        "elementary",
        ("a", "b", "k", "m"),
        ("a", "b"),
        ("k", "m"),
        (
            ShapeRegion(
                ((0.25, 20.0), (0.0, 1.0)),
                ((0.5, 0.1), (10.0, 0.5), (19.0, 0.9), (5.0, 0.35), (15.0, 0.65)),
                (3, 4),
            ),
        ),
    ),
)

FAMILY_REGISTRY: Mapping[str, FamilySpec] = {family.family_id: family for family in _FAMILIES}
FAMILY_IDS: tuple[str, ...] = tuple(family.family_id for family in _FAMILIES)


@dataclass(frozen=True, slots=True)
class FamilyCertificate:
    family_id: str
    direction: Direction
    valid: bool
    domain_valid: bool
    finite: bool
    monotone: bool
    signed_derivative_margin: float | None
    checked_points: tuple[float, ...]
    polynomial_degree: int
    reason_codes: tuple[str, ...]


def family_spec(family_id: str) -> FamilySpec:
    """Return a registry entry, rejecting unknown/external formula names."""

    try:
        return FAMILY_REGISTRY[family_id]
    except KeyError as exc:
        raise ValueError(f"unknown {REGISTRY_VERSION} family: {family_id!r}") from exc


def evaluate_family(
    family_id: str,
    t: np.ndarray | float,
    parameters: Mapping[str, float],
) -> np.ndarray:
    """Evaluate one canonical registry AST in its local coordinate."""

    family_spec(family_id)
    local = np.asarray(t, dtype=np.float64)
    a = float(parameters["a"])
    if family_id == "constant_v1":
        return np.full_like(local, a, dtype=np.float64)

    b = float(parameters["b"])
    if family_id == "poly1_v1":
        return a + b * local
    if family_id == "poly2_v1":
        return a + local * (b + float(parameters["c"]) * local)
    if family_id == "poly3_v1":
        return a + local * (
            b + local * (float(parameters["c"]) + float(parameters["d"]) * local)
        )
    if family_id == "exp_affine_v1":
        return a + b * np.exp(float(parameters["k"]) * local)
    if family_id == "log_shift_v1":
        return a + b * np.log(local + float(parameters["d"]))
    if family_id == "reciprocal_shift_pos_v1":
        return a + b / (local + float(parameters["d"]))
    if family_id == "logistic_v1":
        return a + b * expit(float(parameters["k"]) * (local - float(parameters["m"])))
    raise AssertionError("registry and evaluator are out of sync")


def feature_columns(
    family_id: str,
    t: np.ndarray,
    shape: tuple[float, ...] = (),
) -> np.ndarray:
    """Return non-intercept conditional-linear columns for a family."""

    local = np.asarray(t, dtype=np.float64)
    spec = family_spec(family_id)
    if spec.kind == "constant":
        return np.empty((local.size, 0), dtype=np.float64)
    if spec.kind == "polynomial":
        return np.column_stack(tuple(local**power for power in range(1, spec.polynomial_degree + 1)))
    if len(shape) != len(spec.shape_parameter_names):
        raise ValueError(f"{family_id} requires {len(spec.shape_parameter_names)} shape values")
    if family_id == "exp_affine_v1":
        column = np.exp(shape[0] * local)
    elif family_id == "log_shift_v1":
        column = np.log(local + shape[0])
    elif family_id == "reciprocal_shift_pos_v1":
        column = 1.0 / (local + shape[0])
    elif family_id == "logistic_v1":
        column = expit(shape[0] * (local - shape[1]))
    else:  # pragma: no cover - guarded by the finite registry
        raise AssertionError("registry and feature builder are out of sync")
    return np.asarray(column, dtype=np.float64).reshape(-1, 1)


def parameters_from_parts(
    family_id: str,
    intercept: float,
    linear_shape: np.ndarray | tuple[float, ...],
    nonlinear_shape: tuple[float, ...] = (),
) -> dict[str, float]:
    """Assemble named canonical parameters from conditional fit parts."""

    spec = family_spec(family_id)
    linear = tuple(float(value) for value in linear_shape)
    if len(linear) != len(spec.linear_parameter_names) - 1:
        raise ValueError(f"wrong linear parameter count for {family_id}")
    if len(nonlinear_shape) != len(spec.shape_parameter_names):
        raise ValueError(f"wrong nonlinear parameter count for {family_id}")
    values = {"a": float(intercept)}
    values.update(zip(spec.linear_parameter_names[1:], linear, strict=True))
    values.update(zip(spec.shape_parameter_names, map(float, nonlinear_shape), strict=True))
    return values


def signed_derivative_minimum(
    family_id: str,
    parameters: Mapping[str, float],
    direction: Direction,
) -> tuple[float, tuple[float, ...]]:
    """Compute the analytic minimum of ``s * df/dt`` on ``[0, 1]``."""

    if direction == "flat":
        return (0.0, (0.0, 1.0)) if family_id == "constant_v1" else (-np.inf, (0.0, 1.0))
    sign = 1.0 if direction == "increasing" else -1.0
    if family_id == "constant_v1":
        return 0.0, (0.0, 1.0)

    b = float(parameters["b"])
    if family_id == "poly1_v1":
        return sign * b, (0.0, 1.0)
    if family_id == "poly2_v1":
        c = float(parameters["c"])
        values = (sign * b, sign * (b + 2.0 * c))
        return min(values), (0.0, 1.0)
    if family_id == "poly3_v1":
        c = float(parameters["c"])
        d = float(parameters["d"])
        points = [0.0, 1.0]
        if d != 0.0:
            vertex = -c / (3.0 * d)
            if 0.0 < vertex < 1.0:
                points.append(float(vertex))
        values = tuple(sign * (b + 2.0 * c * point + 3.0 * d * point**2) for point in points)
        return min(values), tuple(points)
    if family_id == "exp_affine_v1":
        k = float(parameters["k"])
        values = (sign * b * k, sign * b * k * float(np.exp(k)))
        return min(values), (0.0, 1.0)
    if family_id == "log_shift_v1":
        shift = float(parameters["d"])
        values = (sign * b / shift, sign * b / (1.0 + shift))
        return min(values), (0.0, 1.0)
    if family_id == "reciprocal_shift_pos_v1":
        shift = float(parameters["d"])
        values = (-sign * b / shift**2, -sign * b / (1.0 + shift) ** 2)
        return min(values), (0.0, 1.0)
    if family_id == "logistic_v1":
        k = float(parameters["k"])
        midpoint = float(parameters["m"])
        sigma0 = float(expit(-k * midpoint))
        sigma1 = float(expit(k * (1.0 - midpoint)))
        values = (
            sign * b * k * sigma0 * (1.0 - sigma0),
            sign * b * k * sigma1 * (1.0 - sigma1),
        )
        return min(values), (0.0, 1.0)
    raise AssertionError("registry and derivative certificate are out of sync")


def certify_family(
    family_id: str,
    parameters: Mapping[str, float],
    direction: Direction,
) -> FamilyCertificate:
    """Independently certify domain, finiteness and monotonicity on ``[0,1]``."""

    spec = family_spec(family_id)
    reasons: list[str] = []
    expected = set(spec.parameter_names)
    finite = set(parameters) == expected and all(np.isfinite(float(value)) for value in parameters.values())
    if not finite:
        reasons.append("NONFINITE_OR_MISSING_PARAMETER")

    domain_valid = finite
    if finite and family_id in {"log_shift_v1", "reciprocal_shift_pos_v1"}:
        domain_valid = float(parameters["d"]) > 0.0
    elif finite and family_id == "logistic_v1":
        domain_valid = float(parameters["k"]) > 0.0
    elif finite and family_id == "exp_affine_v1":
        k = float(parameters["k"])
        domain_valid = -12.0 <= k <= -0.10 or 0.10 <= k <= 12.0
    if not domain_valid:
        reasons.append("DOMAIN_FAILURE")

    checked_points: tuple[float, ...] = ()
    margin: float | None = None
    monotone = False
    if finite and domain_valid:
        try:
            margin, checked_points = signed_derivative_minimum(family_id, parameters, direction)
            probe = evaluate_family(family_id, np.asarray(checked_points, dtype=np.float64), parameters)
            finite = bool(np.all(np.isfinite(probe)) and np.isfinite(margin))
            # The allowance is only a floating-point roundoff envelope for an
            # analytic certificate; it is not a sampled monotonicity test.
            parameter_scale = max(1.0, *(abs(float(value)) for value in parameters.values()))
            roundoff = 128.0 * np.finfo(np.float64).eps * parameter_scale
            monotone = finite and margin >= -roundoff
        except (FloatingPointError, OverflowError, ValueError, ZeroDivisionError):
            finite = False
    if not finite:
        reasons.append("NONFINITE_EVALUATION")
    if finite and domain_valid and not monotone:
        reasons.append("SHAPE_FAILURE")
    if direction == "flat" and family_id != "constant_v1":
        monotone = False
        reasons.append("NONCONSTANT_FLAT_DIRECTION")

    valid = finite and domain_valid and monotone and not reasons
    return FamilyCertificate(
        family_id=family_id,
        direction=direction,
        valid=valid,
        domain_valid=domain_valid,
        finite=finite,
        monotone=monotone,
        signed_derivative_margin=margin,
        checked_points=checked_points,
        polynomial_degree=spec.polynomial_degree,
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


def amplitude_bounds(family_id: str, shape: tuple[float, ...], direction: Direction) -> tuple[float, float]:
    """Exact sign bounds for an elementary family's conditional amplitude."""

    if direction == "flat":
        return 0.0, 0.0
    sign = 1.0 if direction == "increasing" else -1.0
    derivative_sign = 1.0
    if family_id == "exp_affine_v1":
        derivative_sign = 1.0 if shape[0] > 0.0 else -1.0
    elif family_id == "reciprocal_shift_pos_v1":
        derivative_sign = -1.0
    elif family_id not in {"log_shift_v1", "logistic_v1", "poly1_v1"}:
        raise ValueError(f"amplitude bounds are not defined for {family_id}")
    if sign * derivative_sign > 0.0:
        return 0.0, np.inf
    return -np.inf, 0.0


def registry_manifest() -> dict[str, object]:
    """Small serializable identity used by later report/provenance modules."""

    return {
        "registry_version": REGISTRY_VERSION,
        "polynomial_degree_max": POLYNOMIAL_DEGREE_MAX,
        "family_ids": list(FAMILY_IDS),
        "families": [
            {
                "family_id": spec.family_id,
                "canonical_ast_id": spec.canonical_ast_id,
                "kind": spec.kind,
                "parameter_names": list(spec.parameter_names),
                "polynomial_degree": spec.polynomial_degree,
                "supports_p2": spec.supports_p2,
            }
            for spec in _FAMILIES
        ],
    }
