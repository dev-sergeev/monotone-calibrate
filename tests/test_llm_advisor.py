from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import numpy as np

from monotone_calibrate.config import LLMConfig, LLMConfigError
from monotone_calibrate.llm_advisor import (
    ChatOpenAIStartAdvisor,
    ReplaceableStartSlot,
    StartSuggestion,
    summarize_training,
)


def test_llm_configuration_is_offline_by_default_and_keeps_secrets_out_of_repr() -> None:
    offline = LLMConfig.from_mapping({})

    assert offline.enabled is False
    assert offline.timeout_seconds == 20
    assert offline.max_retries == 1
    assert offline.search_iterations == 4

    enabled = LLMConfig.from_mapping(
        {
            "MONOTONE_CALIBRATE_LLM_ENABLED": "true",
            "MONOTONE_CALIBRATE_LLM_MODEL": "compatible-model",
            "MONOTONE_CALIBRATE_LLM_BASE_URL": "https://llm.example.test/v1",
            "MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN": "never-print-this-token",
        }
    )

    assert enabled.enabled is True
    assert "never-print-this-token" not in repr(enabled)


def test_enabled_configuration_requires_explicit_credentials_and_a_safe_endpoint() -> None:
    with pytest.raises(LLMConfigError) as missing:
        LLMConfig.from_mapping({"MONOTONE_CALIBRATE_LLM_ENABLED": "true"})
    assert missing.value.code == "LLM_CONFIG_MISSING"

    common = {
        "MONOTONE_CALIBRATE_LLM_ENABLED": "true",
        "MONOTONE_CALIBRATE_LLM_MODEL": "compatible-model",
        "MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN": "secret",
    }
    with pytest.raises(LLMConfigError) as unsafe:
        LLMConfig.from_mapping(
            {**common, "MONOTONE_CALIBRATE_LLM_BASE_URL": "http://models.example/v1"}
        )
    assert unsafe.value.code == "LLM_CONFIG_INSECURE_HTTP"

    assert LLMConfig.from_mapping(
        {**common, "MONOTONE_CALIBRATE_LLM_BASE_URL": "http://127.0.0.1:8080/v1"}
    ).base_url == "http://127.0.0.1:8080/v1"
    assert LLMConfig.from_mapping(
        {
            **common,
            "MONOTONE_CALIBRATE_LLM_BASE_URL": "http://models.example/v1",
            "MONOTONE_CALIBRATE_LLM_ALLOW_INSECURE_HTTP": "true",
        }
    ).allow_insecure_http is True


@pytest.mark.parametrize(
    ("name", "value", "code"),
    [
        ("MONOTONE_CALIBRATE_LLM_ENABLED", "yes", "LLM_CONFIG_BOOLEAN"),
        ("MONOTONE_CALIBRATE_LLM_TIMEOUT_SECONDS", "0", "LLM_CONFIG_RANGE"),
        ("MONOTONE_CALIBRATE_LLM_TIMEOUT_SECONDS", "121", "LLM_CONFIG_RANGE"),
        ("MONOTONE_CALIBRATE_LLM_MAX_RETRIES", "-1", "LLM_CONFIG_RANGE"),
        ("MONOTONE_CALIBRATE_LLM_MAX_RETRIES", "4", "LLM_CONFIG_RANGE"),
        ("MONOTONE_CALIBRATE_LLM_SEARCH_ITERATIONS", "0", "LLM_CONFIG_RANGE"),
    ],
)
def test_environment_values_are_exact_and_bounded(name: str, value: str, code: str) -> None:
    with pytest.raises(LLMConfigError) as captured:
        LLMConfig.from_mapping({name: value})

    assert captured.value.code == code


