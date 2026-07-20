from __future__ import annotations

import json
from hashlib import sha256

import pytest

import monotone_calibrate.application as application
from monotone_calibrate.application import (
    CalibrationRunError,
    RunRequest,
    run_calibration,
)
from monotone_calibrate.llm_advisor import AdviceResult, StartSuggestion
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
            f"{value / 1000:.3f},{2.0 + value / 2000:.4f}"
            for value in range(5000)
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
    assert result.llm_advisor["scope"] == "full_data_p1_refit_only"
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

    class RacingAdvisor:
        def __init__(self, _config) -> None:
            pass

        def advise(self, _training_summary) -> AdviceResult:
            output.mkdir()
            (output / "owner-marker.txt").write_text("keep", encoding="utf-8")
            return AdviceResult(status="ACCEPTED")

    monkeypatch.setattr(application, "ChatOpenAIStartAdvisor", RacingAdvisor)

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


def test_enabled_advisor_can_only_supply_a_declared_full_data_p1_start(
    tmp_path,
    monkeypatch,
) -> None:
    _clear_llm_environment(monkeypatch)
    source = tmp_path / "curve.csv"
    output = tmp_path / "advised-report"
    dotenv = tmp_path / ".env"
    _write_ready_curve(source)
    base_url = "https://llm.example.test/v1"
    dotenv.write_text(
        "MONOTONE_CALIBRATE_LLM_ENABLED=true\n"
        "MONOTONE_CALIBRATE_LLM_MODEL=compatible-model\n"
        f"MONOTONE_CALIBRATE_LLM_BASE_URL={base_url}\n"
        "MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN=boundary-secret\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class AcceptedAdvisor:
        def __init__(self, config) -> None:
            captured["config"] = config

        def advise(self, training_summary) -> AdviceResult:
            captured["summary"] = training_summary
            first = training_summary.replaceable_slots[0]
            return AdviceResult(
                status="ACCEPTED",
                suggestions=(
                    StartSuggestion(
                        slot_id=first.slot_id,
                        parameter_vector=(-3.5,),
                    ),
                ),
            )

    monkeypatch.setattr(application, "ChatOpenAIStartAdvisor", AcceptedAdvisor)

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

    assert captured["summary"].observation_count == 12
    assert captured["summary"].replaceable_slots
    assert result.llm_advisor["status"] == "ACCEPTED"
    assert result.llm_advisor["accepted_slot_count"] == 1
    assert result.llm_advisor["scope"] == "full_data_p1_refit_only"
    assert result.llm_advisor["used_in_validation"] is False
    assert result.llm_advisor["endpoint_origin_sha256"] == sha256(
        base_url.encode("utf-8")
    ).hexdigest()
    assert result.llm_advisor["baseline_p1_model_hash"]
    assert result.llm_advisor["final_p1_model_hash"]
    serialized = result.bundle.report_json.read_text(encoding="utf-8")
    assert base_url not in serialized
    assert "boundary-secret" not in serialized
