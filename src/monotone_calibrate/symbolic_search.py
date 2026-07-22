"""LLM-SR-style search over typed monotone equation skeletons.

The paper's generate -> optimize/evaluate -> experience-management loop lives
behind :func:`run_symbolic_search`.  Generated output is strict JSON naming
only audited registry families.  Numeric parameters, breakpoints, monotonicity
and continuity remain owned by the existing fitting engine.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Callable, Literal, Mapping, Protocol

import numpy as np
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .config import LLMConfig
from .engine import FitOptions, fit_hypothesis
from .hypotheses import EquationHypothesis, HypothesisSpace
from .llm_advisor import summarize_training
from .registry import FAMILY_IDS, family_spec


PROMPT_VERSION = "llm-sr-registry-prompt-v1"
OUTPUT_SCHEMA_VERSION = "llm-sr-hypotheses-v1"
MAX_RESPONSE_CHARACTERS = 262_144


@dataclass(frozen=True, slots=True)
class SymbolicSearchOptions:
    """Bounded adaptation of the paper's evolutionary-search settings."""

    iterations: int = 4
    samples_per_prompt: int = 4
    num_islands: int = 10
    experiences_per_prompt: int = 2
    generation_temperature: float = 0.8
    cluster_temperature: float = 0.1
    cluster_temperature_period: int = 10_000
    reset_period_iterations: int = 32
    random_seed: int = 20260722

    def __post_init__(self) -> None:
        integer_bounds = (
            ("iterations", self.iterations, 1, 64),
            ("samples_per_prompt", self.samples_per_prompt, 1, 16),
            ("num_islands", self.num_islands, 2, 32),
            ("experiences_per_prompt", self.experiences_per_prompt, 1, 8),
            ("cluster_temperature_period", self.cluster_temperature_period, 2, 1_000_000),
            ("reset_period_iterations", self.reset_period_iterations, 2, 1_000_000),
            ("random_seed", self.random_seed, 0, 2**63 - 1),
        )
        for name, value, lower, upper in integer_bounds:
            if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
                raise ValueError(f"{name} must be an integer in {lower}..{upper}")
        for name, value in (
            ("generation_temperature", self.generation_temperature),
            ("cluster_temperature", self.cluster_temperature),
        ):
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and positive")


SearchStatus = Literal["ACCEPTED", "FALLBACK"]


@dataclass(frozen=True, slots=True)
class SymbolicSearchResult:
    status: SearchStatus
    hypothesis_space: HypothesisSpace
    iterations_requested: int
    calls_succeeded: int
    calls_failed: int
    hypotheses_proposed: int
    hypotheses_evaluated: int
    hypotheses_accepted: int
    hypotheses_buffered: int
    warning_codes: tuple[str, ...] = ()

    @property
    def calls_requested(self) -> int:
        return self.calls_succeeded + self.calls_failed


class HypothesisSampler(Protocol):
    """Adapter seam for one structured LLM sampling call."""

    def sample(self, request: Mapping[str, object]) -> tuple[EquationHypothesis, ...]: ...