def test_dotenv_loader_uses_only_named_settings_and_environment_wins(tmp_path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "MONOTONE_CALIBRATE_LLM_ENABLED=true\n"
        "MONOTONE_CALIBRATE_LLM_MODEL=file-model\n"
        "MONOTONE_CALIBRATE_LLM_BASE_URL=http://localhost:8080/v1\n"
        "MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN=file-secret\n"
        "OPENAI_API_KEY=must-not-be-used\n",
        encoding="utf-8",
    )

    config = LLMConfig.load(
        dotenv,
        environ={"MONOTONE_CALIBRATE_LLM_MODEL": "environment-model"},
    )

    assert config.model == "environment-model"
    assert config.access_token == "file-secret"


def test_training_summary_is_numeric_deterministic_and_bounded_to_64_x_bins() -> None:
    x = np.repeat(np.arange(100, dtype=float), 10)
    y = 2.0 + 0.5 * x + np.tile(np.linspace(-0.2, 0.2, 10), 100)

    first = summarize_training(x, y)
    second = summarize_training(x[::-1], y[::-1])
    payload = first.to_payload()

    assert first == second
    assert first.observation_count == 1_000
    assert 1 <= len(first.bins) <= 64
    assert sum(item.observation_count for item in first.bins) == 1_000
    assert set(payload) == {
        "observation_count",
        "x_min",
        "x_max",
        "y_min",
        "y_max",
        "y_mean",
        "bins",
        "replaceable_slots",
    }
    assert all(
        isinstance(value, (int, float))
        for item in payload["bins"]
        for value in item.values()
    )


def test_disabled_advisor_does_not_construct_or_call_the_external_client() -> None:
    def forbidden_factory(**_kwargs):
        raise AssertionError("the ChatOpenAI boundary must remain untouched")

    advisor = ChatOpenAIStartAdvisor(
        LLMConfig.from_mapping({}),
        chat_factory=forbidden_factory,
    )
    result = advisor.advise(
        summarize_training(np.asarray([0.0, 1.0]), np.asarray([2.0, 3.0]))
    )

    assert result.status == "DISABLED"
    assert result.suggestions == ()
    assert result.warning_code is None


def test_enabled_advisor_wires_chatopenai_explicitly_and_accepts_known_bounded_slots() -> None:
    captured: dict[str, object] = {}

    class FakeChatClient:
        def invoke(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "schema_version": "llm-start-advice-v1",
                        "suggestions": [
                            {"slot_id": "scope:p1:exp:s02", "parameter_vector": [-3.5]},
                            {
                                "slot_id": "scope:p1:logistic:s03",
                                "parameter_vector": [7.0, 0.4],
                            },
                        ],
                    }
                )
            )

    def fake_chat_factory(**kwargs):
        captured["kwargs"] = kwargs
        return FakeChatClient()

    config = LLMConfig.from_mapping(
        {
            "MONOTONE_CALIBRATE_LLM_ENABLED": "true",
            "MONOTONE_CALIBRATE_LLM_MODEL": "compatible-model",
            "MONOTONE_CALIBRATE_LLM_BASE_URL": "https://llm.example.test/v1",
            "MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN": "boundary-secret",
            "MONOTONE_CALIBRATE_LLM_TIMEOUT_SECONDS": "17",
            "MONOTONE_CALIBRATE_LLM_MAX_RETRIES": "2",
        }
    )
    summary = summarize_training(
        np.asarray([0.0, 1.0, 2.0, 3.0]),
        np.asarray([1.0, 1.4, 2.2, 3.1]),
        replaceable_slots=(
            ReplaceableStartSlot(
                "scope:p1:exp:s02", ((-12.0, -0.1),), (-6.05,)
            ),
            ReplaceableStartSlot(
                "scope:p1:logistic:s03",
                ((0.25, 20.0), (0.0, 1.0)),
                (5.0, 0.35),
            ),
        ),
    )

    result = ChatOpenAIStartAdvisor(config, chat_factory=fake_chat_factory).advise(summary)

    assert captured["kwargs"] == {
        "model": "compatible-model",
        "base_url": "https://llm.example.test/v1",
        "api_key": "boundary-secret",
        "temperature": 0,
        "timeout": 17,
        "max_retries": 2,
        "streaming": False,
        "stream_usage": False,
        "disable_streaming": True,
        "use_responses_api": False,
    }
    assert result.status == "ACCEPTED"
    assert result.suggestions == (
        StartSuggestion("scope:p1:exp:s02", (-3.5,)),
        StartSuggestion("scope:p1:logistic:s03", (7.0, 0.4)),
    )
    messages = captured["messages"]
    assert len(messages) == 2
    assert "boundary-secret" not in repr(messages)
    request = json.loads(messages[1].content)
    assert request == {"training_summary": summary.to_payload()}


