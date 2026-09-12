from __future__ import annotations

import json

import pytest

import monotone_calibrate.application as application
from monotone_calibrate.application import (
    CalibrationRunError,
    RunRequest,
    run_calibration,
)
from monotone_calibrate.hypotheses import HypothesisSpace
from monotone_calibrate.comparison import FormulaComparison
from monotone_calibrate.formula_search import FormulaSearchResult
from monotone_calibrate.validation import ValidationOptions


def _write_ready_curve(path) -> None:
    path.write_text(
        "x,y,row_id\n"
        + "\n".join(f"{value},{2.0 + 0.5 * value},p-{value}" for value in range(12))
        + "\nnot-a-number,4,bad-row\n",
        encoding="utf-8",
    )


def _clear_llm_environment(monkeypatch) -> None:
    for suffix in (
        "ENABLED",
        "MODEL",
        "BASE_URL",
        "ACCESS_TOKEN",
        "TIMEOUT_SECONDS",
        "MAX_RETRIES",
        "SEARCH_ITERATIONS",
        "ALLOW_INSECURE_HTTP",
    ):
        monkeypatch.delenv(f"MONOTONE_CALIBRATE_LLM_{suffix}", raising=False)


def test_budgeted_search_admits_the_reported_large_input_to_the_solver(
    tmp_path,
    monkeypatch,
) -> None:
    source = tmp_path / "large-curve.csv"
    output = tmp_path / "oversized-report"
    source.write_text(
        "x,y\n"
        + "\n".join(
            f"{value / 1000:.3f},{2.0 + value / 2000:.4f}" for value in range(5000)
        )
        + "\n",
        encoding="utf-8",
    )

    class SolverEntered(RuntimeError):
        pass

    def mark_solver_entry(*_args, **_kwargs):
        raise SolverEntered

    monkeypatch.setattr(application, "fit_candidates", mark_solver_entry)

    with pytest.raises(SolverEntered):
        run_calibration(
            RunRequest(
                input_path=source,
                output_dir=output,
                dotenv_path=None,
            )
        )

    assert not output.exists()


def test_offline_run_publishes_one_complete_bundle_and_returns_final_paths(
    tmp_path,
    monkeypatch,
) -> None:
    _clear_llm_environment(monkeypatch)
    source = tmp_path / "curve.csv"
    output = tmp_path / "published-report"
    _write_ready_curve(source)

    result = run_calibration(
        RunRequest(
            input_path=source,
            output_dir=output,
            dotenv_path=None,
            validation_options=ValidationOptions(
                repetitions=1,
                bootstrap_resamples=0,
            ),
        )
    )

    assert result.bundle.root == output
    assert result.bundle.report_json == output / "report.json"
    assert result.bundle.report_json.is_file()
    assert result.dataset.n_used == 12
    assert result.dataset.n_skipped == 1
    assert result.llm_advisor["status"] == "DISABLED"
    assert result.llm_advisor["scope"] == "deterministic_full_registry"
    assert result.llm_advisor["used_in_validation"] is False
    report = json.loads(result.bundle.report_json.read_text(encoding="utf-8"))
    assert report["llm_advisor"] == result.llm_advisor
    assert not tuple(tmp_path.glob(".published-report.tmp-*"))


def test_invalid_llm_configuration_falls_back_without_blocking_the_report(
    tmp_path,
    monkeypatch,
) -> None:
    _clear_llm_environment(monkeypatch)
    source = tmp_path / "curve.csv"
    output = tmp_path / "config-fallback-report"
    dotenv = tmp_path / ".env"
    _write_ready_curve(source)
    dotenv.write_text(
        "MONOTONE_CALIBRATE_LLM_ENABLED=true\n"
        "MONOTONE_CALIBRATE_LLM_MODEL=model-name-is-not-secret\n"
        "MONOTONE_CALIBRATE_LLM_BASE_URL=https://private-host.example.test/v1\n",
        encoding="utf-8",
    )

    result = run_calibration(
        RunRequest(
            input_path=source,
            output_dir=output,
            dotenv_path=dotenv,
            validation_options=ValidationOptions(
                repetitions=1,
                bootstrap_resamples=0,
            ),
        )
    )

    assert result.bundle.report_json.is_file()
    assert result.llm_advisor["status"] == "CONFIG_INVALID"
    assert result.llm_advisor["used_in_validation"] is False
    assert "LLM_CONFIG_MISSING" in result.warning_codes
    serialized = result.bundle.report_json.read_text(encoding="utf-8")
    assert "private-host.example.test" not in serialized
    assert "MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN" not in serialized


