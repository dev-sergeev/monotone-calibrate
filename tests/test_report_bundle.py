from __future__ import annotations

import json
from hashlib import sha256

import numpy as np
import pytest

from monotone_calibrate.data import read_xy_csv
from monotone_calibrate.engine import fit_candidates
from monotone_calibrate.model_runtime import model_from_dict
from monotone_calibrate.reporting import write_report_bundle
from monotone_calibrate.validation import (
    OOFRecord,
    ProcedureMetrics,
    StabilityMetrics,
    UpliftMetrics,
    ValidationOptions,
    ValidationResult,
    validate_candidates,
)


def test_report_bundle_contains_comparison_plots_metrics_and_executable_recommendation(
    tmp_path,
) -> None:
    source = tmp_path / "curve.csv"
    rows = ["x,y,row_id"]
    c = 23.5
    join = 2.0 + 0.15 * c
    for value in range(60):
        response = join + (0.15 if value <= 23 else 1.20) * (value - c)
        rows.append(f"{value},{response},point-{value:02d}")
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")
    dataset = read_xy_csv(source)
    x = np.asarray([row.x for row in dataset.observations])
    y = np.asarray([row.y for row in dataset.observations])
    candidates = fit_candidates(x, y)
    validation = validate_candidates(
        x,
        y,
        candidates,
        ValidationOptions(repetitions=4, bootstrap_resamples=80),
    )

    llm_provenance = {
        "status": "ACCEPTED",
        "mode": "openai_compatible",
        "provider_model": "local-compatible-model",
        "endpoint_origin_sha256": "a" * 64,
        "prompt_version": "start-advisor-v1",
        "output_schema_version": "llm-start-advice-v1",
        "calls_requested": 1,
        "accepted_slot_count": 1,
        "scope": "full_data_p1_refit_only",
        "used_in_validation": False,
        "family_search_space_changed": False,
        "selection_policy_changed": False,
        "certificate_policy_changed": False,
        "baseline_p1_model_hash": candidates.one.model.model_instance_hash,
        "final_p1_model_hash": candidates.one.model.model_instance_hash,
        "influenced_final_refit": False,
        "formula_source": "certified_registry_solver",
    }
    bundle = write_report_bundle(
        dataset,
        candidates,
        validation,
        tmp_path / "report",
        llm_advisor=llm_provenance,
        extra_warning_codes=("LLM_ADVISOR_NO_IMPROVEMENT",),
    )

    assert bundle.report_html.is_file()
    assert bundle.report_json.is_file()
    assert bundle.plot_one_svg.is_file()
    assert bundle.plot_two_svg is not None and bundle.plot_two_svg.is_file()
    assert bundle.recommended_model is not None and bundle.recommended_model.is_file()
    report = json.loads(bundle.report_json.read_text(encoding="utf-8"))
    assert report["candidates"]["P1"]["metrics"]["r2_refit"] is not None
    assert report["candidates"]["P2"]["metrics"]["r2_refit"] > 0.999999
    assert len(report["candidates"]["P2"]["segment_metrics"]) == 2
    assert report["search"]["policy_id"] == "candidate-search-v1"
    assert report["search"]["profile"] == "fast"
    assert report["search"]["approximate"] is True
    assert report["recommendation"]["structure"] == "P2"
    assert report["diagnostics"]["source"] == "grouped_oof"
    assert report["llm_advisor"] == llm_provenance
    assert "LLM_ADVISOR_NO_IMPROVEMENT" in report["warnings"]
    rendered_html = bundle.report_html.read_text(encoding="utf-8")
    raw_r2 = report["candidates"]["P2"]["metrics"]["r2_refit"]
    assert f"Refit global R²: {raw_r2}</p>" not in rendered_html
    assert f"Refit global R²: {raw_r2:.3f}" in rendered_html
    serialized_bundle = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in bundle.root.iterdir()
        if path.is_file()
    )
    assert "boundary-secret" not in serialized_bundle
    assert "https://llm.example.test/v1" not in serialized_bundle
    svg = bundle.plot_two_svg.read_text(encoding="utf-8")
    assert 'data-role="interval-boundary"' in svg
    assert 'stroke="#c62828"' in svg
    assert 'data-segment="left"' in svg
    assert 'data-segment="right"' in svg
    assert svg.count('data-role="observation"') == 60
    loaded = model_from_dict(json.loads(bundle.recommended_model.read_text(encoding="utf-8")))
    expected = candidates.two.model.predict(x)  # type: ignore[union-attr]
    assert np.allclose(loaded.predict(x), expected)
    manifest = json.loads(bundle.manifest_json.read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        payload = (bundle.root / artifact["path"]).read_bytes()
        assert artifact["bytes"] == len(payload)
        assert artifact["sha256"] == sha256(payload).hexdigest()

    unsafe_output = tmp_path / "unsafe-report"
    with pytest.raises(ValueError, match="llm_advisor"):
        write_report_bundle(
            dataset,
            candidates,
            validation,
            unsafe_output,
            llm_advisor={"status": "ACCEPTED", "access_token": "boundary-secret"},
        )
    assert not unsafe_output.exists()


def test_descriptive_report_keeps_second_scatter_panel_without_fake_p2_or_oof_claims(
    tmp_path,
) -> None:
    source = tmp_path / "small.csv"
    rows = ["x,y"]
    rows.extend("0,1" for _ in range(3))
    rows.extend("1,2" for _ in range(14))
    rows.extend("2,3" for _ in range(3))
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")
    dataset = read_xy_csv(source)
    x = np.asarray([row.x for row in dataset.observations])
    y = np.asarray([row.y for row in dataset.observations])
    candidates = fit_candidates(x, y)
    validation = validate_candidates(x, y, candidates)

    bundle = write_report_bundle(dataset, candidates, validation, tmp_path / "small-report")

    assert bundle.plot_two_svg is not None and bundle.plot_two_svg.is_file()
    svg = bundle.plot_two_svg.read_text(encoding="utf-8")
    assert svg.count('data-role="observation"') == 20
    assert 'data-role="approximation"' not in svg
    assert 'data-role="interval-boundary"' not in svg
    assert "P2 недоступна" in svg
    rendered = bundle.report_html.read_text(encoding="utf-8")
    assert "Проверочная оценка недоступна" in rendered
    assert "OOF R²: None" not in rendered
    report = json.loads(bundle.report_json.read_text(encoding="utf-8"))
    assert report["diagnostics"]["status"] == "UNAVAILABLE_NO_OOF"
    assert bundle.recommended_model is None


def test_constant_response_uses_human_typed_undefined_metrics_in_html(tmp_path) -> None:
    source = tmp_path / "constant.csv"
    source.write_text("x,y\n" + "\n".join(f"{value},2.5" for value in range(12)) + "\n", encoding="utf-8")
    dataset = read_xy_csv(source)
    x = np.asarray([row.x for row in dataset.observations])
    y = np.asarray([row.y for row in dataset.observations])
    candidates = fit_candidates(x, y)
    validation = validate_candidates(
        x,
        y,
        candidates,
        ValidationOptions(repetitions=2, bootstrap_resamples=10),
    )

    bundle = write_report_bundle(dataset, candidates, validation, tmp_path / "constant-report")

    rendered = bundle.report_html.read_text(encoding="utf-8")
    assert "R²: None" not in rendered
    assert "[None, None]" not in rendered
    assert "R² не определён" in rendered
    assert "Uplift не определён" in rendered


def test_zero_mad_oof_outlier_is_flagged_with_auditable_prediction_and_residual(tmp_path) -> None:
    source = tmp_path / "outlier.csv"
    rows = ["x,y,row_id"] + [f"{value},{value},p-{value}" for value in range(9)] + ["9,19,p-9"]
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")
    dataset = read_xy_csv(source)
    x = np.asarray([row.x for row in dataset.observations])
    y = np.asarray([row.y for row in dataset.observations])
    candidates = fit_candidates(x, y)
    oof = tuple(
        OOFRecord(
            repetition=0,
            fold=index % 2,
            canonical_index=index,
            x=float(x[index]),
            y=float(y[index]),
            prediction_one=float(y[index] - (10.0 if index == 9 else 0.0)),
            prediction_two=float(y[index] - (10.0 if index == 9 else 0.0)),
            null_prediction=0.0,
            two_fallback=False,
        )
        for index in range(10)
    )
    validation = ValidationResult(
        status="VALIDATED",
        decision_state="P1_RETAINED",
        recommended_structure="P1",
        one=ProcedureMetrics(0.7, 1.0, 1.0, 1.0, "DEFINED"),
        two=ProcedureMetrics(0.7, 1.0, 1.0, 1.0, "DEFINED"),
        uplift=UpliftMetrics(0.0, 0.0, 0.0, 0.0, (0.0,), 0.0, 0.0, 0.0, 0.0),
        stability=StabilityMetrics(1.0, 1.0, 1.0, 0.0, 0.0, (), (), True),
        warning_codes=(),
        folds=2,
        repetitions=1,
        oof=oof,
    )

    bundle = write_report_bundle(dataset, candidates, validation, tmp_path / "outlier-report")

    report = json.loads(bundle.report_json.read_text(encoding="utf-8"))
    assert report["diagnostics"]["problem_observations"] == ["p-9"]
    assert report["diagnostics"]["problem_rows"] == [
        {"row_id": "p-9", "oof_prediction": 9.0, "oof_residual": 10.0}
    ]
    observations = bundle.observations_csv.read_text(encoding="utf-8")
    assert "prediction_recommended_oof,residual_recommended_oof" in observations
    rendered = bundle.report_html.read_text(encoding="utf-8")
    assert "p-9" in rendered and ">10.0<" in rendered
