from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import httpx
import numpy as np
import pytest
from langsmith import get_tracing_context

from monotone_calibrate.config import GIGACHAT_BASE_URL, LLMConfig, LLMConfigError
from monotone_calibrate.formula_search import (
    FormulaFitOptions,
    FormulaProviderError,
    HTTPFormulaSampler,
    make_formula_sampler,
    run_formula_search,
)
from monotone_calibrate.gigachat_sampler import GigaChatFormulaSampler

PREFIX = "MONOTONE_CALIBRATE_LLM_"
BRANCH = {"op": "sqrt1p", "arg": {"op": "scale", "arg": {"op": "t"}}}


def config(**overrides):
    values = {
        "ENABLED": "true",
        "PROVIDER": "gigachat",
        "MODEL": "GigaChat-2",
        "GIGACHAT_CREDENTIALS": "explicit-auth-key",
        "SEARCH_ITERATIONS": "1",
    }
    values.update(overrides)
    return LLMConfig.from_mapping(
        {PREFIX + key: value for key, value in values.items()}
    )


def install_transport(
    monkeypatch, *, hypotheses=None, content=None, statuses=(), finish="stop"
):
    """Exercise the real LangChain wrapper, SDK, OAuth and response conversion."""
    requests = []
    clients = []
    pending = iter(statuses)
    client_class = httpx.Client

    def handle(request):
        requests.append(request)
        assert get_tracing_context()["enabled"] is False
        if request.url.path.endswith("/oauth"):
            return httpx.Response(
                200,
                json={
                    "access_token": "oauth-result",
                    "expires_at": int(time.time() * 1000) + 3600_000,
                },
            )
        status = next(pending, 200)
        if status != 200:
            return httpx.Response(
                status, json={"message": "private-response explicit-auth-key"}
            )
        text = (
            content
            if content is not None
            else json.dumps(
                {
                    "schema_version": "llm-formulas-v1",
                    "hypotheses": hypotheses
                    if hypotheses is not None
                    else [{"branches": [BRANCH]}],
                }
            )
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": finish,
                        "message": {"role": "assistant", "content": text},
                    }
                ],
                "created": 1,
                "model": "GigaChat-2",
                "object": "chat.completion",
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 23,
                    "total_tokens": 34,
                },
            },
        )

    def client(**kwargs):
        instance = client_class(
            **kwargs, transport=httpx.MockTransport(handle), trust_env=False
        )
        clients.append(instance)
        return instance

    monkeypatch.setattr("gigachat.client.httpx.Client", client)
    return requests, clients


def test_provider_config_is_explicit_and_openai_remains_default():
    assert isinstance(make_formula_sampler(LLMConfig()), HTTPFormulaSampler)
    resolved = config(
        BASE_URL="https://openrouter.ai/api/v1", ACCESS_TOKEN="openrouter-secret"
    )
    assert resolved.base_url == GIGACHAT_BASE_URL and resolved.access_token is None
    assert resolved.gigachat_verify_ssl_certs is True
    assert isinstance(make_formula_sampler(resolved), GigaChatFormulaSampler)
    assert "explicit-auth-key" not in repr(resolved)
    assert "direct-token" not in repr(
        config(GIGACHAT_CREDENTIALS="", GIGACHAT_ACCESS_TOKEN="direct-token")
    )


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"PROVIDER": "unknown"}, "LLM_CONFIG_PROVIDER"),
        (
            {"GIGACHAT_CREDENTIALS": "", "ACCESS_TOKEN": "wrong-provider"},
            "LLM_CONFIG_MISSING",
        ),
        ({"GIGACHAT_ACCESS_TOKEN": "ambiguous"}, "LLM_CONFIG_AUTH"),
        ({"GIGACHAT_SCOPE": "invalid"}, "LLM_CONFIG_SCOPE"),
        ({"GIGACHAT_VERIFY_SSL_CERTS": "no"}, "LLM_CONFIG_BOOLEAN"),
        (
            {
                "GIGACHAT_VERIFY_SSL_CERTS": "false",
                "GIGACHAT_CA_BUNDLE_FILE": "/tmp/cert",
            },
            "LLM_CONFIG_TLS",
        ),
        (
            {"GIGACHAT_AUTH_URL": "http://auth.example/oauth"},
            "LLM_CONFIG_INSECURE_HTTP",
        ),
        ({"GIGACHAT_BASE_URL": "https://secret@api.example/v1"}, "LLM_CONFIG_BASE_URL"),
    ],
)
def test_invalid_config_fails_before_network(overrides, code):
    with pytest.raises(LLMConfigError) as error:
        config(**overrides)
    assert error.value.code == code


