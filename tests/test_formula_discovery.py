from __future__ import annotations

import copy
import json

import numpy as np
import pytest

from monotone_calibrate.comparison import compare_formula_discovery, holdout_mask
from monotone_calibrate.config import LLMConfig
from monotone_calibrate.engine import FitOptions
from monotone_calibrate.expressions import (
    Expression as E,
    FormulaHypothesis,
    FormulaModel,
    FormulaSegment,
    formula_model_from_dict,
)
from monotone_calibrate.formula_search import (
    FormulaFitOptions,
    HTTPFormulaSampler,
    fit_formula,
    parse_formulas,
    run_formula_search,
)


def sqrt_hypothesis(two=False):
    branch = E("sqrt1p", (E("scale", (E("t"),)),))
    return FormulaHypothesis((branch, branch) if two else (branch,))


def config(iterations=2):
    return LLMConfig(
        enabled=True,
        model="test-model",
        base_url="https://example.invalid/v1",
        access_token="boundary-secret",
        search_iterations=iterations,
    )


class Sampler:
    def __init__(self, hypothesis=None):
        self.hypothesis = hypothesis or sqrt_hypothesis()
        self.requests = []

    def sample(self, request):
        self.requests.append(copy.deepcopy(request))
        return (self.hypothesis,)


@pytest.mark.parametrize(
    "raw",
    [
        {"op": "pow", "arg": {"op": "t"}, "power": 12},
        {"op": "square", "arg": {"op": "square", "arg": {"op": "t"}}},
        {"op": "mul", "args": [{"op": "cube", "arg": {"op": "t"}}, {"op": "t"}]},
        {"op": "where", "condition": "t<0.5"},
        {"op": "t", "code": "__import__('os').system('false')"},
        {"op": "scale", "arg": {"op": "t"}, "coefficient": 0.1},
        {
            "op": "expm1",
            "arg": {"op": "scale", "arg": {"op": "log1p", "arg": {"op": "t"}}},
        },
        {"op": "div", "args": [{"op": "t"}, {"op": "t"}]},
    ],
)
def test_rejects_code_hidden_branches_degree_growth_and_disguised_high_powers(raw):
    with pytest.raises(ValueError):
        E.from_dict(raw)


def test_rejects_third_branch_and_registry_only_formulas():
    with pytest.raises(ValueError):
        FormulaHypothesis((E("t"), E("t"), E("t")))
    with pytest.raises(ValueError, match="ALREADY_IN_REGISTRY"):
        FormulaHypothesis((E("cube", (E("t"),)),))
    raw = {"op": "t"}
    for _ in range(100):
        raw = {"op": "scale", "arg": raw}
    with pytest.raises(ValueError):
        E.from_dict(raw)


def test_response_rejects_duplicate_keys_nonfinite_and_numeric_coefficients():
    h = sqrt_hypothesis().to_dict()
    assert parse_formulas(
        json.dumps({"schema_version": "llm-formulas-v1", "hypotheses": [h]})
    ) == (sqrt_hypothesis(),)
    for content in (
        '{"schema_version":"llm-formulas-v1","schema_version":"llm-formulas-v1","hypotheses":[]}',
        '{"schema_version":"llm-formulas-v1","hypotheses":[NaN]}',
        json.dumps(
            {
                "schema_version": "llm-formulas-v1",
                "hypotheses": [{**h, "parameters": [3]}],
            }
        ),
    ):
        with pytest.raises(ValueError):
            parse_formulas(content)


def test_invalid_proposal_does_not_discard_valid_neighbors_in_the_batch():
    valid = sqrt_hypothesis().to_dict()
    invalid = {
        "branches": [{"op": "square", "arg": {"op": "square", "arg": {"op": "t"}}}]
    }
    rejections = []
    result = parse_formulas(
        json.dumps(
            {"schema_version": "llm-formulas-v1", "hypotheses": [invalid, valid]}
        ),
        rejections=rejections,
    )
    assert result == (sqrt_hypothesis(),)
    assert rejections == ["LLM_POLYNOMIAL_DEGREE_LIMIT"]


@pytest.mark.parametrize("sign", [1, -1])
def test_numeric_solver_recovers_new_formula_without_llm_coefficients(sign):
    x = np.linspace(0, 10, 50)
    y = 4 + sign * 2 * (np.sqrt(1 + 3 * x / 10) - 1)
    fitted = fit_formula(x, y, sqrt_hypothesis())
    assert fitted is not None and fitted.mse < 1e-6
    assert abs(fitted.model.segments[0].shape[0] - 3) < 0.03
    model = formula_model_from_dict(fitted.model.to_dict())
    assert np.max(np.abs(model.predict(x) - y)) < 0.003
    assert np.isnan(model.predict(-0.1)) and np.isnan(model.predict(10.1))
    assert np.all(sign * np.diff(model.predict(np.linspace(0, 10, 10001))) >= -1e-12)
    assert all(
        round(v, 3) == v
        for s in model.segments
        for v in (*s.shape, s.offset, s.amplitude)
    )


