"""Bounded mathematical trees and a certified, non-executable formula runtime.

Every shape is nonnegative, increasing and zero at t=0. Composition proves
monotonicity on the entire interval, independently of optimizer samples.
There are no conditionals, user constants, variable exponents or Python code.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math

import numpy as np


GRAMMAR_VERSION = "monotone-expression-v1"
MAX_NODES = 31
MAX_DEPTH = 8
MAX_PARAMETERS = 10
SHAPE_BOUNDS = (0.001, 12.0)
_UNARY = {"scale", "square", "cube", "expm1", "log1p", "sqrt1p", "saturate"}
_TRANSCENDENTAL = {"expm1", "log1p", "sqrt1p", "saturate"}


@dataclass(frozen=True, slots=True)
class Expression:
    op: str
    args: tuple[Expression, ...] = ()

    def __post_init__(self) -> None:
        arity = (
            0
            if self.op == "t"
            else 1
            if self.op in _UNARY
            else 2
            if self.op in {"add", "mul"}
            else -1
        )
        if (
            not isinstance(self.args, tuple)
            or len(self.args) != arity
            or not all(isinstance(a, Expression) for a in self.args)
        ):
            raise ValueError("EXPRESSION_GRAMMAR")
        if self.nodes > MAX_NODES or self.depth > MAX_DEPTH:
            raise ValueError("EXPRESSION_COMPLEXITY")
        # Count algebraic degree even through products and nested powers.
        # Transcendental nesting is excluded: exp(k*log(1+t)) could hide a
        # polynomial of arbitrary degree despite a small syntax tree.
        if self.degree_budget > 3:
            raise ValueError("POLYNOMIAL_DEGREE_LIMIT")
        if self.transcendental_depth > 1:
            raise ValueError("TRANSCENDENTAL_NESTING")
        if self.parameter_count > MAX_PARAMETERS - 2:
            raise ValueError("EXPRESSION_PARAMETER_LIMIT")

    @property
    def nodes(self) -> int:
        return 1 + sum(a.nodes for a in self.args)

    @property
    def depth(self) -> int:
        return 1 + max((a.depth for a in self.args), default=0)

    @property
    def degree_budget(self) -> int:
        if self.op == "t":
            return 1
        degrees = [a.degree_budget for a in self.args]
        if self.op == "mul":
            return sum(degrees)
        if self.op in {"square", "cube"}:
            return degrees[0] * (2 if self.op == "square" else 3)
        return max(degrees)

    @property
    def transcendental_depth(self) -> int:
        return int(self.op in _TRANSCENDENTAL) + max(
            (a.transcendental_depth for a in self.args), default=0
        )

    @property
    def parameter_count(self) -> int:
        return int(self.op == "scale") + sum(a.parameter_count for a in self.args)

    @property
    def outside_registry(self) -> bool:
        """Conservative structural novelty; not a claim of scientific discovery."""
        if self.transcendental_depth == 0:
            return False
        if self.op == "scale":
            return self.args[0].outside_registry
        if self.op in {"add", "mul", "square", "cube", "sqrt1p"}:
            return True
        return self.args[0].degree_budget > 1

    def to_dict(self) -> dict:
        if not self.args:
            return {"op": self.op}
        if len(self.args) == 1:
            return {"op": self.op, "arg": self.args[0].to_dict()}
        return {"op": self.op, "args": [a.to_dict() for a in self.args]}

    @classmethod
    def from_dict(cls, raw: object, _depth: int = 0) -> Expression:
        if (
            _depth >= MAX_DEPTH
            or not isinstance(raw, dict)
            or not isinstance(raw.get("op"), str)
        ):
            raise ValueError("EXPRESSION_GRAMMAR")
        op = raw["op"]
        keys = (
            {"op"} if op == "t" else {"op", "arg"} if op in _UNARY else {"op", "args"}
        )
        if set(raw) != keys:
            raise ValueError("EXPRESSION_FIELDS")
        if op == "t":
            return cls(op)
        if op in _UNARY:
            return cls(op, (cls.from_dict(raw["arg"], _depth + 1),))
        args = raw.get("args")
        if op not in {"add", "mul"} or not isinstance(args, list) or len(args) != 2:
            raise ValueError("EXPRESSION_GRAMMAR")
        return cls(op, tuple(cls.from_dict(a, _depth + 1) for a in args))

    def evaluate(self, t: np.ndarray, parameters: tuple[float, ...]) -> np.ndarray:
        if len(parameters) != self.parameter_count:
            raise ValueError("EXPRESSION_PARAMETERS")
        values = iter(parameters)

        def visit(node: Expression) -> np.ndarray:
            if node.op == "t":
                return np.asarray(t, dtype=float)
            coefficient = next(values) if node.op == "scale" else None
            a = visit(node.args[0])
            if node.op == "scale":
                return coefficient * a
            if node.op == "add":
                return a + visit(node.args[1])
            if node.op == "mul":
                return a * visit(node.args[1])
            if node.op == "square":
                return a * a
            if node.op == "cube":
                return a * a * a
            if node.op == "expm1":
                return np.expm1(a)
            if node.op == "log1p":
                return np.log1p(a)
            if node.op == "sqrt1p":
                return a / (np.sqrt(1.0 + a) + 1.0)
            return a / (1.0 + a)

        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            return visit(self)

    def display(self, variable: str, parameters: tuple[float, ...]) -> str:
        values = iter(parameters)

        def visit(node: Expression) -> str:
            if node.op == "t":
                return variable
            coefficient = next(values) if node.op == "scale" else None
            a = visit(node.args[0])
            if node.op == "scale":
                return f"({coefficient:.3f}*{a})"
            if node.op in {"add", "mul"}:
                return f"({a}{'+' if node.op == 'add' else '*'}{visit(node.args[1])})"
            if node.op in {"square", "cube"}:
                return f"({a}^{2 if node.op == 'square' else 3})"
            if node.op == "sqrt1p":
                return f"(sqrt(1+{a})-1)"
            if node.op == "saturate":
                return f"({a}/(1+{a}))"
            return f"{node.op}({a})"

        return visit(self)


@dataclass(frozen=True, slots=True)
class FormulaHypothesis:
    branches: tuple[Expression, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.branches, tuple)
            or len(self.branches) not in {1, 2}
            or not all(isinstance(b, Expression) for b in self.branches)
        ):
            raise ValueError("FORMULA_BRANCH_LIMIT")
        if (
            self.parameter_count > MAX_PARAMETERS
            or sum(b.nodes for b in self.branches) > MAX_NODES
        ):
            raise ValueError("FORMULA_COMPLEXITY")
        if not any(b.outside_registry for b in self.branches):
            raise ValueError("FORMULA_ALREADY_IN_REGISTRY")

    @property
    def parameter_count(self) -> int:
        return 1 + len(self.branches) + sum(b.parameter_count for b in self.branches)

    def to_dict(self) -> dict:
        return {"branches": [b.to_dict() for b in self.branches]}

    @classmethod
    def from_dict(cls, raw: object) -> FormulaHypothesis:
        if (
            not isinstance(raw, dict)
            or set(raw) != {"branches"}
            or not isinstance(raw["branches"], list)
            or not 1 <= len(raw["branches"]) <= 2
        ):
            raise ValueError("FORMULA_FIELDS")
        return cls(tuple(Expression.from_dict(b) for b in raw["branches"]))

    @property
    def hypothesis_id(self) -> str:
        return sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()


def _grid_number(value: object, *, nonnegative: bool = False) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or round(value, 3) != value
        or (nonnegative and value < 0)
    ):
        raise ValueError("FORMULA_COEFFICIENT_GRID")
    return float(value)


@dataclass(frozen=True, slots=True)
class FormulaSegment:
    expression: Expression
    x_lower: float
    x_upper: float
    shape: tuple[float, ...]
    offset: float
    amplitude: float
    direction: str
    anchored_right: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.expression, Expression)
            or not all(math.isfinite(v) for v in (self.x_lower, self.x_upper))
            or not self.x_lower < self.x_upper
        ):
            raise ValueError("FORMULA_DOMAIN")
        if not math.isfinite(self.x_upper - self.x_lower):
            raise ValueError("FORMULA_DOMAIN")
        if (
            not isinstance(self.shape, tuple)
            or len(self.shape) != self.expression.parameter_count
        ):
            raise ValueError("FORMULA_SHAPE")
        for p in self.shape:
            if not SHAPE_BOUNDS[0] <= _grid_number(p) <= SHAPE_BOUNDS[1]:
                raise ValueError("FORMULA_SHAPE_BOUNDS")
        _grid_number(self.offset)
        _grid_number(self.amplitude, nonnegative=True)
        if self.direction not in {"increasing", "decreasing", "flat"} or not isinstance(
            self.anchored_right, bool
        ):
            raise ValueError("FORMULA_DIRECTION")
        if self.direction == "flat" and self.amplitude != 0:
            raise ValueError("FORMULA_DIRECTION")
        # Structural certificate: all operations preserve nonnegative order.
        # The maximum is at t=1, so checking it bounds the complete interval.
        endpoint = float(self.expression.evaluate(np.asarray(1.0), self.shape))
        if not math.isfinite(endpoint) or not 1e-12 <= endpoint <= 1e100:
            raise ValueError("FORMULA_RANGE_CERTIFICATE")
        if not np.all(
            np.isfinite(self.predict_unchecked(np.array([self.x_lower, self.x_upper])))
        ):
            raise ValueError("FORMULA_RANGE_CERTIFICATE")

    def predict_unchecked(self, x: np.ndarray | float) -> np.ndarray:
        t = (np.asarray(x, dtype=float) - self.x_lower) / (self.x_upper - self.x_lower)
        normalizer = float(self.expression.evaluate(np.asarray(1.0), self.shape))
        z = self.expression.evaluate(t, self.shape) / normalizer
        sign = -1 if self.direction == "decreasing" else 1
        return self.offset + sign * self.amplitude * (z - int(self.anchored_right))

    @property
    def formula(self) -> str:
        # Domain transforms retain full precision; coefficients are thousandths.
        t = f"((x-({self.x_lower!r}))/({self.x_upper!r}-({self.x_lower!r})))"
        numerator = self.expression.display(t, self.shape)
        denominator = self.expression.display("1", self.shape)
        normalized = f"({numerator}/{denominator}{'-1' if self.anchored_right else ''})"
        sign = "-" if self.direction == "decreasing" else "+"
        return f"{self.offset:.3f}{sign}{self.amplitude:.3f}*{normalized}"

    def to_dict(self) -> dict:
        return {
            "expression": self.expression.to_dict(),
            "x_lower": self.x_lower,
            "x_upper": self.x_upper,
            "shape": list(self.shape),
            "offset": self.offset,
            "amplitude": self.amplitude,
            "direction": self.direction,
            "anchored_right": self.anchored_right,
        }


@dataclass(frozen=True, slots=True)
class FormulaModel:
    segments: tuple[FormulaSegment, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.segments, tuple) or not all(
            isinstance(s, FormulaSegment) for s in self.segments
        ):
            raise ValueError("FORMULA_SEGMENTS")
        FormulaHypothesis(tuple(s.expression for s in self.segments))
        if len({s.direction for s in self.segments}) != 1:
            raise ValueError("FORMULA_DIRECTION")
        if len(self.segments) == 1 and self.segments[0].anchored_right:
            raise ValueError("FORMULA_ANCHOR")
        if len(self.segments) == 2:
            left, right = self.segments
            if (
                left.x_upper != right.x_lower
                or left.offset != right.offset
                or not left.anchored_right
                or right.anchored_right
            ):
                raise ValueError("FORMULA_CONTINUITY")
            _grid_number(left.x_upper)
        if all(s.amplitude == 0 for s in self.segments):
            # A constant belongs to canonical registry P1, not new discovery.
            raise ValueError("FORMULA_COLLAPSED_TO_REGISTRY_CONSTANT")

    @property
    def segment_count(self) -> int:
        return len(self.segments)

    @property
    def x_lower(self) -> float:
        return self.segments[0].x_lower

    @property
    def x_upper(self) -> float:
        return self.segments[-1].x_upper

    @property
    def direction(self) -> str:
        return self.segments[0].direction

    @property
    def breakpoint(self) -> float | None:
        return self.segments[0].x_upper if self.segment_count == 2 else None

    @property
    def formula(self) -> str:
        if self.segment_count == 1:
            return "f(x)=" + self.segments[0].formula
        return f"f(x)={self.segments[0].formula} for x<={self.breakpoint:.3f}; {self.segments[1].formula} for x>{self.breakpoint:.3f}"

    def predict(self, x: np.ndarray | float) -> np.ndarray | float:
        values = np.asarray(x, dtype=float)
        result = np.full(values.shape, np.nan)
        for i, segment in enumerate(self.segments):
            mask = (
                np.isfinite(values)
                & (values >= segment.x_lower)
                & (values <= segment.x_upper)
            )
            if i:
                mask &= values > segment.x_lower
            result[mask] = segment.predict_unchecked(values[mask])
        return float(result) if result.ndim == 0 else result

    def to_dict(self) -> dict:
        body = {
            "schema_version": "formula-runtime-v1",
            "grammar_version": GRAMMAR_VERSION,
            "segments": [s.to_dict() for s in self.segments],
            "formula": self.formula,
            "monotonicity_certified": True,
            "coefficient_decimal_places": 3,
        }
        body["model_instance_hash"] = sha256(
            json.dumps(body, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        return body

    @property
    def model_instance_hash(self) -> str:
        return self.to_dict()["model_instance_hash"]


def formula_model_from_dict(raw: object) -> FormulaModel:
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "grammar_version",
        "segments",
        "formula",
        "monotonicity_certified",
        "coefficient_decimal_places",
        "model_instance_hash",
    }:
        raise ValueError("FORMULA_RUNTIME_FIELDS")
    segments = raw["segments"]
    if not isinstance(segments, list) or not 1 <= len(segments) <= 2:
        raise ValueError("FORMULA_BRANCH_LIMIT")
    parsed = []
    for s in segments:
        if (
            not isinstance(s, dict)
            or set(s)
            != {
                "expression",
                "x_lower",
                "x_upper",
                "shape",
                "offset",
                "amplitude",
                "direction",
                "anchored_right",
            }
            or not isinstance(s["shape"], list)
        ):
            raise ValueError("FORMULA_SEGMENT_FIELDS")
        for key in ("x_lower", "x_upper", "offset", "amplitude"):
            if isinstance(s[key], bool) or not isinstance(s[key], (float, int)):
                raise ValueError("FORMULA_NUMBER")
        parsed.append(
            FormulaSegment(
                Expression.from_dict(s["expression"]),
                s["x_lower"],
                s["x_upper"],
                tuple(s["shape"]),
                s["offset"],
                s["amplitude"],
                s["direction"],
                s["anchored_right"],
            )
        )
    model = FormulaModel(tuple(parsed))
    if (
        raw != model.to_dict()
        or raw["monotonicity_certified"] is not True
        or type(raw["coefficient_decimal_places"]) is not int
    ):
        raise ValueError("FORMULA_RUNTIME_IDENTITY")
    return model
