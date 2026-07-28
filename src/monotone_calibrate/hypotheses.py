"""Typed equation skeletons for LLM-SR-style calibration search.

The language model is allowed to choose only structures from this module.  It
never supplies executable code or fitted numeric coefficients: the fitting
engine owns parameter optimization, quantization, and certification.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Literal, Mapping

from .registry import FAMILY_IDS, family_spec


Structure = Literal["P1", "P2"]
HYPOTHESIS_SCHEMA_VERSION = "typed-equation-hypothesis-v1"


@dataclass(frozen=True, slots=True)
class EquationHypothesis:
    """One equation-program skeleton with numeric parameters left implicit."""

    structure: Structure
    family_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.structure not in {"P1", "P2"}:
            raise ValueError("hypothesis structure must be P1 or P2")
        families = tuple(str(value) for value in self.family_ids)
        expected = 1 if self.structure == "P1" else 2
        if len(families) != expected:
            raise ValueError("P1 hypotheses need one family and P2 hypotheses need two")
        for family_id in families:
            family_spec(family_id)
        if self.structure == "P2" and families == ("constant_v1", "constant_v1"):
            raise ValueError("two constant branches collapse to canonical P1")
        object.__setattr__(self, "family_ids", families)

    @property
    def hypothesis_id(self) -> str:
        return f"{self.structure}:" + "/".join(self.family_ids)

    @property
    def complexity(self) -> int:
        parameters = sum(family_spec(value).free_parameter_count for value in self.family_ids)
        return parameters + (1 if self.structure == "P2" else 0)

    @property
    def program_length(self) -> int:
        """Stable proxy used by the paper's shorter-program sampling rule."""

        return len(
            json.dumps(
                self.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "structure": self.structure,
            "family_ids": list(self.family_ids),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EquationHypothesis:
        if set(value) != {"structure", "family_ids"}:
            raise ValueError("hypothesis object has unknown or missing fields")
        structure = value["structure"]
        families = value["family_ids"]
        if structure not in {"P1", "P2"} or not isinstance(families, list):
            raise ValueError("hypothesis structure or family_ids is invalid")
        if not all(isinstance(item, str) for item in families):
            raise ValueError("hypothesis family IDs must be strings")
        return cls(structure, tuple(families))  # type: ignore[arg-type]


def _hypothesis_order(hypothesis: EquationHypothesis) -> tuple[object, ...]:
    return (
        0 if hypothesis.structure == "P1" else 1,
        *(FAMILY_IDS.index(family_id) for family_id in hypothesis.family_ids),
    )


@dataclass(frozen=True, slots=True)
class HypothesisSpace:
    """A canonical, finite portfolio searched by the numerical engine."""

    hypotheses: tuple[EquationHypothesis, ...]
    source: Literal["full_registry", "llm_sr"] = "full_registry"

    def __post_init__(self) -> None:
        if self.source not in {"full_registry", "llm_sr"}:
            raise ValueError("unknown hypothesis-space source")
        unique: dict[str, EquationHypothesis] = {}
        for raw in self.hypotheses:
            if not isinstance(raw, EquationHypothesis):
                raise ValueError("hypothesis space contains a non-hypothesis value")
            unique[raw.hypothesis_id] = raw
        canonical = tuple(sorted(unique.values(), key=_hypothesis_order))
        if not any(item.structure == "P1" for item in canonical):
            raise ValueError("hypothesis space must contain at least one P1 skeleton")
        if not any(item.structure == "P2" for item in canonical):
            raise ValueError("hypothesis space must contain at least one P2 skeleton")
        object.__setattr__(self, "hypotheses", canonical)

    @property
    def p1_family_ids(self) -> tuple[str, ...]:
        return tuple(item.family_ids[0] for item in self.hypotheses if item.structure == "P1")

    @property
    def p2_family_pairs(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (item.family_ids[0], item.family_ids[1])
            for item in self.hypotheses
            if item.structure == "P2"
        )

    @property
    def space_hash(self) -> str:
        payload = {
            "schema_version": HYPOTHESIS_SCHEMA_VERSION,
            "hypotheses": [item.to_dict() for item in self.hypotheses],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(encoded).hexdigest()

    @classmethod
    def full_registry(cls) -> HypothesisSpace:
        hypotheses = [EquationHypothesis("P1", (family_id,)) for family_id in FAMILY_IDS]
        hypotheses.extend(
            EquationHypothesis("P2", (left, right))
            for left in FAMILY_IDS
            for right in FAMILY_IDS
            if (left, right) != ("constant_v1", "constant_v1")
        )
        return cls(tuple(hypotheses), "full_registry")

    @classmethod
    def linear_seeds(cls) -> HypothesisSpace:
        """Adapt the paper's linear seed to the project's P1/P2 comparison."""

        return cls(
            (
                EquationHypothesis("P1", ("constant_v1",)),
                EquationHypothesis("P1", ("poly1_v1",)),
                EquationHypothesis("P2", ("poly1_v1", "poly1_v1")),
            ),
            "llm_sr",
        )

    @classmethod
    def safe_search_seeds(cls) -> HypothesisSpace:
        """Keep a complete P1 baseline while the LLM explores P2 structures."""

        hypotheses = [
            EquationHypothesis("P1", (family_id,))
            for family_id in FAMILY_IDS
        ]
        hypotheses.append(EquationHypothesis("P2", ("poly1_v1", "poly1_v1")))
        return cls(tuple(hypotheses), "llm_sr")
