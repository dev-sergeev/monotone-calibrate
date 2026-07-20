"""Bounded optional LLM advice for deterministic nonlinear start values.

This module is intentionally outside the mathematical model runtime. Advice
can only name a predeclared replaceable slot and supply a bounded nonlinear
vector; an empty result leaves every deterministic start unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any, Callable, Literal, Protocol

import numpy as np
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from monotone_calibrate.config import LLMConfig


MAX_TRAINING_BINS = 64
MAX_REPLACEABLE_SLOTS = 4096
MAX_RESPONSE_CHARACTERS = 262_144
_SLOT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:~-]{0,255}$")


@dataclass(frozen=True, slots=True)
class ReplaceableStartSlot:
    """One predeclared nonlinear start that advice is allowed to replace."""

    slot_id: str
    bounds: tuple[tuple[float, float], ...]
    default_vector: tuple[float, ...]

    def __post_init__(self) -> None:
        if not _SLOT_ID.fullmatch(self.slot_id):
            raise ValueError("slot_id does not match the start-advice contract")
        if (
            not 1 <= len(self.bounds) <= 16
            or len(self.default_vector) != len(self.bounds)
        ):
            raise ValueError("slot bounds and default vector must have the same length in 1..16")
        for (lower, upper), default in zip(self.bounds, self.default_vector, strict=True):
            if not all(math.isfinite(float(value)) for value in (lower, upper, default)):
                raise ValueError("slot bounds and defaults must be finite")
            if float(lower) > float(upper) or not float(lower) <= float(default) <= float(
                upper
            ):
                raise ValueError("slot default must be within inclusive ordered bounds")

    def to_payload(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "bounds": [[float(lower), float(upper)] for lower, upper in self.bounds],
            "default_vector": [float(value) for value in self.default_vector],
        }


@dataclass(frozen=True, slots=True)
class TrainingBin:
    observation_count: int
    x_min: float
    x_max: float
    x_mean: float
    y_min: float
    y_max: float
    y_mean: float

    def to_payload(self) -> dict[str, int | float]:
        return {
            "observation_count": self.observation_count,
            "x_min": self.x_min,
            "x_max": self.x_max,
            "x_mean": self.x_mean,
            "y_min": self.y_min,
            "y_max": self.y_max,
            "y_mean": self.y_mean,
        }


@dataclass(frozen=True, slots=True)
class TrainingSummary:
    observation_count: int
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    y_mean: float
    bins: tuple[TrainingBin, ...]
    replaceable_slots: tuple[ReplaceableStartSlot, ...] = ()

    def __post_init__(self) -> None:
        if len(self.replaceable_slots) > MAX_REPLACEABLE_SLOTS:
            raise ValueError("too many replaceable start slots")
        slot_ids = tuple(slot.slot_id for slot in self.replaceable_slots)
        if len(set(slot_ids)) != len(slot_ids):
            raise ValueError("replaceable start slot IDs must be unique")

    def to_payload(self) -> dict[str, Any]:
        return {
            "observation_count": self.observation_count,
            "x_min": self.x_min,
            "x_max": self.x_max,
            "y_min": self.y_min,
            "y_max": self.y_max,
            "y_mean": self.y_mean,
            "bins": [item.to_payload() for item in self.bins],
            "replaceable_slots": [item.to_payload() for item in self.replaceable_slots],
        }


@dataclass(frozen=True, slots=True)
class StartSuggestion:
    slot_id: str
    parameter_vector: tuple[float, ...]


AdviceStatus = Literal["DISABLED", "ACCEPTED", "FALLBACK"]


@dataclass(frozen=True, slots=True)
class AdviceResult:
    status: AdviceStatus
    suggestions: tuple[StartSuggestion, ...] = ()
    warning_code: str | None = None


class StartAdvisor(Protocol):
    """Public boundary consumed by the application/fitting orchestration."""

    def advise(self, training_summary: TrainingSummary) -> AdviceResult: ...


class ChatOpenAIStartAdvisor:
    """Stateless ChatOpenAI implementation of the bounded advice boundary."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        chat_factory: Callable[..., Any] = ChatOpenAI,
    ) -> None:
        self._config = config
        self._chat_factory = chat_factory

    def advise(self, training_summary: TrainingSummary) -> AdviceResult:
        if not self._config.enabled:
            return AdviceResult(status="DISABLED")
        try:
            client = self._chat_factory(
                model=self._config.model,
                base_url=self._config.base_url,
                api_key=self._config.access_token,
                temperature=0,
                timeout=self._config.timeout_seconds,
                max_retries=self._config.max_retries,
                streaming=False,
                stream_usage=False,
                disable_streaming=True,
                use_responses_api=False,
            )
            response = client.invoke(
                [
                    SystemMessage(content=_SYSTEM_PROMPT),
                    HumanMessage(
                        content=json.dumps(
                            {"training_summary": training_summary.to_payload()},
                            ensure_ascii=True,
                            allow_nan=False,
                            separators=(",", ":"),
                        )
                    ),
                ]
            )
            content = getattr(response, "content", None)
            suggestions = _parse_suggestions(content, training_summary.replaceable_slots)
        except Exception:
            # Provider, transport and untrusted-output details deliberately do
            # not cross into reports. Deterministic starts remain in force.
            return AdviceResult(
                status="FALLBACK",
                warning_code="LLM_ADVISOR_UNAVAILABLE",
            )
        return AdviceResult(status="ACCEPTED", suggestions=suggestions)