def test_publish_race_preserves_existing_output_and_cleans_the_staging_tree(
    tmp_path,
    monkeypatch,
) -> None:
    _clear_llm_environment(monkeypatch)
    source = tmp_path / "curve.csv"
    output = tmp_path / "race-report"
    dotenv = tmp_path / ".env"
    _write_ready_curve(source)
    dotenv.write_text(
        "MONOTONE_CALIBRATE_LLM_ENABLED=true\n"
        "MONOTONE_CALIBRATE_LLM_MODEL=compatible-model\n"
        "MONOTONE_CALIBRATE_LLM_BASE_URL=https://llm.example.test/v1\n"
        "MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN=boundary-secret\n",
        encoding="utf-8",
    )

    def racing_search(*_args, **_kwargs):
        output.mkdir()
        (output / "owner-marker.txt").write_text("keep", encoding="utf-8")
        return FormulaComparison(FormulaSearchResult("UNAVAILABLE"))

    monkeypatch.setattr(application, "compare_formula_discovery", racing_search)

    with pytest.raises(CalibrationRunError) as captured:
        run_calibration(
            RunRequest(
                input_path=source,
                output_dir=output,
                dotenv_path=dotenv,
                validation_options=ValidationOptions(
                    repetitions=1,
                    bootstrap_resamples=0,
                ),
            )
        )

    assert captured.value.code == "OUTPUT_EXISTS"
    assert (output / "owner-marker.txt").read_text(encoding="utf-8") == "keep"
    assert not tuple(tmp_path.glob(".race-report.tmp-*"))


def test_formula_discovery_never_changes_registry_baselines(
    tmp_path, monkeypatch
) -> None:
    from monotone_calibrate.bundle_runtime import verify_bundle, write_predictions
    from monotone_calibrate.comparison import compare_formula_discovery
    from monotone_calibrate.expressions import Expression as E, FormulaHypothesis

    _clear_llm_environment(monkeypatch)
    source = tmp_path / "curve.csv"
    source.write_text(
        "x,y\n"
        + "\n".join(
            f"{i},{3 + (0.2 if i <= 11 else 1.5) * (i - 11.5)}" for i in range(24)
        )
    )
    offline = run_calibration(
        RunRequest(
            source,
            tmp_path / "offline",
            dotenv_path=None,
            validation_options=ValidationOptions(1, 0),
        )
    )
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "MONOTONE_CALIBRATE_LLM_ENABLED=true\n"
        "MONOTONE_CALIBRATE_LLM_MODEL=compatible-model\n"
        "MONOTONE_CALIBRATE_LLM_BASE_URL=https://llm.example.test/v1\n"
        "MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN=boundary-secret\n"
    )
    captured = {}

    def discovery(x, y, config, fit_options):
        captured["x"] = x
        captured["fit_options"] = fit_options
        h = FormulaHypothesis((E("sqrt1p", (E("scale", (E("t"),)),)),))
        class Sampler:
            def sample(self, request):
                return (h,)

        return compare_formula_discovery(
            x, y, config, fit_options, sampler=Sampler(),
        )

    monkeypatch.setattr(application, "compare_formula_discovery", discovery)
    result = run_calibration(
        RunRequest(
            source,
            tmp_path / "with-llm",
            dotenv_path=dotenv,
            validation_options=ValidationOptions(1, 0),
        )
    )
    assert len(captured["x"]) == 24
    assert result.candidates.two is not None and offline.candidates.two is not None
    assert captured["fit_options"].hypothesis_space is None
    assert result.candidates.hypothesis_space == HypothesisSpace.full_registry()
    assert (
        result.candidates.one.model.to_dict() == offline.candidates.one.model.to_dict()
    )
    assert result.candidates.two_status == offline.candidates.two_status
    assert (
        None if result.candidates.two is None else result.candidates.two.model.to_dict()
    ) == (
        None
        if offline.candidates.two is None
        else offline.candidates.two.model.to_dict()
    )
    assert result.validation.to_dict() == offline.validation.to_dict()
    assert result.llm_advisor["status"] == "ACCEPTED"
    assert result.llm_advisor["family_search_space_changed"] is False
    report = json.loads(result.bundle.report_json.read_text())
    assert set(report["candidates"]) == {"P1", "P2", "LLM"}
    assert report["candidates"]["LLM"]["available"]
    assert verify_bundle(result.bundle.root).status == "VERIFIED"
    prediction_input = tmp_path / "predict.csv"
    prediction_input.write_text("x\n5\n100\nbad\n")
    output = write_predictions(
        result.bundle.root / "model-llm.json",
        prediction_input,
        tmp_path / "predictions.csv",
    )
    assert (output.n_ok, output.n_out_of_domain, output.n_invalid_x) == (1, 1, 1)
    assert "boundary-secret" not in result.bundle.report_json.read_text()
    assert "https://llm.example.test/v1" not in result.bundle.report_json.read_text()