def test_two_formula_branches_are_continuous_balanced_and_share_direction():
    x = np.linspace(0, 10, 60)
    y = np.where(
        x <= 5,
        3 + 2 * (np.sqrt(1 + 3 * x / 5) - 2),
        3 + 4 * (np.sqrt(1 + 2 * (np.maximum(x, 5) - 5) / 5) - 1),
    )
    fitted = fit_formula(
        x, y, sqrt_hypothesis(two=True), FormulaFitOptions(starts=2, max_cells=5)
    )
    assert fitted is not None
    model = fitted.model
    assert model.segment_count == 2
    assert 0.4 <= np.mean(x <= model.breakpoint) <= 0.6
    left, right = model.segments
    assert float(left.predict_unchecked(model.breakpoint)) == float(
        right.predict_unchecked(model.breakpoint)
    )
    assert np.all(np.diff(model.predict(np.linspace(0, 10, 10001))) >= -1e-12)
    assert fitted.mse < 0.01


def test_serialized_formula_is_recertified_not_trusted():
    h = sqrt_hypothesis()
    model = FormulaModel(
        (FormulaSegment(h.branches[0], 0.0, 1.0, (3.0,), 1.0, 2.0, "increasing"),)
    )
    for mutate in (
        lambda raw: raw.update(formula="evil()"),
        lambda raw: raw["segments"][0].update(amplitude=-1),
        lambda raw: raw["segments"][0].update(shape=[-3.0]),
        lambda raw: raw["segments"][0].update(shape=[3.0001]),
        lambda raw: raw["segments"][0].update(expression={"op": "t", "code": "evil()"}),
    ):
        raw = model.to_dict()
        mutate(raw)
        with pytest.raises(ValueError):
            formula_model_from_dict(raw)


def test_iterative_feedback_contains_optimized_scores_and_caches_duplicates():
    x = np.linspace(0, 10, 30)
    y = 1 + np.sqrt(1 + x)
    sampler = Sampler()
    result = run_formula_search(x, y, config(), sampler=sampler)
    assert result.status == "ACCEPTED"
    assert result.calls_succeeded == 2 and len(result.trace) == 1
    assert not sampler.requests[0]["experience"]
    feedback = sampler.requests[1]["experience"][0]
    assert feedback["fitness_negative_mse"] == -result.best.mse
    assert "boundary-secret" not in json.dumps(sampler.requests)


def test_holdout_y_cannot_change_discovered_formula_or_any_test_prediction():
    x = np.repeat(np.linspace(0, 10, 20), 2)
    y = 1 + np.sqrt(1 + x)
    test = holdout_mask(x)
    assert not test[0] and not test[-1]
    assert all(len(set(test[x == v])) == 1 for v in np.unique(x))
    changed = y.copy()
    changed[test] += 100
    samplers = [Sampler(), Sampler()]
    options = FormulaFitOptions(starts=1, maxiter=20, max_cells=1)
    a = compare_formula_discovery(
        x, y, config(1), FitOptions(), sampler=samplers[0], formula_options=options
    )
    b = compare_formula_discovery(
        x,
        changed,
        config(1),
        FitOptions(),
        sampler=samplers[1],
        formula_options=options,
    )
    assert samplers[0].requests == samplers[1].requests
    assert a.search.best.model.to_dict() == b.search.best.model.to_dict()
    for name in ("P1", "P2", "LLM"):
        assert (
            a.holdout["candidates"][name]["model"]
            == b.holdout["candidates"][name]["model"]
        )
        assert (
            a.holdout["candidates"][name]["predictions"]
            == b.holdout["candidates"][name]["predictions"]
        )
    assert (
        a.holdout["candidates"]["LLM"]["mse"] != b.holdout["candidates"]["LLM"]["mse"]
    )


def test_provider_failure_never_becomes_a_fake_new_formula():
    class Broken:
        def sample(self, request):
            raise TimeoutError("boundary-secret")

    result = run_formula_search(
        np.arange(20), np.arange(20), config(), sampler=Broken()
    )
    assert result.status == "UNAVAILABLE" and result.best is None
    assert "boundary-secret" not in json.dumps(result.to_dict())


def test_http_uses_explicit_model_and_token_without_redirects_or_ambient_proxy(
    monkeypatch,
):
    from dataclasses import replace

    captured = {}

    class Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            captured.update(url=url, **kwargs)

            class Response:
                status_code = 200
                content = b"bounded"

                def json(self):
                    return {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {
                                            "schema_version": "llm-formulas-v1",
                                            "hypotheses": [sqrt_hypothesis().to_dict()],
                                        }
                                    )
                                }
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 20,
                            "cost": 0.0001,
                        },
                    }

            return Response()

    monkeypatch.setattr("monotone_calibrate.formula_search.httpx.Client", Client)
    sampler = HTTPFormulaSampler(
        replace(
            config(), base_url="https://openrouter.ai/api/v1", reasoning_effort="none"
        )
    )
    assert sampler.sample({}) == (sqrt_hypothesis(),)
    assert captured["client"]["follow_redirects"] is False
    assert captured["client"]["trust_env"] is False
    assert captured["json"]["model"] == "test-model"
    assert captured["headers"]["Authorization"] == "Bearer boundary-secret"
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert captured["json"]["reasoning"] == {"effort": "none"}
    assert sampler.usage["cost"] == 0.0001


