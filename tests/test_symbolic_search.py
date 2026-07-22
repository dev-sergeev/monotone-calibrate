from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from monotone_calibrate.config import LLMConfig
from monotone_calibrate.engine import FitOptions, fit_candidates
from monotone_calibrate.hypotheses import EquationHypothesis, HypothesisSpace
from monotone_calibrate.symbolic_search import (
    ChatOpenAIHypothesisSampler,
    OUTPUT_SCHEMA_VERSION,
    SymbolicSearchOptions,
    run_symbolic_search,
)


def _enabled_config() -> LLMConfig:
    return LLMConfig.from_mapping(
        {
            "MONOTONE_CALIBRATE_LLM_ENABLED": "true",
            "MONOTONE_CALIBRATE_LLM_MODEL": "compatible-model",
            "MONOTONE_CALIBRATE_LLM_BASE_URL": "https://llm.example.test/v1",
            "MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN": "boundary-secret",
        }
    )


def test_equation_hypothesis_rejects_a_runtime_unknown_structure() -> None:
    with pytest.raises(ValueError, match="structure"):
        EquationHypothesis("P3", ())  # type: ignore[arg-type]


def test_chat_sampler_requests_stochastic_typed_hypotheses_without_executable_code() -> None:
    captured: dict[str, object] = {}

    class FakeChat:
        def invoke(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "schema_version": OUTPUT_SCHEMA_VERSION,
                        "hypotheses": [
                            {"structure": "P1", "family_ids": ["logistic_v1"]},
                            {
                                "structure": "P2",
                                "family_ids": ["log_shift_v1", "poly2_v1"],
                            },
                        ],
                    }
                )
            )

    def fake_factory(**kwargs):
        captured["kwargs"] = kwargs
        return FakeChat()

    sampler = ChatOpenAIHypothesisSampler(
        _enabled_config(),
        chat_factory=fake_factory,
    )
    hypotheses = sampler.sample({"training_summary": {"observation_count": 20}})

    assert captured["kwargs"] == {
        "model": "compatible-model",
        "base_url": "https://llm.example.test/v1",
        "api_key": "boundary-secret",
        "temperature": 0.8,
        "timeout": 20,
        "max_retries": 1,
        "streaming": False,
        "stream_usage": False,
        "disable_streaming": True,
        "use_responses_api": False,
    }
    assert hypotheses == (
        EquationHypothesis("P1", ("logistic_v1",)),
        EquationHypothesis("P2", ("log_shift_v1", "poly2_v1")),
    )
    messages = captured["messages"]
    assert "boundary-secret" not in repr(messages)
    assert "Return no prose" in messages[0].content


@pytest.mark.parametrize(
    "content",
    [
        "```python\ndef equation(x): return x\n```",
        '{"schema_version":"llm-sr-hypotheses-v1","hypotheses":[],"extra":1}',
        '{"schema_version":"llm-sr-hypotheses-v1","hypotheses":['
        '{"structure":"P1","family_ids":["unknown_family"]}]}',
        '{"schema_version":"llm-sr-hypotheses-v1","hypotheses":['
        '{"structure":"P2","family_ids":["constant_v1","constant_v1"]}]}',
        '{"schema_version":"llm-sr-hypotheses-v1","hypotheses":['
        '{"structure":"P1","family_ids":["poly1_v1"],"code":"eval(x)"}]}',
    ],
)
def test_chat_sampler_atomically_rejects_code_unknown_families_and_malformed_output(
    content: str,
) -> None:
    class FakeChat:
        def invoke(self, _messages):
            return SimpleNamespace(content=content)

    sampler = ChatOpenAIHypothesisSampler(
        _enabled_config(),
        chat_factory=lambda **_kwargs: FakeChat(),
    )

    with pytest.raises(ValueError):
        sampler.sample({"training_summary": {"observation_count": 20}})


def test_search_iteratively_evaluates_hypotheses_and_returns_a_frozen_portfolio() -> None:
    x = np.linspace(0.0, 10.0, 24)
    t = x / 10.0
    y = 1.0 + 0.3 * t + 2.0 * t**2

    class FakeSampler:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        def sample(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return (
                    EquationHypothesis("P1", ("poly2_v1",)),
                    EquationHypothesis("P2", ("poly1_v1", "poly2_v1")),
                )
            return (
                EquationHypothesis("P1", ("logistic_v1",)),
                EquationHypothesis("P2", ("poly2_v1", "poly2_v1")),
            )

    sampler = FakeSampler()
    result = run_symbolic_search(
        x,
        y,
        _enabled_config(),
        FitOptions(),
        options=SymbolicSearchOptions(iterations=2, random_seed=7),
        sampler=sampler,
    )

    assert result.status == "ACCEPTED"
    assert result.calls_succeeded == 2
    assert result.hypotheses_proposed == 4
    assert result.hypotheses_accepted == 4
    assert result.hypotheses_buffered == 4
    assert result.hypothesis_space.source == "llm_sr"
    assert "poly2_v1" in result.hypothesis_space.p1_family_ids
    assert ("poly1_v1", "poly2_v1") in result.hypothesis_space.p2_family_pairs
    assert all(
        isinstance(item["fitness_negative_mse"], float)
        for request in sampler.requests
        for item in request["experience"]
    )
    assert all(len(request["experience"]) == 2 for request in sampler.requests)

    fitted = fit_candidates(
        x,
        y,
        FitOptions(hypothesis_space=result.hypothesis_space),
    )
    assert fitted.hypothesis_space.space_hash == result.hypothesis_space.space_hash
    assert fitted.one.model.family_ids == ("poly2_v1",)


def test_failed_sampling_falls_back_to_the_complete_deterministic_registry() -> None:
    class FailingSampler:
        def sample(self, _request):
            raise TimeoutError("provider detail must not escape")

    x = np.arange(12, dtype=float)
    y = 1.0 + 0.5 * x
    result = run_symbolic_search(
        x,
        y,
        _enabled_config(),
        FitOptions(),
        options=SymbolicSearchOptions(iterations=2),
        sampler=FailingSampler(),
    )

    assert result.status == "FALLBACK"
    assert result.calls_failed == 2
    assert result.hypothesis_space == HypothesisSpace.full_registry()
    assert result.warning_codes == (
        "LLM_TRAINING_SUMMARY_DISCLOSED",
        "LLM_SR_SEARCH_UNAVAILABLE",
    )
    assert "provider detail" not in repr(result)
