"""Typed, non-executable runtime for fitted registry models.

Callers never evaluate a formula string.  Prediction dispatches only through
the finite registry and refuses malformed, discontinuous, or uncertified
models at construction time.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Mapping

import numpy as np

from .registry import (
    Direction,
    REGISTRY_VERSION,
    certify_family,
    evaluate_family,
    family_spec,
)


COEFFICIENT_DECIMAL_PLACES = 3
COEFFICIENT_QUANTUM = 10.0**-COEFFICIENT_DECIMAL_PLACES
_GRID_ARITHMETIC_TOLERANCE = 1e-12


def _canonical_float(value: float) -> str:
    return float(value).hex()


def _format_coefficient(value: float, decimal_places: int | None) -> str:
    if decimal_places is None:
        return f"{float(value):.17g}"
    rounded = round(float(value), decimal_places)
    if rounded == 0.0:
        rounded = 0.0
    return f"{rounded:.{decimal_places}f}"


def _format_signed_coefficient(value: float, decimal_places: int | None) -> str:
    if decimal_places is None:
        return "+" + _format_coefficient(value, decimal_places)
    rounded = round(float(value), decimal_places)
    if rounded == 0.0:
        rounded = 0.0
    return f"{rounded:+.{decimal_places}f}"


def _is_on_coefficient_grid(value: float, decimal_places: int) -> bool:
    """Return whether the executable float is the rounded grid value itself."""

    rounded = round(float(value), decimal_places)
    if rounded == 0.0:
        rounded = 0.0
    return float(value) == rounded


def _join_tolerances(
    left_value: float,
    right_value: float,
    decimal_places: int | None,
) -> tuple[float, float]:
    if decimal_places is None:
        arithmetic = 1e-10 * max(1.0, abs(left_value), abs(right_value))
        return arithmetic, arithmetic
    quantum = 10.0**-decimal_places
    return _GRID_ARITHMETIC_TOLERANCE, quantum + _GRID_ARITHMETIC_TOLERANCE


def _join_preserves_direction(
    left_value: float,
    right_value: float,
    direction: Direction,
    tolerance: float,
) -> bool:
    if direction == "increasing":
        return right_value >= left_value - tolerance
    if direction == "decreasing":
        return right_value <= left_value + tolerance
    return abs(right_value - left_value) <= tolerance


@dataclass(frozen=True, slots=True)
class SegmentModel:
    """One certified registry function on a closed observed interval."""

    family_id: str
    x_lower: float
    x_upper: float
    parameters: Mapping[str, float]
    segment_id: str = "single"

    def __post_init__(self) -> None:
        spec = family_spec(self.family_id)
        lower = float(self.x_lower)
        upper = float(self.x_upper)
        if not np.isfinite(lower) or not np.isfinite(upper) or not lower < upper:
            raise ValueError("a segment requires a finite, non-empty interval")
        copied = {str(name): float(value) for name, value in self.parameters.items()}
        if set(copied) != set(spec.parameter_names) or not all(np.isfinite(value) for value in copied.values()):
            raise ValueError(f"invalid parameters for {self.family_id}")
        object.__setattr__(self, "x_lower", lower)
        object.__setattr__(self, "x_upper", upper)
        object.__setattr__(self, "parameters", MappingProxyType(copied))

    @property
    def polynomial_degree(self) -> int:
        return family_spec(self.family_id).polynomial_degree

    @property
    def canonical_ast_id(self) -> str:
        return family_spec(self.family_id).canonical_ast_id

    @property
    def is_constant_function(self) -> bool:
        """Whether this parameterization is exactly constant on its interval."""

        if self.family_id == "constant_v1":
            return True
        if self.family_id == "poly1_v1":
            return self.parameters["b"] == 0.0
        if self.family_id == "poly2_v1":
            return self.parameters["b"] == 0.0 and self.parameters["c"] == 0.0
        if self.family_id == "poly3_v1":
            return (
                self.parameters["b"] == 0.0
                and self.parameters["c"] == 0.0
                and self.parameters["d"] == 0.0
            )
        return self.parameters["b"] == 0.0

    def local_coordinate(self, x: np.ndarray | float) -> np.ndarray:
        values = np.asarray(x, dtype=np.float64)
        return (values - self.x_lower) / (self.x_upper - self.x_lower)

    def predict_unchecked(self, x: np.ndarray | float) -> np.ndarray:
        return evaluate_family(self.family_id, self.local_coordinate(x), self.parameters)

    def certificate(self, direction: Direction):
        return certify_family(self.family_id, self.parameters, direction)

    def formula(
        self,
        variable: str = "x",
        *,
        coefficient_decimal_places: int | None = 3,
    ) -> str:
        """Return a display-only canonical formula with an explicit transform."""

        p = self.parameters
        lower = _format_coefficient(self.x_lower, coefficient_decimal_places)
        span = _format_coefficient(
            self.x_upper - self.x_lower,
            coefficient_decimal_places,
        )
        t = f"(({variable}-{lower})/{span})"

        def coefficient(name: str) -> str:
            return _format_coefficient(p[name], coefficient_decimal_places)

        def signed_coefficient(name: str) -> str:
            return _format_signed_coefficient(p[name], coefficient_decimal_places)
        if self.family_id == "constant_v1":
            return coefficient("a")
        if self.family_id == "poly1_v1":
            return f"{coefficient('a')}{signed_coefficient('b')}*{t}"
        if self.family_id == "poly2_v1":
            return (
                f"{coefficient('a')}{signed_coefficient('b')}*{t}"
                f"{signed_coefficient('c')}*{t}^2"
            )
        if self.family_id == "poly3_v1":
            return (
                f"{coefficient('a')}{signed_coefficient('b')}*{t}"
                f"{signed_coefficient('c')}*{t}^2"
                f"{signed_coefficient('d')}*{t}^3"
            )
        if self.family_id == "exp_affine_v1":
            return f"{coefficient('a')}{signed_coefficient('b')}*exp({coefficient('k')}*{t})"
        if self.family_id == "log_shift_v1":
            return f"{coefficient('a')}{signed_coefficient('b')}*log({t}+{coefficient('d')})"
        if self.family_id == "reciprocal_shift_pos_v1":
            return f"{coefficient('a')}{signed_coefficient('b')}/({t}+{coefficient('d')})"
        if self.family_id == "logistic_v1":
            return (
                f"{coefficient('a')}{signed_coefficient('b')}/(1+exp(-{coefficient('k')}*"
                f"({t}-{coefficient('m')})))"
            )
        raise AssertionError("registry and formatter are out of sync")

    def to_dict(self, *, coefficient_decimal_places: int | None = 3) -> dict[str, object]:
        return {
            "segment_id": self.segment_id,
            "family_id": self.family_id,
            "canonical_ast_id": self.canonical_ast_id,
            "interval": {"lower": self.x_lower, "upper": self.x_upper},
            "parameters": dict(self.parameters),
            "formula": self.formula(
                coefficient_decimal_places=coefficient_decimal_places,
            ),
        }


@dataclass(frozen=True, slots=True)
class FittedModel:
    """One globally monotone P1 or coefficient-grid-contiguous P2 model."""

    segments: tuple[SegmentModel, ...]
    direction: Direction
    registry_version: str = REGISTRY_VERSION
    coefficient_decimal_places: int | None = None

    def __post_init__(self) -> None:
        if self.registry_version != REGISTRY_VERSION:
            raise ValueError(f"unsupported registry version: {self.registry_version}")
        if self.coefficient_decimal_places not in {None, COEFFICIENT_DECIMAL_PLACES}:
            raise ValueError("unsupported coefficient precision")
        if len(self.segments) not in {1, 2}:
            raise ValueError("v1 models contain exactly one or two segments")
        if self.coefficient_decimal_places is not None:
            for segment in self.segments:
                for value in segment.parameters.values():
                    if not _is_on_coefficient_grid(
                        value,
                        self.coefficient_decimal_places,
                    ):
                        raise ValueError("model parameter is outside the coefficient grid")
            if len(self.segments) == 2 and not _is_on_coefficient_grid(
                self.segments[0].x_upper,
                self.coefficient_decimal_places,
            ):
                raise ValueError("P2 breakpoint is outside the coefficient grid")
        if any(
            segment.is_constant_function and segment.family_id != "constant_v1"
            for segment in self.segments
        ):
            raise ValueError("constant branches require the canonical constant_v1 family")
        if self.direction == "flat" and any(segment.family_id != "constant_v1" for segment in self.segments):
            raise ValueError("flat direction is canonical only for constant models")
        if self.direction != "flat" and all(segment.is_constant_function for segment in self.segments):
            raise ValueError("a globally constant model requires the canonical flat direction")
        if len(self.segments) == 2 and all(segment.is_constant_function for segment in self.segments):
            raise ValueError("two constant branches must collapse to canonical P1")
        certificates = tuple(segment.certificate(self.direction) for segment in self.segments)
        if not all(certificate.valid for certificate in certificates):
            reasons = tuple(code for certificate in certificates for code in certificate.reason_codes)
            raise ValueError(f"uncertified segment model: {reasons}")
        if len(self.segments) == 2:
            left, right = self.segments
            if left.x_upper != right.x_lower:
                raise ValueError("P2 segment intervals must meet at one exact breakpoint")
            join = left.x_upper
            left_value = float(left.predict_unchecked(join))
            right_value = float(right.predict_unchecked(join))
            direction_tolerance, join_tolerance = _join_tolerances(
                left_value,
                right_value,
                self.coefficient_decimal_places,
            )
            if not np.isfinite(left_value) or not np.isfinite(right_value):
                raise ValueError("P2 branches are not finite at the breakpoint")
            if not _join_preserves_direction(
                left_value,
                right_value,
                self.direction,
                direction_tolerance,
            ):
                raise ValueError("P2 join is not globally monotone")
            if abs(left_value - right_value) > join_tolerance:
                raise ValueError("P2 branches are not continuous within coefficient precision")

    @property
    def segment_count(self) -> int:
        return len(self.segments)

    @property
    def polynomial_degree(self) -> int:
        return max(segment.polynomial_degree for segment in self.segments)

    @property
    def monotonicity_certified(self) -> bool:
        if not all(segment.certificate(self.direction).valid for segment in self.segments):
            return False
        if len(self.segments) == 1:
            return True
        left, right = self.segments
        join = left.x_upper
        left_value = float(left.predict_unchecked(join))
        right_value = float(right.predict_unchecked(join))
        direction_tolerance, join_tolerance = _join_tolerances(
            left_value,
            right_value,
            self.coefficient_decimal_places,
        )
        return bool(
            np.isfinite(left_value)
            and np.isfinite(right_value)
            and _join_preserves_direction(
                left_value,
                right_value,
                self.direction,
                direction_tolerance,
            )
            and abs(left_value - right_value) <= join_tolerance
        )

    @property
    def x_lower(self) -> float:
        return self.segments[0].x_lower

    @property
    def x_upper(self) -> float:
        return self.segments[-1].x_upper

    @property
    def breakpoint(self) -> float | None:
        return self.segments[0].x_upper if len(self.segments) == 2 else None

    @property
    def family_ids(self) -> tuple[str, ...]:
        return tuple(segment.family_id for segment in self.segments)

    @property
    def formula(self) -> str:
        return self._formula(
            coefficient_decimal_places=self.coefficient_decimal_places,
        )

    def _formula(self, *, coefficient_decimal_places: int | None) -> str:
        if len(self.segments) == 1:
            return (
                "f(x)="
                + self.segments[0].formula(
                    coefficient_decimal_places=coefficient_decimal_places,
                )
            )
        left, right = self.segments
        breakpoint = _format_coefficient(
            left.x_upper,
            coefficient_decimal_places,
        )
        return (
            f"f(x)={left.formula(coefficient_decimal_places=coefficient_decimal_places)} "
            f"for x<={breakpoint}; "
            f"{right.formula(coefficient_decimal_places=coefficient_decimal_places)} "
            f"for x>{breakpoint}"
        )

    def predict(self, x: np.ndarray | float) -> np.ndarray | float:
        """Predict on the observed support; return NaN outside it."""

        scalar = np.ndim(x) == 0
        values = np.asarray(x, dtype=np.float64)
        flat_values = values.reshape(-1)
        result = np.full(flat_values.shape, np.nan, dtype=np.float64)
        in_domain = np.isfinite(flat_values) & (flat_values >= self.x_lower) & (flat_values <= self.x_upper)
        if len(self.segments) == 1:
            result[in_domain] = self.segments[0].predict_unchecked(flat_values[in_domain])
        else:
            left, right = self.segments
            left_mask = in_domain & (flat_values <= left.x_upper)
            right_mask = in_domain & (flat_values > left.x_upper)
            result[left_mask] = left.predict_unchecked(flat_values[left_mask])
            result[right_mask] = right.predict_unchecked(flat_values[right_mask])
        shaped = result.reshape(values.shape)
        return float(shaped) if scalar else shaped

    @property
    def model_structure_hash(self) -> str:
        payload = {
            "registry_version": self.registry_version,
            "direction": self.direction,
            "families": [segment.family_id for segment in self.segments],
            "asts": [segment.canonical_ast_id for segment in self.segments],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(encoded).hexdigest()

    @property
    def model_instance_hash(self) -> str:
        payload = {
            "structure_hash": self.model_structure_hash,
            "segments": [
                {
                    "lower": _canonical_float(segment.x_lower),
                    "upper": _canonical_float(segment.x_upper),
                    "parameters": {
                        name: _canonical_float(value) for name, value in sorted(segment.parameters.items())
                    },
                }
                for segment in self.segments
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(encoded).hexdigest()

    def to_dict(self, *, schema_version: str | None = None) -> dict[str, object]:
        resolved_schema = schema_version or (
            "model-runtime-v2"
            if self.coefficient_decimal_places == COEFFICIENT_DECIMAL_PLACES
            else "model-runtime-v1"
        )
        if resolved_schema not in {"model-runtime-v1", "model-runtime-v2"}:
            raise ValueError("unsupported model runtime schema version")
        if resolved_schema == "model-runtime-v2" and self.coefficient_decimal_places != COEFFICIENT_DECIMAL_PLACES:
            raise ValueError("model-runtime-v2 requires thousandth-grid coefficients")
        coefficient_decimal_places = None if resolved_schema == "model-runtime-v1" else COEFFICIENT_DECIMAL_PLACES
        payload = {
            "schema_version": resolved_schema,
            "registry_version": self.registry_version,
            "direction": self.direction,
            "segment_count": self.segment_count,
            "polynomial_degree": self.polynomial_degree,
            "monotonicity_certified": self.monotonicity_certified,
            "breakpoint": self.breakpoint,
            "segments": [
                segment.to_dict(
                    coefficient_decimal_places=coefficient_decimal_places,
                )
                for segment in self.segments
            ],
            "formula": self._formula(
                coefficient_decimal_places=coefficient_decimal_places,
            ),
            "model_structure_hash": self.model_structure_hash,
            "model_instance_hash": self.model_instance_hash,
        }
        if resolved_schema == "model-runtime-v2":
            payload["coefficient_decimal_places"] = COEFFICIENT_DECIMAL_PLACES
        return payload


def model_from_dict(payload: Mapping[str, object]) -> FittedModel:
    """Reconstruct a typed model while rejecting formula-driven execution."""

    required_v1 = {
        "schema_version",
        "registry_version",
        "direction",
        "segment_count",
        "polynomial_degree",
        "monotonicity_certified",
        "breakpoint",
        "segments",
        "formula",
        "model_structure_hash",
        "model_instance_hash",
    }
    schema_version = payload.get("schema_version")
    required_v2 = required_v1 | {"coefficient_decimal_places"}
    expected_fields = required_v1 if schema_version == "model-runtime-v1" else required_v2
    if (
        schema_version not in {"model-runtime-v1", "model-runtime-v2"}
        or set(payload) != expected_fields
        or (
            schema_version == "model-runtime-v2"
            and payload.get("coefficient_decimal_places") != COEFFICIENT_DECIMAL_PLACES
        )
    ):
        raise ValueError("invalid model-runtime object")
    coefficient_decimal_places = (
        None if schema_version == "model-runtime-v1" else COEFFICIENT_DECIMAL_PLACES
    )
    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list):
        raise ValueError("model segments must be a list")
    segments: list[SegmentModel] = []
    for raw in raw_segments:
        if not isinstance(raw, dict) or set(raw) != {
            "segment_id",
            "family_id",
            "canonical_ast_id",
            "interval",
            "parameters",
            "formula",
        }:
            raise ValueError("invalid segment object")
        interval = raw["interval"]
        parameters = raw["parameters"]
        if not isinstance(interval, dict) or set(interval) != {"lower", "upper"}:
            raise ValueError("invalid segment interval")
        if not isinstance(parameters, dict):
            raise ValueError("invalid segment parameters")
        segment = SegmentModel(
            family_id=str(raw["family_id"]),
            x_lower=float(interval["lower"]),
            x_upper=float(interval["upper"]),
            parameters={str(name): float(value) for name, value in parameters.items()},
            segment_id=str(raw["segment_id"]),
        )
        if (
            raw["canonical_ast_id"] != segment.canonical_ast_id
            or raw["formula"]
            != segment.formula(
                coefficient_decimal_places=coefficient_decimal_places,
            )
        ):
            raise ValueError("segment identity/display formula mismatch")
        segments.append(segment)
    direction = str(payload["direction"])
    if direction not in {"increasing", "decreasing", "flat"}:
        raise ValueError("invalid model direction")
    model = FittedModel(
        tuple(segments),
        direction,  # type: ignore[arg-type]
        registry_version=str(payload["registry_version"]),
        coefficient_decimal_places=coefficient_decimal_places,
    )
    if (
        payload["segment_count"] != model.segment_count
        or payload["polynomial_degree"] != model.polynomial_degree
        or payload["monotonicity_certified"] is not True
        or payload["breakpoint"] != model.breakpoint
        or payload["formula"]
        != model._formula(
            coefficient_decimal_places=coefficient_decimal_places,
        )
        or payload["model_structure_hash"] != model.model_structure_hash
        or payload["model_instance_hash"] != model.model_instance_hash
    ):
        raise ValueError("model identity or derived fields do not reconcile")
    return model