_SYSTEM_PROMPT = """You advise only nonlinear optimizer start vectors.
Return exactly one JSON object with schema_version \"llm-start-advice-v1\" and
suggestions, an array of objects containing only slot_id and parameter_vector.
Use only declared slot IDs, preserve vector lengths, and keep every value in
its inclusive bounds. Omit a slot to keep its deterministic default. Do not
return prose, formulas, code, thresholds, families, or additional fields."""


class _AdviceParseError(ValueError):
    pass


def _parse_suggestions(
    content: object,
    slots: tuple[ReplaceableStartSlot, ...],
) -> tuple[StartSuggestion, ...]:
    if not isinstance(content, str) or len(content) > MAX_RESPONSE_CHARACTERS:
        raise _AdviceParseError("response must be a bounded JSON string")

    def reject_constant(_value: str) -> None:
        raise _AdviceParseError("non-finite JSON number")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _AdviceParseError("duplicate JSON object key")
            result[key] = value
        return result

    try:
        document = json.loads(
            content,
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise _AdviceParseError("invalid JSON") from exc
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "suggestions",
    }:
        raise _AdviceParseError("wrong response object")
    if document["schema_version"] != "llm-start-advice-v1":
        raise _AdviceParseError("wrong advice schema version")
    raw_suggestions = document["suggestions"]
    if (
        not isinstance(raw_suggestions, list)
        or len(raw_suggestions) > MAX_REPLACEABLE_SLOTS
    ):
        raise _AdviceParseError("suggestions must be a bounded array")

    slot_lookup = {slot.slot_id: slot for slot in slots}
    seen: set[str] = set()
    accepted: list[StartSuggestion] = []
    for raw in raw_suggestions:
        if not isinstance(raw, dict) or set(raw) != {"slot_id", "parameter_vector"}:
            raise _AdviceParseError("wrong suggestion object")
        slot_id = raw["slot_id"]
        vector = raw["parameter_vector"]
        if not isinstance(slot_id, str) or slot_id in seen or slot_id not in slot_lookup:
            raise _AdviceParseError("duplicate or unknown slot")
        slot = slot_lookup[slot_id]
        if not isinstance(vector, list) or len(vector) != len(slot.bounds):
            raise _AdviceParseError("wrong parameter vector length")
        values: list[float] = []
        for value, (lower, upper) in zip(vector, slot.bounds, strict=True):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise _AdviceParseError("parameter vector must be numeric")
            numeric = float(value)
            if not math.isfinite(numeric) or not float(lower) <= numeric <= float(
                upper
            ):
                raise _AdviceParseError("parameter vector is outside its slot bounds")
            values.append(numeric)
        seen.add(slot_id)
        accepted.append(StartSuggestion(slot_id=slot_id, parameter_vector=tuple(values)))
    return tuple(accepted)


def summarize_training(
    x: np.ndarray,
    y: np.ndarray,
    *,
    max_bins: int = MAX_TRAINING_BINS,
    replaceable_slots: tuple[ReplaceableStartSlot, ...] = (),
) -> TrainingSummary:
    """Aggregate finite training observations into at most 64 atomic x bins."""

    x_values = np.asarray(x, dtype=np.float64)
    y_values = np.asarray(y, dtype=np.float64)
    if x_values.ndim != 1 or y_values.ndim != 1:
        raise ValueError("training x and y must be one-dimensional")
    if x_values.size == 0 or x_values.size != y_values.size:
        raise ValueError("training x and y must have the same non-zero length")
    if not np.all(np.isfinite(x_values)) or not np.all(np.isfinite(y_values)):
        raise ValueError("training x and y must be finite")
    if (
        isinstance(max_bins, bool)
        or not isinstance(max_bins, int)
        or not 1 <= max_bins <= 64
    ):
        raise ValueError("max_bins must be an integer in 1..64")

    # Sorting on both values makes summaries independent of input row order,
    # including the order of separate observations that share an x value.
    order = np.lexsort((y_values, x_values))
    sorted_x = x_values[order]
    sorted_y = y_values[order]
    _, group_starts = np.unique(sorted_x, return_index=True)
    group_chunks = np.array_split(
        np.arange(group_starts.size), min(max_bins, group_starts.size)
    )

    bins: list[TrainingBin] = []
    for chunk in group_chunks:
        first_group = int(chunk[0])
        final_group = int(chunk[-1]) + 1
        start = int(group_starts[first_group])
        stop = (
            int(group_starts[final_group])
            if final_group < group_starts.size
            else sorted_x.size
        )
        bx = sorted_x[start:stop]
        by = sorted_y[start:stop]
        bins.append(
            TrainingBin(
                observation_count=int(bx.size),
                x_min=float(bx[0]),
                x_max=float(bx[-1]),
                x_mean=float(np.mean(bx)),
                y_min=float(np.min(by)),
                y_max=float(np.max(by)),
                y_mean=float(np.mean(by)),
            )
        )

    return TrainingSummary(
        observation_count=int(sorted_x.size),
        x_min=float(sorted_x[0]),
        x_max=float(sorted_x[-1]),
        y_min=float(np.min(sorted_y)),
        y_max=float(np.max(sorted_y)),
        y_mean=float(np.mean(sorted_y)),
        bins=tuple(bins),
        replaceable_slots=replaceable_slots,
    )