def test_semantic_rejections_are_fed_back_without_accepting_registry_fallback():
    from monotone_calibrate.formula_search import FormulaProviderError

    class CorrectingSampler(Sampler):
        def sample(self, request):
            self.requests.append(copy.deepcopy(request))
            if len(self.requests) == 1:
                raise FormulaProviderError("LLM_FORMULA_ALREADY_IN_REGISTRY")
            return (self.hypothesis,)

    sampler = CorrectingSampler()
    x = np.linspace(0, 1, 20)
    result = run_formula_search(x, np.sqrt(1 + x), config(), sampler=sampler)
    assert (
        "LLM_FORMULA_ALREADY_IN_REGISTRY" in sampler.requests[1]["previous_rejections"]
    )
    assert result.status == "ACCEPTED" and result.calls_failed == 1
    assert "LLM_FORMULA_PARTIAL_FAILURE" in result.warning_codes


def test_constant_fit_is_not_published_as_new_two_branch_formula():
    assert (
        fit_formula(
            np.linspace(0, 1, 30),
            np.ones(30),
            sqrt_hypothesis(True),
            FormulaFitOptions(starts=1, max_cells=1, maxiter=2),
        )
        is None
    )


def test_provider_schema_and_local_parser_accept_the_same_valid_tree():
    from jsonschema import Draft202012Validator
    from monotone_calibrate.formula_search import FORMULA_RESPONSE_SCHEMA

    value = {
        "schema_version": "llm-formulas-v1",
        "hypotheses": [sqrt_hypothesis().to_dict()],
    }
    Draft202012Validator.check_schema(FORMULA_RESPONSE_SCHEMA)
    validator = Draft202012Validator(FORMULA_RESPONSE_SCHEMA)
    validator.validate(value)
    assert parse_formulas(json.dumps(value)) == (sqrt_hypothesis(),)
    value["extra"] = "invalid"
    assert list(validator.iter_errors(value))


def test_complete_bundle_reconciles_all_three_models_and_detects_resealed_tampering(
    tmp_path, monkeypatch
):
    from hashlib import sha256
    import shutil
    import monotone_calibrate.application as app
    from monotone_calibrate.bundle_runtime import BundleRuntimeError, verify_bundle
    from monotone_calibrate.validation import ValidationOptions

    x = np.linspace(0, 10, 30)
    y = 1 + 2 * (np.sqrt(1 + 3 * x / 10) - 1)
    source = tmp_path / "data.csv"
    np.savetxt(
        source, np.column_stack([x, y]), delimiter=",", header="x,y", comments=""
    )
    monkeypatch.setattr(app.LLMConfig, "load", lambda *a, **k: config(1))
    monkeypatch.setattr(
        app,
        "compare_formula_discovery",
        lambda x, y, c, options: compare_formula_discovery(
            x, y, c, options, sampler=Sampler()
        ),
    )
    result = app.run_calibration(
        app.RunRequest(
            source,
            tmp_path / "bundle",
            dotenv_path=None,
            validation_options=ValidationOptions(1, 0),
        )
    )
    assert verify_bundle(result.bundle.root).status == "VERIFIED"
    report = json.loads(result.bundle.report_json.read_text())
    assert report["comparison_holdout"]["status"] == "AVAILABLE"
    assert all(report["candidates"][name]["available"] for name in ("P1", "P2", "LLM"))
    assert report["comparison_holdout"]["candidates"]["LLM"]["rmse"] < 0.002
    text = result.bundle.report_html.read_text()
    assert "LLM — новая формула" in text and 'src="plot-llm.svg"' in text

    for i, corrupt in enumerate(("prediction", "metric", "expression")):
        destination = tmp_path / f"corrupt-{i}"
        shutil.copytree(result.bundle.root, destination)
        if corrupt == "prediction":
            path = destination / "observations.csv"
            import csv

            rows = list(csv.DictReader(path.open()))
            rows[0]["prediction_llm_refit"] = "12345"
            with path.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        else:
            path = destination / (
                "report.json" if corrupt == "metric" else "model-llm.json"
            )
            payload = json.loads(path.read_text())
            if corrupt == "metric":
                payload["comparison_holdout"]["candidates"]["LLM"]["rmse"] = 12345
            else:
                payload["segments"][0]["expression"] = {
                    "op": "square",
                    "arg": {"op": "cube", "arg": {"op": "t"}},
                }
            path.write_text(json.dumps(payload))
        manifest_path = destination / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        for item in manifest["artifacts"]:
            contents = (destination / item["path"]).read_bytes()
            item.update(bytes=len(contents), sha256=sha256(contents).hexdigest())
        manifest_path.write_text(json.dumps(manifest))
        with pytest.raises(BundleRuntimeError, match="three-way"):
            verify_bundle(destination)