class ChatOpenAIHypothesisSampler:
    """OpenAI-compatible adapter that returns only typed registry skeletons."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        temperature: float = 0.8,
        max_hypotheses: int = 4,
        chat_factory: Callable[..., Any] = ChatOpenAI,
    ) -> None:
        if not config.enabled:
            raise ValueError("LLM configuration must be enabled")
        if (
            isinstance(max_hypotheses, bool)
            or not isinstance(max_hypotheses, int)
            or not 1 <= max_hypotheses <= 16
        ):
            raise ValueError("max_hypotheses must be an integer in 1..16")
        if not math.isfinite(temperature) or temperature <= 0.0:
            raise ValueError("temperature must be finite and positive")
        self._max_hypotheses = max_hypotheses
        self._client = chat_factory(
            model=config.model,
            base_url=config.base_url,
            api_key=config.access_token,
            temperature=temperature,
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
            streaming=False,
            stream_usage=False,
            disable_streaming=True,
            use_responses_api=False,
        )

    def sample(self, request: Mapping[str, object]) -> tuple[EquationHypothesis, ...]:
        response = self._client.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(
                    content=json.dumps(
                        request,
                        ensure_ascii=True,
                        allow_nan=False,
                        separators=(",", ":"),
                    )
                ),
            ]
        )
        return _parse_hypotheses(getattr(response, "content", None), self._max_hypotheses)


_SYSTEM_PROMPT = f"""You are the hypothesis-generation step of LLM-SR for one-dimensional curve calibration.
Propose diverse equation program skeletons using only the declared registry families. Numeric
coefficients are placeholders optimized by the evaluator, so never invent coefficient values.
Use the problem specification, data summary, evaluation rule, and scored experience examples to
improve the structures while balancing exploration and exploitation. P1 has exactly one family;
P2 has exactly two ordered families. Return exactly one JSON object with schema_version
\"{OUTPUT_SCHEMA_VERSION}\" and hypotheses. Each hypothesis contains only structure and
family_ids. Return no prose, Markdown, Python code, formulas, extra fields, or unknown families."""


class _ResponseError(ValueError):
    pass


def _parse_hypotheses(
    content: object,
    maximum: int,
) -> tuple[EquationHypothesis, ...]:
    if not isinstance(content, str) or len(content) > MAX_RESPONSE_CHARACTERS:
        raise _ResponseError("response must be a bounded JSON string")

    def reject_constant(_value: str) -> None:
        raise _ResponseError("non-finite JSON number")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise _ResponseError("duplicate JSON object key")
            value[key] = item
        return value

    try:
        document = json.loads(
            content,
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise _ResponseError("invalid JSON") from exc
    if not isinstance(document, dict) or set(document) != {"schema_version", "hypotheses"}:
        raise _ResponseError("wrong response object")
    if document["schema_version"] != OUTPUT_SCHEMA_VERSION:
        raise _ResponseError("wrong response schema version")
    raw_hypotheses = document["hypotheses"]
    if not isinstance(raw_hypotheses, list) or not 1 <= len(raw_hypotheses) <= maximum:
        raise _ResponseError("hypotheses must be a non-empty bounded array")
    accepted: list[EquationHypothesis] = []
    seen: set[str] = set()
    try:
        for raw in raw_hypotheses:
            if not isinstance(raw, dict):
                raise _ResponseError("each hypothesis must be an object")
            hypothesis = EquationHypothesis.from_dict(raw)
            if hypothesis.hypothesis_id in seen:
                raise _ResponseError("duplicate hypothesis")
            seen.add(hypothesis.hypothesis_id)
            accepted.append(hypothesis)
    except ValueError as exc:
        raise _ResponseError("invalid equation hypothesis") from exc
    return tuple(accepted)


@dataclass(frozen=True, slots=True)
class _Experience:
    hypothesis: EquationHypothesis
    score: float

    @property
    def signature(self) -> tuple[str, float]:
        return self.hypothesis.structure, round(self.score, 12)

    def to_payload(self) -> dict[str, object]:
        return {
            "hypothesis": self.hypothesis.to_dict(),
            "fitness_negative_mse": self.score,
        }


class _Cluster:
    def __init__(self, experience: _Experience) -> None:
        self.score = experience.score
        self.programs: list[_Experience] = [experience]

    def add(self, experience: _Experience) -> bool:
        if any(
            item.hypothesis.hypothesis_id == experience.hypothesis.hypothesis_id
            for item in self.programs
        ):
            return False
        self.programs.append(experience)
        return True

    def sample(self, rng: np.random.Generator) -> _Experience:
        lengths = np.asarray(
            [item.hypothesis.program_length for item in self.programs],
            dtype=np.float64,
        )
        normalized = (lengths - float(np.min(lengths))) / (float(np.max(lengths)) + 1e-6)
        probabilities = _softmax(-normalized, 1.0)
        return self.programs[int(rng.choice(len(self.programs), p=probabilities))]


class _Island:
    def __init__(self) -> None:
        self.clusters: dict[tuple[str, float], _Cluster] = {}
        self.best_scores: dict[str, float] = {"P1": -math.inf, "P2": -math.inf}
        self.num_programs = 0

    @property
    def fitness(self) -> float:
        values = [score for score in self.best_scores.values() if math.isfinite(score)]
        return float(np.mean(values)) if values else -math.inf

    def register(self, experience: _Experience) -> bool:
        structure = experience.hypothesis.structure
        cluster = self.clusters.get(experience.signature)
        if cluster is None:
            self.clusters[experience.signature] = _Cluster(experience)
            inserted = True
        else:
            inserted = cluster.add(experience)
        if not inserted:
            return False
        self.best_scores[structure] = max(
            self.best_scores[structure],
            experience.score,
        )
        self.num_programs += 1
        return True

    def best_experiences(self) -> tuple[_Experience, ...]:
        selected: list[_Experience] = []
        for structure in ("P1", "P2"):
            candidates = [
                cluster
                for (cluster_structure, _), cluster in self.clusters.items()
                if cluster_structure == structure
            ]
            if candidates:
                selected.append(max(candidates, key=lambda cluster: cluster.score).programs[0])
        return tuple(selected)

    def sample(
        self,
        rng: np.random.Generator,
        count: int,
        initial_temperature: float,
        temperature_period: int,
    ) -> tuple[_Experience, ...]:
        if not self.clusters:
            return ()
        chosen: list[_Experience] = []
        structures = ("P1", "P2") if count >= 2 else ("P1",)
        for structure in structures:
            matching = [
                cluster
                for (cluster_structure, _), cluster in self.clusters.items()
                if cluster_structure == structure
            ]
            if matching:
                chosen.append(
                    self._sample_cluster(
                        matching,
                        rng,
                        initial_temperature,
                        temperature_period,
                    ).sample(rng)
                )
            if len(chosen) == count:
                break
        all_clusters = list(self.clusters.values())
        while len(chosen) < count:
            chosen.append(
                self._sample_cluster(
                    all_clusters,
                    rng,
                    initial_temperature,
                    temperature_period,
                ).sample(rng)
            )
        return tuple(sorted(chosen, key=lambda item: (item.score, item.hypothesis.hypothesis_id)))

    def _sample_cluster(
        self,
        clusters: list[_Cluster],
        rng: np.random.Generator,
        initial_temperature: float,
        temperature_period: int,
    ) -> _Cluster:
        temperature = initial_temperature * (
            1.0 - (self.num_programs % temperature_period) / temperature_period
        )
        probabilities = _softmax(
            np.asarray([cluster.score for cluster in clusters], dtype=np.float64),
            max(temperature, np.finfo(np.float64).eps),
        )
        return clusters[int(rng.choice(len(clusters), p=probabilities))]


class _ExperienceBuffer:
    def __init__(self, count: int, seeds: tuple[_Experience, ...]) -> None:
        self.islands = [_Island() for _ in range(count)]
        for island in self.islands:
            for seed in seeds:
                island.register(seed)

    def choose(self, rng: np.random.Generator) -> tuple[int, _Island]:
        index = int(rng.integers(0, len(self.islands)))
        return index, self.islands[index]

    def reset_weakest(self, rng: np.random.Generator) -> None:
        ranked = sorted(range(len(self.islands)), key=lambda index: (self.islands[index].fitness, index))
        reset_count = len(ranked) // 2
        survivors = ranked[reset_count:]
        for index in ranked[:reset_count]:
            founder_index = int(rng.choice(survivors))
            founders = self.islands[founder_index].best_experiences()
            replacement = _Island()
            for founder in founders:
                replacement.register(founder)
            self.islands[index] = replacement


def _softmax(values: np.ndarray, temperature: float) -> np.ndarray:
    if values.ndim != 1 or values.size == 0 or not np.all(np.isfinite(values)):
        raise ValueError("softmax needs a non-empty finite vector")
    scaled = values / temperature
    shifted = scaled - float(np.max(scaled))
    weights = np.exp(shifted)
    total = float(np.sum(weights))
    if not math.isfinite(total) or total <= 0.0:
        return np.full(values.shape, 1.0 / values.size)
    probabilities = weights / total
    correction_index = int(np.argmax(probabilities))
    probability_before = float(np.sum(probabilities[:correction_index]))
    probability_after = float(np.sum(probabilities[correction_index + 1 :]))
    probabilities[correction_index] = 1.0 - probability_before - probability_after
    return probabilities


_SKELETONS = {
    "constant_v1": "params[0]",
    "poly1_v1": "params[0] + params[1]*t",
    "poly2_v1": "params[0] + params[1]*t + params[2]*t**2",
    "poly3_v1": "params[0] + params[1]*t + params[2]*t**2 + params[3]*t**3",
    "exp_affine_v1": "params[0] + params[1]*exp(params[2]*t)",
    "log_shift_v1": "params[0] + params[1]*log(t + params[2])",
    "reciprocal_shift_pos_v1": "params[0] + params[1]/(t + params[2])",
    "logistic_v1": "params[0] + params[1]/(1 + exp(-params[2]*(t-params[3])))",
}


def _registry_payload() -> list[dict[str, object]]:
    return [
        {
            "family_id": family_id,
            "program_skeleton": _SKELETONS[family_id],
            "parameter_count": family_spec(family_id).free_parameter_count,
        }
        for family_id in FAMILY_IDS
    ]


def _prompt_payload(
    x: np.ndarray,
    y: np.ndarray,
    experiences: tuple[_Experience, ...],
    iteration: int,
    options: SymbolicSearchOptions,
) -> dict[str, object]:
    summary = summarize_training(x, y).to_payload()
    summary.pop("replaceable_slots", None)
    return {
        "prompt_version": PROMPT_VERSION,
        "iteration": iteration,
        "problem_specification": {
            "input": "one finite scalar x",
            "target": "one finite scalar y",
            "coordinate": "t=(x-x_min)/(x_max-x_min), constrained to [0,1]",
            "goal": "find interpretable globally monotone calibration curves",
            "P1": "one family on the observed x domain",
            "P2": "two ordered families with a fitted balanced breakpoint and continuous monotone join",
        },
        "training_summary": summary,
        "evaluation": {
            "fitness": "negative mean squared error after numeric parameter optimization",
            "optimizer": "bounded scipy optimizers plus conditional linear least squares",
            "postconditions": [
                "finite on the complete observed interval",
                "analytic monotonicity certificate",
                "thousandth-grid executable coefficients",
                "P2 segment shares each in [0.40,0.60]",
                "P2 direction-preserving continuous join",
            ],
        },
        "registry": _registry_payload(),
        "experience": [item.to_payload() for item in experiences],
        "request": {
            "maximum_hypotheses": options.samples_per_prompt,
            "required_schema_version": OUTPUT_SCHEMA_VERSION,
        },
    }


def run_symbolic_search(
    x: np.ndarray,
    y: np.ndarray,
    config: LLMConfig,
    fit_options: FitOptions,
    *,
    options: SymbolicSearchOptions | None = None,
    sampler: HypothesisSampler | None = None,
) -> SymbolicSearchResult:
    """Discover a finite skeleton portfolio with the LLM-SR evolutionary loop."""

    if not config.enabled:
        raise ValueError("run_symbolic_search requires enabled LLM configuration")
    resolved = options or SymbolicSearchOptions(iterations=config.search_iterations)
    x_values = np.asarray(x, dtype=np.float64)
    y_values = np.asarray(y, dtype=np.float64)
    # The summary performs the complete dimensionality/finiteness validation.
    summarize_training(x_values, y_values)
    proposal_sampler = sampler or ChatOpenAIHypothesisSampler(
        config,
        temperature=resolved.generation_temperature,
        max_hypotheses=resolved.samples_per_prompt,
    )
    rng = np.random.default_rng(resolved.random_seed)
    cache: dict[str, _Experience | None] = {}
    evaluated = 0

    def evaluate(hypothesis: EquationHypothesis) -> _Experience | None:
        nonlocal evaluated
        if hypothesis.hypothesis_id in cache:
            return cache[hypothesis.hypothesis_id]
        evaluated += 1
        try:
            candidate = fit_hypothesis(x_values, y_values, hypothesis, fit_options)
        except (FloatingPointError, OverflowError, ValueError, np.linalg.LinAlgError):
            candidate = None
        if candidate is None:
            experience = None
        else:
            score = -float(candidate.sse / candidate.predictions.size)
            experience = _Experience(hypothesis, score) if math.isfinite(score) else None
        cache[hypothesis.hypothesis_id] = experience
        return experience

    seeds = HypothesisSpace.linear_seeds().hypotheses
    seed_experiences = tuple(item for item in (evaluate(seed) for seed in seeds) if item is not None)
    if not any(item.hypothesis.structure == "P1" for item in seed_experiences):
        raise ValueError("linear LLM-SR initialization produced no valid P1")
    buffer = _ExperienceBuffer(resolved.num_islands, seed_experiences)
    portfolio: dict[str, EquationHypothesis] = {
        hypothesis.hypothesis_id: hypothesis for hypothesis in seeds
    }
    calls_succeeded = 0
    calls_failed = 0
    proposed = 0
    accepted = 0
    buffered = 0

    for iteration in range(resolved.iterations):
        island_index, island = buffer.choose(rng)
        experiences = island.sample(
            rng,
            resolved.experiences_per_prompt,
            resolved.cluster_temperature,
            resolved.cluster_temperature_period,
        )
        try:
            hypotheses = proposal_sampler.sample(
                _prompt_payload(x_values, y_values, experiences, iteration, resolved)
            )
            if len(hypotheses) > resolved.samples_per_prompt:
                raise ValueError("sampler exceeded samples_per_prompt")
        except Exception:
            calls_failed += 1
            continue
        calls_succeeded += 1
        proposed += len(hypotheses)
        for hypothesis in hypotheses:
            was_known = hypothesis.hypothesis_id in cache
            experience = evaluate(hypothesis)
            if experience is None or was_known:
                continue
            accepted += 1
            portfolio[hypothesis.hypothesis_id] = hypothesis
            if buffer.islands[island_index].register(experience):
                buffered += 1
        if (iteration + 1) % resolved.reset_period_iterations == 0:
            buffer.reset_weakest(rng)

    if accepted == 0:
        warning = "LLM_SR_SEARCH_UNAVAILABLE" if calls_succeeded == 0 else "LLM_SR_NO_VALID_HYPOTHESES"
        return SymbolicSearchResult(
            "FALLBACK",
            HypothesisSpace.full_registry(),
            resolved.iterations,
            calls_succeeded,
            calls_failed,
            proposed,
            evaluated,
            accepted,
            buffered,
            ("LLM_TRAINING_SUMMARY_DISCLOSED", warning),
        )

    warnings = [
        "LLM_TRAINING_SUMMARY_DISCLOSED",
        "LLM_SR_PORTFOLIO_CONDITIONAL_VALIDATION",
    ]
    if calls_failed:
        warnings.append("LLM_SR_PARTIAL_FAILURE")
    return SymbolicSearchResult(
        "ACCEPTED",
        HypothesisSpace(tuple(portfolio.values()), "llm_sr"),
        resolved.iterations,
        calls_succeeded,
        calls_failed,
        proposed,
        evaluated,
        accepted,
        buffered,
        tuple(warnings),
    )
