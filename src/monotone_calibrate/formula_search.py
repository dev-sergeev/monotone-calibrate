"""LLM-SR: generate mathematical trees, optimize parameters, return fitness.

This search is independent of the complete P1/P2 registry baselines. The
provider sees only discovery-training data; holdout assessment lives outside
this module. No provider output is executed or interpreted as Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from typing import Protocol
from urllib.parse import urlsplit

import httpx
import numpy as np
from scipy.optimize import minimize, nnls

from .config import LLMConfig
from .engine import _as_problem, _eligible_cells
from .expressions import (
    FormulaHypothesis,
    FormulaModel,
    FormulaSegment,
    GRAMMAR_VERSION,
    MAX_DEPTH,
    MAX_NODES,
    MAX_PARAMETERS,
    SHAPE_BOUNDS,
)
from .llm_advisor import summarize_training


OUTPUT_SCHEMA_VERSION = "llm-formulas-v1"
PROMPT_VERSION = "llm-formula-discovery-v1"

FORMULA_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "hypotheses"],
    "properties": {
        "schema_version": {"const": OUTPUT_SCHEMA_VERSION},
        "hypotheses": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["branches"],
                "properties": {
                    "branches": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 2,
                        "items": {"$ref": "#/$defs/expression"},
                    }
                },
            },
        },
    },
    "$defs": {
        "expression": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["op"],
                    "properties": {"op": {"const": "t"}},
                },
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["op", "arg"],
                    "properties": {
                        "op": {
                            "enum": [
                                "scale",
                                "square",
                                "cube",
                                "expm1",
                                "log1p",
                                "sqrt1p",
                                "saturate",
                            ]
                        },
                        "arg": {"$ref": "#/$defs/expression"},
                    },
                },
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["op", "args"],
                    "properties": {
                        "op": {"enum": ["add", "mul"]},
                        "args": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 2,
                            "items": {"$ref": "#/$defs/expression"},
                        },
                    },
                },
            ]
        }
    },
}


@dataclass(frozen=True, slots=True)
class FormulaFitOptions:
    starts: int = 3
    maxiter: int = 80
    max_cells: int = 9
    max_evaluations: int = 8000
    seed: int = 20260912

    def __post_init__(self) -> None:
        for name, low, high in (
            ("starts", 1, 8),
            ("maxiter", 1, 500),
            ("max_cells", 1, 65),
            ("max_evaluations", 1, 100000),
            ("seed", 0, 2**32 - 1),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError("invalid formula fitting budget")


@dataclass(frozen=True, slots=True)
class FormulaFit:
    hypothesis: FormulaHypothesis
    model: FormulaModel
    mse: float
    evaluations: int
    budget_exhausted: bool


def fit_formula(
    x, y, hypothesis: FormulaHypothesis, options: FormulaFitOptions | None = None
) -> FormulaFit | None:
    """Bounded multistart variable projection; all published parameters are rounded."""
    resolved = options or FormulaFitOptions()
    xv, yv = _as_problem(x, y)
    if len(yv) < hypothesis.parameter_count + 2:
        return None
    branches = hypothesis.branches
    if len(branches) == 2:
        cells = _eligible_cells(xv, 0.40)
        if not cells:
            return None
        indices = np.unique(
            np.linspace(0, len(cells) - 1, min(resolved.max_cells, len(cells)))
            .round()
            .astype(int)
        )
        breakpoints = [cells[i][1] for i in indices]
    else:
        breakpoints = [None]
    shape_count = sum(b.parameter_count for b in branches)
    rng = np.random.default_rng(resolved.seed)
    starts = [np.ones(shape_count)]
    starts += [
        np.exp(rng.uniform(np.log(0.05), np.log(6.0), shape_count))
        for _ in range(resolved.starts - 1)
    ]
    y_scale = max(float(np.std(yv)), 1e-6)
    y_center = float(np.mean(yv))
    target = (yv - y_center) / y_scale
    best: tuple[float, FormulaModel] | None = None
    evaluations = 0

    class BudgetExhausted(Exception):
        pass

    try:
        for breakpoint in breakpoints:
            masks = (
                [np.ones(xv.size, dtype=bool)]
                if breakpoint is None
                else [xv <= breakpoint, xv > breakpoint]
            )
            domains = (
                [(float(xv[0]), float(xv[-1]))]
                if breakpoint is None
                else [(float(xv[0]), breakpoint), (breakpoint, float(xv[-1]))]
            )
            if any(
                np.sum(mask) < branch.parameter_count + 3
                or np.unique(xv[mask]).size < branch.parameter_count + 2
                for mask, branch in zip(masks, branches, strict=True)
            ):
                continue
            for direction in ("increasing", "decreasing"):
                sign = 1 if direction == "increasing" else -1

                def solve(vector, *, publish=False):
                    nonlocal evaluations, best
                    if evaluations >= resolved.max_evaluations:
                        raise BudgetExhausted
                    evaluations += 1
                    vector = np.round(vector, 3) if publish else vector
                    columns = []
                    shapes = []
                    position = 0
                    for i, (branch, mask, (lower, upper)) in enumerate(
                        zip(branches, masks, domains, strict=True)
                    ):
                        shape = tuple(
                            float(v)
                            for v in vector[
                                position : position + branch.parameter_count
                            ]
                        )
                        position += branch.parameter_count
                        shapes.append(shape)
                        endpoint = float(branch.evaluate(np.asarray(1.0), shape))
                        if (
                            not math.isfinite(endpoint)
                            or not 1e-12 <= endpoint <= 1e100
                        ):
                            return 1e100
                        t = (xv[mask] - lower) / (upper - lower)
                        z = branch.evaluate(t, shape) / endpoint
                        column = np.zeros(xv.size)
                        column[mask] = sign * (
                            z - int(breakpoint is not None and i == 0)
                        )
                        columns.append(column)
                    design = np.column_stack(columns)
                    means = np.mean(design, axis=0)
                    try:
                        amplitudes, _ = nnls(design - means, target, maxiter=200)
                    except (ValueError, RuntimeError, np.linalg.LinAlgError):
                        return 1e100
                    offset = -float(means @ amplitudes)
                    prediction = offset + design @ amplitudes
                    loss = float(np.mean((target - prediction) ** 2))
                    if not publish:
                        return loss
                    physical_offset = round(y_center + y_scale * offset, 3)
                    physical_amplitudes = np.round(amplitudes * y_scale, 3)
                    effective_direction = (
                        direction if np.any(physical_amplitudes > 0) else "flat"
                    )
                    try:
                        model = FormulaModel(
                            tuple(
                                FormulaSegment(
                                    branch,
                                    lower,
                                    upper,
                                    shape,
                                    physical_offset,
                                    float(amplitude),
                                    effective_direction,
                                    breakpoint is not None and i == 0,
                                )
                                for i, (
                                    branch,
                                    (lower, upper),
                                    shape,
                                    amplitude,
                                ) in enumerate(
                                    zip(
                                        branches,
                                        domains,
                                        shapes,
                                        physical_amplitudes,
                                        strict=True,
                                    )
                                )
                            )
                        )
                        mse = float(np.mean((yv - model.predict(xv)) ** 2))
                    except (ValueError, FloatingPointError, OverflowError):
                        return 1e100
                    if math.isfinite(mse) and (best is None or mse < best[0]):
                        best = (mse, model)
                    return loss

                for start in starts:
                    solve(start, publish=True)
                    if shape_count:
                        result = minimize(
                            solve,
                            start,
                            method="L-BFGS-B",
                            bounds=[SHAPE_BOUNDS] * shape_count,
                            options={
                                "maxiter": resolved.maxiter,
                                "maxfun": 1000,
                                "ftol": 1e-11,
                            },
                        )
                        if np.all(np.isfinite(result.x)):
                            # Bounded incumbents are usable even at a budget limit;
                            # every one is independently recertified after rounding.
                            solve(result.x, publish=True)
    except BudgetExhausted:
        pass
    if best is None:
        return None
    return FormulaFit(
        hypothesis,
        best[1],
        best[0],
        evaluations,
        evaluations >= resolved.max_evaluations,
    )


class FormulaSampler(Protocol):
    def sample(self, request: dict) -> tuple[FormulaHypothesis, ...]: ...


class FormulaProviderError(RuntimeError):
    """Only a fixed code crosses the provider boundary, never response text."""


def parse_formulas(
    content: object, *, rejections: list[str] | None = None
) -> tuple[FormulaHypothesis, ...]:
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    if not isinstance(content, str) or len(content) > 65536:
        raise ValueError("FORMULA_RESPONSE_SIZE")
    raw = json.loads(
        content,
        object_pairs_hook=object_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NONFINITE_JSON")),
    )
    if (
        not isinstance(raw, dict)
        or set(raw) != {"schema_version", "hypotheses"}
        or raw["schema_version"] != OUTPUT_SCHEMA_VERSION
    ):
        raise ValueError("FORMULA_RESPONSE_SCHEMA")
    if not isinstance(raw["hypotheses"], list) or not 1 <= len(raw["hypotheses"]) <= 4:
        raise ValueError("FORMULA_BATCH_SIZE")
    accepted = {}
    rejected = []
    for raw_hypothesis in raw["hypotheses"]:
        try:
            hypothesis = FormulaHypothesis.from_dict(raw_hypothesis)
        except ValueError as error:
            code = str(error)
            if code not in {
                "FORMULA_ALREADY_IN_REGISTRY",
                "POLYNOMIAL_DEGREE_LIMIT",
                "TRANSCENDENTAL_NESTING",
                "FORMULA_COMPLEXITY",
                "EXPRESSION_COMPLEXITY",
            }:
                code = "INVALID_FORMULA_RESPONSE"
            rejected.append(code)
            continue
        accepted[hypothesis.hypothesis_id] = hypothesis
    if rejections is not None:
        rejections.extend("LLM_" + code for code in rejected)
    if not accepted:
        raise ValueError(rejected[0] if rejected else "FORMULA_BATCH_SIZE")
    return tuple(accepted.values())


SYSTEM_PROMPT = """You perform LLM-SR scientific equation discovery for monotone scalar calibration.
Invent NEW mathematical expressions by composing the grammar operations, not naming registry families.
Return strict JSON only: {"schema_version":"llm-formulas-v1","hypotheses":[{"branches":[EXPRESSION]}]}.
Propose 1 to 4 diverse hypotheses per call. Each has one or two branches, no other fields.
Grammar: {"op":"t"}; unary {"op":OP,"arg":EXPRESSION}; binary {"op":OP,"args":[EXPRESSION,EXPRESSION]}.
Unary OP: scale, square, cube, expm1, log1p, sqrt1p, saturate. Binary OP: add, mul.
scale(u)=p*u with a NEW positive parameter placeholder p optimized locally; supply NO numeric values or parameter names.
expm1(u)=exp(u)-1, sqrt1p(u)=sqrt(1+u)-1, saturate(u)=u/(1+u).
Each branch shape B(t) is normalized as B(t)/B(1). Solver fits offset and nonnegative amplitude,
both directions, and (for two branches) a continuous join with fitted breakpoint and 40/60 balance.
No Python, strings containing formulas, constants, conditionals, arbitrary powers, abs, min, max or piecewise nodes.
At most 31 nodes total, depth 8, 10 numeric parameters including offset and amplitudes.
Polynomial degree budget: t=1, add=max, mul=sum, square=2*arg, cube=3*arg, other unary=arg; maximum 3.
At most ONE of expm1/log1p/sqrt1p/saturate on any root-to-leaf path (also through scale).
At least one branch must go beyond registry: sqrt1p, nonlinear transform of quadratic/cubic,
or sum/product of a transformed term with another variable-dependent term.
Pure polynomials and simple exp/log/reciprocal shapes alone are already in the registry and rejected.
Use the observed training summary and previous negative-MSE feedback to improve fit while preferring short structures.
Include scale nodes to give the numeric optimizer meaningful shape parameters, especially inside nonlinear functions.
The trivial tree {"op":"t"} and scaled t are INVALID answers, even if the data look nearly linear.
Return an actual NEW nonlinear expression. Examples of valid syntax (adapt structures to the data):
{"branches":[{"op":"sqrt1p","arg":{"op":"scale","arg":{"op":"t"}}}]}
{"branches":[{"op":"expm1","arg":{"op":"square","arg":{"op":"scale","arg":{"op":"t"}}}}]}
{"branches":[{"op":"add","args":[{"op":"log1p","arg":{"op":"scale","arg":{"op":"t"}}},{"op":"square","arg":{"op":"t"}}]}]}
These are examples only; invent other valid expressions if they fit better. sqrt1p is also outside the registry.
"""


class HTTPFormulaSampler:
    """Explicit OpenAI-compatible HTTP, no ambient tracing or redirect forwarding."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
        self.http_attempts = 0
        self.rejections: list[str] = []

    def sample(self, request: dict) -> tuple[FormulaHypothesis, ...]:
        self.rejections = []
        config = self.config
        if (
            not config.enabled
            or not config.base_url
            or not config.access_token
            or not config.model
        ):
            raise FormulaProviderError("LLM_CONFIG_MISSING")
        payload = {
            "model": config.model,
            "temperature": 0.8,
            "max_tokens": config.max_output_tokens,
            # Recursive constrained decoding can collapse this provider's
            # output to the minimal t tree. JSON mode plus our strict
            # local parser preserves generation and the same safety rules.
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(request, allow_nan=False)},
            ],
        }
        if config.reasoning_effort is not None:
            if urlsplit(config.base_url).hostname == "openrouter.ai":
                payload["reasoning"] = {"effort": config.reasoning_effort}
            else:
                payload["reasoning_effort"] = config.reasoning_effort
        for attempt in range(config.max_retries + 1):
            self.http_attempts += 1
            try:
                with httpx.Client(
                    timeout=config.timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    response = client.post(
                        config.base_url.rstrip("/") + "/chat/completions",
                        headers={"Authorization": "Bearer " + config.access_token},
                        json=payload,
                    )
                if response.status_code != 200:
                    if (
                        response.status_code in {429, 500, 502, 503, 504}
                        and attempt < config.max_retries
                    ):
                        continue
                    raise FormulaProviderError(f"LLM_HTTP_{response.status_code}")
                if len(response.content) > 1_000_000:
                    raise FormulaProviderError("LLM_RESPONSE_SIZE")
                document = response.json()
                usage = document.get("usage") or {}
                for key in self.usage:
                    value = usage.get(key, 0)
                    if (
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and math.isfinite(value)
                        and value >= 0
                    ):
                        self.usage[key] += value
                choice = document["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise FormulaProviderError("LLM_OUTPUT_TRUNCATED")
                content = choice["message"]["content"]
                return parse_formulas(content, rejections=self.rejections)
            except httpx.HTTPError:
                if attempt == config.max_retries:
                    raise FormulaProviderError("LLM_TRANSPORT_FAILURE") from None
            except ValueError as error:
                semantic_codes = {
                    "FORMULA_ALREADY_IN_REGISTRY",
                    "POLYNOMIAL_DEGREE_LIMIT",
                    "TRANSCENDENTAL_NESTING",
                    "FORMULA_COMPLEXITY",
                    "EXPRESSION_COMPLEXITY",
                }
                if str(error) in semantic_codes:
                    raise FormulaProviderError("LLM_" + str(error)) from None
                raise FormulaProviderError("LLM_INVALID_FORMULA_RESPONSE") from None
            except (KeyError, IndexError, TypeError, AttributeError, RecursionError):
                raise FormulaProviderError("LLM_INVALID_FORMULA_RESPONSE") from None
        raise FormulaProviderError("LLM_TRANSPORT_FAILURE")


@dataclass(frozen=True, slots=True)
class FormulaSearchResult:
    status: str
    best: FormulaFit | None = None
    trace: tuple[dict, ...] = ()
    calls_succeeded: int = 0
    calls_failed: int = 0
    warning_codes: tuple[str, ...] = ()
    usage: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "calls_succeeded": self.calls_succeeded,
            "calls_failed": self.calls_failed,
            "hypotheses_evaluated": len(self.trace),
            "trace": list(self.trace),
            "warnings": list(self.warning_codes),
            "usage": dict(self.usage),
            "selected_hypothesis": None
            if self.best is None
            else self.best.hypothesis.to_dict(),
        }


def run_formula_search(
    x,
    y,
    config: LLMConfig,
    *,
    sampler: FormulaSampler | None = None,
    fit_options: FormulaFitOptions | None = None,
) -> FormulaSearchResult:
    """Generate -> numerically fit -> sample scored experience, up to the call budget."""
    if not config.enabled:
        return FormulaSearchResult("DISABLED")
    xv, yv = _as_problem(x, y)
    resolved = fit_options or FormulaFitOptions()
    provider = sampler or HTTPFormulaSampler(config)
    summary = summarize_training(xv, yv).to_payload()
    summary.pop("replaceable_slots", None)
    rng = np.random.default_rng(resolved.seed)
    islands: list[list[FormulaFit]] = [[] for _ in range(10)]
    cache: dict[str, FormulaFit | None] = {}
    trace = []
    succeeded = failed = 0
    warnings = {"LLM_TRAINING_SUMMARY_DISCLOSED"}
    rejected_responses: list[str] = []
    for iteration in range(config.search_iterations):
        island = islands[int(rng.integers(len(islands)))]
        # Global best is always visible: small interactive budgets should not
        # spend every call on untouched islands. The second example explores.
        pool = sorted(
            (f for f in cache.values() if f is not None),
            key=lambda f: (f.mse, f.hypothesis.hypothesis_id),
        )
        experience = pool[:1]
        choices = [
            f
            for f in (island or pool)
            if not experience or f.hypothesis != experience[0].hypothesis
        ]
        if choices:
            scores = np.asarray(
                [-f.mse / max(float(np.var(yv)), 1e-12) for f in choices]
            )
            weights = np.exp(np.maximum((scores - max(scores)) / 0.1, -700))
            experience.append(
                choices[int(rng.choice(len(choices), p=weights / weights.sum()))]
            )
        request = {
            "prompt_version": PROMPT_VERSION,
            "grammar_version": GRAMMAR_VERSION,
            "iteration": iteration,
            "coordinate": "t=(x-A)/(B-A) in [0,1] within each branch",
            "training_summary": summary,
            "experience": [
                {"hypothesis": f.hypothesis.to_dict(), "fitness_negative_mse": -f.mse}
                for f in experience
            ],
            "previous_rejections": rejected_responses[-4:]
            + [r["status"] for r in trace[-4:] if r["status"] != "VALID"],
            "task": "Discover 1-4 NEW nonlinear expressions outside the registry. The linear t tree and simple registry families will be rejected. Follow the valid examples in the system instruction and adapt to this training data.",
            "evaluation": {
                "fitness": "negative MSE after numeric optimization and rounding",
                "shape_parameter_bounds": list(SHAPE_BOUNDS),
                "coefficient_decimals": 3,
            },
            "limits": {
                "degree": 3,
                "branches": 2,
                "nodes": MAX_NODES,
                "depth": MAX_DEPTH,
                "parameters": MAX_PARAMETERS,
            },
        }
        try:
            proposals = provider.sample(request)
            if not 1 <= len(proposals) <= 4 or not all(
                isinstance(h, FormulaHypothesis) for h in proposals
            ):
                raise ValueError("INVALID_SAMPLER")
        except Exception as error:
            failed += 1
            code = (
                str(error)
                if isinstance(error, FormulaProviderError)
                else "LLM_INVALID_FORMULA_RESPONSE"
            )
            # Only codes constructed by our adapter can enter public output.
            if code not in {
                "LLM_CONFIG_MISSING",
                "LLM_RESPONSE_SIZE",
                "LLM_TRANSPORT_FAILURE",
                "LLM_INVALID_FORMULA_RESPONSE",
                "LLM_OUTPUT_TRUNCATED",
                "LLM_FORMULA_ALREADY_IN_REGISTRY",
                "LLM_POLYNOMIAL_DEGREE_LIMIT",
                "LLM_TRANSCENDENTAL_NESTING",
                "LLM_FORMULA_COMPLEXITY",
                "LLM_EXPRESSION_COMPLEXITY",
            } and not (code.startswith("LLM_HTTP_") and code[9:].isdigit()):
                code = "LLM_PROVIDER_FAILURE"
            warnings.add(code)
            rejected_responses.append(code)
            if code in {
                "LLM_HTTP_400",
                "LLM_HTTP_401",
                "LLM_HTTP_402",
                "LLM_HTTP_403",
                "LLM_HTTP_404",
            }:
                break
            continue
        succeeded += 1
        for code in getattr(provider, "rejections", ()):
            if code in {
                "LLM_FORMULA_ALREADY_IN_REGISTRY",
                "LLM_POLYNOMIAL_DEGREE_LIMIT",
                "LLM_TRANSCENDENTAL_NESTING",
                "LLM_FORMULA_COMPLEXITY",
                "LLM_EXPRESSION_COMPLEXITY",
                "LLM_INVALID_FORMULA_RESPONSE",
            }:
                rejected_responses.append(code)
                warnings.add(code)
                warnings.add("LLM_FORMULA_PROPOSALS_REJECTED")
        for hypothesis in proposals:
            if hypothesis.hypothesis_id in cache:
                continue
            try:
                fitted = fit_formula(xv, yv, hypothesis, resolved)
            except (
                ValueError,
                FloatingPointError,
                OverflowError,
                np.linalg.LinAlgError,
            ):
                fitted = None
            cache[hypothesis.hypothesis_id] = fitted
            trace.append(
                {
                    "iteration": iteration,
                    "hypothesis": hypothesis.to_dict(),
                    "hypothesis_id": hypothesis.hypothesis_id,
                    "status": "VALID" if fitted else "NO_CERTIFIED_FIT",
                    "fitness_negative_mse": None if fitted is None else -fitted.mse,
                    "evaluations": 0 if fitted is None else fitted.evaluations,
                    "budget_exhausted": False
                    if fitted is None
                    else fitted.budget_exhausted,
                }
            )
            if fitted is not None:
                island.append(fitted)
        if (iteration + 1) % 32 == 0:
            ranked = sorted(
                islands, key=lambda items: min((f.mse for f in items), default=math.inf)
            )
            founders = [
                min(items, key=lambda f: f.mse) for items in ranked[:5] if items
            ]
            islands = ranked[:5] + [
                [founders[i % len(founders)]] if founders else [] for i in range(5)
            ]
    valid = [f for f in cache.values() if f is not None]
    # Training-only BIC guards excessive complexity; test labels never select a tree.
    floor = max(float(np.var(yv)), 1.0) * 1e-12
    best = (
        min(
            valid,
            key=lambda f: (
                len(yv) * math.log(max(f.mse, floor))
                + (f.hypothesis.parameter_count + int(f.model.segment_count == 2))
                * math.log(len(yv)),
                f.hypothesis.hypothesis_id,
            ),
        )
        if valid
        else None
    )
    if failed:
        warnings.add(
            "LLM_FORMULA_PARTIAL_FAILURE" if best else "LLM_FORMULA_UNAVAILABLE"
        )
    if best is None:
        warnings.add("LLM_FORMULA_NO_VALID_CANDIDATE")
    usage = dict(getattr(provider, "usage", {}))
    usage["http_attempts"] = getattr(provider, "http_attempts", succeeded + failed)
    return FormulaSearchResult(
        "ACCEPTED" if best else "UNAVAILABLE",
        best,
        tuple(trace),
        succeeded,
        failed,
        tuple(sorted(warnings)),
        usage,
    )
