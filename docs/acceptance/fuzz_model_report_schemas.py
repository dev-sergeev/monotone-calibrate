#!/usr/bin/env python3
"""Negative schema checks for model/report state transitions.

This script is deliberately read-only: every fixture and mutation lives in
memory.  ``schema_reject`` cases are invariants Draft 2020-12 can express.
``semantic_reject`` cases document exact verifier obligations that require
cross-field equality, arithmetic, evaluation, or conditional warning logic.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
MODEL_SCHEMA_PATH = ROOT / "docs/specification/model.schema.json"
REPORT_SCHEMA_PATH = ROOT / "docs/specification/production-report.schema.json"
HASH = "0" * 64

Json = dict[str, Any]
Mutation = Callable[[Json], None]


def load_validator(path: Path) -> Draft202012Validator:
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def metric(
    value: float | None = 1.0,
    scope: str = "oof",
    status: str = "defined",
    reason: str | None = None,
) -> Json:
    return {
        "value": value,
        "status": status,
        "reason": reason,
        "scope": scope,
        "definition": "literal fuzz fixture",
        "unit": "1",
    }


def unavailable_metric(scope: str = "oof") -> Json:
    return metric(None, scope, "unavailable", "UNAVAILABLE")


def undefined_metric(scope: str = "oof") -> Json:
    return metric(None, scope, "undefined", "UNDEFINED_R2")


def report_certificate() -> Json:
    return {
        "status": "PASS",
        "reason": None,
        "domain_valid": True,
        "monotonicity_valid": True,
        "finite": True,
        "continuity_valid": True,
        "certificate_version": "v1",
    }


def report_procedure(
    status: str = "valid",
    r2: float = 0.8,
    rmse: float = 0.2,
    mae: float = 0.1,
) -> Json:
    return {
        "status": status,
        "reason": None if status == "valid" else "PARTIAL_FALLBACK",
        "r2_oos": metric(r2),
        "rmse_oof": metric(rmse),
        "mae_oof": metric(mae),
        "appearance_count": 100,
        "repetitions": 10,
        "fallback_count": 0 if status == "valid" else 2,
        "failure_codes": [] if status == "valid" else ["OPTIMIZER_FAILURE"],
        "full_data_refit_status": "certified",
    }


def report_segment(
    segment_id: str,
    lower: float,
    upper: float,
    lower_closed: bool,
    upper_closed: bool,
) -> Json:
    return {
        "segment_id": segment_id,
        "interval": [lower, upper],
        "lower_closed": lower_closed,
        "upper_closed": upper_closed,
        "family_id": "poly1_v1",
        "formula_display": "f(x)",
        "parameters": {"q": 1.0},
        "direction": "nondecreasing",
        "certificate": report_certificate(),
        "n": 5,
        "share": 0.5,
        "n_unique_x": 3,
        "r2_fit": metric(0.9, "in_sample"),
        "rmse_fit": metric(0.1, "in_sample"),
        "mae_fit": metric(0.05, "in_sample"),
    }


def artifact(path: str, role: str, media_type: str = "application/json") -> Json:
    return {
        "path": path,
        "media_type": media_type,
        "size": 1,
        "sha256": HASH,
        "role": role,
    }


def base_report() -> Json:
    p1_refit = {
        "status": "certified",
        "reason": None,
        "model_path": "models/one-model.json",
        "family_id": "poly1_v1",
        "formula_display": "f1(x)",
        "parameters": {"q": 1.0},
        "domain": [0.0, 1.0],
        "direction": "nondecreasing",
        "certificate": report_certificate(),
        "model_structure_hash": HASH,
        "model_instance_hash": HASH,
        "r2_fit_all": metric(0.8, "in_sample"),
        "rmse_fit_all": metric(0.2, "in_sample"),
        "mae_fit_all": metric(0.1, "in_sample"),
    }
    p2_refit = {
        "status": "certified",
        "reason": None,
        "model_path": "models/two-segment-model.json",
        "formula_display": "f2(x)",
        "domain": [0.0, 1.0],
        "direction": "nondecreasing",
        "boundary": 0.5,
        "shared_value": 1.0,
        "certificate": report_certificate(),
        "segments": [
            report_segment("left", 0.0, 0.5, True, True),
            report_segment("right", 0.5, 1.0, False, True),
        ],
        "model_structure_hash": HASH,
        "model_instance_hash": HASH,
        "r2_fit_all": metric(0.9, "in_sample"),
        "rmse_fit_all": metric(0.1, "in_sample"),
        "mae_fit_all": metric(0.05, "in_sample"),
    }
    pass_gate = {
        "id": "PRACTICAL_UPLIFT",
        "status": "pass",
        "observed": 0.75,
        "operator": ">=",
        "threshold": 0.1,
        "reason": None,
        "policy_version": "v1",
    }
    return {
        "schema_version": "report-v1",
        "report_id": "schema-fuzz",
        "generated_at": "2026-07-17T00:00:00Z",
        "status": {
            "bundle_status": "COMPLETE",
            "analysis_status": "SUCCEEDED",
            "recommendation_status": "TWO_RECOMMENDED",
            "decision_reason": "CLEAR_PRACTICAL_UPLIFT",
        },
        "decision": {
            "code": "CLEAR_PRACTICAL_UPLIFT",
            "reason": "all gates pass",
            "recommended_procedure": "two",
            "related_reason_codes": ["CLEAR_PRACTICAL_UPLIFT"],
        },
        "recommendation": {
            "role": "two",
            "model_path": "models/recommended-model.json",
            "formula_display": "f2(x)",
            "domain": [0.0, 1.0],
            "global_primary_r2_oos": metric(0.9),
            "global_primary_rmse_oof": metric(0.1),
            "global_primary_mae_oof": metric(0.05),
            "extrapolation": "forbidden",
        },
        "input": {
            "filename": "input.csv",
            "media_type": "text/csv",
            "input_sha256": HASH,
            "n_input": 10,
            "n_used": 10,
            "n_excluded": 0,
            "n_unique_x": 10,
            "repeated_x_groups": 0,
            "exact_duplicate_records": 0,
            "x_unit": "1",
            "y_unit": "1",
            "domain": [0.0, 1.0],
            "exclusions": [],
        },
        "preprocessing": {
            "sorting": "canonical_x_y_row_id",
            "row_id_policy": "stable_logical_row_id",
            "weights": "equal_per_valid_row",
            "invalid_rows": "skip_with_warning_and_audit",
            "automatic_outlier_removal": False,
            "ties_atomic_for_validation_and_segmentation": True,
            "x_transform": {
                "kind": "affine",
                "offset": 0.0,
                "scale": 1.0,
                "invertible": True,
                "scope": "full_data",
            },
            "y_transform": {
                "kind": "affine",
                "offset": 0.0,
                "scale": 1.0,
                "invertible": True,
                "scope": "full_data",
            },
            "x_resolution_ratio": 0.0,
            "normalized_x_collision": False,
        },
        "validation": {
            "status": "available",
            "reason": None,
            "estimand": "new_x_level_within_observed_domain",
            "group_key": "canonical_binary64_x",
            "outer_folds": 5,
            "inner_fit_scopes": 0,
            "repetitions": 10,
            "procedures": {
                "p1": report_procedure(),
                "p2": report_procedure(r2=0.9, rmse=0.1, mae=0.05),
            },
            "uplift": {
                "delta_mse": metric(0.03),
                "delta_rmse": metric(0.1),
                "delta_mae": metric(0.05),
                "delta_r2": metric(0.1),
                "relative_mse_uplift": metric(0.75),
                "gates": [pass_gate],
            },
            "split_sensitivity": {
                "status": "available",
                "reason": None,
                "method": "repetition_level_paired_relative_mse_uplift",
                "effects": [0.2] * 10,
            },
            "bootstrap_stability": {
                "status": "available",
                "reason": None,
                "method": "paired_fixed_oof_group_bootstrap_stability_interval",
                "level": 0.9,
                "requested_resamples": 200,
                "defined_resamples": 200,
                "undefined_resamples": 0,
                "lower": metric(0.1, "bootstrap"),
                "upper": metric(0.3, "bootstrap"),
            },
            "p2_stability": {
                "status": "available",
                "reason": None,
                "valid_rate": metric(1.0),
                "fallback_rate": metric(0.0),
                "direction_agreement": metric(1.0),
                "dominant_pair_frequency": metric(1.0),
                "breakpoint_width_fraction": metric(0.1),
                "edge_hit_rate": metric(0.0),
                "instability_rate": metric(0.0),
                "hard_failure_rate": metric(0.0),
            },
        },
        "refit": {"p1": p1_refit, "p2": p2_refit},
        "diagnostics": {
            "oof_residuals": {
                "status": "available",
                "reason": None,
                "method": "robust OOF residual",
                "row_count": 10,
                "flagged_count": 0,
            },
            "patterns": [],
            "influence": {
                "status": "available",
                "reason": None,
                "method": "selected-structure LGO",
                "row_count": 10,
                "flagged_count": 0,
            },
            "action": "review_only",
        },
        "warnings": [],
        "failures": [],
        "artifacts": [
            artifact("models/one-model.json", "model_candidate"),
            artifact("models/two-segment-model.json", "model_candidate"),
            artifact("models/recommended-model.json", "model_recommended"),
            artifact("plot-data.csv", "plot_data", "text/csv"),
            artifact("figures/one-function.svg", "figure", "image/svg+xml"),
            artifact("figures/two-segment.svg", "figure", "image/svg+xml"),
        ],
        "provenance": {
            "analysis_id": HASH,
            "input_sha256": HASH,
            "request_sha256": HASH,
            "policy_version": "v1",
            "policy_sha256": HASH,
            "registry_version": "registry-v1",
            "registry_sha256": HASH,
            "source_sha256": HASH,
            "dependency_lock_sha256": HASH,
            "base_seed": 20260716,
            "thread_policy": "single_process_single_thread",
            "llm_advisor": {
                "mode": "off",
                "status": "disabled",
                "provider_model": None,
                "endpoint_origin_sha256": None,
                "normalized_endpoint_sha256": None,
                "prompt_version": None,
                "output_schema_version": None,
                "calls_requested": 0,
                "calls_accepted": 0,
                "calls_fallback": 0,
                "ledger_sha256": None,
                "training_summary_disclosed": False,
            },
            "environment": {
                "execution_environment_id": HASH,
                "python_implementation": "CPython",
                "python_version": "3.12",
                "platform": "test",
                "architecture": "test",
                "numerical_backend": "test",
                "thread_policy": "single_process_single_thread",
                "packages": {"jsonschema": "test"},
            },
        },
    }


def model_interval(lower: float, upper: float, left: bool, right: bool) -> Json:
    return {
        "lower": lower,
        "upper": upper,
        "left_closed": left,
        "right_closed": right,
    }


def model_certificate(family: str, direction: str, interval: Json) -> Json:
    return {
        "status": "CERTIFIED",
        "registry_version": "registry-v1",
        "family_id": family,
        "ast_sha256": HASH,
        "direction": direction,
        "interval": interval,
        "domain_finite": True,
        "derivative_finite": True,
        "signed_derivative_margin": 0.0,
        "critical_points": [],
        "reason_codes": [],
    }


def model_segment(
    segment_id: str,
    lower: float,
    upper: float,
    left: bool,
    right: bool,
) -> Json:
    interval = model_interval(lower, upper, left, right)
    return {
        "segment_id": segment_id,
        "interval": interval,
        "family_id": "poly1_v1",
        "canonical_ast_id": "poly1_v1",
        "canonical_ast": {"op": "poly1_v1"},
        "ast_sha256": HASH,
        "parameters": {"q": 1.0},
        "transforms": {
            "x_offset": lower,
            "x_scale": 0.5,
            "y_offset": 0.0,
            "y_scale": 1.0,
        },
        "certificate": model_certificate("poly1_v1", "increasing", interval),
        "formula_display": "mu + q*t",
    }


def base_model() -> Json:
    return {
        "schema_version": "model-v1",
        "registry_version": "registry-v1",
        "role": "recommended",
        "analysis_id": HASH,
        "decision_state": "CLEAR_PRACTICAL_UPLIFT",
        "recommendation_status": "TWO_RECOMMENDED",
        "model_structure_hash": HASH,
        "model_instance_hash": HASH,
        "direction": "increasing",
        "domain": {
            "lower": 0.0,
            "upper": 1.0,
            "lower_closed": True,
            "upper_closed": True,
            "extrapolation": False,
        },
        "units": {"x": "1", "y": "1"},
        "segments": [
            model_segment("left", 0.0, 0.5, True, True),
            model_segment("right", 0.5, 1.0, False, True),
        ],
        "p2": {
            "breakpoint": 0.5,
            "membership_convention": "x<=c:left;x>c:right",
            "membership_cell_id": "cell-1",
            "cell_lower_x": 0.4,
            "cell_upper_x": 0.6,
            "shared_mu": 1.0,
            "continuity": {
                "status": "CERTIFIED",
                "left_at_c": 1.0,
                "right_at_c": 1.0,
                "absolute_residual": 0.0,
                "tolerance": 1e-10,
            },
        },
        "formula_display": "F(x)",
        "warning_codes": [],
    }


def make_p2_validation_unavailable(report: Json) -> None:
    procedure = report["validation"]["procedures"]["p2"]
    procedure.update(
        status="unavailable",
        reason="NO_P2",
        r2_oos=unavailable_metric(),
        rmse_oof=unavailable_metric(),
        mae_oof=unavailable_metric(),
        appearance_count=0,
        fallback_count=0,
        failure_codes=["NO_P2"],
    )


def make_uplift_failed(report: Json) -> None:
    report["validation"]["uplift"]["relative_mse_uplift"] = metric(-0.5)
    report["validation"]["uplift"]["delta_mse"] = metric(-0.1)
    report["validation"]["uplift"]["delta_rmse"] = metric(-0.1)
    report["validation"]["uplift"]["delta_r2"] = metric(-0.1)
    report["validation"]["uplift"]["gates"][0].update(
        status="fail", observed=-0.5, reason="BELOW_GATE"
    )


def make_p2_stability_unavailable(report: Json) -> None:
    stability = report["validation"]["p2_stability"]
    stability.update(status="unavailable", reason="UNAVAILABLE")
    for key in (
        "valid_rate",
        "fallback_rate",
        "direction_agreement",
        "dominant_pair_frequency",
        "breakpoint_width_fraction",
        "edge_hit_rate",
        "instability_rate",
        "hard_failure_rate",
    ):
        stability[key] = unavailable_metric()


def make_bootstrap_unavailable(report: Json) -> None:
    bootstrap = report["validation"]["bootstrap_stability"]
    bootstrap.update(
        status="unavailable",
        reason="UNAVAILABLE",
        defined_resamples=0,
        undefined_resamples=200,
        lower=unavailable_metric("bootstrap"),
        upper=unavailable_metric("bootstrap"),
    )


def make_recommendation_metrics_unavailable(report: Json) -> None:
    for key in (
        "global_primary_r2_oos",
        "global_primary_rmse_oof",
        "global_primary_mae_oof",
    ):
        report["recommendation"][key] = unavailable_metric()


def make_refit_r2_unavailable(report: Json) -> None:
    report["refit"]["p2"]["r2_fit_all"] = unavailable_metric("in_sample")
    for segment in report["refit"]["p2"]["segments"]:
        segment["r2_fit"] = unavailable_metric("in_sample")


def make_report_both_constant(report: Json) -> None:
    for segment in report["refit"]["p2"]["segments"]:
        segment.update(
            family_id="constant_v1",
            formula_display="1",
            parameters={"a": 1.0},
            direction="nondecreasing",
        )


def make_report_direction_mismatch(report: Json) -> None:
    report["refit"]["p2"]["segments"][1]["direction"] = "nonincreasing"


def remove_required_plot_artifacts(report: Json) -> None:
    report["artifacts"] = [
        item
        for item in report["artifacts"]
        if item["role"] not in {"figure", "plot_data"}
    ]


def make_llm_frozen_with_fallback(report: Json) -> None:
    report["provenance"]["llm_advisor"] = {
        "mode": "start_advisor",
        "status": "frozen",
        "provider_model": "model",
        "endpoint_origin_sha256": HASH,
        "normalized_endpoint_sha256": HASH,
        "prompt_version": "v1",
        "output_schema_version": "v1",
        "calls_requested": 2,
        "calls_accepted": 1,
        "calls_fallback": 1,
        "ledger_sha256": HASH,
        "training_summary_disclosed": True,
    }
    report["artifacts"].append(
        artifact("trace/llm-advisor.jsonl.gz", "trace", "application/gzip")
    )


def make_model_both_constant(model: Json) -> None:
    for segment in model["segments"]:
        segment["family_id"] = "constant_v1"
        segment["parameters"] = {}
        segment["certificate"]["family_id"] = "constant_v1"


def make_model_certificate_family_mismatch(model: Json) -> None:
    model["segments"][1]["certificate"]["family_id"] = "poly2_v1"


def make_model_direction_mismatch(model: Json) -> None:
    model["segments"][1]["certificate"]["direction"] = "decreasing"


def make_two_nonclear(model: Json) -> None:
    model["decision_state"] = "NO_UPLIFT_OR_HARM"


def make_model_breakpoint_outside_cell(model: Json) -> None:
    model["p2"]["breakpoint"] = 0.9


def make_false_continuity(model: Json) -> None:
    model["p2"]["continuity"].update(
        left_at_c=1.0, right_at_c=2.0, absolute_residual=0.0
    )


def make_certificate_interval_mismatch(model: Json) -> None:
    model["segments"][1]["certificate"]["interval"] = model_interval(
        0.75, 1.0, False, True
    )


def make_shares_sum_to_point_eight(report: Json) -> None:
    for segment in report["refit"]["p2"]["segments"]:
        segment["share"] = 0.4


def make_low_r2_without_warning(report: Json) -> None:
    report["recommendation"]["global_primary_r2_oos"] = metric(0.5996)
    report["warnings"] = []


def make_llm_count_sum_mismatch(report: Json) -> None:
    make_llm_frozen_with_fallback(report)
    advisor = report["provenance"]["llm_advisor"]
    advisor.update(status="partial_fallback", calls_requested=1, calls_accepted=99, calls_fallback=99)


def make_recommendation_metric_mismatch(report: Json) -> None:
    report["recommendation"]["global_primary_r2_oos"] = metric(0.123)


def make_passing_gate_numerically_false(report: Json) -> None:
    report["validation"]["uplift"]["gates"][0].update(
        status="pass", observed=0.0, operator=">=", threshold=0.1, reason=None
    )


def make_bootstrap_count_mismatch(report: Json) -> None:
    report["validation"]["bootstrap_stability"].update(
        requested_resamples=200,
        defined_resamples=190,
        undefined_resamples=20,
    )


def make_bootstrap_bounds_reversed(report: Json) -> None:
    report["validation"]["bootstrap_stability"].update(
        lower=metric(0.30, "bootstrap"),
        upper=metric(0.10, "bootstrap"),
    )


def make_clear_split_effects_fail(report: Json) -> None:
    report["validation"]["split_sensitivity"]["effects"] = [-0.2] * 10


def make_model_ast_certificate_hash_mismatch(model: Json) -> None:
    model["segments"][1]["certificate"]["ast_sha256"] = "1" * 64


def make_one_state(report: Json, code: str) -> None:
    report["status"].update(
        recommendation_status="ONE_RECOMMENDED",
        decision_reason=code,
    )
    report["decision"].update(
        code=code,
        reason="P1 is the selected deployable procedure",
        recommended_procedure="one",
        related_reason_codes=[code],
    )
    report["recommendation"].update(
        role="one",
        formula_display="f1(x)",
        global_primary_r2_oos=metric(0.8),
        global_primary_rmse_oof=metric(0.2),
        global_primary_mae_oof=metric(0.1),
    )


def make_no_valid_p2_with_certified_clear_p2(report: Json) -> None:
    make_one_state(report, "NO_VALID_TWO_SEGMENT")


def make_unstable_with_all_stability_gates_passing(report: Json) -> None:
    make_one_state(report, "UNSTABLE_SELECTION")


def make_statistical_small_with_large_uplift(report: Json) -> None:
    make_one_state(report, "STATISTICAL_ONLY_SMALL")


def make_uncertain_with_clear_bootstrap(report: Json) -> None:
    make_one_state(report, "PRACTICALLY_PROMISING_UNCERTAIN")


def make_null_recommendation(report: Json) -> None:
    report["recommendation"] = {
        "role": None,
        "model_path": None,
        "formula_display": None,
        "domain": None,
        "global_primary_r2_oos": unavailable_metric(),
        "global_primary_rmse_oof": unavailable_metric(),
        "global_primary_mae_oof": unavailable_metric(),
        "extrapolation": None,
    }


def make_descriptive_only_with_validation_quality(report: Json) -> None:
    report["status"].update(
        analysis_status="NO_RECOMMENDATION",
        recommendation_status="NO_VALIDATED_RECOMMENDATION",
        decision_reason="DESCRIPTIVE_ONLY",
    )
    report["decision"].update(
        code="DESCRIPTIVE_ONLY",
        reason="too few x groups for validation",
        recommended_procedure=None,
        related_reason_codes=["DESCRIPTIVE_ONLY"],
    )
    make_null_recommendation(report)
    report["validation"].update(
        status="descriptive_only",
        reason="too few x groups for validation",
    )
    report["artifacts"] = [
        item for item in report["artifacts"] if item["role"] != "model_recommended"
    ]


def make_no_model_refit(refit: Json, reason: str) -> None:
    refit.update(
        status="failed",
        reason=reason,
        model_path=None,
        formula_display=None,
        domain=None,
        direction=None,
        model_structure_hash=None,
        model_instance_hash=None,
    )
    refit["certificate"] = {
        "status": "FAIL",
        "reason": reason,
        "domain_valid": False,
        "monotonicity_valid": False,
        "finite": False,
        "continuity_valid": False,
        "certificate_version": "v1",
    }
    if "family_id" in refit:
        refit.update(family_id=None, parameters=None)
    else:
        refit.update(boundary=None, shared_value=None, segments=[])


def make_pipeline_failure_with_validation_quality(report: Json) -> None:
    report["status"].update(
        analysis_status="FAILED",
        recommendation_status="NO_VALIDATED_RECOMMENDATION",
        decision_reason="PIPELINE_FAILURE",
    )
    report["decision"].update(
        code="PIPELINE_FAILURE",
        reason="P1 fitting failed",
        recommended_procedure=None,
        related_reason_codes=["PIPELINE_FAILURE"],
    )
    make_null_recommendation(report)
    report["validation"].update(status="failed", reason="P1 fitting failed")
    make_no_model_refit(report["refit"]["p1"], "P1 fitting failed")
    make_no_model_refit(report["refit"]["p2"], "P1 fitting failed")
    report["artifacts"] = [
        item
        for item in report["artifacts"]
        if item["role"] not in {"model_candidate", "model_recommended"}
    ]


def make_single_model(model: Json, family: str, direction: str) -> None:
    interval = model_interval(0.0, 1.0, True, True)
    segment = copy.deepcopy(model["segments"][0])
    segment.update(
        segment_id="single",
        interval=interval,
        transforms={
            "x_offset": 0.0,
            "x_scale": 1.0,
            "y_offset": 0.0,
            "y_scale": 1.0,
        },
        certificate=model_certificate(family, direction, interval),
    )
    if family == "constant_v1":
        segment.update(
            family_id="constant_v1",
            canonical_ast_id="constant.a.v1",
            canonical_ast={"op": "constant.a.v1"},
            parameters={"a": 1.0},
            formula_display="a",
        )
    model.update(
        decision_state="NO_VALID_TWO_SEGMENT",
        recommendation_status="ONE_RECOMMENDED",
        direction=direction,
        segments=[segment],
        p2=None,
        formula_display="f1(x)",
    )


def make_single_constant_nonflat(model: Json) -> None:
    make_single_model(model, "constant_v1", "increasing")


def make_single_nonconstant_flat(model: Json) -> None:
    make_single_model(model, "poly1_v1", "flat")


def make_legitimate_partial_fallback(report: Json) -> None:
    report["validation"]["procedures"]["p2"] = report_procedure(
        "fallback", r2=0.9, rmse=0.1, mae=0.05
    )
    report["validation"]["p2_stability"]["valid_rate"] = metric(0.98)
    report["validation"]["p2_stability"]["fallback_rate"] = metric(0.02)


def make_legitimate_undefined_local_r2(report: Json) -> None:
    report["refit"]["p2"]["segments"][0]["r2_fit"] = undefined_metric(
        "in_sample"
    )


def make_legitimate_one_constant_branch(model: Json) -> None:
    segment = model["segments"][0]
    segment["family_id"] = "constant_v1"
    segment["parameters"] = {}
    segment["certificate"]["family_id"] = "constant_v1"


def make_legitimate_one_with_undefined_r2(report: Json) -> None:
    report["status"].update(
        recommendation_status="ONE_RECOMMENDED",
        decision_reason="NO_VALID_TWO_SEGMENT",
    )
    report["decision"].update(
        code="NO_VALID_TWO_SEGMENT",
        reason="P2 unavailable; P1 remains deployable",
        recommended_procedure="one",
        related_reason_codes=["NO_VALID_TWO_SEGMENT", "UNDEFINED_R2"],
    )
    report["recommendation"].update(
        role="one",
        formula_display="f1(x)",
        global_primary_r2_oos=undefined_metric(),
        global_primary_rmse_oof=metric(0.2),
        global_primary_mae_oof=metric(0.1),
    )
    report["validation"]["procedures"]["p1"]["r2_oos"] = undefined_metric()
    report["validation"]["procedures"]["p2"] = {
        "status": "unavailable",
        "reason": "NO_VALID_TWO_SEGMENT",
        "r2_oos": unavailable_metric(),
        "rmse_oof": unavailable_metric(),
        "mae_oof": unavailable_metric(),
        "appearance_count": 0,
        "repetitions": 10,
        "fallback_count": 0,
        "failure_codes": ["NO_VALID_TWO_SEGMENT"],
        "full_data_refit_status": "unavailable",
    }
    for key in (
        "delta_mse",
        "delta_rmse",
        "delta_mae",
        "delta_r2",
        "relative_mse_uplift",
    ):
        report["validation"]["uplift"][key] = unavailable_metric()
    report["validation"]["uplift"]["gates"] = [
        {
            "id": "P2_AVAILABLE",
            "status": "unavailable",
            "observed": None,
            "operator": "available",
            "threshold": None,
            "reason": "NO_VALID_TWO_SEGMENT",
            "policy_version": "v1",
        }
    ]
    report["validation"]["split_sensitivity"].update(
        status="unavailable", reason="NO_VALID_TWO_SEGMENT", effects=[]
    )
    report["validation"]["bootstrap_stability"].update(
        status="unavailable",
        reason="NO_VALID_TWO_SEGMENT",
        defined_resamples=0,
        undefined_resamples=200,
        lower=unavailable_metric("bootstrap"),
        upper=unavailable_metric("bootstrap"),
    )
    make_p2_stability_unavailable(report)
    report["refit"]["p2"] = {
        "status": "unavailable",
        "reason": "NO_VALID_TWO_SEGMENT",
        "model_path": None,
        "formula_display": None,
        "domain": None,
        "direction": None,
        "boundary": None,
        "shared_value": None,
        "certificate": {
            "status": "NOT_RUN",
            "reason": "NO_VALID_TWO_SEGMENT",
            "domain_valid": None,
            "monotonicity_valid": None,
            "finite": None,
            "continuity_valid": None,
            "certificate_version": "v1",
        },
        "segments": [],
        "model_structure_hash": None,
        "model_instance_hash": None,
        "r2_fit_all": unavailable_metric("in_sample"),
        "rmse_fit_all": unavailable_metric("in_sample"),
        "mae_fit_all": unavailable_metric("in_sample"),
    }
    report["warnings"] = [
        {
            "code": "UNDEFINED_R2",
            "scope": "recommendation",
            "stage": "validation",
            "severity": "warning",
            "reason": "Cross-fitted null denominator is zero",
            "recommendation_effect": "warning_only",
            "related_json_pointers": [
                "/recommendation/global_primary_r2_oos"
            ],
        }
    ]
    report["artifacts"] = [
        item
        for item in report["artifacts"]
        if item["path"] != "models/two-segment-model.json"
    ]


def make_no_uplift_with_unavailable_p2(report: Json) -> None:
    make_legitimate_one_with_undefined_r2(report)
    report["status"]["decision_reason"] = "NO_UPLIFT_OR_HARM"
    report["decision"].update(
        code="NO_UPLIFT_OR_HARM",
        reason="synthetic contradictory untested P2",
        related_reason_codes=["NO_UPLIFT_OR_HARM"],
    )


def make_legitimate_no_uplift(report: Json) -> None:
    make_one_state(report, "NO_UPLIFT_OR_HARM")
    report["validation"]["uplift"].update(
        delta_mse=metric(-0.03),
        delta_rmse=metric(-0.1),
        delta_mae=metric(-0.05),
        delta_r2=metric(-0.1),
        relative_mse_uplift=metric(-0.25),
    )
    report["validation"]["uplift"]["gates"][0].update(
        status="fail",
        observed=-0.25,
        reason="NO_UPLIFT_OR_HARM",
    )


def make_legitimate_statistical_small(report: Json) -> None:
    make_one_state(report, "STATISTICAL_ONLY_SMALL")
    report["validation"]["uplift"].update(
        delta_mse=metric(0.01),
        delta_rmse=metric(0.01),
        delta_mae=metric(0.0),
        delta_r2=metric(0.01),
        relative_mse_uplift=metric(0.05),
    )
    report["validation"]["uplift"]["gates"][0].update(
        status="fail",
        observed=0.05,
        reason="BELOW_PRACTICAL_GATE",
    )


def make_legitimate_promising_uncertain(report: Json) -> None:
    make_one_state(report, "PRACTICALLY_PROMISING_UNCERTAIN")
    make_bootstrap_unavailable(report)
    report["validation"]["uplift"]["gates"][0].update(
        id="BOOTSTRAP_STABILITY",
        status="unavailable",
        observed=None,
        operator=">=",
        threshold=0.05,
        reason="UNCERTAINTY_UNAVAILABLE",
    )


def make_legitimate_unstable(report: Json) -> None:
    make_one_state(report, "UNSTABLE_SELECTION")
    report["validation"]["p2_stability"]["valid_rate"] = metric(0.80)
    report["validation"]["uplift"]["gates"][0].update(
        id="P2_VALID_RATE",
        status="fail",
        observed=0.80,
        operator=">=",
        threshold=0.90,
        reason="UNSTABLE_SELECTION",
    )


def make_validation_quality_unavailable(
    report: Json,
    *,
    status: str,
    reason: str,
    full_data_refit_status: str,
) -> None:
    validation = report["validation"]
    validation.update(
        status=status,
        reason=reason,
        outer_folds=0,
        repetitions=0,
    )
    for procedure in validation["procedures"].values():
        procedure.update(
            status="unavailable",
            reason=reason,
            r2_oos=unavailable_metric(),
            rmse_oof=unavailable_metric(),
            mae_oof=unavailable_metric(),
            appearance_count=0,
            repetitions=0,
            fallback_count=0,
            failure_codes=[reason],
            full_data_refit_status=full_data_refit_status,
        )
    for key in (
        "delta_mse",
        "delta_rmse",
        "delta_mae",
        "delta_r2",
        "relative_mse_uplift",
    ):
        validation["uplift"][key] = unavailable_metric()
    validation["uplift"]["gates"] = [
        {
            "id": "VALIDATION_AVAILABLE",
            "status": "unavailable",
            "observed": None,
            "operator": "available",
            "threshold": None,
            "reason": reason,
            "policy_version": "v1",
        }
    ]
    validation["split_sensitivity"].update(
        status="unavailable",
        reason=reason,
        effects=[],
    )
    validation["bootstrap_stability"].update(
        status="unavailable",
        reason=reason,
        requested_resamples=200,
        defined_resamples=0,
        undefined_resamples=200,
        lower=unavailable_metric("bootstrap"),
        upper=unavailable_metric("bootstrap"),
    )
    make_p2_stability_unavailable(report)
    validation["p2_stability"]["reason"] = reason


def make_legitimate_descriptive_only(report: Json) -> None:
    make_descriptive_only_with_validation_quality(report)
    make_validation_quality_unavailable(
        report,
        status="descriptive_only",
        reason="DESCRIPTIVE_ONLY",
        full_data_refit_status="certified",
    )


def make_legitimate_pipeline_failure(report: Json) -> None:
    make_pipeline_failure_with_validation_quality(report)
    make_validation_quality_unavailable(
        report,
        status="failed",
        reason="PIPELINE_FAILURE",
        full_data_refit_status="failed",
    )
    for refit in report["refit"].values():
        refit["r2_fit_all"] = unavailable_metric("in_sample")
        refit["rmse_fit_all"] = unavailable_metric("in_sample")
        refit["mae_fit_all"] = unavailable_metric("in_sample")


def make_legitimate_single_constant_flat(model: Json) -> None:
    make_single_model(model, "constant_v1", "flat")


def make_legitimate_single_nonconstant(model: Json) -> None:
    make_single_model(model, "poly1_v1", "increasing")


def make_llm_state(
    report: Json,
    status: str,
    requested: int,
    accepted: int,
    fallback: int,
) -> None:
    report["provenance"]["llm_advisor"] = {
        "mode": "start_advisor",
        "status": status,
        "provider_model": "model",
        "endpoint_origin_sha256": HASH,
        "normalized_endpoint_sha256": HASH,
        "prompt_version": "v1",
        "output_schema_version": "v1",
        "calls_requested": requested,
        "calls_accepted": accepted,
        "calls_fallback": fallback,
        "ledger_sha256": HASH,
        "training_summary_disclosed": True,
    }
    report["artifacts"].append(
        artifact("trace/llm-advisor.jsonl.gz", "trace", "application/gzip")
    )


def make_legitimate_llm_frozen(report: Json) -> None:
    make_llm_state(report, "frozen", 1, 1, 0)


def make_legitimate_llm_partial(report: Json) -> None:
    make_llm_state(report, "partial_fallback", 2, 1, 1)


def make_legitimate_llm_unavailable(report: Json) -> None:
    make_llm_state(report, "unavailable", 1, 0, 1)


SCHEMA_REJECT_REPORT: list[tuple[str, Mutation]] = [
    ("two_clear_requires_p2_validation", make_p2_validation_unavailable),
    ("two_requires_recommendation_metrics", make_recommendation_metrics_unavailable),
    ("two_clear_requires_positive_passing_uplift", make_uplift_failed),
    ("two_clear_requires_p2_stability", make_p2_stability_unavailable),
    ("two_clear_requires_bootstrap_stability", make_bootstrap_unavailable),
    ("certified_p2_requires_computed_global_and_local_r2", make_refit_r2_unavailable),
    ("report_p2_must_not_have_two_constant_branches", make_report_both_constant),
    ("report_p2_segment_directions_match_root", make_report_direction_mismatch),
    ("successful_report_requires_plot_artifacts", remove_required_plot_artifacts),
    ("llm_frozen_status_forbids_fallback", make_llm_frozen_with_fallback),
    ("no_valid_two_segment_forbids_certified_p2", make_no_valid_p2_with_certified_clear_p2),
    ("no_uplift_requires_tested_p2", make_no_uplift_with_unavailable_p2),
    ("unstable_selection_requires_failed_stability_gate", make_unstable_with_all_stability_gates_passing),
    ("statistical_only_small_requires_subthreshold_uplift", make_statistical_small_with_large_uplift),
    ("promising_uncertain_forbids_clear_bootstrap", make_uncertain_with_clear_bootstrap),
    ("descriptive_only_forbids_validation_quality", make_descriptive_only_with_validation_quality),
    ("pipeline_failure_forbids_validation_and_fit_quality", make_pipeline_failure_with_validation_quality),
]

SCHEMA_REJECT_MODEL: list[tuple[str, Mutation]] = [
    ("model_p2_must_not_have_two_constant_branches", make_model_both_constant),
    ("model_segment_certificate_family_matches", make_model_certificate_family_mismatch),
    ("model_segment_certificate_direction_matches", make_model_direction_mismatch),
    ("two_recommendation_requires_clear_uplift", make_two_nonclear),
    ("single_constant_requires_flat_direction", make_single_constant_nonflat),
    ("single_flat_requires_canonical_constant", make_single_nonconstant_flat),
]

SCHEMA_ACCEPT_REPORT: list[tuple[str, Mutation]] = [
    ("two_allows_partial_outer_fallback_when_gates_pass", make_legitimate_partial_fallback),
    ("certified_p2_allows_undefined_local_r2", make_legitimate_undefined_local_r2),
    ("one_allows_undefined_r2_and_unavailable_p2", make_legitimate_one_with_undefined_r2),
    ("one_allows_tested_p2_with_no_uplift", make_legitimate_no_uplift),
    ("one_allows_subthreshold_positive_uplift", make_legitimate_statistical_small),
    ("one_allows_promising_uplift_with_unavailable_uncertainty", make_legitimate_promising_uncertain),
    ("one_allows_practical_uplift_with_failed_stability", make_legitimate_unstable),
    ("descriptive_only_allows_candidate_fits_without_validation_quality", make_legitimate_descriptive_only),
    ("pipeline_failure_allows_typed_unavailable_quality", make_legitimate_pipeline_failure),
    ("llm_frozen_consistent_counts", make_legitimate_llm_frozen),
    ("llm_partial_fallback_consistent_counts", make_legitimate_llm_partial),
    ("llm_unavailable_consistent_counts", make_legitimate_llm_unavailable),
]

SCHEMA_ACCEPT_MODEL: list[tuple[str, Mutation]] = [
    ("p2_allows_one_constant_branch", make_legitimate_one_constant_branch),
    ("p1_allows_canonical_flat_constant", make_legitimate_single_constant_flat),
    ("p1_allows_nonflat_nonconstant", make_legitimate_single_nonconstant),
]

SEMANTIC_REJECT_REPORT: list[tuple[str, Mutation, str]] = [
    (
        "segment_shares_must_sum_to_one_and_counts_reconcile",
        make_shares_sum_to_point_eight,
        "sum(segment.share)==1 and sum(segment.n)==input.n_used",
    ),
    (
        "raw_r2_below_point_six_requires_warning",
        make_low_r2_without_warning,
        "BELOW_PRODUCT_R2 iff defined recommended raw R2_OOS < 0.60",
    ),
    (
        "llm_call_counts_must_reconcile",
        make_llm_count_sum_mismatch,
        "calls_accepted + calls_fallback == calls_requested",
    ),
    (
        "recommendation_metrics_equal_selected_procedure",
        make_recommendation_metric_mismatch,
        "recommendation R2/RMSE/MAE objects equal validation.procedures[recommended_procedure]",
    ),
    (
        "passing_gate_must_satisfy_operator",
        make_passing_gate_numerically_false,
        "re-evaluate every gate observed/operator/threshold and require the exact frozen gate inventory",
    ),
    (
        "bootstrap_counts_must_reconcile",
        make_bootstrap_count_mismatch,
        "defined_resamples + undefined_resamples == requested_resamples == 200",
    ),
    (
        "bootstrap_interval_bounds_must_order_and_recompute",
        make_bootstrap_bounds_reversed,
        "when available, lower <= upper and both endpoints recompute from retained bootstrap resamples",
    ),
    (
        "split_effects_must_recompute_positive_share_and_p10",
        make_clear_split_effects_fail,
        "effects length equals repetitions; recomputed positive share >= 0.90 and p10 >= 0 for CLEAR_PRACTICAL_UPLIFT",
    ),
]

SEMANTIC_REJECT_MODEL: list[tuple[str, Mutation, str]] = [
    (
        "breakpoint_must_be_inside_cell_and_equal_segment_join",
        make_model_breakpoint_outside_cell,
        "cell_lower_x <= breakpoint < cell_upper_x and segment endpoints equal breakpoint",
    ),
    (
        "continuity_values_must_recompute",
        make_false_continuity,
        "left_at_c/right_at_c/shared_mu/residual must equal independent evaluator output",
    ),
    (
        "certificate_interval_must_equal_segment_interval",
        make_certificate_interval_mismatch,
        "certificate.interval equals its segment.interval field-for-field",
    ),
    (
        "functionally_indistinguishable_p2_must_collapse",
        lambda model: None,
        "evaluate both centered branches; indistinguishable joined P2 becomes canonical P1",
    ),
    (
        "certificate_ast_hash_must_equal_segment_ast_hash",
        make_model_ast_certificate_hash_mismatch,
        "certificate.ast_sha256 equals segment.ast_sha256 and both recompute from canonical_ast",
    ),
]


def errors(validator: Draft202012Validator, instance: Json) -> list[str]:
    return [error.message for error in validator.iter_errors(instance)]


def mutated(base: Json, mutation: Mutation) -> Json:
    candidate = copy.deepcopy(base)
    mutation(candidate)
    return candidate


def main() -> int:
    model_validator = load_validator(MODEL_SCHEMA_PATH)
    report_validator = load_validator(REPORT_SCHEMA_PATH)
    model = base_model()
    report = base_report()
    failures: list[str] = []

    for label, validator, instance in (
        ("base_model", model_validator, model),
        ("base_report", report_validator, report),
    ):
        detail = errors(validator, instance)
        if detail:
            failures.append(f"{label}: expected VALID: {detail[:2]}")

    for label, mutation in SCHEMA_REJECT_MODEL:
        if not errors(model_validator, mutated(model, mutation)):
            failures.append(f"model/{label}: expected schema rejection")
    for label, mutation in SCHEMA_REJECT_REPORT:
        if not errors(report_validator, mutated(report, mutation)):
            failures.append(f"report/{label}: expected schema rejection")

    for label, mutation in SCHEMA_ACCEPT_MODEL:
        detail = errors(model_validator, mutated(model, mutation))
        if detail:
            failures.append(f"model/{label}: expected VALID: {detail[:2]}")
    for label, mutation in SCHEMA_ACCEPT_REPORT:
        detail = errors(report_validator, mutated(report, mutation))
        if detail:
            failures.append(f"report/{label}: expected VALID: {detail[:2]}")

    semantic_requirements: list[dict[str, str]] = []
    for label, mutation, requirement in SEMANTIC_REJECT_MODEL:
        detail = errors(model_validator, mutated(model, mutation))
        if detail:
            failures.append(
                f"model/{label}: semantic-only fixture unexpectedly rejected: {detail[:2]}"
            )
        semantic_requirements.append(
            {"schema": "model", "case": label, "requirement": requirement}
        )
    for label, mutation, requirement in SEMANTIC_REJECT_REPORT:
        detail = errors(report_validator, mutated(report, mutation))
        if detail:
            failures.append(
                f"report/{label}: semantic-only fixture unexpectedly rejected: {detail[:2]}"
            )
        semantic_requirements.append(
            {"schema": "report", "case": label, "requirement": requirement}
        )

    result = {
        "status": "PASS" if not failures else "FAIL",
        "schema_reject_cases": len(SCHEMA_REJECT_MODEL) + len(SCHEMA_REJECT_REPORT),
        "schema_accept_cases": len(SCHEMA_ACCEPT_MODEL) + len(SCHEMA_ACCEPT_REPORT),
        "semantic_validator_requirements": semantic_requirements,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