@pytest.mark.parametrize(
    "content",
    [
        "not JSON",
        '{"schema_version":"llm-start-advice-v1","suggestions":[],"extra":1}',
        '{"schema_version":"llm-start-advice-v1","suggestions":['
        '{"slot_id":"unknown:s02","parameter_vector":[-3.0]}]}',
        '{"schema_version":"llm-start-advice-v1","suggestions":['
        '{"slot_id":"scope:p1:exp:s02","parameter_vector":[-3.0]},'
        '{"slot_id":"scope:p1:exp:s02","parameter_vector":[-4.0]}]}',
        '{"schema_version":"llm-start-advice-v1","suggestions":['
        '{"slot_id":"scope:p1:exp:s02","parameter_vector":[-3.0,0.2]}]}',
        '{"schema_version":"llm-start-advice-v1","suggestions":['
        '{"slot_id":"scope:p1:exp:s02","parameter_vector":[0.0]}]}',
        '{"schema_version":"llm-start-advice-v1","suggestions":['
        '{"slot_id":"scope:p1:exp:s02","parameter_vector":[NaN]}]}',
        '{"schema_version":"llm-start-advice-v1","schema_version":'
        '"llm-start-advice-v1","suggestions":[]}',
    ],
)
def test_any_malformed_or_invalid_suggestion_atomically_keeps_deterministic_starts(
    content: str,
) -> None:
    class FakeChatClient:
        def invoke(self, _messages):
            return SimpleNamespace(content=content)

    config = LLMConfig.from_mapping(
        {
            "MONOTONE_CALIBRATE_LLM_ENABLED": "true",
            "MONOTONE_CALIBRATE_LLM_MODEL": "compatible-model",
            "MONOTONE_CALIBRATE_LLM_BASE_URL": "https://llm.example.test/v1",
            "MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN": "secret-not-in-result",
        }
    )
    summary = summarize_training(
        np.asarray([0.0, 1.0]),
        np.asarray([2.0, 3.0]),
        replaceable_slots=(
            ReplaceableStartSlot(
                "scope:p1:exp:s02", ((-12.0, -0.1),), (-6.05,)
            ),
        ),
    )

    result = ChatOpenAIStartAdvisor(
        config,
        chat_factory=lambda **_kwargs: FakeChatClient(),
    ).advise(summary)

    assert result.status == "FALLBACK"
    assert result.suggestions == ()
    assert result.warning_code == "LLM_ADVISOR_UNAVAILABLE"
    assert "secret-not-in-result" not in repr(result)


def test_provider_exception_is_a_typed_deterministic_fallback() -> None:
    class FailingChatClient:
        def invoke(self, _messages):
            raise TimeoutError("provider included sensitive diagnostics")

    config = LLMConfig.from_mapping(
        {
            "MONOTONE_CALIBRATE_LLM_ENABLED": "true",
            "MONOTONE_CALIBRATE_LLM_MODEL": "compatible-model",
            "MONOTONE_CALIBRATE_LLM_BASE_URL": "https://llm.example.test/v1",
            "MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN": "secret-not-in-result",
        }
    )
    result = ChatOpenAIStartAdvisor(
        config,
        chat_factory=lambda **_kwargs: FailingChatClient(),
    ).advise(summarize_training(np.asarray([0.0]), np.asarray([1.0])))

    assert result.status == "FALLBACK"
    assert result.suggestions == ()
    assert result.warning_code == "LLM_ADVISOR_UNAVAILABLE"
    assert "sensitive diagnostics" not in repr(result)