def test_real_sdk_oauth_generation_usage_and_ambient_isolation(monkeypatch):
    for key, value in {
        "GIGACHAT_ACCESS_TOKEN": "ambient-token",
        "GIGACHAT_BASE_URL": "https://wrong.invalid",
        "GIGACHAT_AUTH_URL": "https://wrong.invalid/oauth",
        "GIGACHAT_MODEL": "wrong-model",
        "GIGACHAT_CA_BUNDLE_FILE": "/nonexistent/ambient.pem",
        "GIGACHAT_CERT_FILE": "/nonexistent/client.pem",
        "GIGACHAT_VERIFY_SSL_CERTS": "false",
        "GIGACHAT_MAX_RETRIES": "99",
        "LANGSMITH_TRACING": "true",
        "LANGCHAIN_TRACING_V2": "true",
    }.items():
        monkeypatch.setenv(key, value)
    requests, clients = install_transport(monkeypatch, statuses=[503])
    sampler = GigaChatFormulaSampler(
        config(GIGACHAT_SCOPE="GIGACHAT_API_B2B", REASONING_EFFORT="low")
    )
    result = sampler.sample({"training_summary": {"n": 20}})
    assert len(result) == 1
    assert sampler.http_attempts == 2 and len(requests) == 3
    assert requests[0].headers["Authorization"] == "Basic explicit-auth-key"
    assert b"GIGACHAT_API_B2B" in requests[0].content
    assert requests[-1].headers["Authorization"] == "Bearer oauth-result"
    assert str(requests[-1].url) == GIGACHAT_BASE_URL + "/chat/completions"
    payload = json.loads(requests[-1].content)
    assert payload["model"] == "GigaChat-2"
    assert payload["max_tokens"] == 4096 and payload["reasoning_effort"] == "low"
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    assert "maximum 3" in payload["messages"][0]["content"]
    assert sampler.usage == {"prompt_tokens": 11, "completion_tokens": 23}
    assert all(client.is_closed for client in clients)


def test_direct_token_does_not_request_oauth_or_use_ambient_credentials(monkeypatch):
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "ambient-auth-key")
    requests, _ = install_transport(monkeypatch)
    sampler = GigaChatFormulaSampler(
        config(GIGACHAT_CREDENTIALS="", GIGACHAT_ACCESS_TOKEN="direct-token")
    )
    sampler.sample({})
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Bearer direct-token"


@pytest.mark.parametrize(
    ("content", "finish", "code"),
    [
        ("private-response", "stop", "LLM_INVALID_FORMULA_RESPONSE"),
        ("{}", "length", "LLM_OUTPUT_TRUNCATED"),
        ("x" * 65537, "stop", "LLM_RESPONSE_SIZE"),
    ],
)
def test_invalid_responses_are_typed_and_never_exposed(
    monkeypatch, content, finish, code
):
    install_transport(monkeypatch, content=content, finish=finish)
    with pytest.raises(FormulaProviderError) as error:
        GigaChatFormulaSampler(config()).sample({})
    assert str(error.value) == code


def test_formula_constraints_and_numeric_optimization_apply_to_gigachat(monkeypatch):
    degree_four = {"op": "square", "arg": {"op": "square", "arg": {"op": "t"}}}
    install_transport(
        monkeypatch,
        hypotheses=[
            {"branches": [degree_four]},
            {"branches": [BRANCH, BRANCH, BRANCH]},
            {"branches": [BRANCH]},
        ],
    )
    x = np.linspace(0, 1, 20)
    result = run_formula_search(
        x,
        2 + np.sqrt(1 + 4 * x),
        config(),
        fit_options=FormulaFitOptions(starts=1, maxiter=40, max_cells=1),
    )
    assert result.status == "ACCEPTED" and result.best.mse < 1e-5
    assert "LLM_POLYNOMIAL_DEGREE_LIMIT" in result.warning_codes
    assert "LLM_FORMULA_PROPOSALS_REJECTED" in result.warning_codes
    assert result.best.model.segment_count == 1


def test_auth_failure_stops_search_and_does_not_leak_response(monkeypatch):
    requests, _ = install_transport(monkeypatch, statuses=[401])
    result = run_formula_search(
        np.arange(12), np.arange(12), config(SEARCH_ITERATIONS="4")
    )
    assert result.status == "UNAVAILABLE" and result.calls_failed == 1
    assert "LLM_HTTP_401" in result.warning_codes
    assert len(requests) == 2
    assert "private-response" not in json.dumps(result.to_dict())
    assert "explicit-auth-key" not in json.dumps(result.to_dict())


def test_gigachat_example_is_offline_and_loads_without_ambient_settings():
    path = Path(__file__).resolve().parents[1] / ".env.gigachat.example"
    settings = LLMConfig.load(path, environ={})
    assert settings.provider == "gigachat" and not settings.enabled
    assert (
        replace(settings, enabled=True).gigachat_credentials == "your-authorization-key"
    )
    assert settings.gigachat_verify_ssl_certs is True


@pytest.mark.parametrize("status", [200, 401])
def test_gigachat_application_bundle_preserves_baselines(tmp_path, monkeypatch, status):
    import monotone_calibrate.application as app
    from monotone_calibrate.bundle_runtime import verify_bundle
    from monotone_calibrate.validation import ValidationOptions

    install_transport(monkeypatch, statuses=[status])
    x = np.linspace(0, 1, 20)
    source = tmp_path / "curve.csv"
    np.savetxt(
        source,
        np.column_stack([x, 2 + np.sqrt(1 + 4 * x)]),
        delimiter=",",
        header="x,y",
        comments="",
    )
    settings = config()
    monkeypatch.setattr(app.LLMConfig, "load", lambda *a, **k: settings)
    result = app.run_calibration(
        app.RunRequest(
            source,
            tmp_path / "gigachat",
            dotenv_path=None,
            validation_options=ValidationOptions(1, 0),
        )
    )
    assert verify_bundle(result.bundle.root).status == "VERIFIED"
    report = json.loads(result.bundle.report_json.read_text())
    assert report["llm_advisor"]["provider"] == "gigachat"
    assert report["candidates"]["P1"]["available"]
    assert report["candidates"]["P2"]["available"]
    assert report["candidates"]["LLM"]["available"] is (status == 200)
    assert "explicit-auth-key" not in json.dumps(report)
    settings = replace(settings, enabled=False)
    baseline = app.run_calibration(
        app.RunRequest(
            source,
            tmp_path / "offline",
            dotenv_path=None,
            validation_options=ValidationOptions(1, 0),
        )
    )
    for filename in ("model-one.json", "model-two.json", "recommended-model.json"):
        assert (result.bundle.root / filename).read_bytes() == (
            baseline.bundle.root / filename
        ).read_bytes()


def test_timeouts_obey_adapter_retry_budget(monkeypatch):
    from langchain_gigachat import GigaChat

    attempts = []

    def timeout(*args, **kwargs):
        attempts.append(1)
        raise httpx.ReadTimeout("private-response explicit-auth-key")

    monkeypatch.setattr(GigaChat, "invoke", timeout)
    with pytest.raises(FormulaProviderError, match="^LLM_TRANSPORT_FAILURE$"):
        GigaChatFormulaSampler(config(MAX_RETRIES="2")).sample({})
    assert len(attempts) == 3
