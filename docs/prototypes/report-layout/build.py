#!/usr/bin/env python3
"""Build the throwaway synthetic calibration-report layout prototype.

The generated bundles are intentionally production-shaped but never production
reports: they contain no COMPLETE marker and no recommended-model.json.  Every
human artifact is rendered from the report.json that was just written and read
back.  Python stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import json
import math
import os
import re
import shutil
import statistics
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit


HERE = Path(__file__).resolve().parent
MATRIX = HERE / "fixtures" / "matrix.json"
GENERATED = HERE / "generated"
CANONICAL_SCHEMA = HERE.parents[1] / "specification" / "report.schema.json"
SCHEMA_VERSION = "prototype-report-1.0.0"
IDENTIFIER_SAFE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-"
CSP = (
    "default-src 'none'; script-src 'none'; connect-src 'none'; "
    "object-src 'none'; frame-src 'none'; base-uri 'none'; "
    "form-action 'none'; font-src 'none'; media-src 'none'; "
    "img-src 'self' data:; style-src 'unsafe-inline'"
)


def dump_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def artifact_media_type(path: Path | str) -> str:
    name = str(path)
    if name.endswith(".html"):
        return "text/html"
    if name.endswith(".svg"):
        return "image/svg+xml"
    if name.endswith(".csv"):
        return "text/csv"
    if name.endswith(".jsonl.gz"):
        return "application/gzip"
    if name.endswith(".jsonl"):
        return "application/x-ndjson"
    if name.endswith(".json"):
        return "application/json"
    return "application/octet-stream"


def csvw_metadata(filename: str, title: str, fields: list[str], *, identifiers: bool) -> dict:
    value = {
        "@context": "http://www.w3.org/ns/csvw",
        "url": filename,
        "dc:title": title,
        "dialect": {
            "encoding": "utf-8",
            "delimiter": ",",
            "lineTerminators": ["\n"],
            "quoteChar": '"',
            "doubleQuote": True,
            "header": True,
            "null": [""],
        },
        "tableSchema": {"columns": [{"name": name} for name in fields]},
    }
    if identifiers:
        value["identifierEncoding"] = "id: + UTF-8 percent encoding"
    return value


def encode_export_id(value: str) -> str:
    return "id:" + quote(value, safe=IDENTIFIER_SAFE)


def metric(
    value: float | None,
    scope: str,
    reason: str | None = None,
    unit: str = "1",
    definition: str | None = None,
) -> dict:
    return {
        "value": value,
        "status": "defined" if value is not None else "undefined",
        "reason": None if value is not None else (reason or "UNDEFINED_R2"),
        "scope": scope,
        "definition": definition or ("pooled_oof_cross_fitted_null" if scope == "oof" else "in_sample_sse"),
        "unit": unit,
    }


def canonical_registry_ast(family_id: str) -> dict:
    if family_id == "constant_v1":
        return {
            "ast_id": "registry_v1:constant_v1",
            "root": "parameter",
            "prefix": ["parameter:a"],
            "node_count": 1,
        }
    if family_id == "poly1_v1":
        return {
            "ast_id": "registry_v1:poly1_v1",
            "root": "add",
            "prefix": ["add", "parameter:a", "multiply", "parameter:b", "variable:t"],
            "node_count": 5,
        }
    raise ValueError(f"family is not exercised by the report prototype: {family_id}")


def points_for(profile: str) -> list[dict]:
    if profile == "repeated":
        xs = [0, 1, 2, 3, 3, 3, 3, 4, 5, 6, 7]
        ys = [1.0, 10.0, 2.0, 2.5, 2.7, 2.7, 3.2, 3.8, 4.2, 5.0, 5.4]
    elif profile == "constant":
        xs = list(range(10))
        ys = [4.0] * 10
    elif profile == "small":
        xs = list(range(6))
        ys = [1.1, 1.4, 1.9, 2.3, 2.9, 3.1]
    elif profile == "negative":
        xs = list(range(10))
        ys = [2.0, 5.0, 1.0, 6.0, 2.4, 20.0, 1.8, 6.5, 2.1, 5.5]
    elif profile == "hard_failure":
        xs = list(range(10))
        ys = [1.0 + 0.4 * x for x in xs]
    elif profile == "two":
        xs = list(range(10))
        ys = [1.8, 2.1, 2.4, 20.0, 3.4, 4.4, 5.4, 6.4, 7.5, 8.5]
    else:
        xs = list(range(12))
        ys = [1.2 + 0.63 * x + (0.10 if x % 3 == 0 else -0.05) for x in xs]
    return [
        {
            "row_id": f"row-{index + 1:02d}",
            "source_row_id": str(index + 2),
            "x": float(x),
            "y": float(y),
            "x_group_id": f"x:{float(x).hex()}",
            "input_status": "USED",
            "flags": [],
            "action": "review_only",
        }
        for index, (x, y) in enumerate(zip(xs, ys))
    ]


def p1_value(x: float, profile: str) -> float:
    if profile == "constant":
        return 4.0
    return 1.25 + 0.64 * x


def p2_value(x: float, c: float = 5.5) -> float:
    return 1.75 + 0.32 * x if x <= c else (1.75 + 0.32 * c) + 0.98 * (x - c)


def grouped_null_predictions(rows: list[dict]) -> list[float]:
    """Synthetic leave-one-x-group-out null predictions for one OOF appearance."""
    result: list[float] = []
    for row in rows:
        training = [other["y"] for other in rows if other["x_group_id"] != row["x_group_id"]]
        if not training:
            raise ValueError("synthetic fixture has no training group for the OOF null")
        result.append(sum(training) / len(training))
    return result


def fold_count_for_unique_x(unique_x: int) -> int:
    return 5 if unique_x >= 10 else 4


def outer_fit_id(row_index: int, repetition: int, unique_x: int) -> str:
    fold_count = fold_count_for_unique_x(unique_x)
    fold = ((row_index + repetition - 1) % fold_count) + 1
    return f"outer-{repetition:02d}-fold-{fold}"


def oof_predictions_for_r2(
    rows: list[dict], null_predictions: list[float], target_r2: float | None, *, exact_constant: bool = False
) -> tuple[list[float | None], list[float | None]]:
    """Construct deterministic synthetic OOF predictions with the declared pooled R²."""
    if target_r2 is None and not exact_constant:
        return [None] * len(rows), [None] * len(rows)
    null_residuals = [row["y"] - prediction for row, prediction in zip(rows, null_predictions)]
    denominator = sum(value * value for value in null_residuals)
    if denominator == 0.0:
        residuals = [0.0] * len(rows)
    else:
        if target_r2 is None or target_r2 > 1.0:
            raise ValueError(f"invalid synthetic R² target: {target_r2!r}")
        scale = math.sqrt(1.0 - target_r2)
        residuals = [scale * value for value in null_residuals]
    predictions = [row["y"] - residual for row, residual in zip(rows, residuals)]
    return predictions, residuals


def error_stats(residuals: list[float | None]) -> tuple[float | None, float | None, float | None]:
    finite = [value for value in residuals if value is not None]
    if len(finite) != len(residuals) or not finite:
        return None, None, None
    mse = sum(value * value for value in finite) / len(finite)
    rmse = math.sqrt(mse)
    mae = sum(abs(value) for value in finite) / len(finite)
    return mse, rmse, mae


def finite_sample_qn(values: list[float]) -> float | None:
    """Return the finite-sample-corrected Rousseeuw-Croux Qn scale.

    For n>=2, Qn = 2.2219 * d_n * {|x_i-x_j|; i<j}_{(k)}, where
    h=floor(n/2)+1 and k=choose(h, 2).  The small-sample corrections are the
    published/common robustbase values; for n>9, d_n=n/(n+1.4) for odd n and
    n/(n+3.8) for even n.  Zero is a valid computed result and is handled by
    the separate zero-scale tolerance policy, never by a positive floor.
    """
    n = len(values)
    if n < 2 or any(not math.isfinite(value) for value in values):
        return None
    pairwise = sorted(
        abs(values[left] - values[right])
        for left in range(n - 1)
        for right in range(left + 1, n)
    )
    h = n // 2 + 1
    k = h * (h - 1) // 2
    small_sample = {
        2: 0.399,
        3: 0.994,
        4: 0.512,
        5: 0.844,
        6: 0.611,
        7: 0.857,
        8: 0.669,
        9: 0.872,
    }
    correction = small_sample.get(
        n,
        n / (n + 1.4) if n % 2 else n / (n + 3.8),
    )
    return 2.2219 * correction * pairwise[k - 1]


def robust_scale(values: list[float], tolerance: float) -> tuple[float | None, str]:
    """Apply contract order Qn first, then MAD only when Qn is impossible."""
    raw = finite_sample_qn(values)
    estimator = "qn_finite_sample"
    if raw is None:
        estimator = "finite_sample_s_mad"
        if not values or any(not math.isfinite(value) for value in values):
            return None, estimator
        center = statistics.median(values)
        raw = 1.4826 * statistics.median(abs(item - center) for item in values)
    return (raw if math.isfinite(raw) and raw > tolerance else None), estimator


def tie_safe_cells(rows: list[dict]) -> list[dict]:
    """Enumerate raw-binary64 adjacent-x cells satisfying the 40/60 rule."""
    unique_x = sorted({float(row["x"]) for row in rows})
    minimum_left = math.ceil(0.4 * len(rows))
    maximum_left = math.floor(0.6 * len(rows))
    cells: list[dict] = []
    for raw_index, (lower, upper) in enumerate(zip(unique_x, unique_x[1:]), 1):
        left_n = sum(float(row["x"]) <= lower for row in rows)
        if minimum_left <= left_n <= maximum_left:
            cells.append({
                "cell_id": f"raw-cell-{raw_index:03d}",
                "lower_x": lower,
                "upper_x_exclusive": upper,
                "left_n": left_n,
                "right_n": len(rows) - left_n,
                "left_share": left_n / len(rows),
                "right_share": (len(rows) - left_n) / len(rows),
                "ties_atomic": True,
            })
    return cells


def optimizer_summary(
    rows: list[dict],
    *,
    validation_available: bool,
    p2_refit_available: bool,
    p2_failure: str | None,
    pipeline_failure: str | None,
) -> dict:
    cells = tie_safe_cells(rows)
    if p2_refit_available:
        status, reason = "SUCCEEDED", None
    elif p2_failure == "COLLAPSED_SEGMENTS":
        status, reason = "COLLAPSED", p2_failure
    elif p2_failure == "NO_BALANCED_SPLIT":
        status, reason = "NO_FEASIBLE_CELL", p2_failure
    elif pipeline_failure == "OPTIMIZER_FAILURE" or (
        p2_failure and p2_failure not in {"NO_BALANCED_SPLIT", "COLLAPSED_SEGMENTS"}
    ):
        status, reason = "FAILED", pipeline_failure or p2_failure
    else:
        status, reason = "NOT_RUN", pipeline_failure or (
            "VALIDATION_UNAVAILABLE" if not validation_available else "P2_REFIT_UNAVAILABLE"
        )

    attempts_run = status in {"SUCCEEDED", "COLLAPSED", "FAILED"}
    starts_per_cell = 2 if attempts_run and cells else 0
    attempt_count = len(cells) * starts_per_cell
    successful_attempt_count = attempt_count if status in {"SUCCEEDED", "COLLAPSED"} else 0
    failed_attempt_count = attempt_count - successful_attempt_count
    evaluation_count = sum(
        (12 + (index % 3)) if successful_attempt_count else 2
        for index in range(attempt_count)
    )
    return {
        "strategy": "PROFILE_CELLS",
        "scope": "full_data_p2_refit",
        "status": status,
        "reason": reason,
        "trace_path": "trace/fit-attempts.jsonl.gz",
        "tie_safe_cell_count": len(cells),
        "eligible_cell_count": len(cells),
        "starts_per_cell": starts_per_cell,
        "start_count": attempt_count,
        "attempt_count": attempt_count,
        "trace_record_count": attempt_count,
        "successful_attempt_count": successful_attempt_count,
        "failed_attempt_count": failed_attempt_count,
        "evaluation_count": evaluation_count,
        "competing_basin_count": 0,
        "competing_basin_status": "NONE" if successful_attempt_count else "UNAVAILABLE",
        "certificate_result": "PASS" if status in {"SUCCEEDED", "COLLAPSED"} else "NOT_RUN",
        "reason_codes": [reason] if reason else [],
        "warning_codes": ["COLLAPSED_SEGMENTS"] if status == "COLLAPSED" else [],
    }


def r2_from_predictions(rows: list[dict], field: str, subset: str | None = None) -> float | None:
    selected = [row for row in rows if subset is None or row.get("segment_refit") == subset]
    predictions = [row.get(field) for row in selected]
    if not selected or any(value is None for value in predictions):
        return None
    mean_y = sum(row["y"] for row in selected) / len(selected)
    denominator = sum((row["y"] - mean_y) ** 2 for row in selected)
    if denominator == 0.0:
        return None
    numerator = sum((row["y"] - prediction) ** 2 for row, prediction in zip(selected, predictions))
    return 1.0 - numerator / denominator


def pooled_oof_r2(rows: list[dict], prediction_field: str) -> float | None:
    predictions = [row.get(prediction_field) for row in rows]
    null_predictions = [row.get("pred_null_oof") for row in rows]
    if any(value is None for value in predictions + null_predictions):
        return None
    denominator = sum((row["y"] - prediction) ** 2 for row, prediction in zip(rows, null_predictions))
    if denominator == 0.0:
        return None
    numerator = sum((row["y"] - prediction) ** 2 for row, prediction in zip(rows, predictions))
    return 1.0 - numerator / denominator


def linear_percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def synthetic_bootstrap_values(
    recommendation: str | None,
    p2_refit_available: bool,
    full_fallback_rel_uplift: float | None = None,
) -> list[float]:
    if full_fallback_rel_uplift is not None:
        return [full_fallback_rel_uplift] * 200
    if not p2_refit_available:
        return []
    low, high = (0.05, 0.32) if recommendation == "two" else (-0.05, 0.05)
    return [low + (high - low) * index / 189 for index in range(190)]


def build_diagnostics(
    rows: list[dict],
    spec: dict,
    recommendation: str | None,
    validation_available: bool,
    p1_refit_available: bool,
    p2_refit_available: bool,
    repetitions: int,
    c: float,
    x_domain: list[float],
) -> dict:
    """Create deterministic synthetic diagnostics whose flags are computed, never copied.

    The prototype treats residuals from all other x-groups as the outer-training
    inner-OOF residual sample for each held-out group.  This exercises the
    cross-fitted scale/null semantics without pretending to be the production
    nested solver.
    """

    threshold = 3.5
    scale_method = "qn_then_mad_fallback"
    scale_scope = "outer_train_inner_oof"
    procedure = recommendation
    procedure_name = "two" if procedure == "two" else "one"

    scale_traces: list[dict] = []
    scale_trace_by_key: dict[tuple[str, str], dict] = {}

    def scale_for(row: dict, procedure_id: str) -> tuple[float | None, str | None, dict]:
        residual_field = f"resid_{procedure_id}_oof"
        training_rows = [
            other for other in rows
            if other["x_group_id"] != row["x_group_id"] and other.get(residual_field) is not None
        ]
        training_y = [abs(float(other["y"])) for other in training_rows]
        tolerance = 64.0 * sys.float_info.epsilon * max([1.0, *training_y])
        if not validation_available or not training_rows:
            trace = {
                "row_id": row["row_id"], "x_group_id": row["x_group_id"], "procedure": procedure_id,
                "status": "unavailable", "reason": "OOF_DIAGNOSTICS_UNAVAILABLE" if not validation_available else "SCALE_UNDEFINED",
                "estimator": "qn_finite_sample", "scope": scale_scope, "global_scale": None,
                "bin_count": 0, "target_bin_id": None, "target_bin_group_ids": [],
                "raw_local_scale": None, "local_floor": None, "final_scale": None,
                "zero_tolerance": tolerance, "training_sample_count": 0, "local_sample_count": 0,
            }
            return None, trace["reason"], trace

        training_values = [float(other[residual_field]) for other in training_rows]
        global_scale, global_estimator = robust_scale(training_values, tolerance)
        group_x = {
            other["x_group_id"]: float(other["x"])
            for other in training_rows
        }
        ordered_groups = sorted(group_x, key=group_x.get)
        bin_count = max(1, min(4, len(ordered_groups) // 5))
        bins: list[list[str]] = [[] for _ in range(bin_count)]
        for index, group_id in enumerate(ordered_groups):
            bins[min(bin_count - 1, math.floor(index * bin_count / len(ordered_groups)))].append(group_id)

        def distance_to_bin(group_ids: list[str]) -> float:
            low = min(group_x[group_id] for group_id in group_ids)
            high = max(group_x[group_id] for group_id in group_ids)
            return low - row["x"] if row["x"] < low else row["x"] - high if row["x"] > high else 0.0

        target_index = min(range(bin_count), key=lambda index: (distance_to_bin(bins[index]), index))
        target_groups = bins[target_index]
        local_values = [
            float(other[residual_field]) for other in training_rows
            if other["x_group_id"] in target_groups
        ]
        raw_local_scale, local_estimator = robust_scale(local_values, tolerance)
        local_floor = 0.5 * global_scale if global_scale is not None else None
        if bin_count == 1 or raw_local_scale is None:
            final_scale = global_scale
            final_estimator = global_estimator
        elif local_floor is None:
            final_scale = raw_local_scale
            final_estimator = local_estimator
        else:
            final_scale = max(raw_local_scale, local_floor)
            final_estimator = local_estimator if raw_local_scale >= local_floor else global_estimator
        reason = None if final_scale is not None else "ZERO_OR_UNDEFINED_SCALE"
        trace = {
            "row_id": row["row_id"], "x_group_id": row["x_group_id"], "procedure": procedure_id,
            "status": "defined" if final_scale is not None else "undefined", "reason": reason,
            "estimator": final_estimator, "scope": scale_scope, "global_scale": global_scale,
            "bin_count": bin_count, "target_bin_id": f"bin-{target_index + 1}",
            "target_bin_group_ids": target_groups, "raw_local_scale": raw_local_scale,
            "local_floor": local_floor, "final_scale": final_scale,
            "zero_tolerance": tolerance, "training_sample_count": len(training_rows),
            "local_sample_count": len(local_values),
        }
        return final_scale, reason, trace

    for row in rows:
        for name in ("one", "two"):
            residual = row.get(f"resid_{name}_oof")
            scale, reason, trace = scale_for(row, name)
            z_value = residual / scale if residual is not None and scale is not None else None
            row[f"scale_{name}_oof"] = scale
            row[f"scale_{name}_reason"] = reason
            row[f"z_{name}_oof"] = z_value
            scale_traces.append(trace)
            scale_trace_by_key[(row["row_id"], name)] = trace

    selected_reference_scales = [
        row.get(f"scale_{procedure_name}_oof")
        for row in rows
    ]
    influence_available = (
        validation_available
        and recommendation is not None
        and bool(selected_reference_scales)
        and all(scale is not None and scale > 0 for scale in selected_reference_scales)
    )
    influence_overrides = spec.get("synthetic_influence_dmax_by_x", {})
    x_span = x_domain[1] - x_domain[0]
    center_x = sum(x_domain) / 2.0
    influence_groups: list[dict] = []
    influence_by_group: dict[str, dict] = {}
    full_influence_state = {
        "recommendation": recommendation,
        "decision_code": spec["decision_reason"],
        "p2_status": "certified" if p2_refit_available else "unavailable",
        "direction": (
            "flat" if p1_refit_available and spec["profile"] == "constant"
            else "nondecreasing" if p1_refit_available
            else None
        ),
        "family": (
            "poly1_v1|poly1_v1" if recommendation == "two"
            else "constant_v1" if p1_refit_available and spec["profile"] == "constant"
            else "poly1_v1" if p1_refit_available
            else None
        ),
        "certificate": "PASS" if p1_refit_available else spec.get("pipeline_failure"),
    }
    for x_group_id in sorted({row["x_group_id"] for row in rows}, key=lambda group: next(row["x"] for row in rows if row["x_group_id"] == group)):
        group_rows = [row for row in rows if row["x_group_id"] == x_group_id]
        x_value = float(group_rows[0]["x"])
        override_key = format(x_value, ".17g")
        if influence_available:
            baseline = 0.08 + 0.02 * (abs(x_value - center_x) / (x_span / 2.0 or 1.0))
            dmax = float(influence_overrides.get(override_key, baseline))
            drms = dmax / 2.0
            delta_c = (0.06 if override_key in influence_overrides else 0.01) if p2_refit_available else None
            share_delta = 0.06 if override_key in influence_overrides else 0.01
            rmse_relative_delta = 0.12 if override_key in influence_overrides else 0.02
            uplift_delta = 0.06 if override_key in influence_overrides else 0.01
            high = (
                dmax >= 0.50 or drms >= 0.25
                or (delta_c is not None and delta_c >= 0.05)
                or share_delta >= 0.05 or rmse_relative_delta >= 0.10 or uplift_delta >= 0.05
            )
            status = "sensitivity_only"
            reason = None
        else:
            dmax = drms = delta_c = share_delta = rmse_relative_delta = uplift_delta = None
            high = False
            status = "unassessable"
            reason = "INFLUENCE_UNASSESSABLE"
        item = {
            "x_group_id": x_group_id,
            "x": x_value,
            "row_ids": [row["row_id"] for row in group_rows],
            "status": status,
            "reason": reason,
            "dmax": dmax,
            "drms": drms,
            "delta_c": delta_c,
            "segment_share_delta": share_delta,
            "oof_rmse_relative_delta": rmse_relative_delta,
            "rel_mse_uplift_delta": uplift_delta,
            "full_state": full_influence_state,
            "without_group_state": dict(full_influence_state),
            "recommendation_changed": False,
            "decision_changed": False,
            "p2_status_changed": False,
            "family_changed": False,
            "direction_changed": False,
            "certificate_changed": False,
            "high_refit_influence": high,
            "action": "review_only",
        }
        influence_groups.append(item)
        influence_by_group[x_group_id] = item

    diagnostic_rows: list[dict] = []
    flagged_rows: list[dict] = []
    for row_index, row in enumerate(rows):
        residual = row.get(f"resid_{procedure_name}_oof") if procedure else None
        prediction = row.get(f"pred_{procedure_name}_oof") if procedure else None
        scale = row.get(f"scale_{procedure_name}_oof") if procedure else None
        scale_reason = row.get(f"scale_{procedure_name}_reason") if procedure else "OOF_DIAGNOSTICS_UNAVAILABLE"
        selected_scale_trace = scale_trace_by_key[(row["row_id"], procedure_name)]
        scale_trace_index = scale_traces.index(selected_scale_trace)
        z_value = row.get(f"z_{procedure_name}_oof") if procedure else None
        score = abs(z_value) if z_value is not None else None
        flag_rate = 1.0 if score is not None and score >= threshold else 0.0 if score is not None else None
        influence = influence_by_group[row["x_group_id"]]
        typed_flags: list[dict] = []
        if score is not None and score >= threshold and flag_rate >= 0.5:
            typed_flags.append({
                "code": "LARGE_OOF_RESIDUAL",
                "severity": "extreme" if score >= 5.25 else "review",
                "reason": "ROBUST_STANDARDIZED_OOF_RESIDUAL",
                "threshold": threshold,
                "policy_version": "prototype-policy-v1",
                "explanation": "median |z_OOF| and appearance flag rate meet the review threshold",
            })
        if influence["high_refit_influence"]:
            typed_flags.append({
                "code": "HIGH_REFIT_INFLUENCE",
                "severity": "review",
                "reason": "LEAVE_ONE_X_GROUP_OUT_SENSITIVITY",
                "threshold": 0.50,
                "policy_version": "prototype-policy-v1",
                "explanation": "at least one versioned influence threshold is met",
            })
        flag_codes = [item["code"] for item in typed_flags]
        row["flags"] = flag_codes
        row["action"] = "review_only"
        refit_field = "pred_two_refit" if recommendation == "two" else "pred_one_refit"
        if recommendation is None and p1_refit_available:
            refit_field = "pred_one_refit"
        refit_prediction = row.get(refit_field) if p1_refit_available else None
        unique_x_count = len({item["x_group_id"] for item in rows})
        outer_folds = [
            outer_fit_id(row_index, repetition, unique_x_count)
            for repetition in range(1, repetitions + 1)
        ]
        diagnostic_row = {
            "row_id": row["row_id"],
            "source_row_id": row["source_row_id"],
            "x": row["x"],
            "y": row["y"],
            "x_group_id": row["x_group_id"],
            "segment_refit": row.get("segment_refit"),
            "procedure": procedure,
            "oof_prediction_median": prediction,
            "oof_prediction_mean": prediction,
            "oof_prediction_spread": 0.0 if prediction is not None else None,
            "oof_residual_median": residual,
            "oof_residual_mean": residual,
            "oof_residual_spread": 0.0 if residual is not None else None,
            "appearance_count": repetitions,
            "outer_fold_ids": outer_folds,
            "robust_scale": {
                "value": scale,
                "status": "defined" if scale is not None else "undefined",
                "reason": None if scale is not None else scale_reason,
                "method": selected_scale_trace["estimator"],
                "scope": scale_scope,
            },
            "scale_trace_index": scale_trace_index,
            "z_median": z_value,
            "z_spread": 0.0 if z_value is not None else None,
            "score_median_abs_z": score,
            "threshold": threshold,
            "flag_rate": flag_rate,
            "refit_prediction": refit_prediction,
            "refit_residual": row["y"] - refit_prediction if refit_prediction is not None else None,
            "refit_label": "descriptive_final_refit" if refit_prediction is not None else None,
            "influence_group": influence,
            "flags": typed_flags,
            "action": "review_only",
        }
        diagnostic_rows.append(diagnostic_row)
        if typed_flags:
            flagged_rows.append({
                "row_id": row["row_id"],
                "flag_codes": flag_codes,
                "flags": typed_flags,
                "action": "review_only",
                "diagnostic_row_index": row_index,
            })

    recommended_residual_field = f"resid_{procedure_name}_oof" if procedure else None
    repeated_groups: list[dict] = []
    for x_group_id in sorted({row["x_group_id"] for row in rows}, key=lambda group: next(row["x"] for row in rows if row["x_group_id"] == group)):
        group_rows = [row for row in rows if row["x_group_id"] == x_group_id]
        ys = [float(row["y"]) for row in group_rows]
        residuals = [float(row[recommended_residual_field]) for row in group_rows] if recommended_residual_field and all(row.get(recommended_residual_field) is not None for row in group_rows) else []
        z_values = [abs(float(row[f"z_{procedure_name}_oof"])) for row in group_rows if procedure and row.get(f"z_{procedure_name}_oof") is not None]
        y_center = statistics.median(ys)
        repeated_groups.append({
            "x_group_id": x_group_id,
            "x": group_rows[0]["x"],
            "n": len(group_rows),
            "mean_y": sum(ys) / len(ys),
            "median_y": statistics.median(ys),
            "mean_oof_residual": sum(residuals) / len(residuals) if residuals else None,
            "median_oof_residual": statistics.median(residuals) if residuals else None,
            "within_group_mad_y": 1.4826 * statistics.median(abs(value - y_center) for value in ys),
            "positive_residual_share": sum(value > 0 for value in residuals) / len(residuals) if residuals else None,
            "negative_residual_share": sum(value < 0 for value in residuals) / len(residuals) if residuals else None,
            "group_systematic_residual": bool(z_values and statistics.median(z_values) >= threshold),
            "action": "review_only",
        })

    repeated_present = any(item["n"] > 1 for item in repeated_groups)
    refit_prediction_field = "pred_two_refit" if recommendation == "two" else "pred_one_refit"
    if repeated_present and p1_refit_available and all(row.get(refit_prediction_field) is not None for row in rows):
        pure_error = 0.0
        lack_of_fit = 0.0
        total = sum((row["y"] - row[refit_prediction_field]) ** 2 for row in rows)
        for group in repeated_groups:
            group_rows = [row for row in rows if row["x_group_id"] == group["x_group_id"]]
            mean_y = sum(row["y"] for row in group_rows) / len(group_rows)
            pure_error += sum((row["y"] - mean_y) ** 2 for row in group_rows)
            lack_of_fit += len(group_rows) * (mean_y - group_rows[0][refit_prediction_field]) ** 2
        decomposition = {
            "status": "descriptive",
            "reason": None,
            "procedure": recommendation or "one",
            "sse_total": total,
            "sse_pure_error": pure_error,
            "sse_lack_of_fit": lack_of_fit,
            "test_performed": False,
        }
    else:
        decomposition = {
            "status": "unavailable",
            "reason": "NO_REPEATED_X" if not repeated_present else "REFIT_UNAVAILABLE",
            "procedure": recommendation,
            "sse_total": None,
            "sse_pure_error": None,
            "sse_lack_of_fit": None,
            "test_performed": False,
        }

    def pattern(code: str, available: bool, triggered: bool, observed: dict, threshold_text: str) -> dict:
        return {
            "code": code,
            "status": "triggered" if available and triggered else "not_triggered" if available else "unavailable",
            "observed": observed if available else None,
            "threshold": threshold_text,
            "reason": None if available else "OOF_DIAGNOSTICS_UNAVAILABLE",
            "action": "review_only",
        }

    scale_values = [item["robust_scale"]["value"] for item in diagnostic_rows if item["robust_scale"]["value"] is not None]
    global_scale = statistics.median(scale_values) if scale_values else None
    group_residuals = [
        (item["x"], item["mean_oof_residual"])
        for item in repeated_groups if item["mean_oof_residual"] is not None
    ]
    bin_means: list[float] = []
    if validation_available and global_scale is not None and group_residuals:
        for bin_index in range(5):
            start = math.floor(len(group_residuals) * bin_index / 5)
            end = math.floor(len(group_residuals) * (bin_index + 1) / 5)
            values = [value for _, value in group_residuals[start:end]]
            if values:
                bin_means.append(sum(values) / len(values))
    same_sign_run = 0
    longest_run = 0
    previous_sign = 0
    for value in bin_means:
        sign = 1 if value > 0 else -1 if value < 0 else 0
        same_sign_run = same_sign_run + 1 if sign and sign == previous_sign else 1 if sign else 0
        longest_run = max(longest_run, same_sign_run)
        previous_sign = sign
    systematic_trigger = bool(global_scale is not None and longest_run >= 3 and any(abs(value) >= 0.5 * global_scale for value in bin_means))
    scale_ratio = max(scale_values) / min(scale_values) if scale_values and min(scale_values) > 0 else None
    hetero_trigger = bool(scale_ratio is not None and scale_ratio >= 2.0)
    if validation_available and p2_refit_available and procedure == "two" and global_scale is not None:
        window_rows = [row for row in diagnostic_rows if abs(row["x"] - c) <= 0.10 * (x_span or 1.0) and row["oof_residual_median"] is not None]
        window_count = len(window_rows) * repetitions
        window_residuals = [row["oof_residual_median"] for row in window_rows]
        window_mean = sum(window_residuals) / len(window_residuals) if window_residuals else None
        same_sign_share = max(
            sum(value > 0 for value in window_residuals),
            sum(value < 0 for value in window_residuals),
        ) / len(window_residuals) if window_residuals else None
        breakpoint_trigger = bool(window_count >= 5 and same_sign_share is not None and same_sign_share >= 0.8 and window_mean is not None and abs(window_mean) >= 0.5 * global_scale)
        breakpoint_available = True
    else:
        window_count = 0
        window_mean = same_sign_share = None
        breakpoint_trigger = False
        breakpoint_available = validation_available and procedure == "two"
    patterns = [
        pattern("SYSTEMATIC_OOF_RESIDUAL", validation_available and global_scale is not None, systematic_trigger, {"five_bin_means": bin_means, "longest_same_sign_run": longest_run, "global_scale": global_scale}, "3 adjacent same-sign bins and |mean| >= 0.5*scale"),
        pattern("HETEROSCEDASTIC_PATTERN", validation_available and scale_ratio is not None, hetero_trigger, {"max_min_scale_ratio": scale_ratio, "outer_fit_trigger_rate": 1.0 if hetero_trigger else 0.0}, "ratio >= 2.0 in >= 0.80 outer fits"),
        pattern("BREAKPOINT_LOCAL_BIAS", breakpoint_available, breakpoint_trigger, {"appearance_count": window_count, "same_sign_share": same_sign_share, "mean_residual": window_mean, "global_scale": global_scale}, "n >= 5, sign share >= 0.80, |mean| >= 0.5*scale"),
    ]
    influence_grid_values = {
        x_domain[0] + (x_domain[1] - x_domain[0]) * index / 500
        for index in range(501)
    }
    influence_grid_values.update(row["x"] for row in rows)
    if p2_refit_available:
        influence_grid_values.add(c)

    return {
        "policy": {
            "version": "prototype-policy-v1",
            "tau_z": 3.5,
            "extreme_tau_z": 5.25,
            "persistent_flag_rate": 0.5,
            "scale_estimators": ["qn_finite_sample", "finite_sample_s_mad"],
            "local_bin_rule": "B=max(1,min(4,floor(G_train/5)))",
            "local_floor_factor": 0.5,
            "zero_scale_tolerance_rule": "64*eps*max(1,max_abs_y_train)",
            "influence_thresholds": {"dmax": 0.50, "drms": 0.25, "delta_c": 0.05, "segment_share_delta": 0.05, "oof_rmse_relative_delta": 0.10, "rel_mse_uplift_delta": 0.05},
        },
        "oof": {
            "status": "available" if validation_available and recommendation else "unavailable",
            "reason": None if validation_available and recommendation else "OOF_DIAGNOSTICS_UNAVAILABLE",
            "procedure": procedure,
            "residual_definition": "y - same-appearance out-of-fold prediction",
            "prediction_count": len(rows) * repetitions,
            "appearance_export": "diagnostic-appearances.csv",
            "scale_trace_export": "diagnostic-scale-trace.csv",
            "threshold": threshold,
            "scale_method": scale_method,
            "scale_scope": scale_scope,
            "separate_from_refit_influence": True,
            "plot": {
                "path": "figures/residuals.svg",
                "status": "four_separate_panels" if validation_available and recommendation else "typed_unavailable_with_descriptive_refit",
                "panels": ["oof_residual_vs_x", "oof_residual_vs_prediction", "absolute_z_vs_x", "refit_influence_by_x_group"],
            },
        },
        "rows": diagnostic_rows,
        "scale_traces": scale_traces,
        "repeated_x_groups": repeated_groups,
        "pure_error_lack_of_fit": decomposition,
        "patterns": patterns,
        "influence": {
            "status": "available" if influence_available else "unavailable",
            "method": "full leave-one-x-group-out rerun (synthetic prototype sensitivity outcomes)",
            "scope": "sensitivity_only",
            "grid_export": "influence-grid.csv",
            "evaluation_grid_size": len(influence_grid_values),
            "thresholds": {"dmax": 0.50, "drms": 0.25, "delta_c": 0.05, "segment_share_delta": 0.05, "oof_rmse_relative_delta": 0.10, "rel_mse_uplift_delta": 0.05},
            "group_count": len(influence_groups),
            "flagged_group_count": sum(item["high_refit_influence"] for item in influence_groups),
            "reason": None if influence_available else "INFLUENCE_UNASSESSABLE",
        },
        "influence_groups": influence_groups,
        "flagged_rows": flagged_rows,
    }


def build_report(spec: dict) -> dict:
    rows = points_for(spec["profile"])
    y_overrides = spec.get("y_overrides", {})
    for row in rows:
        if row["row_id"] in y_overrides:
            row["y"] = float(y_overrides[row["row_id"]])
    if spec.get("malicious_fields"):
        rows[0]["row_id"] = '<img src=x onerror="alert(1)">'
        rows[0]["source_row_id"] = "=1+1"
    recommendation = spec.get("recommendation")
    p2_failure = spec.get("p2_failure")
    declared_partial_fallback_fit_ids = sorted(set(spec.get("p2_partial_fallback_outer_fit_ids", [])))
    partial_fallback_code = spec.get("p2_partial_fallback_code")
    pipeline_failure = spec.get("pipeline_failure")
    if pipeline_failure and recommendation is not None:
        raise ValueError(f"{spec['id']}: a pipeline failure cannot publish a recommendation")
    if p2_failure and recommendation == "two":
        raise ValueError(f"{spec['id']}: a failed full-data P2 cannot be recommended")
    if p2_failure and declared_partial_fallback_fit_ids:
        raise ValueError(f"{spec['id']}: full and partial P2 fallback modes are mutually exclusive")
    if bool(declared_partial_fallback_fit_ids) != bool(partial_fallback_code):
        raise ValueError(f"{spec['id']}: partial P2 fallback requires fit IDs and one reason code")

    p1_refit_available = not pipeline_failure
    validation_available = not pipeline_failure and spec["profile"] != "small"
    p2_refit_available = validation_available and not p2_failure
    c = 3.5 if spec["profile"] in {"repeated", "two"} else 5.5

    n_used = len(rows)
    n_excluded = 1 if spec.get("invalid_row") else 0
    unique_x = len({row["x"] for row in rows})
    x_values = [row["x"] for row in rows]
    y_values = [row["y"] for row in rows]
    coordinate_counts: dict[tuple[float, float], int] = {}
    for row in rows:
        coordinate = (float(row["x"]), float(row["y"]))
        coordinate_counts[coordinate] = coordinate_counts.get(coordinate, 0) + 1
    exact_duplicate_records = sum(count - 1 for count in coordinate_counts.values() if count > 1)
    x_domain = [min(x_values), max(x_values)]
    y_pad = max(0.5, (max(y_values) - min(y_values)) * 0.12)
    y_domain = [min(y_values) - y_pad, max(y_values) + y_pad]
    repetitions = 10 if validation_available and unique_x >= 10 else 20 if validation_available and unique_x >= 8 else 0
    appearance_count = n_used * repetitions
    all_outer_fit_ids = sorted({
        outer_fit_id(row_index, repetition, unique_x)
        for repetition in range(1, repetitions + 1)
        for row_index in range(n_used)
    })
    if p2_failure and validation_available:
        p2_fallback_outer_fit_ids = all_outer_fit_ids
        p2_fallback_code = p2_failure
    else:
        unknown_fit_ids = sorted(set(declared_partial_fallback_fit_ids) - set(all_outer_fit_ids))
        if unknown_fit_ids:
            raise ValueError(f"{spec['id']}: unknown partial fallback outer fit IDs: {unknown_fit_ids}")
        p2_fallback_outer_fit_ids = declared_partial_fallback_fit_ids
        p2_fallback_code = partial_fallback_code
    p2_fallback_appearance_count = sum(
        outer_fit_id(row_index, repetition, unique_x) in set(p2_fallback_outer_fit_ids)
        for repetition in range(1, repetitions + 1)
        for row_index in range(n_used)
    )

    null_predictions = grouped_null_predictions(rows) if validation_available else [None] * n_used
    target_r2_one = spec.get("r2_one")
    if validation_available and spec.get("p1_oof_predictions") is not None:
        p1_predictions = [float(value) for value in spec["p1_oof_predictions"]]
        if len(p1_predictions) != n_used:
            raise ValueError(f"{spec['id']}: P1 prediction override length mismatch")
        p1_residuals = [row["y"] - prediction for row, prediction in zip(rows, p1_predictions)]
    else:
        p1_predictions, p1_residuals = oof_predictions_for_r2(
            rows,
            null_predictions,
            target_r2_one,
            exact_constant=validation_available and spec["profile"] == "constant",
        ) if validation_available else ([None] * n_used, [None] * n_used)

    if validation_available and p2_failure:
        # Validation compares the deployable P2 procedure: attempt P2, then use
        # the same-scope P1 prediction on every failed appearance.
        p2_predictions = list(p1_predictions)
        p2_residuals = list(p1_residuals)
    elif validation_available and spec.get("p2_oof_predictions") is not None:
        p2_predictions = [float(value) for value in spec["p2_oof_predictions"]]
        if len(p2_predictions) != n_used:
            raise ValueError(f"{spec['id']}: P2 prediction override length mismatch")
        p2_residuals = [row["y"] - prediction for row, prediction in zip(rows, p2_predictions)]
    elif validation_available:
        p2_predictions, p2_residuals = oof_predictions_for_r2(
            rows, null_predictions, spec.get("r2_two")
        )
    else:
        p2_predictions, p2_residuals = [None] * n_used, [None] * n_used

    if p2_fallback_outer_fit_ids:
        for row_index, (p1_prediction, p2_prediction) in enumerate(zip(p1_predictions, p2_predictions)):
            row_has_fallback = any(
                outer_fit_id(row_index, repetition, unique_x) in set(p2_fallback_outer_fit_ids)
                for repetition in range(1, repetitions + 1)
            )
            if row_has_fallback and not math.isclose(float(p1_prediction), float(p2_prediction), rel_tol=0, abs_tol=1e-12):
                raise ValueError(
                    f"{spec['id']}: synthetic row-level partial fallback requires equal P1/P2 prediction on fallback rows"
                )

    for index, row in enumerate(rows):
        row["flags"] = []
        row["pred_null_oof"] = null_predictions[index]
        row["pred_one_oof"] = p1_predictions[index]
        row["resid_one_oof"] = p1_residuals[index]
        row["pred_two_oof"] = p2_predictions[index]
        row["resid_two_oof"] = p2_residuals[index]
        row["p2_oof_fallback_code"] = p2_failure if validation_available and p2_failure else None
        row["pred_one_refit"] = p1_value(row["x"], spec["profile"]) if p1_refit_available else None
        if p2_refit_available:
            row["pred_two_refit"] = p2_value(row["x"], c)
            row["segment_refit"] = "left" if row["x"] <= c else "right"
        else:
            row["pred_two_refit"] = None
            row["segment_refit"] = None

    r2_one = pooled_oof_r2(rows, "pred_one_oof")
    r2_two = pooled_oof_r2(rows, "pred_two_oof")
    if target_r2_one is not None and not math.isclose(r2_one, target_r2_one, rel_tol=0, abs_tol=1e-12):
        raise AssertionError(f"{spec['id']}: P1 R² construction drifted: {r2_one} != {target_r2_one}")
    expected_r2_two = target_r2_one if p2_failure and validation_available else spec.get("r2_two")
    if expected_r2_two is not None and not math.isclose(r2_two, expected_r2_two, rel_tol=0, abs_tol=1e-12):
        raise AssertionError(f"{spec['id']}: P2 R² construction drifted: {r2_two} != {expected_r2_two}")

    mse_one, rmse_one, mae_one = error_stats(p1_residuals)
    mse_two, rmse_two, mae_two = error_stats(p2_residuals)
    delta_mse = mse_one - mse_two if mse_one is not None and mse_two is not None else None
    delta_rmse = rmse_one - rmse_two if rmse_one is not None and rmse_two is not None else None
    delta_mae = mae_one - mae_two if mae_one is not None and mae_two is not None else None
    delta_r2 = r2_two - r2_one if r2_one is not None and r2_two is not None else None
    rel_uplift = delta_mse / mse_one if delta_mse is not None and mse_one and mse_one > 0 else None
    recommended_r2 = r2_two if recommendation == "two" else r2_one if recommendation == "one" else None
    recommended_rmse = rmse_two if recommendation == "two" else rmse_one if recommendation == "one" else None
    recommended_mae = mae_two if recommendation == "two" else mae_one if recommendation == "one" else None

    domain_span = x_domain[1] - x_domain[0]
    y_center = sum(y_values) / len(y_values)
    y_scale = max([abs(value - y_center) for value in y_values] + [1.0])
    p1_family = "constant_v1" if spec["profile"] == "constant" else "poly1_v1"
    p1_a = p1_value(x_domain[0], spec["profile"])
    p1_b = 0.0 if spec["profile"] == "constant" else 0.64 * domain_span
    p1_ast = canonical_registry_ast(p1_family)
    p2_branch_ast = canonical_registry_ast("poly1_v1")
    p1_formula = (
        f"f(x) = {repr(float(p1_a))}"
        if p1_family == "constant_v1"
        else (
            f"f(x) = {repr(float(p1_a))} + {repr(float(p1_b))} * "
            f"((x - {repr(float(x_domain[0]))}) / {repr(float(domain_span))})"
        )
    )
    shared_mu = p2_value(c, c)
    q_left = 0.32 * (c - x_domain[0])
    q_right = 0.98 * (x_domain[1] - c)
    p2_formula_left = (
        f"F(x) = {repr(float(shared_mu))} + {repr(float(q_left))} * "
        f"(((x - {repr(float(x_domain[0]))}) / {repr(float(c - x_domain[0]))}) - 1.0)"
    )
    p2_formula_right = (
        f"F(x) = {repr(float(shared_mu))} + {repr(float(q_right))} * "
        f"((x - {repr(float(c))}) / {repr(float(x_domain[1] - c))})"
    )
    p2_formula = f"{p2_formula_left} (x <= {repr(float(c))}); {p2_formula_right} (x > {repr(float(c))})"
    p1_refit_r2 = r2_from_predictions(rows, "pred_one_refit")
    p2_refit_r2 = r2_from_predictions(rows, "pred_two_refit") if p2_refit_available else None
    p2_left_r2 = r2_from_predictions(rows, "pred_two_refit", "left") if p2_refit_available else None
    p2_right_r2 = r2_from_predictions(rows, "pred_two_refit", "right") if p2_refit_available else None
    p1_refit_residuals = [
        row["y"] - row["pred_one_refit"] if row["pred_one_refit"] is not None else None
        for row in rows
    ]
    _, p1_refit_rmse, p1_refit_mae = error_stats(p1_refit_residuals)
    p2_refit_residuals = [
        row["y"] - row["pred_two_refit"] if row["pred_two_refit"] is not None else None
        for row in rows
    ]
    _, p2_refit_rmse, p2_refit_mae = error_stats(p2_refit_residuals)
    left_rows = [row for row in rows if p2_refit_available and row["x"] <= c]
    right_rows = [row for row in rows if p2_refit_available and row["x"] > c]
    _, p2_left_rmse, p2_left_mae = error_stats([
        row["y"] - row["pred_two_refit"] for row in left_rows
    ]) if left_rows else (None, None, None)
    _, p2_right_rmse, p2_right_mae = error_stats([
        row["y"] - row["pred_two_refit"] for row in right_rows
    ]) if right_rows else (None, None, None)

    def derivative_certificate(
        available: bool,
        family_id: str,
        interval: list[float],
        derivative: float,
        direction: str,
        reason: str | None,
    ) -> dict:
        return {
            "status": "PASS" if available else "UNAVAILABLE",
            "reason": None if available else (reason or "CERTIFICATE_UNAVAILABLE"),
            "method": "analytic_family_specific" if available else None,
            "family_id": family_id if available else None,
            "polynomial_degree_after_simplification": (0 if family_id == "constant_v1" else 1) if available else None,
            "interval": interval if available else None,
            "direction": direction if available else None,
            "analytic_derivative": repr(float(derivative)) if available else None,
            "critical_points": [] if available else None,
            "minimum_signed_derivative": derivative if available else None,
            "domain_margin": 1.0 if available else None,
            "finite_function": True if available else None,
            "finite_derivative": True if available else None,
        }

    p1_transform = ({
        "x": {
            "kind": "affine_unit_interval", "offset": x_domain[0], "scale": domain_span,
            "target_interval": [0.0, 1.0], "invertible": True,
        },
        "y": {
            "kind": "affine_center_scale", "offset": y_center, "scale": y_scale,
            "target_interval": None, "invertible": True,
        },
    } if p1_refit_available else None)
    p2_transform = ({
        "left_x": {
            "kind": "affine_unit_interval", "offset": x_domain[0], "scale": c - x_domain[0],
            "target_interval": [0.0, 1.0], "invertible": True,
        },
        "right_x": {
            "kind": "affine_unit_interval", "offset": c, "scale": x_domain[1] - c,
            "target_interval": [0.0, 1.0], "invertible": True,
        },
        "y": {
            "kind": "affine_center_scale", "offset": y_center, "scale": y_scale,
            "target_interval": None, "invertible": True,
        },
    } if p2_refit_available else None)

    metric_unavailable_reason = pipeline_failure or ("VALIDATION_UNAVAILABLE" if not validation_available else None)
    r2_one_reason = metric_unavailable_reason or ("UNDEFINED_R2_CONSTANT_Y" if r2_one is None else None)
    r2_two_reason = metric_unavailable_reason or ("UNDEFINED_R2_CONSTANT_Y" if r2_two is None else None)
    uplift_reason = metric_unavailable_reason or ("ZERO_P1_MSE" if mse_one == 0 else "UPLIFT_UNAVAILABLE")

    warnings: list[dict] = []
    if recommendation and recommended_r2 is not None and recommended_r2 < 0.60:
        warnings.append(
            {
                "code": "BELOW_PRODUCT_R2",
                "scope": "recommended.global_primary_r2_oos",
                "stage": "decision",
                "severity": "warning",
                "reason": "Unrounded pooled OOF R² is below 0.60",
                "recommendation_effect": "none",
                "related_json_pointers": ["/recommendation/global_primary_r2_oos"],
            }
        )
    if spec.get("invalid_row"):
        warnings.append(
            {
                "code": "INVALID_ROWS_SKIPPED",
                "scope": "input",
                "stage": "parse",
                "severity": "warning",
                "reason": "One synthetic invalid row was skipped",
                "recommendation_effect": "none",
                "related_json_pointers": ["/input/n_excluded"],
            }
        )
    if spec.get("extra_warning_reason"):
        warnings.append(
            {
                "code": "SYNTHETIC_HOSTILE_REASON",
                "scope": "prototype.security_fixture",
                "stage": "render",
                "severity": "warning",
                "reason": spec["extra_warning_reason"],
                "recommendation_effect": "none",
                "related_json_pointers": ["/warnings"],
            }
        )
    if r2_one is None and recommendation:
        warnings.append(
            {
                "code": "UNDEFINED_R2",
                "scope": "recommended.global_primary_r2_oos",
                "stage": "metric",
                "severity": "warning",
                "reason": "Cross-fitted null denominator is zero",
                "recommendation_effect": "none",
                "related_json_pointers": ["/recommendation/global_primary_r2_oos"],
            }
        )

    failures: list[dict] = []
    if p2_failure:
        failures.append(
            {
                "code": p2_failure,
                "scope": "refit.p2",
                "stage": "full_data_refit",
                "severity": "candidate_failure",
                "reason": "Synthetic P2 state for report-layout validation",
                "recommendation_effect": "p1_only",
                "related_json_pointers": ["/refit/p2/status", "/validation/procedures/p2/fallback_rate"],
            }
        )
    if pipeline_failure:
        failures.append(
            {
                "code": pipeline_failure,
                "scope": "preprocessing.x_scale" if pipeline_failure == "NONINVERTIBLE_X_SCALE" else "pipeline",
                "stage": "numerical_admission" if pipeline_failure == "NONINVERTIBLE_X_SCALE" else "fit",
                "severity": "error",
                "reason": "Synthetic hard failure; no deployable model",
                "recommendation_effect": "no_recommendation",
                "related_json_pointers": ["/status/analysis_status"],
            }
        )

    if recommendation == "two":
        recommendation_status = "TWO_RECOMMENDED"
    elif recommendation == "one":
        recommendation_status = "ONE_RECOMMENDED"
    else:
        recommendation_status = "NO_VALIDATED_RECOMMENDATION"
    analysis_status = "FAILED" if pipeline_failure else (
        "NO_RECOMMENDATION" if recommendation is None else "SUCCEEDED"
    )

    one_metric = metric(r2_one, "oof", r2_one_reason)
    two_metric = metric(r2_two, "oof", r2_two_reason)
    recommended_metric = metric(
        recommended_r2,
        "oof",
        "NO_VALIDATED_RECOMMENDATION" if not recommendation else "UNDEFINED_R2_CONSTANT_Y",
    )

    p2_validation_status = (
        "failed" if pipeline_failure
        else "unavailable" if not validation_available
        else "fallback" if p2_fallback_appearance_count > 0
        else "valid"
    )
    p1_validation_status = "failed" if pipeline_failure else "unavailable" if not validation_available else "valid"
    p2_fallback_rate = (
        p2_fallback_appearance_count / appearance_count
        if validation_available and appearance_count > 0
        else None
    )
    positive_share = (
        1.0 if rel_uplift is not None and rel_uplift > 0
        else 0.0 if rel_uplift is not None
        else None
    )
    split_p10 = rel_uplift if validation_available and rel_uplift is not None else None
    mae_ratio = (
        mae_two / mae_one
        if mae_one is not None and mae_one > 0 and mae_two is not None
        else None
    )
    fallback_fit_id_set = set(p2_fallback_outer_fit_ids)
    p2_outer_fit_ledger: list[dict] = []
    for ledger_index, fit_id in enumerate(all_outer_fit_ids):
        match = re.fullmatch(r"outer-([0-9]{2})-fold-([1-9][0-9]*)", fit_id)
        if match is None:
            raise AssertionError(f"unexpected outer fit ID: {fit_id}")
        is_fallback = fit_id in fallback_fit_id_set
        valid = p2_refit_available and not is_fallback
        boundary_value = (
            c + domain_span * 0.02 * ((ledger_index % 5) - 2) / 2
            if valid else None
        )
        p2_outer_fit_ledger.append({
            "outer_fit_id": fit_id,
            "repetition": int(match.group(1)),
            "outer_fold": int(match.group(2)),
            "status": "valid" if valid else "fallback",
            "fallback_reason": (p2_fallback_code or p2_failure) if is_fallback else None,
            "direction": "nondecreasing" if valid else None,
            "family_pair": "poly1_v1|poly1_v1" if valid else None,
            "boundary": boundary_value,
            "edge_hit": False if valid else None,
            "flat_profile": False if valid else None,
            "multiple_near_optima": False if valid else None,
            "collapsed": True if p2_failure == "COLLAPSED_SEGMENTS" and is_fallback else False if valid else None,
            "hard_failure": bool(is_fallback and (p2_fallback_code == "OPTIMIZER_FAILURE" or p2_failure == "OPTIMIZER_FAILURE")),
        })
    valid_outer_fits = [item for item in p2_outer_fit_ledger if item["status"] == "valid"]
    valid_fit_rate = len(valid_outer_fits) / len(p2_outer_fit_ledger) if p2_outer_fit_ledger else None
    p2_outer_fallback_rate = (
        sum(item["status"] == "fallback" for item in p2_outer_fit_ledger) / len(p2_outer_fit_ledger)
        if p2_outer_fit_ledger else None
    )
    dominant_pair_frequency = (
        max(
            sum(item["family_pair"] == family for item in valid_outer_fits)
            for family in {item["family_pair"] for item in valid_outer_fits}
        ) / len(valid_outer_fits)
        if valid_outer_fits else None
    )
    direction_frequency = (
        max(
            sum(item["direction"] == direction for item in valid_outer_fits)
            for direction in {item["direction"] for item in valid_outer_fits}
        ) / len(valid_outer_fits)
        if valid_outer_fits else None
    )
    ledger_breakpoints = [float(item["boundary"]) for item in valid_outer_fits]
    breakpoint_central80 = (
        [linear_percentile(ledger_breakpoints, 0.10), linear_percentile(ledger_breakpoints, 0.90)]
        if ledger_breakpoints else None
    )
    boundary_width_fraction = (
        (breakpoint_central80[1] - breakpoint_central80[0]) / domain_span
        if breakpoint_central80 else None
    )
    edge_hit_rate = (
        sum(bool(item["edge_hit"]) for item in valid_outer_fits) / len(valid_outer_fits)
        if valid_outer_fits else None
    )
    flat_profile_rate = (
        sum(bool(item["flat_profile"]) for item in valid_outer_fits) / len(p2_outer_fit_ledger)
        if valid_outer_fits else None
    )
    multiple_near_optima_rate = (
        sum(bool(item["multiple_near_optima"]) for item in valid_outer_fits) / len(p2_outer_fit_ledger)
        if valid_outer_fits else None
    )
    collapse_count = sum(item.get("collapsed") is True for item in p2_outer_fit_ledger)
    collapse_rate = (
        collapse_count / len(p2_outer_fit_ledger)
        if p2_outer_fit_ledger and (valid_outer_fits or collapse_count) else None
    )
    degeneracy_rate = (
        sum(
            item.get("flat_profile") is True
            or item.get("multiple_near_optima") is True
            or item.get("collapsed") is True
            for item in p2_outer_fit_ledger
        ) / len(p2_outer_fit_ledger)
        if p2_outer_fit_ledger and (valid_outer_fits or collapse_count) else None
    )
    hard_failure_rate = (
        sum(bool(item["hard_failure"]) for item in p2_outer_fit_ledger) / len(p2_outer_fit_ledger)
        if p2_outer_fit_ledger else None
    )
    full_fallback_rel_uplift = (
        rel_uplift
        if validation_available and p2_fallback_rate == 1.0 and rel_uplift is not None
        else None
    )
    bootstrap_values = synthetic_bootstrap_values(
        recommendation,
        p2_refit_available,
        full_fallback_rel_uplift,
    )
    bootstrap_interval = (
        [linear_percentile(bootstrap_values, 0.05), linear_percentile(bootstrap_values, 0.95)]
        if bootstrap_values else None
    )
    split_id_hashes = [
        {
            "repetition": repetition,
            "sha256": sha256_text(json.dumps(
                sorted({
                    outer_fit_id(row_index, repetition, unique_x)
                    for row_index in range(n_used)
                }),
                separators=(",", ":"),
            )),
        }
        for repetition in range(1, repetitions + 1)
    ]
    repetition_uplift_distribution = [
        {
            "repetition": repetition,
            "rel_mse_uplift": rel_uplift if split_p10 is not None else None,
            "status": "defined" if split_p10 is not None else "unavailable",
            "reason": None if split_p10 is not None else (uplift_reason or "SPLIT_SENSITIVITY_UNAVAILABLE"),
        }
        for repetition in range(1, repetitions + 1)
    ]

    def gate(
        gate_id: str,
        threshold: float | str,
        observed: float | str | None,
        available: bool,
        passed: bool,
        unavailable_reason: str | None = None,
    ) -> dict:
        return {
            "id": gate_id,
            "policy_version": "prototype-policy-v1",
            "threshold": threshold,
            "observed": observed,
            "status": "pass" if available and passed else "fail" if available else "unavailable",
            "reason": "THRESHOLD_MET" if available and passed else "THRESHOLD_NOT_MET" if available else (unavailable_reason or p2_failure or metric_unavailable_reason or "UNAVAILABLE"),
        }

    decision_gates = [
        gate("PRACTICAL_REL_MSE_UPLIFT", 0.10, rel_uplift, rel_uplift is not None, bool(rel_uplift is not None and rel_uplift >= 0.10), uplift_reason),
        gate("POSITIVE_REPETITION_SHARE", 0.90, positive_share, positive_share is not None, bool(positive_share is not None and positive_share >= 0.90), uplift_reason),
        gate("SPLIT_SENSITIVITY_P10", 0.0, split_p10, split_p10 is not None, bool(split_p10 is not None and split_p10 >= 0.0), uplift_reason),
        gate("BOOTSTRAP_STABILITY_LOWER", 0.05, bootstrap_interval[0] if bootstrap_interval else None, bootstrap_interval is not None, bool(bootstrap_interval and bootstrap_interval[0] >= 0.05), uplift_reason if rel_uplift is None else "UNCERTAINTY_UNAVAILABLE"),
        gate("DELTA_RMSE_POSITIVE", 0.0, delta_rmse, delta_rmse is not None, bool(delta_rmse is not None and delta_rmse > 0.0), uplift_reason),
        gate("MAE_NO_HARM_RATIO", 1.02, mae_ratio, mae_ratio is not None, bool(mae_ratio is not None and mae_ratio <= 1.02), uplift_reason),
        gate("P2_VALID_FIT_RATE", 0.90, valid_fit_rate, valid_fit_rate is not None, bool(valid_fit_rate is not None and valid_fit_rate >= 0.90)),
        gate("P2_DIRECTION_FREQUENCY", 0.80, direction_frequency, direction_frequency is not None, bool(direction_frequency is not None and direction_frequency >= 0.80)),
        gate("P2_DOMINANT_PAIR_FREQUENCY", 0.60, dominant_pair_frequency, dominant_pair_frequency is not None, bool(dominant_pair_frequency is not None and dominant_pair_frequency >= 0.60)),
        gate("P2_BOUNDARY_WIDTH_FRACTION", 0.25, boundary_width_fraction, boundary_width_fraction is not None, bool(boundary_width_fraction is not None and boundary_width_fraction <= 0.25)),
        gate("P2_EDGE_HIT_RATE", 0.20, edge_hit_rate, edge_hit_rate is not None, bool(edge_hit_rate is not None and edge_hit_rate <= 0.20)),
        gate("P2_DEGENERACY_RATE", 0.10, degeneracy_rate, degeneracy_rate is not None, bool(degeneracy_rate is not None and degeneracy_rate <= 0.10)),
        gate("P2_HARD_FAILURE_RATE", 0.10, hard_failure_rate, hard_failure_rate is not None, bool(hard_failure_rate is not None and hard_failure_rate <= 0.10)),
        gate(
            "P2_FULL_REFIT_CERTIFIED",
            "certified",
            "certified" if p2_refit_available else "unavailable" if validation_available else None,
            validation_available,
            p2_refit_available,
        ),
    ]

    diagnostics = build_diagnostics(
        rows,
        spec,
        recommendation,
        validation_available,
        p1_refit_available,
        p2_refit_available,
        repetitions,
        c,
        x_domain,
    )
    optimizer = optimizer_summary(
        rows,
        validation_available=validation_available,
        p2_refit_available=p2_refit_available,
        p2_failure=p2_failure,
        pipeline_failure=pipeline_failure,
    )
    chosen_membership_cell = next(
        (
            cell for cell in tie_safe_cells(rows)
            if cell["lower_x"] <= c < cell["upper_x_exclusive"]
        ),
        None,
    )

    y_unit = spec.get("y_unit", "mV")
    y2_unit = f"({y_unit})^2"
    invalid_reason_counts = ({"INVALID_NUMBER": n_excluded} if n_excluded else {})
    related_reason_codes = sorted({
        spec["decision_reason"],
        *(item["code"] for item in warnings),
        *(item["code"] for item in failures),
        *([p2_fallback_code] if p2_fallback_code else []),
        *([metric_unavailable_reason] if metric_unavailable_reason else []),
    })
    preprocessing_transforms = {
        "status": "unavailable" if pipeline_failure == "NONINVERTIBLE_X_SCALE" else "available",
        "reason": "NONINVERTIBLE_X_SCALE" if pipeline_failure == "NONINVERTIBLE_X_SCALE" else None,
        "x": {
            "kind": "affine_unit_interval",
            "offset": x_domain[0],
            "scale": 0.0 if pipeline_failure == "NONINVERTIBLE_X_SCALE" else domain_span,
            "target_interval": [0.0, 1.0],
            "invertible": pipeline_failure != "NONINVERTIBLE_X_SCALE",
        },
        "y": {
            "kind": "affine_center_scale",
            "offset": y_center,
            "scale": y_scale,
            "target_interval": None,
            "invertible": True,
        },
    }
    request_metadata_payload = {
        "request_schema_version": "prototype-synthetic-request-v1",
        "logical_adapter_version": "prototype-synthetic-input-v1",
        "result_bearing_scenario": {
            key: value
            for key, value in sorted(spec.items())
            if key not in {"id", "title", "covers"}
        },
    }
    input_hash_payload = {
        "logical_adapter_version": "prototype-synthetic-input-v1",
        "x_unit": spec.get("x_unit", "s"),
        "y_unit": y_unit,
        "rows": [
            {
                "row_id": row["row_id"],
                "source_row_id": row["source_row_id"],
                "x": row["x"],
                "y": row["y"],
            }
            for row in rows
        ],
        "excluded": ([{
            "source_row_id": spec.get("invalid_source_row_id", "14"),
            "code": "INVALID_NUMBER",
            "raw_value": spec.get("invalid_raw", "NaN"),
        }] if n_excluded else []),
    }
    request_hash = canonical_sha256(request_metadata_payload)
    input_hash = canonical_sha256(input_hash_payload)
    policy_payload = {
        "policy_version": "prototype-policy-v1",
        "r2_warning_boundary": 0.60,
        "segment_share_bounds": [0.4, 0.6],
        "decision_gates": [
            {"id": item["id"], "threshold": item["threshold"]}
            for item in decision_gates
        ],
    }
    registry_payload = {
        "registry_version": "registry_v1",
        "artifact_scope": "prototype_exercised_subset",
        "authoritative_core_family_ids": [
            "constant_v1", "poly1_v1", "poly2_v1", "poly3_v1",
            "exp_affine_v1", "log_shift_v1", "reciprocal_shift_pos_v1",
            "logistic_v1",
        ],
        "included_family_ids": ["constant_v1", "poly1_v1"],
        "unexercised_family_ids": [
            "poly2_v1", "poly3_v1", "exp_affine_v1", "log_shift_v1",
            "reciprocal_shift_pos_v1", "logistic_v1",
        ],
        "maximum_polynomial_degree": 3,
        "families": {
            "constant_v1": {"canonical_ast": canonical_registry_ast("constant_v1"), "degree": 0, "role": "core"},
            "poly1_v1": {"canonical_ast": canonical_registry_ast("poly1_v1"), "degree": 1, "role": "core"},
        },
        "incubator_excluded": True,
    }
    source_payload = {
        "manifest_version": "prototype-source-manifest-v1",
        "files": [
            {"path": str(path.relative_to(HERE.parents[2])), "sha256": sha256(path)}
            for path in sorted((HERE / "build.py", HERE / "audit_prototype.py", MATRIX, CANONICAL_SCHEMA))
        ],
    }
    dependency_payload = {
        "manifest_version": "prototype-stdlib-dependency-manifest-v1",
        "python_implementation": "CPython",
        "python_version": sys.version.split()[0],
        "third_party_runtime_dependencies": [],
    }
    environment_payload = {
        "status": "prototype_static_not_runtime_benchmark",
        "python_implementation": "CPython",
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "thread_policy": "single_process_single_thread",
    }
    policy_hash = canonical_sha256(policy_payload)
    registry_hash = canonical_sha256(registry_payload)
    source_hash = canonical_sha256(source_payload)
    dependency_hash = canonical_sha256(dependency_payload)
    environment_hash = canonical_sha256(environment_payload)
    analysis_hash = canonical_sha256({
        "input_sha256": input_hash,
        "request_sha256": request_hash,
        "policy_sha256": policy_hash,
        "registry_sha256": registry_hash,
        "source_sha256": source_hash,
        "dependency_lock_sha256": dependency_hash,
        "base_seed": 20260716,
    })
    p1_structure_payload = ({
        "model_schema_version": "prototype-final-refit-dossier-1.0.0",
        "registry_version": "registry_v1",
        "canonical_ast": p1_ast,
        "family_id": p1_family,
        "direction": "flat" if spec["profile"] == "constant" else "nondecreasing",
        "segment_count": 1,
        "domain": x_domain,
    } if p1_refit_available else None)
    p1_instance_payload = ({
        "structure": p1_structure_payload,
        "parameters": {"a": p1_a, "b": p1_b},
        "transforms": p1_transform,
    } if p1_refit_available else None)
    p2_structure_payload = ({
        "model_schema_version": "prototype-final-refit-dossier-1.0.0",
        "registry_version": "registry_v1",
        "ordered_family_pair": "poly1_v1|poly1_v1",
        "canonical_ast_left": p2_branch_ast,
        "canonical_ast_right": p2_branch_ast,
        "direction": "nondecreasing",
        "segment_count": 2,
        "domain": x_domain,
        "membership_cell": chosen_membership_cell,
        "membership": "x<=c:left;x>c:right",
    } if p2_refit_available else None)
    p2_instance_payload = ({
        "structure": p2_structure_payload,
        "parameters": {"c": c, "mu": shared_mu, "q_left": q_left, "q_right": q_right},
        "transforms": p2_transform,
    } if p2_refit_available else None)

    report = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "curve_calibration_regression",
        "prototype_only": True,
        "prototype_notice": "PROTOTYPE — synthetic fixture — not a performed calibration",
        "report_id": f"prototype-{spec['id']}",
        "generated_at": "2026-07-16T12:00:00+03:00",
        "status": {
            "bundle_status": "PROTOTYPE_NOT_PUBLISHED",
            "analysis_status": analysis_status,
            "recommendation_status": recommendation_status,
            "decision_reason": spec["decision_reason"],
        },
        "decision": {
            "code": spec["decision_reason"],
            "reason": spec["title"],
            "recommended_procedure": recommendation,
            "related_reason_codes": related_reason_codes,
        },
        "recommendation": {
            "role": recommendation,
            "model_preview_path": f"models/{recommendation}-model-preview.json" if recommendation else None,
            "formula_display": p2_formula if recommendation == "two" else p1_formula if recommendation == "one" else None,
            "domain": x_domain if recommendation else None,
            "global_primary_r2_oos": recommended_metric,
            "global_primary_rmse_oof": metric(recommended_rmse, "oof", "NO_VALIDATED_RECOMMENDATION", y_unit, "pooled_oof_rmse"),
            "global_primary_mae_oof": metric(recommended_mae, "oof", "NO_VALIDATED_RECOMMENDATION", y_unit, "pooled_oof_mae"),
            "extrapolation": "forbidden" if recommendation else None,
        },
        "input": {
            "filename": spec.get("input_filename", "synthetic-demo.csv"),
            "media_type": "text/csv",
            "n_input": n_used + n_excluded,
            "n_used": n_used,
            "n_excluded": n_excluded,
            "n_unique_x": unique_x,
            "repeated_x_groups": sum(
                1
                for x_value in set(x_values)
                if sum(row["x"] == x_value for row in rows) > 1
            ),
            "exact_duplicate_records": exact_duplicate_records,
            "invalid_reason_counts": invalid_reason_counts,
            "x_unit": spec.get("x_unit", "s"),
            "y_unit": y_unit,
            "domain": x_domain,
            "input_sha256": input_hash,
            "exclusions": ([{
                "source_row_id": spec.get("invalid_source_row_id", "14"),
                "code": "INVALID_NUMBER",
                "raw_value": spec.get("invalid_raw", "NaN"),
            }] if n_excluded else []),
        },
        "preprocessing": {
            "sorting": "stable_by_x_then_row_id",
            "row_id_policy": "preserve_stable_logical_row_id",
            "weights": "equal",
            "invalid_rows": "skipped_with_audit",
            "automatic_outlier_removal": False,
            "ties_atomic_for_validation_and_segmentation": True,
            "affine_transforms": preprocessing_transforms,
        },
        "validation": {
            "estimand": "new_x_group_within_observed_domain",
            "null_baseline": "same-appearance outer-train mean; synthetic prototype uses leave-one-x-group-out means",
            "outer_inner_folds": "5x4" if unique_x >= 10 else "4x3" if unique_x >= 8 else None,
            "outer_fold_policy": "grouped_by_x_group_id; same appearances for P1/P2/null",
            "inner_fold_policy": "grouped_by_x_group_id inside each outer training scope",
            "group_key": "x_group_id",
            "split_id_hashes": split_id_hashes,
            "repetitions": repetitions,
            "policy_sha256": policy_hash,
            "appearance_export": "oof-appearances.csv",
            "pooled_null_sse": metric(
                sum((row["y"] - row["pred_null_oof"]) ** 2 for row in rows) * repetitions if validation_available else None,
                "oof",
                metric_unavailable_reason,
                y2_unit,
                "sum_squared_error_of_same_appearance_null_predictions",
            ),
            "procedures": {
                "p1": {
                    "status": p1_validation_status,
                    "reason": metric_unavailable_reason,
                    "r2_oos": one_metric,
                    "mse_oof": metric(mse_one, "oof", metric_unavailable_reason, y2_unit, "mean_squared_oof_error"),
                    "rmse_oof": metric(rmse_one, "oof", metric_unavailable_reason, y_unit, "root_mean_squared_oof_error"),
                    "mae_oof": metric(mae_one, "oof", metric_unavailable_reason, y_unit, "mean_absolute_oof_error"),
                    "oof_appearances": appearance_count,
                    "successful_appearances": appearance_count if validation_available else 0,
                    "direction_frequency": metric(1.0 if validation_available else None, "oof", metric_unavailable_reason, "fraction", "dominant_direction_frequency_across_outer_fits"),
                    "family_frequency": metric(1.0 if validation_available else None, "oof", metric_unavailable_reason, "fraction", "dominant_canonical_family_frequency_across_outer_fits"),
                    "fallback_rate": metric(0.0 if validation_available else None, "oof", metric_unavailable_reason, "fraction", "fallback_appearances_over_all_appearances"),
                },
                "p2": {
                    "status": p2_validation_status,
                    "attempt_status": (
                        "failed_with_p1_fallback" if p2_failure and validation_available
                        else "partial_p1_fallback" if p2_fallback_appearance_count > 0
                        else "succeeded" if p2_refit_available
                        else "unavailable"
                    ),
                    "reason": p2_failure or p2_fallback_code or metric_unavailable_reason,
                    "full_data_refit_status": "certified" if p2_refit_available else "unavailable",
                    "r2_oos": two_metric,
                    "mse_oof": metric(mse_two, "oof", metric_unavailable_reason, y2_unit, "mean_squared_oof_error_including_p1_fallback"),
                    "rmse_oof": metric(rmse_two, "oof", metric_unavailable_reason, y_unit, "root_mean_squared_oof_error_including_p1_fallback"),
                    "mae_oof": metric(mae_two, "oof", metric_unavailable_reason, y_unit, "mean_absolute_oof_error_including_p1_fallback"),
                    "oof_appearances": appearance_count,
                    "successful_appearances": appearance_count if validation_available else 0,
                    "fallback_appearances": p2_fallback_appearance_count,
                    "fallback_outer_fit_ids": p2_fallback_outer_fit_ids,
                    "fallback_reason_counts": (
                        {p2_fallback_code: p2_fallback_appearance_count}
                        if p2_fallback_code and p2_fallback_appearance_count > 0
                        else {}
                    ),
                    "direction_frequency": metric(direction_frequency, "oof", p2_failure or metric_unavailable_reason, "fraction", "dominant_direction_frequency_across_nonfallback_outer_fits"),
                    "family_frequency": metric(dominant_pair_frequency, "oof", p2_failure or metric_unavailable_reason, "fraction", "dominant_canonical_family_pair_frequency_across_nonfallback_outer_fits"),
                    "fallback_rate": metric(p2_fallback_rate, "oof", metric_unavailable_reason, "fraction", "p1_fallback_appearances_over_all_p2_procedure_appearances"),
                },
            },
            "uplift": {
                "delta_mse": metric(delta_mse, "oof", uplift_reason, y2_unit, "p1_mse_minus_p2_mse"),
                "delta_rmse": metric(delta_rmse, "oof", uplift_reason, y_unit, "p1_rmse_minus_p2_rmse"),
                "delta_mae": metric(delta_mae, "oof", uplift_reason, y_unit, "p1_mae_minus_p2_mae"),
                "delta_r2": metric(delta_r2, "oof", uplift_reason, "1", "p2_r2_minus_p1_r2"),
                "rel_mse_uplift": metric(rel_uplift, "oof", uplift_reason, "fraction", "p1_mse_minus_p2_mse_over_p1_mse"),
                "decision": spec["decision_reason"],
                "positive_repetition_share": metric(positive_share, "oof", uplift_reason, "fraction", "repetitions_with_positive_paired_uplift"),
            },
            "decision_gates": decision_gates,
            "split_sensitivity": {
                "status": "available" if split_p10 is not None else "unavailable",
                "label": "split sensitivity; not a confidence interval",
                "p10": metric(split_p10, "oof", uplift_reason, "fraction", "p10_of_repetition_level_relative_mse_uplift"),
                "repetition_distribution": repetition_uplift_distribution,
            },
            "bootstrap_stability": {
                "status": "available" if bootstrap_interval else "unavailable",
                "label": "central 90% full-pipeline bootstrap stability interval",
                "target": "full_pipeline_rel_mse_uplift",
                "level": 0.90,
                "unit": "fraction",
                "interval": bootstrap_interval,
                "resampling_unit": "x_group",
                "selection_scope": "full_pipeline",
                "percentile_method": "linear_interpolation_at_5_and_95_percent",
                "resample_export": "bootstrap-resamples.csv",
                "successful_resamples": len(bootstrap_values),
                "requested_resamples": 200,
                "failed_resamples": 200 - len(bootstrap_values),
                "failure_reasons": (
                    {"PIPELINE_FAILURE": 200 - len(bootstrap_values)}
                    if 0 < len(bootstrap_values) < 200
                    else {} if len(bootstrap_values) == 200
                    else {"UNCERTAINTY_UNAVAILABLE": 200}
                ),
                "reason": None if bootstrap_interval else (uplift_reason or p2_failure or metric_unavailable_reason or "UNCERTAINTY_UNAVAILABLE"),
            },
            "p2_stability": {
                "status": "available" if validation_available else "unavailable",
                "valid_fit_rate": metric(valid_fit_rate, "oof", p2_failure or metric_unavailable_reason, "fraction", "nonfallback_valid_p2_fit_rate"),
                "dominant_family_pair_frequency": metric(dominant_pair_frequency, "oof", p2_failure or metric_unavailable_reason, "fraction", "dominant_family_pair_frequency_among_nonfallback_fits"),
                "dominant_family_pair": "poly1_v1|poly1_v1" if p2_refit_available else None,
                "direction_frequency": metric(direction_frequency, "oof", p2_failure or metric_unavailable_reason, "fraction", "dominant_direction_frequency_among_nonflat_nonfallback_fits"),
                "outer_fit_ledger": p2_outer_fit_ledger,
                "outer_fit_export": "p2-stability-outer-fits.csv",
                "outer_fit_denominator": len(p2_outer_fit_ledger),
                "valid_fit_count": len(valid_outer_fits),
                "fallback_fit_count": sum(item["status"] == "fallback" for item in p2_outer_fit_ledger),
                "breakpoint_distribution": ledger_breakpoints,
                "breakpoint_central80_interval": breakpoint_central80,
                "boundary_width_fraction": metric(boundary_width_fraction, "oof", p2_failure or metric_unavailable_reason, "fraction_of_observed_x_domain", "central_boundary_interval_width_over_domain"),
                "edge_hit_rate": metric(edge_hit_rate, "oof", p2_failure or metric_unavailable_reason, "fraction", "outer_p2_boundaries_at_40_or_60_percent_edges"),
                "fallback_rate": metric(p2_outer_fallback_rate, "oof", metric_unavailable_reason, "fraction", "p1_fallback_outer_fits_over_all_p2_outer_fits"),
                "flat_profile_rate": metric(flat_profile_rate, "oof", p2_failure or metric_unavailable_reason, "fraction", "FLAT_PROFILE_frequency"),
                "multiple_near_optima_rate": metric(multiple_near_optima_rate, "oof", p2_failure or metric_unavailable_reason, "fraction", "MULTIPLE_NEAR_OPTIMA_frequency"),
                "collapse_rate": metric(collapse_rate, "oof", p2_failure or metric_unavailable_reason, "fraction", "segment_collapse_frequency"),
                "degeneracy_rate": metric(degeneracy_rate, "oof", p2_failure or metric_unavailable_reason, "fraction", "flat_profile_near_optima_or_segment_collapse_rate"),
                "hard_failure_rate": metric(hard_failure_rate, "oof", p2_failure or metric_unavailable_reason, "fraction", "hard_fit_or_certificate_failure_rate"),
                "sensitivity_estimands": [
                    "full_pipeline_group_bootstrap_rel_mse_uplift",
                    "leave_one_x_group_out_refit_prediction_change",
                ],
                "policy_sha256": policy_hash,
                "reason": None if p2_refit_available else (p2_failure or metric_unavailable_reason),
            },
        },
        "refit": {
            "p1": {
                "status": "certified" if p1_refit_available else "failed",
                "reason": None if p1_refit_available else pipeline_failure,
                "registry_version": "registry_v1",
                "registry_role": "core",
                "incubator_excluded": True,
                "family_id": p1_family if p1_refit_available else None,
                "canonical_ast_id": p1_ast["ast_id"] if p1_refit_available else None,
                "canonical_ast": p1_ast if p1_refit_available else None,
                "formula_display": p1_formula if p1_refit_available else None,
                "transforms": p1_transform,
                "parameters": ({"a": p1_a, "b": p1_b} if p1_refit_available else None),
                "domain": x_domain if p1_refit_available else None,
                "direction": "flat" if p1_refit_available and spec["profile"] == "constant" else "nondecreasing" if p1_refit_available else None,
                "certificate": "PASS" if p1_refit_available else pipeline_failure,
                "derivative_certificate": derivative_certificate(
                    p1_refit_available,
                    p1_family,
                    x_domain,
                    0.0 if p1_family == "constant_v1" else 0.64,
                    "flat" if p1_family == "constant_v1" else "nondecreasing",
                    pipeline_failure,
                ),
                "identifiability_status": (
                    "CANONICAL_IDENTIFIED" if p1_refit_available and p1_family == "constant_v1"
                    else "IDENTIFIED" if p1_refit_available else "UNAVAILABLE"
                ),
                "bound_status": "NO_ACTIVE_BOUNDS" if p1_refit_available else "UNAVAILABLE",
                "collapse_status": "CANONICAL_CONSTANT" if p1_refit_available and p1_family == "constant_v1" else "NO_COLLAPSE" if p1_refit_available else "UNAVAILABLE",
                "solver_status": "SUCCEEDED" if p1_refit_available else "FAILED" if pipeline_failure == "OPTIMIZER_FAILURE" else "NOT_RUN",
                "model_structure_hash": canonical_sha256(p1_structure_payload) if p1_structure_payload else None,
                "model_instance_hash": canonical_sha256(p1_instance_payload) if p1_instance_payload else None,
                "model_path": "models/one-model.json" if p1_refit_available else None,
                "r2_fit_all": metric(p1_refit_r2, "in_sample", pipeline_failure or "UNDEFINED_R2_CONSTANT_Y"),
                "rmse_fit_all": metric(p1_refit_rmse, "in_sample", pipeline_failure, y_unit, "root_mean_squared_full_data_refit_error"),
                "mae_fit_all": metric(p1_refit_mae, "in_sample", pipeline_failure, y_unit, "mean_absolute_full_data_refit_error"),
                "n": n_used if p1_refit_available else None,
                "n_unique_x": unique_x if p1_refit_available else None,
                "plot": {
                    "path": "figures/one-function.svg",
                    "status": "line_and_points" if p1_refit_available else "typed_failure_with_points",
                    "line_present": p1_refit_available,
                },
            },
            "p2": {
                "status": "certified" if p2_refit_available else "unavailable",
                "reason": None if p2_refit_available else (p2_failure or pipeline_failure or "VALIDATION_UNAVAILABLE"),
                "registry_version": "registry_v1",
                "registry_role": "core",
                "incubator_excluded": True,
                "family_left": "poly1_v1" if p2_refit_available else None,
                "family_right": "poly1_v1" if p2_refit_available else None,
                "ordered_family_pair": "poly1_v1|poly1_v1" if p2_refit_available else None,
                "canonical_ast_left": p2_branch_ast if p2_refit_available else None,
                "canonical_ast_right": p2_branch_ast if p2_refit_available else None,
                "formula_left": p2_formula_left if p2_refit_available else None,
                "formula_right": p2_formula_right if p2_refit_available else None,
                "transforms": p2_transform,
                "parameters": ({
                    "c": c, "mu": shared_mu, "q_left": q_left, "q_right": q_right,
                } if p2_refit_available else None),
                "boundary": c if p2_refit_available else None,
                "shared_mu": p2_value(c, c) if p2_refit_available else None,
                "membership": "x<=c:left;x>c:right" if p2_refit_available else None,
                "membership_cell": chosen_membership_cell if p2_refit_available else None,
                "domain": x_domain if p2_refit_available else None,
                "direction": "nondecreasing" if p2_refit_available else None,
                "continuity_residual": 0.0 if p2_refit_available else None,
                "certificate": "PASS" if p2_refit_available else (p2_failure or pipeline_failure or "VALIDATION_UNAVAILABLE"),
                "derivative_certificate_left": derivative_certificate(
                    p2_refit_available, "poly1_v1", [x_domain[0], c], 0.32,
                    "nondecreasing", p2_failure or pipeline_failure or "VALIDATION_UNAVAILABLE",
                ),
                "derivative_certificate_right": derivative_certificate(
                    p2_refit_available, "poly1_v1", [c, x_domain[1]], 0.98,
                    "nondecreasing", p2_failure or pipeline_failure or "VALIDATION_UNAVAILABLE",
                ),
                "identifiability_status": "IDENTIFIED" if p2_refit_available else "UNAVAILABLE",
                "bound_status": "NO_ACTIVE_BOUNDS" if p2_refit_available else "UNAVAILABLE",
                "collapse_status": "NO_COLLAPSE" if p2_refit_available else "COLLAPSED_SEGMENTS" if p2_failure == "COLLAPSED_SEGMENTS" else "UNAVAILABLE",
                "solver_status": "SUCCEEDED" if p2_refit_available or p2_failure == "COLLAPSED_SEGMENTS" else "FAILED" if (p2_failure == "OPTIMIZER_FAILURE" or pipeline_failure == "OPTIMIZER_FAILURE") else "NOT_RUN",
                "stability_status": "STABLE" if p2_refit_available else "UNAVAILABLE",
                "optimizer": optimizer,
                "model_structure_hash": canonical_sha256(p2_structure_payload) if p2_structure_payload else None,
                "model_instance_hash": canonical_sha256(p2_instance_payload) if p2_instance_payload else None,
                "model_path": "models/two-segment-model.json" if p2_refit_available else None,
                "left": {
                    "n": len(left_rows) if p2_refit_available else None,
                    "share": len(left_rows) / n_used if p2_refit_available else None,
                    "n_unique_x": len({row["x"] for row in left_rows}) if p2_refit_available else None,
                    "span": [min(row["x"] for row in left_rows), max(row["x"] for row in left_rows)] if p2_refit_available else None,
                    "r2_fit": metric(p2_left_r2, "in_sample", p2_failure or pipeline_failure or "UNDEFINED_LOCAL_R2"),
                    "rmse_fit": metric(p2_left_rmse, "in_sample", p2_failure or pipeline_failure or "UNDEFINED_LOCAL_RMSE", y_unit, "segment_root_mean_squared_full_data_refit_error"),
                    "mae_fit": metric(p2_left_mae, "in_sample", p2_failure or pipeline_failure or "UNDEFINED_LOCAL_MAE", y_unit, "segment_mean_absolute_full_data_refit_error"),
                },
                "right": {
                    "n": len(right_rows) if p2_refit_available else None,
                    "share": len(right_rows) / n_used if p2_refit_available else None,
                    "n_unique_x": len({row["x"] for row in right_rows}) if p2_refit_available else None,
                    "span": [min(row["x"] for row in right_rows), max(row["x"] for row in right_rows)] if p2_refit_available else None,
                    "r2_fit": metric(p2_right_r2, "in_sample", p2_failure or pipeline_failure or "UNDEFINED_LOCAL_R2"),
                    "rmse_fit": metric(p2_right_rmse, "in_sample", p2_failure or pipeline_failure or "UNDEFINED_LOCAL_RMSE", y_unit, "segment_root_mean_squared_full_data_refit_error"),
                    "mae_fit": metric(p2_right_mae, "in_sample", p2_failure or pipeline_failure or "UNDEFINED_LOCAL_MAE", y_unit, "segment_mean_absolute_full_data_refit_error"),
                },
                "r2_fit_all": metric(p2_refit_r2, "in_sample", p2_failure or pipeline_failure or "VALIDATION_UNAVAILABLE"),
                "rmse_fit_all": metric(p2_refit_rmse, "in_sample", p2_failure or pipeline_failure or "VALIDATION_UNAVAILABLE", y_unit, "root_mean_squared_full_data_refit_error"),
                "mae_fit_all": metric(p2_refit_mae, "in_sample", p2_failure or pipeline_failure or "VALIDATION_UNAVAILABLE", y_unit, "mean_absolute_full_data_refit_error"),
                "n": n_used if p2_refit_available else None,
                "n_unique_x": unique_x if p2_refit_available else None,
                "plot": {
                    "path": "figures/two-segment.svg",
                    "status": "branches_boundary_and_points" if p2_refit_available else "typed_failure_with_points",
                    "line_present": p2_refit_available,
                    "boundary_present": p2_refit_available,
                },
            },
        },
        "diagnostics": diagnostics,
        "warnings": warnings,
        "failures": failures,
        "provenance": {
            "analysis_id": analysis_hash,
            "execution_environment_id": environment_hash,
            "policy_version": "prototype-policy-v1",
            "registry_version": "registry_v1",
            "input_sha256": input_hash,
            "request_sha256": request_hash,
            "policy_sha256": policy_hash,
            "registry_sha256": registry_hash,
            "source_sha256": source_hash,
            "dependency_lock_sha256": dependency_hash,
            "solver_backend": "synthetic_reference_closed_form_and_profile_cells",
            "environment": environment_payload,
            "thread_policy": "single_process_single_thread",
            "base_seed": 20260716,
            "numerical_tolerances": {
                "absolute_reconciliation": 1e-12,
                "continuity": 1e-12,
                "derivative": 1e-12,
            },
            "work_counts": {
                "validation_appearances": appearance_count,
                "outer_fit_count": len(all_outer_fit_ids),
                "bootstrap_requested": 200,
                "bootstrap_successful": len(bootstrap_values),
                "bootstrap_failed": 200 - len(bootstrap_values),
                "influence_groups": diagnostics["influence"]["group_count"],
                "influence_grid_evaluations": diagnostics["influence"]["evaluation_grid_size"],
                "optimizer_attempts": optimizer["attempt_count"],
                "optimizer_evaluations": optimizer["evaluation_count"],
            },
            "fallback_failure_counts": {
                "p2_fallback_appearances": p2_fallback_appearance_count,
                "p2_fallback_outer_fits": len(p2_fallback_outer_fit_ids),
                "reported_failures": len(failures),
            },
            "certificate_versions": {
                "derivative": "prototype-analytic-certificate-v1",
                "continuity": "prototype-continuity-certificate-v1",
            },
            "timezone": "Europe/Moscow",
            "hash_payloads": {
                "policy": policy_payload,
                "registry": registry_payload,
                "source_manifest": source_payload,
                "dependency_manifest": dependency_payload,
                "input": input_hash_payload,
                "request_metadata": request_metadata_payload,
            },
        },
        "visualization": {
            "geometry": {"view_box": [0, 0, 640, 400], "plot_rect": [70, 40, 520, 280], "xlim": x_domain, "ylim": y_domain, "x_ticks": 6, "y_ticks": 5},
            "palette": {"model": "#005A9C", "boundary": "#B00020", "point": "#4B5563", "text": "#111827", "grid": "#D1D5DB"},
        },
        "observations": rows,
        "artifacts": [],
    }
    return report


def fnum(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "NA"
    return f"{value:.{digits}f}"


def h(value: object) -> str:
    return html.escape(str(value), quote=True)


def series_for(report: dict) -> dict[str, list[tuple[float, float]]]:
    lo, hi = report["input"]["domain"]
    profile = "constant" if report["refit"]["p1"]["family_id"] == "constant_v1" else "one"
    p1 = (
        [(lo + (hi - lo) * i / 100, p1_value(lo + (hi - lo) * i / 100, profile)) for i in range(101)]
        if report["refit"]["p1"]["status"] == "certified" else []
    )
    result = {"p1": p1, "p2_left": [], "p2_right": []}
    p2 = report["refit"]["p2"]
    if p2["status"] == "certified":
        c = float(p2["boundary"])
        result["p2_left"] = [(lo + (c - lo) * i / 50, p2_value(lo + (c - lo) * i / 50, c)) for i in range(51)]
        result["p2_right"] = [(c + (hi - c) * i / 50, p2_value(c + (hi - c) * i / 50, c)) for i in range(51)]
    return result


def plot_transform(report: dict):
    geometry = report["visualization"]["geometry"]
    left, top, width, height = geometry["plot_rect"]
    x0, x1 = geometry["xlim"]
    y0, y1 = geometry["ylim"]

    def sx(value: float) -> float:
        return left + (value - x0) / (x1 - x0 or 1.0) * width

    def sy(value: float) -> float:
        return top + height - (value - y0) / (y1 - y0 or 1.0) * height

    return sx, sy


def svg_figure(report: dict, kind: str) -> str:
    geometry = report["visualization"]["geometry"]
    left, top, width, height = geometry["plot_rect"]
    x0, x1 = geometry["xlim"]
    y0, y1 = geometry["ylim"]
    residual_field = None
    if kind == "residuals":
        recommended = report["decision"]["recommended_procedure"]
        residual_field = "resid_two_oof" if recommended == "two" else "resid_one_oof"
        residual_values = [row.get(residual_field) for row in report["observations"] if row.get(residual_field) is not None]
        residual_limit = max(1.0, max((abs(value) for value in residual_values), default=1.0) * 1.2)
        y0, y1 = -residual_limit, residual_limit
    def sx(value: float) -> float:
        return left + (value - x0) / (x1 - x0 or 1.0) * width
    def sy(value: float) -> float:
        return top + height - (value - y0) / (y1 - y0 or 1.0) * height
    colors = report["visualization"]["palette"]
    title = {
        "one": "Односегментная монотонная аппроксимация",
        "two": "Двухсегментная монотонная аппроксимация",
        "residuals": "OOF-остатки рекомендованной процедуры",
    }[kind]
    desc = (
        f"Синтетический scatterplot, {report['input']['n_used']} наблюдений; "
        f"x от {x0} до {x1} {report['input']['x_unit']}, "
        f"y от {fnum(y0,2)} до {fnum(y1,2)} {report['input']['y_unit']}."
    )
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400" viewBox="0 0 640 400" ',
        f'role="img" aria-labelledby="title desc" data-figure-kind="{kind}" ',
        f'data-xlim="{x0},{x1}" data-ylim="{y0},{y1}" data-plot-rect="{left},{top},{width},{height}">',
        f"<title id=\"title\">{h(title)}</title><desc id=\"desc\">{h(desc)}</desc>",
        f'<rect x="0" y="0" width="640" height="400" fill="#FFFFFF"/>',
    ]
    for i in range(6):
        x = left + width * i / 5
        value = x0 + (x1 - x0) * i / 5
        parts.append(f'<line x1="{x:.3f}" y1="{top}" x2="{x:.3f}" y2="{top+height}" stroke="{colors["grid"]}" stroke-width="1"/>')
        parts.append(f'<text x="{x:.3f}" y="{top+height+24}" text-anchor="middle" fill="{colors["text"]}" font-size="13">{h(fnum(value,1))}</text>')
    for i in range(5):
        y = top + height * i / 4
        value = y1 - (y1 - y0) * i / 4
        parts.append(f'<line x1="{left}" y1="{y:.3f}" x2="{left+width}" y2="{y:.3f}" stroke="{colors["grid"]}" stroke-width="1"/>')
        parts.append(f'<text x="{left-12}" y="{y+4:.3f}" text-anchor="end" fill="{colors["text"]}" font-size="13">{h(fnum(value,1))}</text>')
    parts.append(f'<rect x="{left}" y="{top}" width="{width}" height="{height}" fill="none" stroke="{colors["text"]}" stroke-width="1.5"/>')
    parts.append(f'<text x="{left+width/2}" y="386" text-anchor="middle" fill="{colors["text"]}" font-size="14">x, {h(report["input"]["x_unit"])}</text>')
    parts.append(f'<text x="18" y="{top+height/2}" text-anchor="middle" transform="rotate(-90 18 {top+height/2})" fill="{colors["text"]}" font-size="14">y, {h(report["input"]["y_unit"])}</text>')

    if kind == "residuals":
        zero = sy(0.0)
        parts.append(f'<line x1="{left}" y1="{zero:.3f}" x2="{left+width}" y2="{zero:.3f}" stroke="{colors["text"]}" stroke-width="2" stroke-dasharray="7 5" data-zero-line="true"/>')
        if report["diagnostics"]["oof"]["status"] != "available":
            reason = report["diagnostics"]["oof"].get("reason") or "OOF_DIAGNOSTICS_UNAVAILABLE"
            parts.append(f'<rect x="{left+80}" y="{top+90}" width="{width-160}" height="90" rx="8" fill="#FFF4E5" stroke="#8A4B00" stroke-width="2"/>')
            parts.append(f'<text x="{left+width/2}" y="{top+125}" text-anchor="middle" fill="#5C3100" font-size="18" font-weight="700">OOF diagnostics unavailable</text>')
            parts.append(f'<text x="{left+width/2}" y="{top+154}" text-anchor="middle" fill="#5C3100" font-size="14">{h(reason)}</text></svg>')
            return "".join(parts)
        for row in report["observations"]:
            value = row.get(residual_field)
            if value is None:
                continue
            parts.append(f'<circle cx="{sx(row["x"]):.3f}" cy="{sy(value):.3f}" r="5" fill="{colors["point"]}" stroke="{colors["text"]}" data-row-id="{h(row["row_id"])}" data-residual="{value:.17g}"/>')
        parts.append(f'<text x="{left+8}" y="{top+18}" fill="{colors["text"]}" font-size="13">OOF residual = y − ŷ; separate from refit influence</text>')
        parts.append("</svg>")
        return "".join(parts)

    series = series_for(report)
    if kind == "one" and report["refit"]["p1"]["status"] == "certified":
        coords = " ".join(f"{sx(x):.3f},{sy(y):.3f}" for x, y in series["p1"])
        parts.append(f'<polyline points="{coords}" fill="none" stroke="{colors["model"]}" stroke-width="4" data-series="p1" data-domain="{x0},{x1}"/>')
        parts.append(f'<text x="{left+10}" y="{top+22}" fill="{colors["model"]}" font-size="14" font-weight="700">P1 · {h(report["refit"]["p1"]["family_id"])} · {h(report["refit"]["p1"]["direction"])} · final refit</text>')
    elif kind == "two" and report["refit"]["p2"]["status"] == "certified":
        p2 = report["refit"]["p2"]
        for series_id in ("p2_left", "p2_right"):
            coords = " ".join(f"{sx(x):.3f},{sy(y):.3f}" for x, y in series[series_id])
            domain = f"{series[series_id][0][0]},{series[series_id][-1][0]}"
            dash = "" if series_id == "p2_left" else ' stroke-dasharray="10 5"'
            parts.append(f'<polyline points="{coords}" fill="none" stroke="{colors["model"]}" stroke-width="4"{dash} data-series="{series_id}" data-domain="{domain}"/>')
        bx = sx(p2["boundary"])
        parts.append(f'<line x1="{bx:.3f}" y1="{top}" x2="{bx:.3f}" y2="{top+height}" stroke="{colors["boundary"]}" stroke-width="4" stroke-dasharray="8 6" data-boundary="{p2["boundary"]}"/>')
        parts.append(f'<text x="{min(bx+8,left+width-150):.3f}" y="{top+20}" fill="{colors["boundary"]}" font-size="14" font-weight="700">граница c={h(fnum(p2["boundary"],2))}</text>')
        label_style = 'font-size="12" font-weight="700"'
        parts.append(f'<text x="{left+8:.3f}" y="{top+45:.3f}" fill="{colors["model"]}" {label_style} data-branch-label="left">левая: {h(p2["family_left"])}</text>')
        parts.append(f'<text x="{max(bx+8,left+width-185):.3f}" y="{top+45:.3f}" fill="{colors["model"]}" {label_style} data-branch-label="right">правая: {h(p2["family_right"])}</text>')
    else:
        reason = report["refit"]["p2"].get("reason") if kind == "two" else report["refit"]["p1"].get("reason")
        parts.append(f'<rect x="{left+80}" y="{top+90}" width="{width-160}" height="90" rx="8" fill="#FFF4E5" stroke="#8A4B00" stroke-width="2"/>')
        parts.append(f'<text x="{left+width/2}" y="{top+125}" text-anchor="middle" fill="#5C3100" font-size="18" font-weight="700">Модель недоступна</text>')
        parts.append(f'<text x="{left+width/2}" y="{top+154}" text-anchor="middle" fill="#5C3100" font-size="14">{h(reason or "NO_VALID_MODEL")}</text>')

    coordinate_counts: dict[tuple[float, float], int] = {}
    for row in report["observations"]:
        coordinate = (row["x"], row["y"])
        coordinate_counts[coordinate] = coordinate_counts.get(coordinate, 0) + 1
    seen_coordinates: set[tuple[float, float]] = set()
    for row in report["observations"]:
        px, py = sx(row["x"]), sy(row["y"])
        coordinate = (row["x"], row["y"])
        multiplicity = coordinate_counts[coordinate]
        parts.append(f'<circle cx="{px:.3f}" cy="{py:.3f}" r="5" fill="{colors["point"]}" fill-opacity="0.78" stroke="{colors["text"]}" stroke-width="1.5" data-row-id="{h(row["row_id"])}" data-x="{row["x"]:.17g}" data-y="{row["y"]:.17g}" data-multiplicity="{multiplicity}"/>')
        if multiplicity > 1 and coordinate not in seen_coordinates:
            rug_y = top + height
            parts.append(f'<line x1="{px:.3f}" y1="{rug_y-13:.3f}" x2="{px:.3f}" y2="{rug_y:.3f}" stroke="{colors["text"]}" stroke-width="3" data-rug-multiplicity="{multiplicity}"/>')
            parts.append(f'<text x="{px+7:.3f}" y="{rug_y-5:.3f}" fill="{colors["text"]}" font-size="12" font-weight="700" data-count-label="{multiplicity}">×{multiplicity}</text>')
        seen_coordinates.add(coordinate)
        flags = set(row["flags"])
        if "LARGE_OOF_RESIDUAL" in flags and "HIGH_REFIT_INFLUENCE" in flags:
            parts.append(f'<path d="M {px:.3f} {py-10:.3f} L {px+3:.3f} {py-3:.3f} L {px+10:.3f} {py:.3f} L {px+3:.3f} {py+3:.3f} L {px:.3f} {py+10:.3f} L {px-3:.3f} {py+3:.3f} L {px-10:.3f} {py:.3f} L {px-3:.3f} {py-3:.3f} Z" fill="none" stroke="#111827" stroke-width="2.5" data-flag="combined"/>')
        elif "LARGE_OOF_RESIDUAL" in flags:
            parts.append(f'<path d="M {px:.3f} {py-10:.3f} L {px+9:.3f} {py+8:.3f} L {px-9:.3f} {py+8:.3f} Z" fill="none" stroke="#111827" stroke-width="2.5" data-flag="residual"/>')
        elif "HIGH_REFIT_INFLUENCE" in flags:
            parts.append(f'<path d="M {px:.3f} {py-10:.3f} L {px+10:.3f} {py:.3f} L {px:.3f} {py+10:.3f} L {px-10:.3f} {py:.3f} Z" fill="none" stroke="#111827" stroke-width="2.5" data-flag="influence"/>')
    parts.append("</svg>")
    return "".join(parts)


def diagnostics_svg(report: dict) -> str:
    width, height = 760, 940
    left, panel_width, panel_height = 92.0, 610.0, 150.0
    panel_tops = [55.0, 275.0, 495.0, 715.0]
    colors = report["visualization"]["palette"]
    rows = report["diagnostics"]["rows"]
    oof_available = report["diagnostics"]["oof"]["status"] == "available"
    influence_available = report["diagnostics"]["influence"]["status"] == "available"
    x0, x1 = report["input"]["domain"]

    def scale(value: float, low: float, high: float, start: float, span: float) -> float:
        return start + (value - low) / (high - low or 1.0) * span

    def panel_frame(parts: list[str], index: int, title: str, x_label: str, y_label: str) -> tuple[float, float]:
        top = panel_tops[index]
        parts.append(f'<g data-panel="{index + 1}"><text x="{left}" y="{top - 18}" fill="{colors["text"]}" font-size="17" font-weight="700">{h(title)}</text>')
        parts.append(f'<rect x="{left}" y="{top}" width="{panel_width}" height="{panel_height}" fill="#FFFFFF" stroke="{colors["text"]}" stroke-width="1.5"/>')
        parts.append(f'<text x="{left + panel_width / 2}" y="{top + panel_height + 30}" text-anchor="middle" fill="{colors["text"]}" font-size="13">{h(x_label)}</text>')
        parts.append(f'<text x="26" y="{top + panel_height / 2}" text-anchor="middle" transform="rotate(-90 26 {top + panel_height / 2})" fill="{colors["text"]}" font-size="13">{h(y_label)}</text>')
        return top, top + panel_height

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="diag-title diag-desc" data-figure-kind="residuals" data-panel-count="4">',
        '<title id="diag-title">Четыре раздельные панели диагностики OOF и sensitivity</title>',
        f'<desc id="diag-desc">OOF residual против x; OOF residual против OOF prediction; абсолютный robust z против x с порогом 3.5; отдельно sensitivity финального refit по независимым x-группам. OOF status: {h(report["diagnostics"]["oof"]["status"])}.</desc>',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#FFFFFF"/>',
    ]

    residual_values = [item["oof_residual_median"] for item in rows if item["oof_residual_median"] is not None]
    residual_limit = max(1.0, max((abs(value) for value in residual_values), default=1.0) * 1.15)
    prediction_values = [item["oof_prediction_median"] for item in rows if item["oof_prediction_median"] is not None]
    p0, p1 = (min(prediction_values), max(prediction_values)) if prediction_values else (0.0, 1.0)
    z_values = [item["score_median_abs_z"] for item in rows if item["score_median_abs_z"] is not None]
    z_high = max(4.0, max(z_values, default=0.0) * 1.12)

    top, bottom = panel_frame(parts, 0, "1. OOF residual против x", f'x, {report["input"]["x_unit"]}', f'residual, {report["input"]["y_unit"]}')
    zero_y = scale(0.0, -residual_limit, residual_limit, bottom, -panel_height)
    parts.append(f'<line x1="{left}" y1="{zero_y:.3f}" x2="{left + panel_width}" y2="{zero_y:.3f}" stroke="{colors["text"]}" stroke-width="1.5" stroke-dasharray="7 5" data-zero-line="true"/>')
    if oof_available:
        for item in rows:
            if item["oof_residual_median"] is None:
                continue
            px = scale(item["x"], x0, x1, left, panel_width)
            py = scale(item["oof_residual_median"], -residual_limit, residual_limit, bottom, -panel_height)
            parts.append(f'<circle cx="{px:.3f}" cy="{py:.3f}" r="5" fill="{colors["point"]}" data-series="oof_residual_vs_x" data-row-id="{h(item["row_id"])}" data-residual="{item["oof_residual_median"]:.17g}"/>')
    else:
        parts.append(f'<text x="{left + panel_width / 2}" y="{top + 75}" text-anchor="middle" fill="#8F001A" font-size="16" font-weight="700">OOF_DIAGNOSTICS_UNAVAILABLE</text>')
    parts.append("</g>")

    top, bottom = panel_frame(parts, 1, "2. OOF residual против OOF prediction", f'OOF prediction, {report["input"]["y_unit"]}', f'residual, {report["input"]["y_unit"]}')
    zero_y = scale(0.0, -residual_limit, residual_limit, bottom, -panel_height)
    parts.append(f'<line x1="{left}" y1="{zero_y:.3f}" x2="{left + panel_width}" y2="{zero_y:.3f}" stroke="{colors["text"]}" stroke-width="1.5" stroke-dasharray="7 5"/>')
    if oof_available:
        for item in rows:
            if item["oof_prediction_median"] is None or item["oof_residual_median"] is None:
                continue
            px = scale(item["oof_prediction_median"], p0, p1, left, panel_width)
            py = scale(item["oof_residual_median"], -residual_limit, residual_limit, bottom, -panel_height)
            parts.append(f'<circle cx="{px:.3f}" cy="{py:.3f}" r="5" fill="{colors["point"]}" data-series="oof_residual_vs_prediction" data-row-id="{h(item["row_id"])}"/>')
    else:
        parts.append(f'<text x="{left + panel_width / 2}" y="{top + 75}" text-anchor="middle" fill="#8F001A" font-size="16" font-weight="700">OOF_DIAGNOSTICS_UNAVAILABLE</text>')
    parts.append("</g>")

    top, bottom = panel_frame(parts, 2, "3. |z OOF| против x", f'x, {report["input"]["x_unit"]}', "median |z|")
    threshold_y = scale(3.5, 0.0, z_high, bottom, -panel_height)
    parts.append(f'<line x1="{left}" y1="{threshold_y:.3f}" x2="{left + panel_width}" y2="{threshold_y:.3f}" stroke="{colors["boundary"]}" stroke-width="3" stroke-dasharray="8 6" data-z-threshold="3.5"/>')
    parts.append(f'<text x="{left + 8}" y="{threshold_y - 7:.3f}" fill="{colors["boundary"]}" font-size="13" font-weight="700">review threshold |z|=3.5</text>')
    if oof_available:
        for item in rows:
            if item["score_median_abs_z"] is None:
                continue
            px = scale(item["x"], x0, x1, left, panel_width)
            py = scale(item["score_median_abs_z"], 0.0, z_high, bottom, -panel_height)
            parts.append(f'<circle cx="{px:.3f}" cy="{py:.3f}" r="5" fill="{colors["point"]}" data-series="absolute_z_vs_x" data-row-id="{h(item["row_id"])}" data-score="{item["score_median_abs_z"]:.17g}"/>')
    else:
        parts.append(f'<text x="{left + panel_width / 2}" y="{top + 75}" text-anchor="middle" fill="#8F001A" font-size="16" font-weight="700">SCALE_UNAVAILABLE · z=null</text>')
    parts.append("</g>")

    top, bottom = panel_frame(parts, 3, "4. sensitivity финального refit по x-группе", f'x-group, {report["input"]["x_unit"]}', "Dmax / Drms")
    influence_values = [value for item in report["diagnostics"]["influence_groups"] for value in (item["dmax"], item["drms"]) if value is not None]
    influence_high = max(0.65, max(influence_values, default=0.0) * 1.12)
    for threshold, label in ((0.50, "Dmax=0.50"), (0.25, "Drms=0.25")):
        y = scale(threshold, 0.0, influence_high, bottom, -panel_height)
        parts.append(f'<line x1="{left}" y1="{y:.3f}" x2="{left + panel_width}" y2="{y:.3f}" stroke="{colors["boundary"]}" stroke-width="2" stroke-dasharray="6 5" data-influence-threshold="{threshold}"/>')
        parts.append(f'<text x="{left + 8}" y="{y - 5:.3f}" fill="{colors["boundary"]}" font-size="12">{label}</text>')
    if influence_available:
        for item in report["diagnostics"]["influence_groups"]:
            px = scale(item["x"], x0, x1, left, panel_width)
            for value, series, offset in ((item["dmax"], "dmax", -4), (item["drms"], "drms", 4)):
                if value is None:
                    continue
                py = scale(value, 0.0, influence_high, bottom, -panel_height)
                points = f"{px + offset:.3f},{py - 6:.3f} {px + offset + 6:.3f},{py:.3f} {px + offset:.3f},{py + 6:.3f} {px + offset - 6:.3f},{py:.3f}"
                parts.append(f'<polygon points="{points}" fill="none" stroke="{colors["model"]}" stroke-width="2" data-series="influence_{series}" data-x-group-id="{h(item["x_group_id"])}" data-value="{value:.17g}"/>')
    else:
        descriptive = [item for item in rows if item["refit_residual"] is not None]
        if not oof_available and descriptive:
            values = [item["refit_residual"] for item in descriptive]
            limit = max(1.0, max(abs(value) for value in values) * 1.15)
            for item in descriptive:
                px = scale(item["x"], x0, x1, left, panel_width)
                py = scale(item["refit_residual"], -limit, limit, bottom, -panel_height)
                parts.append(f'<circle cx="{px:.3f}" cy="{py:.3f}" r="5" fill="none" stroke="{colors["point"]}" stroke-width="2" data-series="refit_residual_descriptive" data-row-id="{h(item["row_id"])}"/>')
            parts.append(f'<text x="{left + 8}" y="{top + 20}" fill="#5C3100" font-size="13" font-weight="700">REFIT_RESIDUAL_DESCRIPTIVE · influence unassessable</text>')
        else:
            parts.append(f'<text x="{left + panel_width / 2}" y="{top + 75}" text-anchor="middle" fill="#8F001A" font-size="16" font-weight="700">INFLUENCE_UNASSESSABLE</text>')
    parts.append("</g></svg>")
    return "".join(parts)


def write_csv_artifacts(bundle: Path, report: dict) -> None:
    exclusions_path = bundle / "input-exclusions.jsonl"
    with exclusions_path.open("w", encoding="utf-8", newline="") as handle:
        for item in report["input"]["exclusions"]:
            handle.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")

    observations_path = bundle / "observations.csv"
    fields = [
        "row_id", "source_row_id", "x", "y", "x_group_id", "input_status", "input_reason",
        "pred_null_oof", "pred_one_oof", "resid_one_oof", "pred_two_oof", "resid_two_oof",
        "p2_oof_fallback_code",
        "pred_one_refit", "pred_two_refit", "segment_refit", "residual_flags",
        "influence_flags", "robust_scale", "z_median", "score_median_abs_z",
        "flag_rate", "appearance_count", "refit_residual", "influence_dmax",
        "influence_drms", "influence_delta_c", "action",
    ]
    diagnostics_by_row = {item["row_id"]: item for item in report["diagnostics"]["rows"]}
    with observations_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in report["observations"]:
            flags = set(row["flags"])
            diagnostic = diagnostics_by_row[row["row_id"]]
            influence = diagnostic["influence_group"]
            writer.writerow({
                "row_id": "id:" + quote(row["row_id"], safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-"),
                "source_row_id": "id:" + quote(row["source_row_id"], safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-"),
                "x": row["x"], "y": row["y"], "x_group_id": "id:" + quote(row["x_group_id"], safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-"),
                "input_status": row["input_status"],
                "input_reason": "",
                "pred_null_oof": "" if row["pred_null_oof"] is None else row["pred_null_oof"],
                "pred_one_oof": row["pred_one_oof"], "resid_one_oof": row["resid_one_oof"],
                "pred_two_oof": "" if row["pred_two_oof"] is None else row["pred_two_oof"],
                "resid_two_oof": "" if row["resid_two_oof"] is None else row["resid_two_oof"],
                "p2_oof_fallback_code": row["p2_oof_fallback_code"] or "",
                "pred_one_refit": row["pred_one_refit"],
                "pred_two_refit": "" if row["pred_two_refit"] is None else row["pred_two_refit"],
                "segment_refit": row["segment_refit"] or "",
                "residual_flags": "LARGE_OOF_RESIDUAL" if "LARGE_OOF_RESIDUAL" in flags else "",
                "influence_flags": "HIGH_REFIT_INFLUENCE" if "HIGH_REFIT_INFLUENCE" in flags else "",
                "robust_scale": "" if diagnostic["robust_scale"]["value"] is None else diagnostic["robust_scale"]["value"],
                "z_median": "" if diagnostic["z_median"] is None else diagnostic["z_median"],
                "score_median_abs_z": "" if diagnostic["score_median_abs_z"] is None else diagnostic["score_median_abs_z"],
                "flag_rate": "" if diagnostic["flag_rate"] is None else diagnostic["flag_rate"],
                "appearance_count": diagnostic["appearance_count"],
                "refit_residual": "" if diagnostic["refit_residual"] is None else diagnostic["refit_residual"],
                "influence_dmax": "" if influence["dmax"] is None else influence["dmax"],
                "influence_drms": "" if influence["drms"] is None else influence["drms"],
                "influence_delta_c": "" if influence["delta_c"] is None else influence["delta_c"],
                "action": row["action"],
            })
        for excluded_index, excluded in enumerate(report["input"]["exclusions"], 1):
            logical_row_id = f'excluded:{excluded["source_row_id"]}:{excluded_index}'
            output = {field: "" for field in fields}
            output.update({
                "row_id": encode_export_id(logical_row_id),
                "source_row_id": encode_export_id(excluded["source_row_id"]),
                "input_status": "EXCLUDED",
                "input_reason": excluded["code"],
                "action": "skipped_with_audit",
            })
            writer.writerow(output)
    dump_json(bundle / "observations.csv-metadata.json", csvw_metadata(
        "observations.csv", "PROTOTYPE synthetic observations", fields, identifiers=True,
    ))

    appearance_fields = [
        "appearance_id", "repetition", "outer_fold_id", "row_id", "x_group_id", "y",
        "pred_null_oof", "pred_one_oof", "resid_one_oof", "pred_two_oof",
        "resid_two_oof", "scale_one_oof", "z_one_oof", "scale_two_oof", "z_two_oof",
        "p2_attempt_status", "fallback_code",
    ]
    appearance_path = bundle / "oof-appearances.csv"
    repetitions = int(report["validation"]["repetitions"])
    fallback_outer_fit_ids = set(report["validation"]["procedures"]["p2"]["fallback_outer_fit_ids"])
    fallback_reason = report["validation"]["procedures"]["p2"]["reason"]
    with appearance_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=appearance_fields, lineterminator="\n")
        writer.writeheader()
        for repetition in range(1, repetitions + 1):
            for row_index, row in enumerate(report["observations"]):
                encoded_row_id = "id:" + quote(row["row_id"], safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-")
                fit_id = outer_fit_id(row_index, repetition, report["input"]["n_unique_x"])
                is_fallback = fit_id in fallback_outer_fit_ids
                writer.writerow({
                    "appearance_id": f"id:rep-{repetition:03d}%3A{quote(row['row_id'], safe='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-')}",
                    "repetition": repetition,
                    "outer_fold_id": encode_export_id(fit_id),
                    "row_id": encoded_row_id,
                    "x_group_id": "id:" + quote(row["x_group_id"], safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-"),
                    "y": row["y"],
                    "pred_null_oof": row["pred_null_oof"],
                    "pred_one_oof": row["pred_one_oof"],
                    "resid_one_oof": row["resid_one_oof"],
                    "pred_two_oof": row["pred_two_oof"],
                    "resid_two_oof": row["resid_two_oof"],
                    "scale_one_oof": row["scale_one_oof"],
                    "z_one_oof": row["z_one_oof"],
                    "scale_two_oof": row["scale_two_oof"],
                    "z_two_oof": row["z_two_oof"],
                    "p2_attempt_status": "failed_with_p1_fallback" if is_fallback else "succeeded",
                    "fallback_code": fallback_reason if is_fallback else "",
                })
    dump_json(bundle / "oof-appearances.csv-metadata.json", csvw_metadata(
        "oof-appearances.csv", "PROTOTYPE synthetic long OOF appearance export", appearance_fields, identifiers=True,
    ))

    diagnostic_fields = [
        "appearance_id", "repetition", "outer_fold_id", "row_id", "x_group_id",
        "procedure", "y", "prediction_oof", "residual_oof", "robust_scale",
        "scale_method", "scale_scope", "z_oof", "fallback_status", "fallback_code",
        "action",
    ]
    diagnostic_path = bundle / "diagnostic-appearances.csv"
    with diagnostic_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=diagnostic_fields, lineterminator="\n")
        writer.writeheader()
        for repetition in range(1, repetitions + 1):
            for row_index, row in enumerate(report["observations"]):
                encoded_row_id = "id:" + quote(row["row_id"], safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-")
                encoded_group = "id:" + quote(row["x_group_id"], safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-")
                fit_id = outer_fit_id(row_index, repetition, report["input"]["n_unique_x"])
                fold_id = encode_export_id(fit_id)
                is_fallback = fit_id in fallback_outer_fit_ids
                for procedure in ("one", "two"):
                    prediction = row[f"pred_{procedure}_oof"]
                    residual = row[f"resid_{procedure}_oof"]
                    scale = row[f"scale_{procedure}_oof"]
                    z_value = row[f"z_{procedure}_oof"]
                    trace = next(
                        item for item in report["diagnostics"]["scale_traces"]
                        if item["row_id"] == row["row_id"] and item["procedure"] == procedure
                    )
                    writer.writerow({
                        "appearance_id": f"id:diag-{procedure}-rep-{repetition:03d}%3A{quote(row['row_id'], safe='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-')}",
                        "repetition": repetition,
                        "outer_fold_id": fold_id,
                        "row_id": encoded_row_id,
                        "x_group_id": encoded_group,
                        "procedure": procedure,
                        "y": row["y"],
                        "prediction_oof": "" if prediction is None else prediction,
                        "residual_oof": "" if residual is None else residual,
                        "robust_scale": "" if scale is None else scale,
                        "scale_method": trace["estimator"],
                        "scale_scope": report["diagnostics"]["oof"]["scale_scope"],
                        "z_oof": "" if z_value is None else z_value,
                        "fallback_status": ("failed_with_p1_fallback" if is_fallback else "succeeded") if procedure == "two" else "not_applicable",
                        "fallback_code": (fallback_reason if is_fallback else "") if procedure == "two" else "",
                        "action": "review_only",
                    })
    dump_json(bundle / "diagnostic-appearances.csv-metadata.json", csvw_metadata(
        "diagnostic-appearances.csv", "PROTOTYPE synthetic long row × procedure × repetition diagnostics", diagnostic_fields, identifiers=True,
    ))

    scale_trace_fields = [
        "trace_id", "row_id", "procedure", "training_row_id", "training_x_group_id",
        "sample_residual", "center_median", "estimator", "bin_count", "target_bin_id",
        "in_target_bin", "raw_local_scale", "local_floor", "computed_scale",
        "zero_tolerance", "scale_status", "scale_reason",
    ]
    scale_trace_lookup = {
        (item["row_id"], item["procedure"]): item
        for item in report["diagnostics"]["scale_traces"]
    }
    scale_trace_path = bundle / "diagnostic-scale-trace.csv"
    with scale_trace_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=scale_trace_fields, lineterminator="\n")
        writer.writeheader()
        if repetitions:
            for row in report["observations"]:
                encoded_row = "id:" + quote(row["row_id"], safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-")
                for procedure in ("one", "two"):
                    trace = scale_trace_lookup[(row["row_id"], procedure)]
                    training = [
                        other for other in report["observations"]
                        if other["x_group_id"] != row["x_group_id"] and other[f"resid_{procedure}_oof"] is not None
                    ]
                    values = [float(other[f"resid_{procedure}_oof"]) for other in training]
                    center = statistics.median(values) if values else None
                    training_y = [abs(float(other["y"])) for other in training]
                    tolerance = trace["zero_tolerance"]
                    scale_value = trace["final_scale"]
                    reason = trace["reason"]
                    for sample_index, other in enumerate(training, 1):
                        writer.writerow({
                            "trace_id": f"id:scale-{procedure}%3A{quote(row['row_id'], safe='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-')}%3A{sample_index:03d}",
                            "row_id": encoded_row,
                            "procedure": procedure,
                            "training_row_id": "id:" + quote(other["row_id"], safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-"),
                            "training_x_group_id": "id:" + quote(other["x_group_id"], safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-"),
                            "sample_residual": other[f"resid_{procedure}_oof"],
                            "center_median": center,
                            "estimator": trace["estimator"],
                            "bin_count": trace["bin_count"],
                            "target_bin_id": encode_export_id(trace["target_bin_id"]) if trace["target_bin_id"] else "",
                            "in_target_bin": str(other["x_group_id"] in trace["target_bin_group_ids"]).lower(),
                            "raw_local_scale": "" if trace["raw_local_scale"] is None else trace["raw_local_scale"],
                            "local_floor": "" if trace["local_floor"] is None else trace["local_floor"],
                            "computed_scale": "" if scale_value is None else scale_value,
                            "zero_tolerance": tolerance,
                            "scale_status": "defined" if scale_value is not None else "undefined",
                            "scale_reason": reason or "",
                        })
    dump_json(bundle / "diagnostic-scale-trace.csv-metadata.json", csvw_metadata(
        "diagnostic-scale-trace.csv", "PROTOTYPE synthetic outer-train residual samples and robust-scale trace", scale_trace_fields, identifiers=True,
    ))

    influence_fields = [
        "x_group_id", "grid_index", "x", "full_prediction", "without_group_prediction",
        "s_ref", "s_ref_status", "normalized_delta", "status",
    ]
    influence_path = bundle / "influence-grid.csv"
    with influence_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=influence_fields, lineterminator="\n")
        writer.writeheader()
        if report["diagnostics"]["influence"]["status"] == "available":
            lo, hi = report["input"]["domain"]
            role = report["decision"]["recommended_procedure"]
            boundary = report["refit"]["p2"].get("boundary")
            p1_profile = "constant" if report["refit"]["p1"].get("family_id") == "constant_v1" else "one"
            grid_values = {lo + (hi - lo) * index / 500 for index in range(501)}
            grid_values.update(row["x"] for row in report["observations"])
            if boundary is not None:
                grid_values.add(float(boundary))
            grid_values = sorted(grid_values)
            grid_size = len(grid_values)
            scale_reference_rows = report["diagnostics"]["rows"]
            if grid_size != report["diagnostics"]["influence"]["evaluation_grid_size"]:
                raise AssertionError("influence grid count drifted from canonical diagnostics")
            for group in report["diagnostics"]["influence_groups"]:
                dmax = float(group["dmax"])
                drms = float(group["drms"])
                remaining_squared = max(0.0, grid_size * drms * drms - dmax * dmax)
                baseline_delta = math.sqrt(remaining_squared / (grid_size - 1)) if grid_size > 1 else dmax
                for index, x_value in enumerate(grid_values):
                    full_prediction = p2_value(x_value, float(boundary)) if role == "two" else p1_value(x_value, p1_profile)
                    normalized_delta = dmax if index == 0 else baseline_delta * (-1.0 if index % 2 else 1.0)
                    reference_row = min(
                        scale_reference_rows,
                        key=lambda item: (abs(float(item["x"]) - x_value), float(item["x"]), item["row_id"]),
                    )
                    s_ref = reference_row["robust_scale"]["value"]
                    if s_ref is None or s_ref <= 0:
                        raise AssertionError("available influence requires retained positive OOF scale")
                    writer.writerow({
                        "x_group_id": "id:" + quote(group["x_group_id"], safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-"),
                        "grid_index": index,
                        "x": x_value,
                        "full_prediction": full_prediction,
                        "without_group_prediction": full_prediction - normalized_delta * s_ref,
                        "s_ref": s_ref,
                        "s_ref_status": "defined",
                        "normalized_delta": normalized_delta,
                        "status": "sensitivity_only",
                    })
    dump_json(bundle / "influence-grid.csv-metadata.json", csvw_metadata(
        "influence-grid.csv", "PROTOTYPE synthetic full-versus-without-group influence grid", influence_fields, identifiers=True,
    ))

    bootstrap = report["validation"]["bootstrap_stability"]
    fallback_rate = report["validation"]["procedures"]["p2"]["fallback_rate"]["value"]
    rel_uplift = report["validation"]["uplift"]["rel_mse_uplift"]["value"]
    bootstrap_values = synthetic_bootstrap_values(
        report["decision"]["recommended_procedure"],
        report["refit"]["p2"]["status"] == "certified",
        rel_uplift if fallback_rate == 1.0 and rel_uplift is not None else None,
    )
    bootstrap_fields = ["resample_id", "status", "rel_mse_uplift", "failure_code"]
    bootstrap_path = bundle / "bootstrap-resamples.csv"
    with bootstrap_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=bootstrap_fields, lineterminator="\n")
        writer.writeheader()
        for index, value in enumerate(bootstrap_values, 1):
            writer.writerow({"resample_id": f"id:bootstrap-{index:03d}", "status": "success", "rel_mse_uplift": value, "failure_code": ""})
        failure_code = "PIPELINE_FAILURE" if bootstrap_values else "UNCERTAINTY_UNAVAILABLE"
        for index in range(len(bootstrap_values) + 1, bootstrap["requested_resamples"] + 1):
            writer.writerow({"resample_id": f"id:bootstrap-{index:03d}", "status": "failed", "rel_mse_uplift": "", "failure_code": failure_code})
    dump_json(bundle / "bootstrap-resamples.csv-metadata.json", csvw_metadata(
        "bootstrap-resamples.csv", "PROTOTYPE synthetic full-pipeline bootstrap results", bootstrap_fields, identifiers=True,
    ))

    stability_fields = [
        "outer_fit_id", "repetition", "outer_fold", "status", "fallback_reason",
        "direction", "family_pair", "boundary", "edge_hit", "flat_profile",
        "multiple_near_optima", "collapsed", "hard_failure",
    ]
    stability_path = bundle / "p2-stability-outer-fits.csv"
    with stability_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=stability_fields, lineterminator="\n")
        writer.writeheader()
        for item in report["validation"]["p2_stability"]["outer_fit_ledger"]:
            writer.writerow({
                **item,
                "outer_fit_id": encode_export_id(item["outer_fit_id"]),
                "fallback_reason": item["fallback_reason"] or "",
                "direction": item["direction"] or "",
                "family_pair": item["family_pair"] or "",
                "boundary": "" if item["boundary"] is None else item["boundary"],
                "edge_hit": "" if item["edge_hit"] is None else str(item["edge_hit"]).lower(),
                "flat_profile": "" if item["flat_profile"] is None else str(item["flat_profile"]).lower(),
                "multiple_near_optima": "" if item["multiple_near_optima"] is None else str(item["multiple_near_optima"]).lower(),
                "collapsed": "" if item["collapsed"] is None else str(item["collapsed"]).lower(),
                "hard_failure": str(item["hard_failure"]).lower(),
            })
    dump_json(bundle / "p2-stability-outer-fits.csv-metadata.json", csvw_metadata(
        "p2-stability-outer-fits.csv",
        "PROTOTYPE synthetic P2 outer-fit stability ledger",
        stability_fields,
        identifiers=True,
    ))

    series = series_for(report)
    plot_path = bundle / "plot-data.csv"
    with plot_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["series", "x", "y", "domain_start", "domain_end"])
        for name, values in series.items():
            if not values:
                continue
            for x, y in values:
                writer.writerow([name, format(x, ".17g"), format(y, ".17g"), format(values[0][0], ".17g"), format(values[-1][0], ".17g")])
    dump_json(bundle / "plot-data.csv-metadata.json", csvw_metadata(
        "plot-data.csv",
        "PROTOTYPE synthetic deterministic plot grid",
        ["series", "x", "y", "domain_start", "domain_end"],
        identifiers=True,
    ))


def fit_attempt_records(report: dict) -> list[dict]:
    """Materialize the deterministic prototype PROFILE_CELLS attempt ledger."""
    optimizer = report["refit"]["p2"]["optimizer"]
    cells = tie_safe_cells(report["observations"])
    records: list[dict] = []
    for cell_index, cell in enumerate(cells):
        for start_index in range(int(optimizer["starts_per_cell"])):
            attempt_index = len(records)
            succeeded = optimizer["status"] in {"SUCCEEDED", "COLLAPSED"}
            evaluations = 12 + (attempt_index % 3) if succeeded else 2
            records.append({
                "trace_version": "prototype-fit-attempts-1.0.0",
                "attempt_id": f"p2-{cell['cell_id']}-start-{start_index + 1:02d}",
                "strategy": "PROFILE_CELLS",
                "scope": "full_data_p2_refit",
                "cell_id": cell["cell_id"],
                "cell_lower_x": cell["lower_x"],
                "cell_upper_x_exclusive": cell["upper_x_exclusive"],
                "left_n": cell["left_n"],
                "right_n": cell["right_n"],
                "left_share": cell["left_share"],
                "right_share": cell["right_share"],
                "ties_atomic": cell["ties_atomic"],
                "family_left": "poly1_v1",
                "family_right": "poly1_v1",
                "direction": "nondecreasing",
                "start_id": f"manifest-start-{start_index + 1:02d}",
                "status": (
                    "CERTIFIED_COLLAPSED" if optimizer["status"] == "COLLAPSED"
                    else "CERTIFIED" if succeeded else "OPTIMIZER_FAILURE"
                ),
                "solver_status": "SUCCEEDED" if succeeded else "FAILED",
                "objective_sse_scaled_y": (
                    float((cell_index + 1) * 0.01 + start_index * 0.001)
                    if succeeded else None
                ),
                "evaluations": evaluations,
                "active_bounds": [],
                "optimality": 1e-10 * (attempt_index + 1) if succeeded else None,
                "certificate_result": "PASS" if succeeded else "NOT_RUN",
                "selected": optimizer["status"] == "SUCCEEDED" and attempt_index == 0,
                "competing_basin": False,
                "reason_codes": [] if succeeded else list(optimizer["reason_codes"]),
                "warning_codes": list(optimizer["warning_codes"]),
            })
    if len(records) != optimizer["trace_record_count"]:
        raise AssertionError("optimizer trace-record count drifted")
    if sum(item["evaluations"] for item in records) != optimizer["evaluation_count"]:
        raise AssertionError("optimizer evaluation count drifted")
    if sum(item["solver_status"] == "SUCCEEDED" for item in records) != optimizer["successful_attempt_count"]:
        raise AssertionError("optimizer successful-attempt count drifted")
    if sum(item["solver_status"] == "FAILED" for item in records) != optimizer["failed_attempt_count"]:
        raise AssertionError("optimizer failed-attempt count drifted")
    return records


def write_fit_attempt_trace(bundle: Path, report: dict) -> None:
    """Write JSONL with a stable gzip header (empty filename and mtime=0)."""
    trace_path = bundle / report["refit"]["p2"]["optimizer"]["trace_path"]
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            for item in fit_attempt_records(report):
                line = json.dumps(
                    item,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ) + "\n"
                compressed.write(line.encode("utf-8"))


REPORT_CSS = r"""
:root{color-scheme:light;--ink:#111827;--muted:#4b5563;--blue:#005a9c;--red:#8f001a;--line:#c7cdd4;--paper:#fff;--soft:#f4f7fa;--warn:#fff4e5;--warn-ink:#5c3100}
*{box-sizing:border-box}html{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);background:#eef2f6;line-height:1.5}
body{margin:0}.skip{position:absolute;left:1rem;top:-5rem;background:#111827;color:white;padding:.7rem 1rem;z-index:4}.skip:focus{top:1rem}
.prototype{background:#3b0764;color:#fff;padding:.75rem 1rem;text-align:center;font-weight:800;letter-spacing:.02em}
header,main,footer{max-width:1180px;margin:auto;background:var(--paper)}header{padding:2rem 2rem 1.2rem;border-bottom:1px solid var(--line)}main{padding:0 2rem 3rem}
h1{font-size:clamp(1.8rem,4vw,3rem);line-height:1.1;margin:.3rem 0}h2{margin:0 0 1rem;font-size:1.65rem}h3{font-size:1.14rem}.eyebrow,.code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.86rem}.eyebrow{color:var(--blue);font-weight:800;text-transform:uppercase;letter-spacing:.08em}
section{padding:2rem 0;border-bottom:1px solid var(--line)}.summary-grid,.plot-grid,.model-grid,.metric-grid{display:grid;gap:1rem}.summary-grid{grid-template-columns:2fr 1fr}.plot-grid,.model-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.metric-grid{grid-template-columns:repeat(3,minmax(0,1fr))}
.card{border:1px solid var(--line);border-radius:.75rem;padding:1rem;background:#fff}.decision{border-left:7px solid var(--blue)}.warning{background:var(--warn);color:var(--warn-ink);border:2px solid #8a4b00;border-radius:.65rem;padding:1rem;margin:1rem 0}.warning strong{display:block}.failure{background:#fff1f2;color:#650014;border:2px solid #8f001a;border-radius:.65rem;padding:1rem}
.metric strong{display:block;font-size:1.7rem}.metric small,.muted{color:var(--muted)}.status{display:inline-block;border:1px solid currentColor;border-radius:999px;padding:.15rem .55rem;font-weight:750;font-size:.82rem}.status.good{color:#075b34}.status.bad{color:#8f001a}.status.neutral{color:#334155}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:.6rem}table{border-collapse:collapse;width:100%;min-width:680px}caption{text-align:left;font-weight:800;padding:.8rem;background:var(--soft)}th,td{text-align:left;vertical-align:top;border-top:1px solid var(--line);padding:.72rem}th{background:#f8fafc}code{white-space:normal;overflow-wrap:anywhere;background:#f1f5f9;padding:.12rem .28rem;border-radius:.25rem}
figure{margin:0;border:1px solid var(--line);border-radius:.7rem;overflow:hidden;background:#fff}figure img{display:block;width:100%;height:auto}figcaption{padding:.9rem;background:#f8fafc}.figure-long{font-size:.92rem;color:#374151;margin:.4rem 0 0}
.pointer{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.75rem;color:#526070}.marker{font-weight:800}.marker.resid::before{content:"△ ";}.marker.influence::before{content:"◇ ";}.marker.both::before{content:"✶ ";}
nav ul{display:flex;flex-wrap:wrap;gap:.6rem;list-style:none;padding:0}nav a,a{color:#004e87;text-decoration-thickness:.12em;text-underline-offset:.16em}a:focus-visible,summary:focus-visible{outline:3px solid #b45309;outline-offset:3px}
details{margin-top:1rem}summary{cursor:pointer;font-weight:800}footer{padding:1.5rem 2rem;color:var(--muted)}
@media(max-width:760px){.summary-grid,.plot-grid,.model-grid,.metric-grid{grid-template-columns:1fr}header,main{padding-left:1rem;padding-right:1rem}.table-wrap{max-width:100%;scrollbar-gutter:stable}h2{font-size:1.4rem}}
@media print{html{background:#fff}.prototype{border:3px solid #3b0764;color:#3b0764;background:#fff}header,main,footer{max-width:none}.card,figure,.table-wrap{break-inside:avoid}nav{display:none}a{color:#111827}}
"""


def warning_blocks(report: dict) -> str:
    blocks = []
    for warning in report["warnings"]:
        blocks.append(
            f'<div class="warning" role="note" data-source-pointer="/warnings" data-warning-code="{h(warning["code"])}">'
            f'<strong>⚠ {h(warning["code"])}</strong>{h(warning["reason"])}'
            + (
                '<p>Порог 0,60 означает уменьшение squared loss относительно cross-fitted null; '
                'это не доля описанных точек и не универсальная гарантия качества.</p>'
                if warning["code"] == "BELOW_PRODUCT_R2" else ""
            )
            + "</div>"
        )
    return "".join(blocks)


def failure_blocks(report: dict) -> str:
    return "".join(
        f'<div class="failure" role="alert" data-source-pointer="/failures" data-failure-code="{h(item["code"])}">'
        f'<strong>{h(item["code"])}</strong><br>{h(item["reason"])}</div>'
        for item in report["failures"]
    )


def r2_cell(metric_value: dict, pointer: str) -> str:
    if metric_value["value"] is None:
        shown = f'NA — {h(metric_value["reason"])}'
    else:
        shown = fnum(metric_value["value"], 4)
        if abs(metric_value["value"] - 0.60) < 0.001:
            shown += " — ниже 0,60" if metric_value["value"] < 0.60 else " — не ниже 0,60"
    raw = "null" if metric_value["value"] is None else format(metric_value["value"], ".17g")
    return f'<span data-source-pointer="{h(pointer)}" data-raw-value="{h(raw)}">{shown}</span>'


def metric_cell(metric_value: dict, pointer: str, digits: int = 4) -> str:
    if metric_value["value"] is None:
        shown = f'NA — {h(metric_value["reason"])}'
        raw = "null"
    else:
        shown = fnum(metric_value["value"], digits)
        raw = format(metric_value["value"], ".17g")
    return f'<span data-source-pointer="{h(pointer)}" data-raw-value="{h(raw)}">{shown}</span>'


def render_report_html(report: dict) -> str:
    status = report["status"]
    rec = report["recommendation"]
    role = report["decision"]["recommended_procedure"]
    recommendation_text = (
        "Рекомендуются две функции" if role == "two" else
        "Рекомендуется одна функция" if role == "one" else
        "Проверенной рекомендации нет"
    )
    p1 = report["validation"]["procedures"]["p1"]
    p2 = report["validation"]["procedures"]["p2"]
    refit1, refit2 = report["refit"]["p1"], report["refit"]["p2"]
    diagnostics = report["diagnostics"]

    validation_rows = [
        ("P1 · одна функция", p1, "one", "/validation/procedures/p1"),
        ("P2 · две функции", p2, "two", "/validation/procedures/p2"),
    ]

    def validation_row(label: str, proc: dict, key: str, pointer: str) -> str:
        fallback_count = int(proc.get("fallback_appearances", 0))
        fallback_codes = proc.get("fallback_reason_counts", {})
        stability = (
            f'direction={metric_cell(proc["direction_frequency"], pointer + "/direction_frequency", 3)}; '
            f'family={metric_cell(proc["family_frequency"], pointer + "/family_frequency", 3)}'
        )
        return (
            f'<tr data-source-pointer="{h(pointer)}">'
            f'<th scope="row">{h(label)}</th><td>{h(proc["status"])}{(" — " + h(proc.get("reason"))) if proc.get("reason") else ""}'
            + (f'<br><small>attempt={h(proc.get("attempt_status"))}; full refit={h(proc.get("full_data_refit_status"))}</small>' if key == "two" else "") + '</td>'
            f'<td>{r2_cell(proc["r2_oos"], pointer + "/r2_oos")}</td>'
            f'<td>{metric_cell(proc["rmse_oof"], pointer + "/rmse_oof", 3)} {h(proc["rmse_oof"]["unit"])}</td>'
            f'<td>{metric_cell(proc["mae_oof"], pointer + "/mae_oof", 3)} {h(proc["mae_oof"]["unit"])}</td>'
            f'<td>{proc["successful_appearances"]} / {proc["oof_appearances"]}; repetitions={report["validation"]["repetitions"]}</td>'
            f'<td>{fallback_count} / {proc["oof_appearances"]}; rate={metric_cell(proc["fallback_rate"], pointer + "/fallback_rate", 3)}; codes={h(fallback_codes or "—")}</td>'
            f'<td>{stability}</td>'
            f'<td>{"recommended" if role == key else "tested alternative" if proc["status"] in {"valid", "fallback"} else "unavailable"}</td></tr>'
        )

    validation_html = "".join(validation_row(*item) for item in validation_rows)

    uplift = report["validation"]["uplift"]
    uplift_html = "".join(
        f'<li><code>{h(name)}</code>: {metric_cell(uplift[name], "/validation/uplift/" + name)}</li>'
        for name in ("delta_mse", "delta_rmse", "delta_mae", "delta_r2", "rel_mse_uplift")
    )
    gate_rows = "".join(
        f'<tr data-source-pointer="/validation/decision_gates/{index}"><th scope="row"><code>{h(item["id"])}</code></th><td>{h(item["status"])}</td>'
        f'<td>{h(item["observed"] if item["observed"] is not None else "NA")}</td><td>{h(item["threshold"])}</td><td>{h(item["reason"])}</td></tr>'
        for index, item in enumerate(report["validation"]["decision_gates"])
    )

    point_rows = "".join(
        f'<tr data-row-id="{h(row["row_id"])}" data-source-pointer="/observations/{index}"><th scope="row">{h(row["row_id"])}</th><td>{row["x"]:.6g}</td><td>{row["y"]:.6g}</td>'
        f'<td>{h(", ".join(row["flags"]) or "—")}</td><td>review_only</td></tr>'
        for index, row in enumerate(report["observations"])
    )
    exclusion_rows = "".join(
        f'<tr data-source-pointer="/input/exclusions/{index}"><th scope="row">{h(item.get("source_row_id", "NA"))}</th><td>{h(item["code"])}</td><td>{h(item.get("raw_value", "not retained"))}</td></tr>'
        for index, item in enumerate(report["input"]["exclusions"])
    ) or '<tr data-source-pointer="/input/exclusions"><td colspan="3">Исключённых строк нет</td></tr>'
    diagnostic_rows = "".join(
        f'<tr data-row-id="{h(item["row_id"])}" data-source-pointer="/diagnostics/rows/{index}">'
        f'<th scope="row">{h(item["row_id"])}</th>'
        f'<td>{h(fnum(item["oof_residual_median"], 4) if item["oof_residual_median"] is not None else "NA — OOF_DIAGNOSTICS_UNAVAILABLE")}</td>'
        f'<td>{h(fnum(item["robust_scale"]["value"], 4) if item["robust_scale"]["value"] is not None else "NA — " + str(item["robust_scale"]["reason"]))}</td>'
        f'<td>{h(fnum(item["z_median"], 3) if item["z_median"] is not None else "NA")}</td>'
        f'<td>{h(fnum(item["score_median_abs_z"], 3) if item["score_median_abs_z"] is not None else "NA")}</td>'
        f'<td>{h(fnum(item["flag_rate"], 3) if item["flag_rate"] is not None else "NA")}</td>'
        f'<td>{item["appearance_count"]}</td>'
        f'<td>{h(fnum(item["refit_residual"], 4) if item["refit_residual"] is not None else "NA")}</td>'
        f'<td>{h(fnum(item["influence_group"]["dmax"], 3) if item["influence_group"]["dmax"] is not None else "NA")}</td>'
        f'<td>{h(fnum(item["influence_group"]["drms"], 3) if item["influence_group"]["drms"] is not None else "NA")}</td>'
        f'<td>{h(fnum(item["influence_group"]["delta_c"], 3) if item["influence_group"]["delta_c"] is not None else "NA")}</td>'
        f'<td class="marker {"both" if len(item["flags"]) > 1 else "resid" if any(flag["code"] == "LARGE_OOF_RESIDUAL" for flag in item["flags"]) else "influence" if item["flags"] else ""}">{h(", ".join(flag["code"] for flag in item["flags"]) or "—")}</td>'
        f'<td>{h(item["action"])}</td></tr>'
        for index, item in enumerate(diagnostics["rows"])
    )
    repeated_rows = "".join(
        f'<tr data-source-pointer="/diagnostics/repeated_x_groups/{index}"><th scope="row">{h(item["x_group_id"])}</th>'
        f'<td>{item["n"]}</td><td>{h(fnum(item["mean_y"], 4))}</td><td>{h(fnum(item["median_y"], 4))}</td>'
        f'<td>{h(fnum(item["median_oof_residual"], 4) if item["median_oof_residual"] is not None else "NA")}</td>'
        f'<td>{h(fnum(item["within_group_mad_y"], 4))}</td>'
        f'<td>{h(fnum(item["positive_residual_share"], 3) if item["positive_residual_share"] is not None else "NA")}</td>'
        f'<td>{h(fnum(item["negative_residual_share"], 3) if item["negative_residual_share"] is not None else "NA")}</td>'
        f'<td>{"GROUP_SYSTEMATIC_RESIDUAL" if item["group_systematic_residual"] else "—"}</td></tr>'
        for index, item in enumerate(diagnostics["repeated_x_groups"])
    )
    pattern_rows = "".join(
        f'<tr data-source-pointer="/diagnostics/patterns/{index}"><th scope="row"><code>{h(item["code"])}</code></th>'
        f'<td>{h(item["status"])}</td><td>{h(item["threshold"])}</td><td>{h(item["reason"] or "—")}</td><td>review_only</td></tr>'
        for index, item in enumerate(diagnostics["patterns"])
    )

    model_link = (
        f'<a href="{h(rec["model_preview_path"])}">prototype model preview</a>'
        if rec["model_preview_path"] else "отсутствует: validated recommendation нет"
    )
    recommendation_domain_html = (
        f'<p><strong>Model domain:</strong> <span data-source-pointer="/recommendation/domain">'
        f'[{h(rec["domain"][0])}, {h(rec["domain"][1])}] {h(report["input"]["x_unit"])}</span>. '
        'Экстраполяция прямо запрещена.</p>'
        if rec["domain"] is not None
        else '<p data-source-pointer="/recommendation/domain"><strong>Model domain/extrapolation:</strong> отсутствуют — validated recommendation нет.</p>'
    )
    quality_warning_codes = [
        item["code"] for item in report["warnings"]
        if item["code"] in {"BELOW_PRODUCT_R2", "UNDEFINED_R2"}
    ]
    quality_warning_html = (
        f'<p data-source-pointer="/warnings"><strong>Quality warning:</strong> <code>{h(quality_warning_codes)}</code>.</p>'
        if quality_warning_codes
        else '<p data-source-pointer="/warnings"><strong>Quality warning:</strong> none.</p>'
    )
    p2_formula = (
        f'<code data-source-pointer="/refit/p2/formula_left">{h(refit2["formula_left"])}</code>; '
        f'<code data-source-pointer="/refit/p2/formula_right">{h(refit2["formula_right"])}</code>'
        if refit2["status"] == "certified" else f'NA — {h(refit2.get("reason") or "P2_UNAVAILABLE")}'
    )
    p2_boundary = fnum(refit2.get("boundary"), 2) if refit2.get("boundary") is not None else "NA"
    p1_formula_html = (
        f'<code data-source-pointer="/refit/p1/formula_display">{h(refit1["formula_display"])}</code>'
        if refit1["status"] == "certified" else f'NA — {h(refit1.get("reason") or "P1_UNAVAILABLE")}'
    )
    p1_refit_details = (
        f'registry={h(refit1["registry_version"])}; registry role={h(refit1["registry_role"])}; incubator excluded={h(refit1["incubator_excluded"])}; AST=<code>{h(refit1["canonical_ast_id"])}</code>; '
        f'parameters=<code>{h(json.dumps(refit1["parameters"], ensure_ascii=False, sort_keys=True))}</code>; '
        f'transforms=<code>{h(json.dumps(refit1["transforms"], ensure_ascii=False, sort_keys=True))}</code>; '
        f'domain={h(refit1["domain"])}; direction={h(refit1["direction"])}; '
        f'certificate={h(refit1["derivative_certificate"]["status"])}; simplified polynomial degree={h(refit1["derivative_certificate"]["polynomial_degree_after_simplification"])} '
        f'(critical points={h(refit1["derivative_certificate"]["critical_points"])}; '
        f'min signed derivative={h(refit1["derivative_certificate"]["minimum_signed_derivative"])}; '
        f'domain margin={h(refit1["derivative_certificate"]["domain_margin"])}); '
        f'identifiability={h(refit1["identifiability_status"])}; bounds={h(refit1["bound_status"])}; '
        f'collapse={h(refit1["collapse_status"])}; solver={h(refit1["solver_status"])}; extrapolation forbidden. '
        f'structure hash=<code data-source-pointer="/refit/p1/model_structure_hash">{h(refit1["model_structure_hash"])}</code>; '
        f'instance hash=<code data-source-pointer="/refit/p1/model_instance_hash">{h(refit1["model_instance_hash"])}</code>. '
        f'<a href="{h(refit1["model_path"])}">typed P1 model dossier</a>.'
        if refit1["status"] == "certified" else "Формула, domain, hashes и линия отсутствуют."
    )
    p2_size_text = (
        f'left n={refit2["left"]["n"]}, unique x={refit2["left"]["n_unique_x"]}, '
        f'span={h(refit2["left"]["span"])} ({refit2["left"]["share"]*100:.1f}%); '
        f'right n={refit2["right"]["n"]}, unique x={refit2["right"]["n_unique_x"]}, '
        f'span={h(refit2["right"]["span"])} ({refit2["right"]["share"]*100:.1f}%).'
        if refit2["status"] == "certified" else "left/right n и shares: NA — P2 refit unavailable."
    )
    p2_refit_details = (
        f'registry={h(refit2["registry_version"])}; registry role={h(refit2["registry_role"])}; incubator excluded={h(refit2["incubator_excluded"])}; pair=<code>{h(refit2["ordered_family_pair"])}</code>; '
        f'AST left/right=<code>{h(refit2["canonical_ast_left"]["ast_id"])}</code> / '
        f'<code>{h(refit2["canonical_ast_right"]["ast_id"])}</code>; '
        f'parameters=<code>{h(json.dumps(refit2["parameters"], ensure_ascii=False, sort_keys=True))}</code>; '
        f'transforms=<code>{h(json.dumps(refit2["transforms"], ensure_ascii=False, sort_keys=True))}</code>; '
        f'cell=<code>{h(refit2["membership_cell"]["cell_id"])}</code>; '
        f'left certificate={h(refit2["derivative_certificate_left"]["status"])}; simplified polynomial degree={h(refit2["derivative_certificate_left"]["polynomial_degree_after_simplification"])} '
        f'(interval={h(refit2["derivative_certificate_left"]["interval"])}; '
        f'critical points={h(refit2["derivative_certificate_left"]["critical_points"])}; '
        f'min signed derivative={h(refit2["derivative_certificate_left"]["minimum_signed_derivative"])}; '
        f'domain margin={h(refit2["derivative_certificate_left"]["domain_margin"])}); '
        f'right certificate={h(refit2["derivative_certificate_right"]["status"])}; simplified polynomial degree={h(refit2["derivative_certificate_right"]["polynomial_degree_after_simplification"])} '
        f'(interval={h(refit2["derivative_certificate_right"]["interval"])}; '
        f'critical points={h(refit2["derivative_certificate_right"]["critical_points"])}; '
        f'min signed derivative={h(refit2["derivative_certificate_right"]["minimum_signed_derivative"])}; '
        f'domain margin={h(refit2["derivative_certificate_right"]["domain_margin"])}); '
        f'identifiability={h(refit2["identifiability_status"])}; bounds={h(refit2["bound_status"])}; '
        f'collapse={h(refit2["collapse_status"])}; solver={h(refit2["solver_status"])}; '
        f'stability={h(refit2["stability_status"])}. '
        f'structure hash=<code data-source-pointer="/refit/p2/model_structure_hash">{h(refit2["model_structure_hash"])}</code>; '
        f'instance hash=<code data-source-pointer="/refit/p2/model_instance_hash">{h(refit2["model_instance_hash"])}</code>. '
        f'<a href="{h(refit2["model_path"])}">typed P2 model dossier</a>.'
        if refit2["status"] == "certified"
        else (
            f'NA — {h(refit2["reason"])}; collapse={h(refit2["collapse_status"])}; '
            f'solver={h(refit2["solver_status"])}; typed dossier absent.'
        )
    )
    optimizer = refit2["optimizer"]
    optimizer_text = (
        f'strategy=<code>{h(optimizer["strategy"])}</code>; status={h(optimizer["status"])}; '
        f'tie-safe/eligible cells={optimizer["tie_safe_cell_count"]}/{optimizer["eligible_cell_count"]}; '
        f'starts={optimizer["start_count"]}; attempts={optimizer["attempt_count"]}; '
        f'evaluations={optimizer["evaluation_count"]}; successes/failures='
        f'{optimizer["successful_attempt_count"]}/{optimizer["failed_attempt_count"]}; '
        f'competing basins={optimizer["competing_basin_count"]} ({h(optimizer["competing_basin_status"])}); '
        f'certificate={h(optimizer["certificate_result"])}; '
        f'reasons={h(optimizer["reason_codes"])}; warnings={h(optimizer["warning_codes"])}. '
        f'<a href="{h(optimizer["trace_path"])}" data-source-pointer="/refit/p2/optimizer/trace_path">Полный PROFILE_CELLS trace (JSONL.gz)</a>.'
    )
    p1_alt = (
        "Одна функция: исходные точки и сертифицированная линия P1 на общем домене"
        if refit1["status"] == "certified" else
        f'P1 недоступна: исходные точки и typed failure {refit1.get("reason") or "P1_UNAVAILABLE"}; линии нет'
    )
    p2_alt = (
        "Две функции: исходные точки, две интервал-ограниченные ветви и красная граница"
        if refit2["status"] == "certified" else
        f'P2 недоступна: исходные точки и typed failure {refit2.get("reason") or "P2_UNAVAILABLE"}; ветвей и границы нет'
    )
    bootstrap = report["validation"]["bootstrap_stability"]
    bootstrap_text = (
        f'{bootstrap["label"]}: target={h(bootstrap["target"])}; level={bootstrap["level"]}; unit={h(bootstrap["unit"])}; '
        f'[{fnum(bootstrap["interval"][0],3)}, {fnum(bootstrap["interval"][1],3)}], '
        f'{bootstrap["successful_resamples"]}/{bootstrap["requested_resamples"]} successful resamples; '
        f'{bootstrap["failed_resamples"]} failed; resampling unit={h(bootstrap["resampling_unit"])}; '
        f'method={h(bootstrap["percentile_method"])}; selection scope={h(bootstrap["selection_scope"])}'
        if bootstrap["status"] == "available" else f'UNCERTAINTY_UNAVAILABLE — {h(bootstrap["reason"])}'
    )
    decomposition = diagnostics["pure_error_lack_of_fit"]
    decomposition_text = (
        f'SSE_total={fnum(decomposition["sse_total"], 5)}; '
        f'SSE_pure_error={fnum(decomposition["sse_pure_error"], 5)}; '
        f'SSE_lack_of_fit={fnum(decomposition["sse_lack_of_fit"], 5)}; '
        'post-selection F-test не выполнялся.'
        if decomposition["status"] == "descriptive"
        else f'NA — {h(decomposition["reason"])}'
    )
    split_sensitivity = report["validation"]["split_sensitivity"]
    p2_stability = report["validation"]["p2_stability"]
    p2_stability_rows = "".join(
        f'<tr data-source-pointer="/validation/p2_stability/{name}"><th scope="row"><code>{h(name)}</code></th>'
        f'<td>{metric_cell(p2_stability[name], "/validation/p2_stability/" + name, 4)}</td><td>{h(p2_stability[name]["reason"] or "—")}</td></tr>'
        for name in (
            "valid_fit_rate", "direction_frequency", "dominant_family_pair_frequency",
            "boundary_width_fraction", "edge_hit_rate", "fallback_rate", "flat_profile_rate",
            "multiple_near_optima_rate", "collapse_rate", "degeneracy_rate", "hard_failure_rate",
        )
    )
    repetition_rows = "".join(
        f'<tr data-source-pointer="/validation/split_sensitivity/repetition_distribution/{index}">'
        f'<th scope="row">{item["repetition"]}</th><td>{h(item["status"])}</td>'
        f'<td>{h(fnum(item["rel_mse_uplift"], 4) if item["rel_mse_uplift"] is not None else "NA — " + str(item["reason"]))}</td></tr>'
        for index, item in enumerate(split_sensitivity["repetition_distribution"])
    ) or '<tr data-source-pointer="/validation/split_sensitivity/repetition_distribution"><td colspan="3">Нет repetition-level estimates</td></tr>'
    artifact_source_pointers = {
        "diagnostic-appearances.csv": "/diagnostics/oof/appearance_export",
        "diagnostic-scale-trace.csv": "/diagnostics/oof/scale_trace_export",
        "influence-grid.csv": "/diagnostics/influence/grid_export",
        "p2-stability-outer-fits.csv": "/validation/p2_stability/outer_fit_export",
    }
    artifact_rows = "".join(
        f'<tr data-source-pointer="{h(artifact_source_pointers.get(item["path"], f"/artifacts/{index}"))}"><th scope="row"><a href="{h(item["path"])}">{h(item["path"])}</a></th>'
        f'<td>{h(item["media_type"])}</td><td>{item["size"]}</td><td><code>{h(item["sha256"])}</code></td></tr>'
        for index, item in enumerate(report["artifacts"])
    )
    reason_codes_text = ", ".join(report["decision"]["related_reason_codes"])
    invalid_counts_text = ", ".join(
        f"{code}={count}" for code, count in sorted(report["input"]["invalid_reason_counts"].items())
    ) or "нет"
    split_hash_text = ", ".join(
        f'rep {item["repetition"]}: {item["sha256"]}' for item in report["validation"]["split_id_hashes"]
    ) or "нет — validation unavailable"
    breakpoint_text = (
        f'values={h(p2_stability["breakpoint_distribution"])}; central-80%={h(p2_stability["breakpoint_central80_interval"])}'
        if p2_stability["breakpoint_distribution"] else f'values=NA; central-80%=NA — {h(p2_stability["reason"] or "BREAKPOINT_DISTRIBUTION_UNAVAILABLE")}'
    )

    return f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{h(CSP)}"><title>{h(report['report_id'])} — прототип отчёта</title><style>{REPORT_CSS}</style></head>
<body data-prototype-only="true" data-fixture="{h(report['report_id'])}" data-recommendation-status="{h(status['recommendation_status'])}">
<a class="skip" href="#decision">К итогу решения</a>
<div class="prototype" role="note">PROTOTYPE — синтетический fixture — это не проведённая калибровка и не production-отчёт</div>
<header><p class="eyebrow">Аппроксимация калибровочной зависимости y=f(x)</p><h1>{h(recommendation_text)}</h1>
<p>{h(report['decision']['reason'])}</p><nav aria-label="Разделы отчёта"><ul><li><a href="#validation">Качество</a></li><li><a href="#curves">Графики</a></li><li><a href="#diagnostics">Диагностика</a></li><li><a href="#reproducibility">Файлы</a></li></ul></nav></header>
<main>
<section id="decision" aria-labelledby="decision-title"><h2 id="decision-title">1. Итог решения</h2><div class="summary-grid"><div class="card decision" data-source-pointer="/status">
<span class="status {'good' if role else 'bad'}" data-source-pointer="/status/recommendation_status">{h(status['recommendation_status'])}</span><h3>{h(recommendation_text)}</h3>
<p><strong>analysis_status:</strong> <code data-source-pointer="/status/analysis_status">{h(status['analysis_status'])}</code>; <strong>recommendation_status:</strong> <code data-source-pointer="/status/recommendation_status">{h(status['recommendation_status'])}</code>; <strong>decision_state:</strong> <code data-source-pointer="/decision/code">{h(report['decision']['code'])}</code>.</p>
<p><strong>Основной reason:</strong> <code data-source-pointer="/status/decision_reason">{h(status['decision_reason'])}</code> — {h(report['decision']['reason'])}. <strong>Связанные reason codes:</strong> <code data-source-pointer="/decision/related_reason_codes">{h(reason_codes_text)}</code>.</p>
<p><strong>Формула:</strong> <code data-source-pointer="/recommendation/formula_display">{h(rec['formula_display'] or 'NA — no validated recommendation')}</code></p><p><strong>Модель:</strong> {model_link}</p>{recommendation_domain_html}{quality_warning_html}</div>
<div class="card metric"><small>Global primary pooled OOF качества рекомендации</small><strong>R² {r2_cell(rec['global_primary_r2_oos'], '/recommendation/global_primary_r2_oos')}</strong><p>RMSE {metric_cell(rec['global_primary_rmse_oof'], '/recommendation/global_primary_rmse_oof', 3)} {h(rec['global_primary_rmse_oof']['unit'])}<br>MAE {metric_cell(rec['global_primary_mae_oof'], '/recommendation/global_primary_mae_oof', 3)} {h(rec['global_primary_mae_oof']['unit'])}</p></div></div>{warning_blocks(report)}{failure_blocks(report)}</section>

<section id="data" aria-labelledby="data-title"><h2 id="data-title">2. Данные и обработка</h2><div class="metric-grid">
<div class="card metric"><small>Вход / использовано / пропущено</small><strong data-source-pointer="/input">{report['input']['n_input']} / {report['input']['n_used']} / {report['input']['n_excluded']}</strong></div>
<div class="card metric"><small>Уникальных x / repeated groups</small><strong>{report['input']['n_unique_x']} / {report['input']['repeated_x_groups']}</strong></div>
<div class="card metric"><small>Домен и единицы</small><strong>[{fnum(report['input']['domain'][0],2)}, {fnum(report['input']['domain'][1],2)}] {h(report['input']['x_unit'])}; y={h(report['input']['y_unit'])}</strong></div></div>
<p><strong>Файл:</strong> <span data-source-pointer="/input/filename">{h(report['input']['filename'])}</span>; media type=<code data-source-pointer="/input/media_type">{h(report['input']['media_type'])}</code>; SHA-256=<code data-source-pointer="/input/input_sha256">{h(report['input']['input_sha256'])}</code>.</p>
<p data-source-pointer="/input/invalid_reason_counts"><strong>Invalid/missing/non-finite по reason:</strong> {h(invalid_counts_text)}. <strong>Exact duplicate records:</strong> <span data-source-pointer="/input/exact_duplicate_records">{report['input']['exact_duplicate_records']}</span>; repeated-x/tie groups={report['input']['repeated_x_groups']}. Одинаковые x остаются отдельными наблюдениями, атомарны и не делятся между folds/сегментами.</p>
<p data-source-pointer="/preprocessing"><strong>Model view:</strong> sorting={h(report['preprocessing']['sorting'])}; row ID={h(report['preprocessing']['row_id_policy'])}; weights={h(report['preprocessing']['weights'])}; automatic_outlier_removal={h(report['preprocessing']['automatic_outlier_removal'])}; ties_atomic={h(report['preprocessing']['ties_atomic_for_validation_and_segmentation'])}. Внутренние affine transforms: <code data-source-pointer="/preprocessing/affine_transforms">{h(json.dumps(report['preprocessing']['affine_transforms'], ensure_ascii=False, sort_keys=True))}</code>.</p>
<details><summary>Все принятые наблюдения</summary><div class="table-wrap" tabindex="0" role="region" aria-label="Таблица наблюдений"><table><caption>Исходные точки; флаги не удаляют строки</caption><thead><tr data-source-pointer="/observations"><th scope="col">row_id</th><th scope="col">x</th><th scope="col">y</th><th scope="col">Флаги</th><th scope="col">Действие</th></tr></thead><tbody>{point_rows}</tbody></table></div></details>
<details><summary>Аудит исключённых строк</summary><div class="table-wrap" tabindex="0" role="region" aria-label="Исключённые строки"><table><caption>Raw values только в escaped HTML/JSONL; не в CSV наблюдений</caption><thead><tr data-source-pointer="/input/exclusions"><th scope="col">source_row_id</th><th scope="col">Причина</th><th scope="col">Raw value</th></tr></thead><tbody>{exclusion_rows}</tbody></table></div></details></section>

<section id="validation" aria-labelledby="validation-title"><h2 id="validation-title">3. Проверочное сравнение процедур</h2><p><strong>Pooled out-of-fold — не показатели final fit.</strong> P1/P2 используют одинаковые appearances, rows и null predictions. Статус P2 procedure включает P1 fallback; статус full-data P2 refit показан отдельно.</p><div class="table-wrap" tabindex="0" role="region" aria-label="Сравнение P1 и P2"><table><caption>Primary OOF comparison</caption><thead><tr data-source-pointer="/validation/procedures"><th scope="col">Процедура</th><th scope="col">Procedure / refit</th><th scope="col">R² OOF</th><th scope="col">RMSE OOF</th><th scope="col">MAE OOF</th><th scope="col">Успешно / всего; repetitions</th><th scope="col">Fallback/failure count, denominator, codes</th><th scope="col">Direction/family stability</th><th scope="col">Роль</th></tr></thead><tbody>{validation_html}</tbody></table></div>
<div class="card"><h3>Paired uplift</h3><p data-source-pointer="/validation/uplift"><code>{h(report['validation']['uplift']['decision'])}</code></p><ul>{uplift_html}</ul><p>Положительный знак означает преимущество deployable P2 procedure; training R² не участвует.</p></div>
<div class="table-wrap" tabindex="0" role="region" aria-label="Decision gates"><table><caption>Все decision gates; observed сравнивается с policy threshold</caption><thead><tr data-source-pointer="/validation/decision_gates"><th scope="col">Gate</th><th scope="col">Статус</th><th scope="col">Observed</th><th scope="col">Threshold</th><th scope="col">Reason</th></tr></thead><tbody>{gate_rows}</tbody></table></div></section>

<section id="curves" aria-labelledby="curves-title"><h2 id="curves-title">4. Графическое сравнение кандидатов</h2><p>Одни и те же {report['input']['n_used']} точек, оси, ticks и plotting area. Линии — final refit; метрики выше — OOF.</p><div class="plot-grid">
<figure data-source-pointer="/refit/p1/plot"><img src="figures/one-function.svg" alt="{h(p1_alt)}"><figcaption><strong>P1 · {h(refit1['status'])}</strong><p class="figure-long">Оси x={h(report['input']['x_unit'])}, y={h(report['input']['y_unit'])}; n={report['input']['n_used']}; {"сертифицированная линия ограничена observed domain" if refit1['status'] == 'certified' else "линия отсутствует"}. Треугольник=большой OOF-остаток; ромб=refit influence; звезда=оба.</p></figcaption></figure>
<figure data-source-pointer="/refit/p2/plot"><img src="figures/two-segment.svg" alt="{h(p2_alt)}"><figcaption><strong>P2 · {h(refit2['status'])}</strong><p class="figure-long">{("Ветви обрезаны своими интервалами; красная граница штриховая и подписана." if refit2['status'] == 'certified' else "Ветви и c отсутствуют; typed reason показан внутри панели.")}</p></figcaption></figure></div></section>

<section id="refit" aria-labelledby="refit-title"><h2 id="refit-title">5. Формулы final refit</h2><p><strong>Описательно / in-sample — не OOF quality и не основание выбора P1/P2.</strong></p><div class="model-grid"><article class="card" data-source-pointer="/refit/p1"><h3>P1 · {h(refit1['status'])}{(" · " + h(refit1['family_id'])) if refit1['family_id'] else ""}</h3><p>{p1_formula_html}</p><p>{p1_refit_details}</p><p>R²/RMSE/MAE fit,all: {r2_cell(refit1['r2_fit_all'], '/refit/p1/r2_fit_all')} / {metric_cell(refit1['rmse_fit_all'], '/refit/p1/rmse_fit_all')} / {metric_cell(refit1['mae_fit_all'], '/refit/p1/mae_fit_all')}; n={h(refit1['n'] if refit1['n'] is not None else 'NA')}; n_unique_x={h(refit1['n_unique_x'] if refit1['n_unique_x'] is not None else 'NA')}.</p></article>
<article class="card" data-source-pointer="/refit/p2"><h3>P2 · {h(refit2['status'])}</h3><p>{p2_formula}</p><p>{p2_refit_details}</p><p>c={p2_boundary}; membership=<code>{h(refit2.get('membership') or 'NA')}</code>; shared μ={h(refit2.get('shared_mu') if refit2.get('shared_mu') is not None else 'NA')}; continuity residual={h(refit2.get('continuity_residual') if refit2.get('continuity_residual') is not None else 'NA')}.</p><p>{p2_size_text}</p><p>R²/RMSE/MAE fit all: {r2_cell(refit2['r2_fit_all'], '/refit/p2/r2_fit_all')} / {metric_cell(refit2['rmse_fit_all'], '/refit/p2/rmse_fit_all')} / {metric_cell(refit2['mae_fit_all'], '/refit/p2/mae_fit_all')}; n={h(refit2['n'] if refit2['n'] is not None else 'NA')}; n_unique_x={h(refit2['n_unique_x'] if refit2['n_unique_x'] is not None else 'NA')}.</p><p>Left local R²/RMSE/MAE: {r2_cell(refit2['left']['r2_fit'], '/refit/p2/left/r2_fit')} / {metric_cell(refit2['left']['rmse_fit'], '/refit/p2/left/rmse_fit')} / {metric_cell(refit2['left']['mae_fit'], '/refit/p2/left/mae_fit')}; right: {r2_cell(refit2['right']['r2_fit'], '/refit/p2/right/r2_fit')} / {metric_cell(refit2['right']['rmse_fit'], '/refit/p2/right/rmse_fit')} / {metric_cell(refit2['right']['mae_fit'], '/refit/p2/right/mae_fit')}.</p><p class="muted">Локальные R² не усредняются и не запускают product threshold.</p></article></div><div class="card" data-source-pointer="/refit/p2/optimizer"><h3>Optimizer и воспроизводимый trace</h3><p>{optimizer_text}</p></div></section>

<section id="diagnostics" aria-labelledby="diag-title"><h2 id="diag-title">6. Проблемные наблюдения и диагностика</h2>
<div class="card" data-source-pointer="/diagnostics/oof"><strong>OOF diagnostics: {h(diagnostics['oof']['status'])}</strong><p>Scale={h(diagnostics['oof']['scale_method'])}: finite-sample Qn first, MAD only when Qn is impossible; actual estimator is typed per row/trace. Scope={h(diagnostics['oof']['scale_scope'])}, threshold |z|={diagnostics['oof']['threshold']}. Флаги только <code>review_only</code> и не меняют fit.</p></div>
<figure data-source-pointer="/diagnostics/oof/plot"><img src="figures/residuals.svg" alt="Четыре отдельные панели: OOF residual против x и prediction, абсолютный z с порогом 3.5, затем sensitivity финального refit"><figcaption>OOF residual, robust z и final-refit influence используют разные series и подписи. При недоступном OOF остаётся typed panel; descriptive refit не выдаётся за OOF.</figcaption></figure>
<details open><summary>Полная row-level diagnostic table</summary><div class="table-wrap" tabindex="0" role="region" aria-label="Row-level diagnostics"><table><caption>Raw OOF residual, outer-train scale, z/score/rate и отдельно descriptive refit/influence</caption><thead><tr data-source-pointer="/diagnostics/rows"><th scope="col">row_id</th><th scope="col">OOF residual</th><th scope="col">scale</th><th scope="col">z median</th><th scope="col">score</th><th scope="col">flag rate</th><th scope="col">appearances</th><th scope="col">refit residual</th><th scope="col">Dmax</th><th scope="col">Drms</th><th scope="col">Δc</th><th scope="col">typed flags</th><th scope="col">action</th></tr></thead><tbody>{diagnostic_rows}</tbody></table></div></details>
<details><summary>Same-x groups и pure-error decomposition</summary><div class="table-wrap" tabindex="0" role="region" aria-label="Same-x group diagnostics"><table><caption>Все x-группы; repeated rows остаются отдельными наблюдениями</caption><thead><tr data-source-pointer="/diagnostics/repeated_x_groups"><th scope="col">x_group_id</th><th scope="col">n</th><th scope="col">mean y</th><th scope="col">median y</th><th scope="col">median OOF residual</th><th scope="col">within MAD</th><th scope="col">positive share</th><th scope="col">negative share</th><th scope="col">group flag</th></tr></thead><tbody>{repeated_rows}</tbody></table></div><p class="card" data-source-pointer="/diagnostics/pure_error_lack_of_fit">{decomposition_text}</p></details>
<div class="table-wrap" tabindex="0" role="region" aria-label="Pattern diagnostics"><table><caption>Pattern triggers — диагностические сигналы, не formal tests</caption><thead><tr data-source-pointer="/diagnostics/patterns"><th scope="col">Pattern</th><th scope="col">Status</th><th scope="col">Threshold</th><th scope="col">Reason</th><th scope="col">Action</th></tr></thead><tbody>{pattern_rows}</tbody></table></div>
<p class="card" data-source-pointer="/diagnostics/influence">Influence: {h(diagnostics['influence']['status'])}; full-procedure leave-one-x-group-out; groups={diagnostics['influence']['group_count']}; flagged={diagnostics['influence']['flagged_group_count']}; grid={diagnostics['influence']['evaluation_grid_size']}. Dmax/Drms/Δc и изменения метрик находятся в row table и canonical JSON.</p></section>

<section id="stability" aria-labelledby="stability-title"><h2 id="stability-title">7. Устойчивость и неопределённость</h2>
<div class="card" data-source-pointer="/validation"><h3>Validation design</h3><p>outer={h(report['validation']['outer_fold_policy'])}; inner={h(report['validation']['inner_fold_policy'])}; repetitions={report['validation']['repetitions']}; group key=<code>{h(report['validation']['group_key'])}</code>.</p><details><summary>Hashes split IDs</summary><p class="code" data-source-pointer="/validation/split_id_hashes">{h(split_hash_text)}</p></details></div>
<div class="summary-grid"><div class="card" data-source-pointer="/validation/split_sensitivity"><h3>Split sensitivity</h3><p>{h(split_sensitivity['status'])}; p10={metric_cell(split_sensitivity['p10'], '/validation/split_sensitivity/p10', 4)}. Это distribution по repetitions, не confidence interval.</p></div><div class="card" data-source-pointer="/validation/bootstrap_stability"><h3>Full-pipeline bootstrap</h3><p>{bootstrap_text}</p><p>Percentiles до simulation calibration не называются confidence interval.</p></div></div>
<details><summary>Repetition-level uplift distribution</summary><div class="table-wrap"><table><caption>Split sensitivity по repetitions</caption><thead><tr data-source-pointer="/validation/split_sensitivity/repetition_distribution"><th scope="col">Repetition</th><th scope="col">Status</th><th scope="col">rel MSE uplift</th></tr></thead><tbody>{repetition_rows}</tbody></table></div></details>
<div class="card" data-source-pointer="/validation/p2_stability"><p>Dominant canonical pair=<code data-source-pointer="/validation/p2_stability/dominant_family_pair">{h(p2_stability['dominant_family_pair'] or 'NA')}</code>; breakpoint distribution: {breakpoint_text}. Sensitivity estimands=<code data-source-pointer="/validation/p2_stability/sensitivity_estimands">{h(p2_stability['sensitivity_estimands'])}</code>; policy SHA-256=<code data-source-pointer="/validation/p2_stability/policy_sha256">{h(p2_stability['policy_sha256'])}</code>.</p></div>
<div class="table-wrap" tabindex="0" role="region" aria-label="P2 stability"><table><caption>P2 valid/fallback/failure, shape, boundary и failure frequencies</caption><thead><tr data-source-pointer="/validation/p2_stability"><th scope="col">Metric</th><th scope="col">Observed</th><th scope="col">Reason</th></tr></thead><tbody>{p2_stability_rows}</tbody></table></div></section>

<section id="reproducibility" aria-labelledby="repro-title"><h2 id="repro-title">8. Воспроизводимость и файлы</h2>
<p class="code" data-source-pointer="/provenance">analysis_id={h(report['provenance']['analysis_id'])}<br>report_id={h(report['report_id'])}; generated={h(report['generated_at'])}; timezone={h(report['provenance']['timezone'])}<br>policy={h(report['provenance']['policy_version'])}; registry={h(report['provenance']['registry_version'])}; retained registry artifact scope={h(report['provenance']['hash_payloads']['registry']['artifact_scope'])}; seed={report['provenance']['base_seed']}<br>input={h(report['provenance']['input_sha256'])}; request={h(report['provenance']['request_sha256'])}; policy={h(report['provenance']['policy_sha256'])}; registry={h(report['provenance']['registry_sha256'])}; source={h(report['provenance']['source_sha256'])}; dependency-lock={h(report['provenance']['dependency_lock_sha256'])}<br>backend={h(report['provenance']['solver_backend'])}; environment={h(report['provenance']['environment'])}; threads={h(report['provenance']['thread_policy'])}; numerical tolerances={h(report['provenance']['numerical_tolerances'])}<br>work={h(report['provenance']['work_counts'])}; fallback/failure={h(report['provenance']['fallback_failure_counts'])}; certificates={h(report['provenance']['certificate_versions'])}</p>
<p data-source-pointer="/refit">Model hashes: P1 structure/instance=<code>{h(refit1['model_structure_hash'] or 'NA')}</code> / <code>{h(refit1['model_instance_hash'] or 'NA')}</code>; P2=<code>{h(refit2['model_structure_hash'] or 'NA')}</code> / <code>{h(refit2['model_instance_hash'] or 'NA')}</code>.</p>
<p><a href="report.json" data-source-pointer="/provenance">Canonical report.json</a>; <a href="provenance.json" data-source-pointer="/provenance">provenance.json</a>; <a href="report.schema.json" data-source-pointer="/artifacts">report schema</a>; <a href="prototype-manifest.json" data-source-pointer="/artifacts">prototype manifest, включая hashes report.json/report.html</a>. <strong>Prototype boundary:</strong> evidence-only bundle intentionally не создаёт production <code>recommended-model.json</code>/<code>predict</code>/<code>COMPLETE</code> и оставляет OOF/influence/input-audit exports в несжатой prototype-упаковке; exact delivery tree — явный <code>NOT RUN</code> следующего тикета.</p>
<div class="table-wrap"><table><caption>Non-circular content manifest; self/report HTML entries находятся в prototype-manifest.json</caption><thead><tr data-source-pointer="/artifacts"><th scope="col">Relative path</th><th scope="col">Media type</th><th scope="col">Size, bytes</th><th scope="col">SHA-256</th></tr></thead><tbody>{artifact_rows}</tbody></table></div><p class="warning" data-source-pointer="/artifacts">Отчёт содержит x, y, row IDs, остатки и influence. Production bundle считается чувствительным.</p></section>
</main><footer>PROTOTYPE ONLY · {h(report['report_id'])} · <span class="pointer">schema={h(report['schema_version'])}</span></footer></body></html>'''


def report_schema() -> dict:
    metric_ref = {"$ref": "#/$defs/metric"}
    nullable_string = {"type": ["string", "null"]}
    nullable_number = {"type": ["number", "null"]}
    domain = {
        "oneOf": [
            {"type": "null"},
            {"type": "array", "prefixItems": [{"type": "number"}, {"type": "number"}], "minItems": 2, "maxItems": 2},
        ]
    }

    def decision_gate_schema(gate_id: str, threshold: float | str, comparator: str) -> dict:
        if comparator == "gte":
            passed_observed = {"type": "number", "minimum": threshold}
            failed_observed = {"type": "number", "exclusiveMaximum": threshold}
        elif comparator == "gt":
            passed_observed = {"type": "number", "exclusiveMinimum": threshold}
            failed_observed = {"type": "number", "maximum": threshold}
        elif comparator == "lte":
            passed_observed = {"type": "number", "maximum": threshold}
            failed_observed = {"type": "number", "exclusiveMinimum": threshold}
        elif comparator == "eq":
            passed_observed = {"const": threshold}
            failed_observed = {"type": "string", "not": {"const": threshold}}
        else:
            raise ValueError(f"unsupported gate comparator: {comparator}")
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "policy_version", "threshold", "observed", "status", "reason"],
            "properties": {
                "id": {"const": gate_id},
                "policy_version": {"const": "prototype-policy-v1"},
                "threshold": {"const": threshold},
                "observed": {"type": ["number", "string", "null"]},
                "status": {"enum": ["pass", "fail", "unavailable"]},
                "reason": {"type": "string", "minLength": 1},
            },
            "allOf": [
                {"if": {"properties": {"status": {"const": "pass"}}}, "then": {"properties": {"observed": passed_observed}}},
                {"if": {"properties": {"status": {"const": "fail"}}}, "then": {"properties": {"observed": failed_observed}}},
                {"if": {"properties": {"status": {"const": "unavailable"}}}, "then": {"properties": {"observed": {"type": "null"}}}},
            ],
        }

    gate_schemas = [
        decision_gate_schema("PRACTICAL_REL_MSE_UPLIFT", 0.10, "gte"),
        decision_gate_schema("POSITIVE_REPETITION_SHARE", 0.90, "gte"),
        decision_gate_schema("SPLIT_SENSITIVITY_P10", 0.0, "gte"),
        decision_gate_schema("BOOTSTRAP_STABILITY_LOWER", 0.05, "gte"),
        decision_gate_schema("DELTA_RMSE_POSITIVE", 0.0, "gt"),
        decision_gate_schema("MAE_NO_HARM_RATIO", 1.02, "lte"),
        decision_gate_schema("P2_VALID_FIT_RATE", 0.90, "gte"),
        decision_gate_schema("P2_DIRECTION_FREQUENCY", 0.80, "gte"),
        decision_gate_schema("P2_DOMINANT_PAIR_FREQUENCY", 0.60, "gte"),
        decision_gate_schema("P2_BOUNDARY_WIDTH_FRACTION", 0.25, "lte"),
        decision_gate_schema("P2_EDGE_HIT_RATE", 0.20, "lte"),
        decision_gate_schema("P2_DEGENERACY_RATE", 0.10, "lte"),
        decision_gate_schema("P2_HARD_FAILURE_RATE", 0.10, "lte"),
        decision_gate_schema("P2_FULL_REFIT_CERTIFIED", "certified", "eq"),
    ]
    pass_gate_schemas = [
        {"type": "object", "required": ["status"], "properties": {"status": {"const": "pass"}}}
        for _ in gate_schemas
    ]

    def gate_status_vector(required: dict[int, str]) -> dict:
        return {
            "prefixItems": [
                {"type": "object", "properties": {"status": {"const": required[index]}}}
                if index in required else {}
                for index in range(len(gate_schemas))
            ]
        }

    def decision_state_branch(code: str, role: str | None, analysis_status: str) -> dict:
        recommendation_status = (
            "TWO_RECOMMENDED" if role == "two"
            else "ONE_RECOMMENDED" if role == "one"
            else "NO_VALIDATED_RECOMMENDATION"
        )
        branch = {
            "properties": {
                "status": {"properties": {
                    "analysis_status": {"const": analysis_status},
                    "recommendation_status": {"const": recommendation_status},
                    "decision_reason": {"const": code},
                }},
                "decision": {"properties": {
                    "code": {"const": code},
                    "recommended_procedure": {"const": role},
                }},
                "recommendation": {"properties": {"role": {"const": role}}},
                "validation": {"properties": {"uplift": {"properties": {"decision": {"const": code}}}}},
            }
        }
        if code == "DESCRIPTIVE_ONLY":
            branch["properties"]["validation"]["properties"].update({
                "repetitions": {"const": 0},
                "procedures": {"properties": {
                    "p1": {"properties": {"status": {"const": "unavailable"}}},
                    "p2": {"properties": {"status": {"const": "unavailable"}}},
                }},
            })
        if code in {
            "NO_UPLIFT_OR_HARM",
            "STATISTICAL_ONLY_SMALL",
            "PRACTICALLY_PROMISING_UNCERTAIN",
            "UNSTABLE_SELECTION",
        }:
            branch["properties"]["refit"] = {
                "properties": {"p2": {"properties": {"status": {"const": "certified"}}}}
            }
            branch["properties"]["validation"]["properties"].setdefault("procedures", {
                "properties": {"p2": {"properties": {"full_data_refit_status": {"const": "certified"}}}}
            })
        if code == "NO_UPLIFT_OR_HARM":
            branch["properties"]["validation"]["properties"]["decision_gates"] = gate_status_vector({4: "fail"})
        if code == "STATISTICAL_ONLY_SMALL":
            branch["properties"]["validation"]["properties"]["decision_gates"] = gate_status_vector({0: "fail", 4: "pass"})
        if code == "PRACTICALLY_PROMISING_UNCERTAIN":
            branch["properties"]["validation"]["properties"]["decision_gates"] = gate_status_vector({
                0: "pass", 1: "pass", 2: "pass", 3: "unavailable", 4: "pass", 5: "pass",
            })
            branch["properties"]["validation"]["properties"]["bootstrap_stability"] = {
                "properties": {"status": {"const": "unavailable"}}
            }
        if code == "UNSTABLE_SELECTION":
            unstable_gate_rule = gate_status_vector({index: "pass" for index in range(6)})
            unstable_gate_rule.update({
                "contains": {
                    "type": "object",
                    "properties": {
                        "id": {"enum": [item[0] for item in (
                            ("P2_VALID_FIT_RATE",), ("P2_DIRECTION_FREQUENCY",),
                            ("P2_DOMINANT_PAIR_FREQUENCY",), ("P2_BOUNDARY_WIDTH_FRACTION",),
                            ("P2_EDGE_HIT_RATE",), ("P2_DEGENERACY_RATE",),
                            ("P2_HARD_FAILURE_RATE",),
                        )]},
                        "status": {"enum": ["fail", "unavailable"]},
                    },
                },
                "minContains": 1,
            })
            branch["properties"]["validation"]["properties"]["decision_gates"] = unstable_gate_rule
        if code == "NO_VALID_TWO_SEGMENT":
            branch["properties"]["validation"]["properties"].update({
                "procedures": {"properties": {"p2": {"properties": {
                    "status": {"enum": ["fallback", "unavailable", "failed"]},
                    "full_data_refit_status": {"const": "unavailable"},
                }}}},
                "decision_gates": gate_status_vector({13: "fail"}),
            })
            branch["properties"]["refit"] = {
                "properties": {"p2": {"properties": {"status": {"const": "unavailable"}}}}
            }
        if code == "PIPELINE_FAILURE":
            branch["properties"]["failures"] = {
                "contains": {
                    "type": "object",
                    "required": ["severity", "recommendation_effect"],
                    "properties": {
                        "severity": {"const": "error"},
                        "recommendation_effect": {"const": "no_recommendation"},
                    },
                },
                "minContains": 1,
            }
        return branch

    decision_state_branches = [
        decision_state_branch("CLEAR_PRACTICAL_UPLIFT", "two", "SUCCEEDED"),
        decision_state_branch("NO_UPLIFT_OR_HARM", "one", "SUCCEEDED"),
        decision_state_branch("STATISTICAL_ONLY_SMALL", "one", "SUCCEEDED"),
        decision_state_branch("PRACTICALLY_PROMISING_UNCERTAIN", "one", "SUCCEEDED"),
        decision_state_branch("UNSTABLE_SELECTION", "one", "SUCCEEDED"),
        decision_state_branch("NO_VALID_TWO_SEGMENT", "one", "SUCCEEDED"),
        decision_state_branch("DESCRIPTIVE_ONLY", None, "NO_RECOMMENDATION"),
        decision_state_branch("PIPELINE_FAILURE", None, "FAILED"),
    ]

    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:prototype:monotone-calibration-report:1",
        "title": "Synthetic prototype calibration report",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version", "report_type", "prototype_only", "prototype_notice",
            "report_id", "generated_at", "status", "decision", "recommendation", "input",
            "preprocessing", "validation", "refit", "diagnostics", "warnings",
            "failures", "provenance", "visualization", "observations", "artifacts",
        ],
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION},
            "report_type": {"const": "curve_calibration_regression"},
            "prototype_only": {"const": True},
            "prototype_notice": {"type": "string", "minLength": 1},
            "report_id": {"type": "string", "pattern": "^prototype-[a-z0-9_]+$"},
            "generated_at": {"type": "string", "format": "date-time"},
            "status": {
                "type": "object",
                "required": ["bundle_status", "analysis_status", "recommendation_status", "decision_reason"],
                "additionalProperties": False,
                "properties": {
                    "bundle_status": {"const": "PROTOTYPE_NOT_PUBLISHED"},
                    "analysis_status": {"enum": ["SUCCEEDED", "NO_RECOMMENDATION", "FAILED"]},
                    "recommendation_status": {"enum": ["ONE_RECOMMENDED", "TWO_RECOMMENDED", "NO_VALIDATED_RECOMMENDATION"]},
                    "decision_reason": {"type": "string", "minLength": 1},
                },
            },
            "decision": {
                "type": "object",
                "required": ["code", "reason", "recommended_procedure", "related_reason_codes"],
                "additionalProperties": False,
                "properties": {
                    "code": {"type": "string", "minLength": 1},
                    "reason": {"type": "string", "minLength": 1},
                    "recommended_procedure": {"enum": ["one", "two", None]},
                    "related_reason_codes": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                },
            },
            "recommendation": {
                "type": "object",
                "required": [
                    "role", "model_preview_path", "formula_display", "domain",
                    "global_primary_r2_oos", "global_primary_rmse_oof",
                    "global_primary_mae_oof", "extrapolation",
                ],
                "additionalProperties": False,
                "properties": {
                    "role": {"enum": ["one", "two", None]},
                    "model_preview_path": nullable_string,
                    "formula_display": nullable_string,
                    "domain": domain,
                    "global_primary_r2_oos": metric_ref,
                    "global_primary_rmse_oof": metric_ref,
                    "global_primary_mae_oof": metric_ref,
                    "extrapolation": {"enum": ["forbidden", None]},
                },
            },
            "input": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "filename", "media_type", "n_input", "n_used", "n_excluded",
                    "n_unique_x", "repeated_x_groups", "exact_duplicate_records",
                    "invalid_reason_counts", "x_unit", "y_unit", "domain", "input_sha256", "exclusions",
                ],
                "properties": {
                    "filename": {"type": "string"},
                    "media_type": {"const": "text/csv"},
                    "n_input": {"type": "integer", "minimum": 0},
                    "n_used": {"type": "integer", "minimum": 0},
                    "n_excluded": {"type": "integer", "minimum": 0},
                    "n_unique_x": {"type": "integer", "minimum": 0},
                    "repeated_x_groups": {"type": "integer", "minimum": 0},
                    "exact_duplicate_records": {"type": "integer", "minimum": 0},
                    "invalid_reason_counts": {"type": "object", "additionalProperties": {"type": "integer", "minimum": 1}},
                    "x_unit": {"type": "string"},
                    "y_unit": {"type": "string"},
                    "domain": {"type": "array", "prefixItems": [{"type": "number"}, {"type": "number"}], "minItems": 2, "maxItems": 2},
                    "input_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "exclusions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["source_row_id", "code", "raw_value"],
                            "properties": {"source_row_id": {"type": "string"}, "code": {"type": "string"}, "raw_value": {"type": "string"}},
                        },
                    },
                },
            },
            "preprocessing": {
                "type": "object",
                "additionalProperties": False,
                "required": ["sorting", "row_id_policy", "weights", "invalid_rows", "automatic_outlier_removal", "ties_atomic_for_validation_and_segmentation", "affine_transforms"],
                "properties": {
                    "sorting": {"const": "stable_by_x_then_row_id"},
                    "row_id_policy": {"const": "preserve_stable_logical_row_id"},
                    "weights": {"const": "equal"},
                    "invalid_rows": {"const": "skipped_with_audit"},
                    "automatic_outlier_removal": {"const": False},
                    "ties_atomic_for_validation_and_segmentation": {"const": True},
                    "affine_transforms": {
                        "type": "object", "additionalProperties": False,
                        "required": ["status", "reason", "x", "y"],
                        "properties": {
                            "status": {"enum": ["available", "unavailable"]},
                            "reason": nullable_string,
                            "x": {"$ref": "#/$defs/admission_transform"},
                            "y": {"$ref": "#/$defs/admission_transform"},
                        },
                    },
                },
            },
            "validation": {"$ref": "#/$defs/validation"},
            "refit": {
                "type": "object",
                "additionalProperties": False,
                "required": ["p1", "p2"],
                "properties": {"p1": {"$ref": "#/$defs/refit_p1"}, "p2": {"$ref": "#/$defs/refit_p2"}},
            },
            "diagnostics": {"$ref": "#/$defs/diagnostics"},
            "warnings": {"type": "array", "items": {"$ref": "#/$defs/message"}},
            "failures": {"type": "array", "items": {"$ref": "#/$defs/message"}},
            "provenance": {
                "type": "object",
                "additionalProperties": False,
                "required": ["analysis_id", "execution_environment_id", "policy_version", "registry_version", "input_sha256", "request_sha256", "policy_sha256", "registry_sha256", "source_sha256", "dependency_lock_sha256", "solver_backend", "environment", "thread_policy", "base_seed", "numerical_tolerances", "work_counts", "fallback_failure_counts", "certificate_versions", "timezone", "hash_payloads"],
                "properties": {
                    "analysis_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "execution_environment_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "policy_version": {"type": "string"},
                    "registry_version": {"type": "string"},
                    "input_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "request_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "policy_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "registry_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "source_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "dependency_lock_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "solver_backend": {"type": "string", "minLength": 1},
                    "environment": {
                        "type": "object", "additionalProperties": False,
                        "required": ["status", "python_implementation", "python_version", "platform", "thread_policy"],
                        "properties": {
                            "status": {"const": "prototype_static_not_runtime_benchmark"},
                            "python_implementation": {"const": "CPython"},
                            "python_version": {"type": "string", "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+"},
                            "platform": {"type": "string", "minLength": 1},
                            "thread_policy": {"const": "single_process_single_thread"},
                        },
                    },
                    "thread_policy": {"type": "string", "minLength": 1},
                    "base_seed": {"type": "integer"},
                    "numerical_tolerances": {
                        "type": "object", "additionalProperties": False,
                        "required": ["absolute_reconciliation", "continuity", "derivative"],
                        "properties": {
                            "absolute_reconciliation": {"type": "number", "exclusiveMinimum": 0},
                            "continuity": {"type": "number", "exclusiveMinimum": 0},
                            "derivative": {"type": "number", "exclusiveMinimum": 0},
                        },
                    },
                    "work_counts": {"type": "object", "minProperties": 1, "additionalProperties": {"type": "integer", "minimum": 0}},
                    "fallback_failure_counts": {"type": "object", "minProperties": 1, "additionalProperties": {"type": "integer", "minimum": 0}},
                    "certificate_versions": {"type": "object", "minProperties": 1, "additionalProperties": {"type": "string", "minLength": 1}},
                    "timezone": {"const": "Europe/Moscow"},
                    "hash_payloads": {
                        "type": "object", "additionalProperties": False,
                        "required": ["policy", "registry", "source_manifest", "dependency_manifest", "input", "request_metadata"],
                        "properties": {
                            "policy": {"type": "object"},
                            "registry": {"type": "object"},
                            "source_manifest": {"type": "object"},
                            "dependency_manifest": {"type": "object"},
                            "input": {"type": "object"},
                            "request_metadata": {"type": "object"},
                        },
                    },
                },
            },
            "visualization": {"$ref": "#/$defs/visualization"},
            "observations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "row_id", "source_row_id", "x", "y", "x_group_id", "input_status",
                        "flags", "action", "pred_null_oof", "pred_one_oof", "resid_one_oof",
                        "pred_two_oof", "resid_two_oof", "p2_oof_fallback_code",
                        "pred_one_refit", "pred_two_refit", "segment_refit",
                        "scale_one_oof", "scale_one_reason", "z_one_oof",
                        "scale_two_oof", "scale_two_reason", "z_two_oof",
                    ],
                    "properties": {
                        "row_id": {"type": "string"}, "source_row_id": {"type": "string"},
                        "x": {"type": "number"}, "y": {"type": "number"}, "x_group_id": {"type": "string"},
                        "input_status": {"const": "USED"},
                        "flags": {"type": "array", "uniqueItems": True, "items": {"enum": ["LARGE_OOF_RESIDUAL", "HIGH_REFIT_INFLUENCE"]}},
                        "action": {"const": "review_only"},
                        "pred_null_oof": nullable_number, "pred_one_oof": nullable_number,
                        "resid_one_oof": nullable_number, "pred_two_oof": nullable_number,
                        "resid_two_oof": nullable_number, "p2_oof_fallback_code": nullable_string,
                        "pred_one_refit": nullable_number, "pred_two_refit": nullable_number,
                        "segment_refit": {"enum": ["left", "right", None]},
                        "scale_one_oof": nullable_number, "scale_one_reason": nullable_string, "z_one_oof": nullable_number,
                        "scale_two_oof": nullable_number, "scale_two_reason": nullable_string, "z_two_oof": nullable_number,
                    },
                },
            },
            "artifacts": {"type": "array", "uniqueItems": True, "items": {"$ref": "#/$defs/artifact"}},
        },
        "oneOf": decision_state_branches,
        "$defs": {
            "metric": {
                "type": "object",
                "required": ["value", "status", "reason", "scope", "definition", "unit"],
                "additionalProperties": False,
                "properties": {
                    "value": nullable_number,
                    "status": {"enum": ["defined", "undefined"]},
                    "reason": nullable_string,
                    "scope": {"enum": ["oof", "in_sample"]},
                    "definition": {"type": "string", "minLength": 1},
                    "unit": {"type": "string", "minLength": 1},
                },
                "allOf": [
                    {"if": {"properties": {"value": {"type": "null"}}}, "then": {"properties": {"status": {"const": "undefined"}, "reason": {"type": "string", "minLength": 1}}}},
                    {"if": {"properties": {"value": {"type": "number"}}}, "then": {"properties": {"status": {"const": "defined"}, "reason": {"type": "null"}}}},
                ],
            },
            "artifact": {
                "type": "object", "additionalProperties": False, "required": ["path", "media_type", "size", "sha256"],
                "properties": {
                    "path": {"type": "string", "pattern": "^(?!/)(?!.*(?:^|/)\\.\\.(?:/|$))[A-Za-z0-9._/-]+$"},
                    "media_type": {"type": "string", "pattern": "^[a-z0-9.+-]+/[a-z0-9.+-]+$"},
                    "size": {"type": "integer", "minimum": 0},
                    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                },
            },
            "admission_transform": {
                "type": "object", "additionalProperties": False,
                "required": ["kind", "offset", "scale", "target_interval", "invertible"],
                "properties": {
                    "kind": {"enum": ["affine_unit_interval", "affine_center_scale"]},
                    "offset": {"type": "number"},
                    "scale": {"type": "number", "minimum": 0},
                    "target_interval": domain,
                    "invertible": {"type": "boolean"},
                },
            },
            "message": {
                "type": "object", "additionalProperties": False,
                "required": ["code", "scope", "stage", "severity", "reason", "recommendation_effect", "related_json_pointers"],
                "properties": {
                    "code": {"type": "string"}, "scope": {"type": "string"}, "stage": {"type": "string"},
                    "severity": {"type": "string"}, "reason": {"type": "string"}, "recommendation_effect": {"type": "string"},
                    "related_json_pointers": {"type": "array", "items": {"type": "string", "pattern": "^/"}},
                },
            },
            "plot": {
                "type": "object", "additionalProperties": False,
                "required": ["path", "status", "line_present"],
                "properties": {
                    "path": {"type": "string", "pattern": "^figures/[a-z-]+\\.svg$"},
                    "status": {"type": "string"}, "line_present": {"type": "boolean"},
                    "boundary_present": {"type": "boolean"},
                },
            },
            "procedure_p1": {
                "type": "object", "additionalProperties": False,
                "required": ["status", "reason", "r2_oos", "mse_oof", "rmse_oof", "mae_oof", "oof_appearances", "successful_appearances", "direction_frequency", "family_frequency", "fallback_rate"],
                "properties": {
                    "status": {"enum": ["valid", "unavailable", "failed"]}, "reason": nullable_string,
                    "r2_oos": metric_ref, "mse_oof": metric_ref, "rmse_oof": metric_ref, "mae_oof": metric_ref,
                    "oof_appearances": {"type": "integer", "minimum": 0}, "successful_appearances": {"type": "integer", "minimum": 0},
                    "direction_frequency": metric_ref, "family_frequency": metric_ref,
                    "fallback_rate": metric_ref,
                },
                "allOf": [
                    {"if": {"properties": {"status": {"const": "valid"}}}, "then": {"properties": {"reason": {"type": "null"}, "oof_appearances": {"minimum": 1}, "successful_appearances": {"minimum": 1}, "mse_oof": {"properties": {"value": {"type": "number", "minimum": 0}}}, "rmse_oof": {"properties": {"value": {"type": "number", "minimum": 0}}}, "mae_oof": {"properties": {"value": {"type": "number", "minimum": 0}}}, "fallback_rate": {"properties": {"value": {"const": 0.0}}}}}},
                    {"if": {"properties": {"status": {"enum": ["unavailable", "failed"]}}}, "then": {"properties": {"reason": {"type": "string", "minLength": 1}, "oof_appearances": {"const": 0}, "successful_appearances": {"const": 0}, "r2_oos": {"properties": {"value": {"type": "null"}}}, "mse_oof": {"properties": {"value": {"type": "null"}}}, "rmse_oof": {"properties": {"value": {"type": "null"}}}, "mae_oof": {"properties": {"value": {"type": "null"}}}, "fallback_rate": {"properties": {"value": {"type": "null"}}}}}},
                ],
            },
            "procedure_p2": {
                "type": "object", "additionalProperties": False,
                "required": ["status", "attempt_status", "reason", "full_data_refit_status", "r2_oos", "mse_oof", "rmse_oof", "mae_oof", "oof_appearances", "successful_appearances", "fallback_appearances", "fallback_outer_fit_ids", "fallback_reason_counts", "direction_frequency", "family_frequency", "fallback_rate"],
                "properties": {
                    "status": {"enum": ["valid", "fallback", "unavailable", "failed"]},
                    "attempt_status": {"enum": ["succeeded", "partial_p1_fallback", "failed_with_p1_fallback", "unavailable"]},
                    "reason": nullable_string, "full_data_refit_status": {"enum": ["certified", "unavailable"]},
                    "r2_oos": metric_ref, "mse_oof": metric_ref, "rmse_oof": metric_ref, "mae_oof": metric_ref,
                    "oof_appearances": {"type": "integer", "minimum": 0}, "successful_appearances": {"type": "integer", "minimum": 0},
                    "fallback_appearances": {"type": "integer", "minimum": 0},
                    "fallback_outer_fit_ids": {"type": "array", "uniqueItems": True, "items": {"type": "string", "pattern": "^outer-[0-9]{2}-fold-[1-9][0-9]*$"}},
                    "fallback_reason_counts": {"type": "object", "additionalProperties": {"type": "integer", "minimum": 1}},
                    "direction_frequency": metric_ref, "family_frequency": metric_ref,
                    "fallback_rate": metric_ref,
                },
                "allOf": [
                    {"if": {"properties": {"status": {"const": "valid"}}}, "then": {"properties": {"attempt_status": {"const": "succeeded"}, "reason": {"type": "null"}, "oof_appearances": {"minimum": 1}, "successful_appearances": {"minimum": 1}, "fallback_appearances": {"const": 0}, "fallback_outer_fit_ids": {"maxItems": 0}, "fallback_reason_counts": {"maxProperties": 0}, "mse_oof": {"properties": {"value": {"type": "number", "minimum": 0}}}, "rmse_oof": {"properties": {"value": {"type": "number", "minimum": 0}}}, "mae_oof": {"properties": {"value": {"type": "number", "minimum": 0}}}, "fallback_rate": {"properties": {"value": {"const": 0.0}}}}}},
                    {"if": {"properties": {"status": {"const": "fallback"}}}, "then": {"properties": {"attempt_status": {"enum": ["partial_p1_fallback", "failed_with_p1_fallback"]}, "reason": {"type": "string", "minLength": 1}, "oof_appearances": {"minimum": 1}, "successful_appearances": {"minimum": 1}, "fallback_appearances": {"minimum": 1}, "fallback_outer_fit_ids": {"minItems": 1}, "fallback_reason_counts": {"minProperties": 1}, "mse_oof": {"properties": {"value": {"type": "number", "minimum": 0}}}, "rmse_oof": {"properties": {"value": {"type": "number", "minimum": 0}}}, "mae_oof": {"properties": {"value": {"type": "number", "minimum": 0}}}, "fallback_rate": {"properties": {"value": {"type": "number", "exclusiveMinimum": 0, "maximum": 1}}}}}},
                    {"if": {"properties": {"status": {"enum": ["unavailable", "failed"]}}}, "then": {"properties": {"attempt_status": {"const": "unavailable"}, "reason": {"type": "string", "minLength": 1}, "full_data_refit_status": {"const": "unavailable"}, "oof_appearances": {"const": 0}, "successful_appearances": {"const": 0}, "fallback_appearances": {"const": 0}, "fallback_outer_fit_ids": {"maxItems": 0}, "fallback_reason_counts": {"maxProperties": 0}, "r2_oos": {"properties": {"value": {"type": "null"}}}, "mse_oof": {"properties": {"value": {"type": "null"}}}, "rmse_oof": {"properties": {"value": {"type": "null"}}}, "mae_oof": {"properties": {"value": {"type": "null"}}}, "fallback_rate": {"properties": {"value": {"type": "null"}}}}}},
                ],
            },
            "validation": {
                "type": "object", "additionalProperties": False,
                "required": ["estimand", "null_baseline", "outer_inner_folds", "outer_fold_policy", "inner_fold_policy", "group_key", "split_id_hashes", "repetitions", "policy_sha256", "appearance_export", "pooled_null_sse", "procedures", "uplift", "decision_gates", "split_sensitivity", "bootstrap_stability", "p2_stability"],
                "properties": {
                    "estimand": {"type": "string"}, "null_baseline": {"type": "string"}, "outer_inner_folds": nullable_string,
                    "outer_fold_policy": {"type": "string", "minLength": 1},
                    "inner_fold_policy": {"type": "string", "minLength": 1},
                    "group_key": {"const": "x_group_id"},
                    "split_id_hashes": {
                        "type": "array", "uniqueItems": True,
                        "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["repetition", "sha256"],
                            "properties": {
                                "repetition": {"type": "integer", "minimum": 1},
                                "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                            },
                        },
                    },
                    "repetitions": {"type": "integer", "minimum": 0}, "appearance_export": {"const": "oof-appearances.csv"}, "pooled_null_sse": metric_ref,
                    "policy_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "procedures": {"type": "object", "additionalProperties": False, "required": ["p1", "p2"], "properties": {"p1": {"$ref": "#/$defs/procedure_p1"}, "p2": {"$ref": "#/$defs/procedure_p2"}}},
                    "uplift": {
                        "type": "object", "additionalProperties": False,
                        "required": ["delta_mse", "delta_rmse", "delta_mae", "delta_r2", "rel_mse_uplift", "decision", "positive_repetition_share"],
                        "properties": {"delta_mse": metric_ref, "delta_rmse": metric_ref, "delta_mae": metric_ref, "delta_r2": metric_ref, "rel_mse_uplift": metric_ref, "decision": {"type": "string"}, "positive_repetition_share": metric_ref},
                    },
                    "decision_gates": {
                        "type": "array", "minItems": len(gate_schemas), "maxItems": len(gate_schemas),
                        "prefixItems": gate_schemas, "items": False,
                    },
                    "split_sensitivity": {
                        "type": "object", "additionalProperties": False,
                        "required": ["status", "label", "p10", "repetition_distribution"],
                        "properties": {
                            "status": {"enum": ["available", "unavailable"]},
                            "label": {"type": "string"},
                            "p10": metric_ref,
                            "repetition_distribution": {
                                "type": "array",
                                "items": {
                                    "type": "object", "additionalProperties": False,
                                    "required": ["repetition", "rel_mse_uplift", "status", "reason"],
                                    "properties": {
                                        "repetition": {"type": "integer", "minimum": 1},
                                        "rel_mse_uplift": nullable_number,
                                        "status": {"enum": ["defined", "unavailable"]},
                                        "reason": nullable_string,
                                    },
                                },
                            },
                        },
                    },
                    "bootstrap_stability": {"$ref": "#/$defs/bootstrap"},
                    "p2_stability": {
                        "type": "object", "additionalProperties": False,
                        "required": ["status", "valid_fit_rate", "dominant_family_pair_frequency", "dominant_family_pair", "direction_frequency", "outer_fit_ledger", "outer_fit_export", "outer_fit_denominator", "valid_fit_count", "fallback_fit_count", "breakpoint_distribution", "breakpoint_central80_interval", "boundary_width_fraction", "edge_hit_rate", "fallback_rate", "flat_profile_rate", "multiple_near_optima_rate", "collapse_rate", "degeneracy_rate", "hard_failure_rate", "sensitivity_estimands", "policy_sha256", "reason"],
                        "properties": {
                            "status": {"enum": ["available", "unavailable"]},
                            "valid_fit_rate": metric_ref,
                            "dominant_family_pair_frequency": metric_ref,
                            "dominant_family_pair": nullable_string,
                            "direction_frequency": metric_ref,
                            "outer_fit_ledger": {
                                "type": "array",
                                "items": {
                                    "type": "object", "additionalProperties": False,
                                    "required": ["outer_fit_id", "repetition", "outer_fold", "status", "fallback_reason", "direction", "family_pair", "boundary", "edge_hit", "flat_profile", "multiple_near_optima", "collapsed", "hard_failure"],
                                    "properties": {
                                        "outer_fit_id": {"type": "string", "pattern": "^outer-[0-9]{2}-fold-[1-9][0-9]*$"},
                                        "repetition": {"type": "integer", "minimum": 1},
                                        "outer_fold": {"type": "integer", "minimum": 1},
                                        "status": {"enum": ["valid", "fallback"]},
                                        "fallback_reason": nullable_string,
                                        "direction": nullable_string,
                                        "family_pair": nullable_string,
                                        "boundary": nullable_number,
                                        "edge_hit": {"type": ["boolean", "null"]},
                                        "flat_profile": {"type": ["boolean", "null"]},
                                        "multiple_near_optima": {"type": ["boolean", "null"]},
                                        "collapsed": {"type": ["boolean", "null"]},
                                        "hard_failure": {"type": "boolean"},
                                    },
                                },
                            },
                            "outer_fit_export": {"const": "p2-stability-outer-fits.csv"},
                            "outer_fit_denominator": {"type": "integer", "minimum": 0},
                            "valid_fit_count": {"type": "integer", "minimum": 0},
                            "fallback_fit_count": {"type": "integer", "minimum": 0},
                            "breakpoint_distribution": {"type": "array", "items": {"type": "number"}},
                            "breakpoint_central80_interval": domain,
                            "boundary_width_fraction": metric_ref,
                            "edge_hit_rate": metric_ref,
                            "fallback_rate": metric_ref,
                            "flat_profile_rate": metric_ref,
                            "multiple_near_optima_rate": metric_ref,
                            "collapse_rate": metric_ref,
                            "degeneracy_rate": metric_ref,
                            "hard_failure_rate": metric_ref,
                            "sensitivity_estimands": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                            "policy_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                            "reason": nullable_string,
                        },
                    },
                },
            },
            "bootstrap": {
                "type": "object", "additionalProperties": False,
                "required": ["status", "label", "target", "level", "unit", "interval", "resampling_unit", "selection_scope", "percentile_method", "resample_export", "successful_resamples", "requested_resamples", "failed_resamples", "failure_reasons", "reason"],
                "properties": {
                    "status": {"enum": ["available", "unavailable"]}, "label": {"type": "string"},
                    "target": {"const": "full_pipeline_rel_mse_uplift"},
                    "level": {"const": 0.90},
                    "unit": {"const": "fraction"},
                    "interval": {"oneOf": [{"type": "null"}, {"type": "array", "prefixItems": [{"type": "number"}, {"type": "number"}], "minItems": 2, "maxItems": 2}]},
                    "resampling_unit": {"const": "x_group"}, "selection_scope": {"const": "full_pipeline"},
                    "percentile_method": {"type": "string"}, "resample_export": {"const": "bootstrap-resamples.csv"},
                    "successful_resamples": {"type": "integer", "minimum": 0}, "requested_resamples": {"type": "integer", "minimum": 1},
                    "failed_resamples": {"type": "integer", "minimum": 0}, "failure_reasons": {"type": "object", "additionalProperties": {"type": "integer", "minimum": 1}},
                    "reason": nullable_string,
                },
                "allOf": [
                    {"if": {"properties": {"status": {"const": "available"}}}, "then": {"properties": {"interval": {"type": "array"}, "successful_resamples": {"minimum": 160}, "reason": {"type": "null"}}}},
                    {"if": {"properties": {"status": {"const": "unavailable"}}}, "then": {"properties": {"interval": {"type": "null"}, "successful_resamples": {"const": 0}, "reason": {"type": "string", "minLength": 1}}}},
                ],
            },
            "canonical_ast": {
                "type": "object", "additionalProperties": False,
                "required": ["ast_id", "root", "prefix", "node_count"],
                "properties": {
                    "ast_id": {"type": "string", "pattern": "^registry_v1:[a-z0-9_]+$"},
                    "root": {"enum": ["parameter", "add"]},
                    "prefix": {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "^(?:add|multiply|parameter:[a-z]+|variable:t)$"}},
                    "node_count": {"type": "integer", "minimum": 1},
                },
            },
            "affine_transform": {
                "type": "object", "additionalProperties": False,
                "required": ["kind", "offset", "scale", "target_interval", "invertible"],
                "properties": {
                    "kind": {"enum": ["affine_unit_interval", "affine_center_scale"]},
                    "offset": {"type": "number"}, "scale": {"type": "number", "exclusiveMinimum": 0},
                    "target_interval": domain, "invertible": {"const": True},
                },
            },
            "derivative_certificate": {
                "type": "object", "additionalProperties": False,
                "required": ["status", "reason", "method", "family_id", "polynomial_degree_after_simplification", "interval", "direction", "analytic_derivative", "critical_points", "minimum_signed_derivative", "domain_margin", "finite_function", "finite_derivative"],
                "properties": {
                    "status": {"enum": ["PASS", "UNAVAILABLE"]}, "reason": nullable_string,
                    "method": nullable_string, "family_id": nullable_string, "interval": domain,
                    "polynomial_degree_after_simplification": {"type": ["integer", "null"], "minimum": 0, "maximum": 3},
                    "direction": nullable_string, "analytic_derivative": nullable_string,
                    "critical_points": {"oneOf": [{"type": "null"}, {"type": "array", "items": {"type": "number"}}]},
                    "minimum_signed_derivative": nullable_number, "domain_margin": nullable_number,
                    "finite_function": {"type": ["boolean", "null"]}, "finite_derivative": {"type": ["boolean", "null"]},
                },
                "allOf": [
                    {"if": {"properties": {"status": {"const": "PASS"}}}, "then": {"properties": {"reason": {"type": "null"}, "method": {"const": "analytic_family_specific"}, "family_id": {"type": "string"}, "polynomial_degree_after_simplification": {"type": "integer"}, "interval": {"type": "array"}, "direction": {"type": "string"}, "analytic_derivative": {"type": "string"}, "critical_points": {"type": "array"}, "minimum_signed_derivative": {"type": "number", "minimum": 0}, "domain_margin": {"type": "number", "minimum": 0}, "finite_function": {"const": True}, "finite_derivative": {"const": True}}}},
                    {"if": {"properties": {"status": {"const": "UNAVAILABLE"}}}, "then": {"properties": {"reason": {"type": "string", "minLength": 1}, "method": {"type": "null"}, "family_id": {"type": "null"}, "polynomial_degree_after_simplification": {"type": "null"}, "interval": {"type": "null"}, "direction": {"type": "null"}, "analytic_derivative": {"type": "null"}, "critical_points": {"type": "null"}, "minimum_signed_derivative": {"type": "null"}, "domain_margin": {"type": "null"}, "finite_function": {"type": "null"}, "finite_derivative": {"type": "null"}}}},
                ],
            },
            "tie_safe_cell": {
                "type": "object", "additionalProperties": False,
                "required": ["cell_id", "lower_x", "upper_x_exclusive", "left_n", "right_n", "left_share", "right_share", "ties_atomic"],
                "properties": {
                    "cell_id": {"type": "string", "pattern": "^raw-cell-[0-9]{3}$"},
                    "lower_x": {"type": "number"}, "upper_x_exclusive": {"type": "number"},
                    "left_n": {"type": "integer", "minimum": 1}, "right_n": {"type": "integer", "minimum": 1},
                    "left_share": {"type": "number", "minimum": 0.4, "maximum": 0.6},
                    "right_share": {"type": "number", "minimum": 0.4, "maximum": 0.6},
                    "ties_atomic": {"const": True},
                },
            },
            "optimizer": {
                "type": "object", "additionalProperties": False,
                "required": ["strategy", "scope", "status", "reason", "trace_path", "tie_safe_cell_count", "eligible_cell_count", "starts_per_cell", "start_count", "attempt_count", "trace_record_count", "successful_attempt_count", "failed_attempt_count", "evaluation_count", "competing_basin_count", "competing_basin_status", "certificate_result", "reason_codes", "warning_codes"],
                "properties": {
                    "strategy": {"const": "PROFILE_CELLS"}, "scope": {"const": "full_data_p2_refit"},
                    "status": {"enum": ["SUCCEEDED", "COLLAPSED", "FAILED", "NO_FEASIBLE_CELL", "NOT_RUN"]},
                    "reason": nullable_string, "trace_path": {"const": "trace/fit-attempts.jsonl.gz"},
                    "tie_safe_cell_count": {"type": "integer", "minimum": 0}, "eligible_cell_count": {"type": "integer", "minimum": 0},
                    "starts_per_cell": {"type": "integer", "minimum": 0}, "start_count": {"type": "integer", "minimum": 0},
                    "attempt_count": {"type": "integer", "minimum": 0}, "trace_record_count": {"type": "integer", "minimum": 0},
                    "successful_attempt_count": {"type": "integer", "minimum": 0}, "failed_attempt_count": {"type": "integer", "minimum": 0},
                    "evaluation_count": {"type": "integer", "minimum": 0}, "competing_basin_count": {"type": "integer", "minimum": 0},
                    "competing_basin_status": {"enum": ["NONE", "MULTIPLE_NEAR_OPTIMA", "UNAVAILABLE"]},
                    "certificate_result": {"enum": ["PASS", "FAIL", "NOT_RUN"]},
                    "reason_codes": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                    "warning_codes": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                },
                "allOf": [
                    {"if": {"properties": {"status": {"enum": ["SUCCEEDED", "COLLAPSED"]}}}, "then": {"properties": {"attempt_count": {"minimum": 1}, "trace_record_count": {"minimum": 1}, "successful_attempt_count": {"minimum": 1}, "certificate_result": {"const": "PASS"}}}},
                    {"if": {"properties": {"status": {"const": "SUCCEEDED"}}}, "then": {"properties": {"reason": {"type": "null"}, "reason_codes": {"maxItems": 0}, "competing_basin_status": {"enum": ["NONE", "MULTIPLE_NEAR_OPTIMA"]}}}},
                    {"if": {"properties": {"status": {"const": "COLLAPSED"}}}, "then": {"properties": {"reason": {"type": "string", "minLength": 1}, "warning_codes": {"minItems": 1}}}},
                    {"if": {"properties": {"status": {"const": "FAILED"}}}, "then": {"properties": {"reason": {"type": "string", "minLength": 1}, "attempt_count": {"minimum": 1}, "failed_attempt_count": {"minimum": 1}, "certificate_result": {"enum": ["FAIL", "NOT_RUN"]}, "reason_codes": {"minItems": 1}}}},
                    {"if": {"properties": {"status": {"enum": ["NO_FEASIBLE_CELL", "NOT_RUN"]}}}, "then": {"properties": {"reason": {"type": "string", "minLength": 1}, "starts_per_cell": {"const": 0}, "start_count": {"const": 0}, "attempt_count": {"const": 0}, "trace_record_count": {"const": 0}, "successful_attempt_count": {"const": 0}, "failed_attempt_count": {"const": 0}, "evaluation_count": {"const": 0}, "certificate_result": {"const": "NOT_RUN"}, "reason_codes": {"minItems": 1}}}},
                    {"if": {"properties": {"status": {"const": "NO_FEASIBLE_CELL"}}}, "then": {"properties": {"tie_safe_cell_count": {"const": 0}, "eligible_cell_count": {"const": 0}}}},
                ],
            },
            "refit_p1": {
                "type": "object", "additionalProperties": False,
                "required": ["status", "reason", "registry_version", "registry_role", "incubator_excluded", "family_id", "canonical_ast_id", "canonical_ast", "formula_display", "transforms", "parameters", "domain", "direction", "certificate", "derivative_certificate", "identifiability_status", "bound_status", "collapse_status", "solver_status", "model_structure_hash", "model_instance_hash", "model_path", "r2_fit_all", "rmse_fit_all", "mae_fit_all", "n", "n_unique_x", "plot"],
                "properties": {
                    "status": {"enum": ["certified", "failed"]}, "reason": nullable_string,
                    "registry_version": {"const": "registry_v1"}, "registry_role": {"const": "core"}, "incubator_excluded": {"const": True}, "family_id": nullable_string,
                    "canonical_ast_id": nullable_string, "canonical_ast": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/canonical_ast"}]},
                    "formula_display": nullable_string, "transforms": {"oneOf": [{"type": "null"}, {"type": "object", "additionalProperties": False, "required": ["x", "y"], "properties": {"x": {"$ref": "#/$defs/affine_transform"}, "y": {"$ref": "#/$defs/affine_transform"}}}]},
                    "parameters": {"oneOf": [{"type": "null"}, {"type": "object", "additionalProperties": False, "required": ["a", "b"], "properties": {"a": {"type": "number"}, "b": {"type": "number"}}}]},
                    "domain": domain, "direction": nullable_string, "certificate": nullable_string,
                    "derivative_certificate": {"$ref": "#/$defs/derivative_certificate"},
                    "identifiability_status": {"enum": ["IDENTIFIED", "CANONICAL_IDENTIFIED", "UNAVAILABLE"]},
                    "bound_status": {"enum": ["NO_ACTIVE_BOUNDS", "PARAMETER_BOUND_HIT", "UNAVAILABLE"]},
                    "collapse_status": {"enum": ["NO_COLLAPSE", "CANONICAL_CONSTANT", "COLLAPSED_TO_SIMPLER", "UNAVAILABLE"]},
                    "solver_status": {"enum": ["SUCCEEDED", "FAILED", "NOT_RUN"]},
                    "model_structure_hash": nullable_string, "model_instance_hash": nullable_string,
                    "model_path": {"enum": ["models/one-model.json", None]}, "r2_fit_all": metric_ref,
                    "rmse_fit_all": metric_ref, "mae_fit_all": metric_ref,
                    "n": {"type": ["integer", "null"], "minimum": 1}, "n_unique_x": {"type": ["integer", "null"], "minimum": 1},
                    "plot": {"$ref": "#/$defs/plot"},
                },
                "allOf": [
                    {"if": {"properties": {"status": {"const": "certified"}}}, "then": {"properties": {"reason": {"type": "null"}, "family_id": {"type": "string"}, "canonical_ast_id": {"type": "string"}, "canonical_ast": {"type": "object"}, "formula_display": {"type": "string"}, "transforms": {"type": "object"}, "parameters": {"type": "object"}, "domain": {"type": "array"}, "direction": {"type": "string"}, "certificate": {"const": "PASS"}, "derivative_certificate": {"properties": {"status": {"const": "PASS"}}}, "identifiability_status": {"enum": ["IDENTIFIED", "CANONICAL_IDENTIFIED"]}, "bound_status": {"enum": ["NO_ACTIVE_BOUNDS", "PARAMETER_BOUND_HIT"]}, "collapse_status": {"enum": ["NO_COLLAPSE", "CANONICAL_CONSTANT", "COLLAPSED_TO_SIMPLER"]}, "solver_status": {"const": "SUCCEEDED"}, "model_structure_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"}, "model_instance_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"}, "model_path": {"const": "models/one-model.json"}, "rmse_fit_all": {"properties": {"value": {"type": "number", "minimum": 0}}}, "mae_fit_all": {"properties": {"value": {"type": "number", "minimum": 0}}}, "n": {"type": "integer"}, "n_unique_x": {"type": "integer"}, "plot": {"properties": {"line_present": {"const": True}}}}}},
                    {"if": {"properties": {"status": {"const": "failed"}}}, "then": {"properties": {"reason": {"type": "string"}, "family_id": {"type": "null"}, "canonical_ast_id": {"type": "null"}, "canonical_ast": {"type": "null"}, "formula_display": {"type": "null"}, "transforms": {"type": "null"}, "parameters": {"type": "null"}, "domain": {"type": "null"}, "direction": {"type": "null"}, "derivative_certificate": {"properties": {"status": {"const": "UNAVAILABLE"}}}, "identifiability_status": {"const": "UNAVAILABLE"}, "bound_status": {"const": "UNAVAILABLE"}, "collapse_status": {"const": "UNAVAILABLE"}, "model_structure_hash": {"type": "null"}, "model_instance_hash": {"type": "null"}, "model_path": {"type": "null"}, "r2_fit_all": {"properties": {"value": {"type": "null"}}}, "rmse_fit_all": {"properties": {"value": {"type": "null"}}}, "mae_fit_all": {"properties": {"value": {"type": "null"}}}, "n": {"type": "null"}, "n_unique_x": {"type": "null"}, "plot": {"properties": {"line_present": {"const": False}}}}}},
                ],
            },
            "segment": {
                "type": "object", "additionalProperties": False,
                "required": ["n", "share", "n_unique_x", "span", "r2_fit", "rmse_fit", "mae_fit"],
                "properties": {
                    "n": {"type": ["integer", "null"], "minimum": 1}, "share": {"type": ["number", "null"], "minimum": 0.4, "maximum": 0.6},
                    "n_unique_x": {"type": ["integer", "null"], "minimum": 1}, "span": domain,
                    "r2_fit": metric_ref, "rmse_fit": metric_ref, "mae_fit": metric_ref,
                },
                "allOf": [
                    {"if": {"properties": {"n": {"type": "integer"}}}, "then": {"properties": {"share": {"type": "number"}, "n_unique_x": {"type": "integer"}, "span": {"type": "array"}, "rmse_fit": {"properties": {"value": {"type": "number", "minimum": 0}}}, "mae_fit": {"properties": {"value": {"type": "number", "minimum": 0}}}}}},
                    {"if": {"properties": {"n": {"type": "null"}}}, "then": {"properties": {"share": {"type": "null"}, "n_unique_x": {"type": "null"}, "span": {"type": "null"}, "r2_fit": {"properties": {"value": {"type": "null"}}}, "rmse_fit": {"properties": {"value": {"type": "null"}}}, "mae_fit": {"properties": {"value": {"type": "null"}}}}}},
                ],
            },
            "refit_p2": {
                "type": "object", "additionalProperties": False,
                "required": ["status", "reason", "registry_version", "registry_role", "incubator_excluded", "family_left", "family_right", "ordered_family_pair", "canonical_ast_left", "canonical_ast_right", "formula_left", "formula_right", "transforms", "parameters", "boundary", "shared_mu", "membership", "membership_cell", "domain", "direction", "continuity_residual", "certificate", "derivative_certificate_left", "derivative_certificate_right", "identifiability_status", "bound_status", "collapse_status", "solver_status", "stability_status", "optimizer", "model_structure_hash", "model_instance_hash", "model_path", "left", "right", "r2_fit_all", "rmse_fit_all", "mae_fit_all", "n", "n_unique_x", "plot"],
                "properties": {
                    "status": {"enum": ["certified", "unavailable"]}, "reason": nullable_string,
                    "registry_version": {"const": "registry_v1"}, "registry_role": {"const": "core"}, "incubator_excluded": {"const": True}, "family_left": nullable_string, "family_right": nullable_string,
                    "ordered_family_pair": nullable_string,
                    "canonical_ast_left": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/canonical_ast"}]},
                    "canonical_ast_right": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/canonical_ast"}]},
                    "formula_left": nullable_string, "formula_right": nullable_string,
                    "transforms": {"oneOf": [{"type": "null"}, {"type": "object", "additionalProperties": False, "required": ["left_x", "right_x", "y"], "properties": {"left_x": {"$ref": "#/$defs/affine_transform"}, "right_x": {"$ref": "#/$defs/affine_transform"}, "y": {"$ref": "#/$defs/affine_transform"}}}]},
                    "parameters": {"oneOf": [{"type": "null"}, {"type": "object", "additionalProperties": False, "required": ["c", "mu", "q_left", "q_right"], "properties": {"c": {"type": "number"}, "mu": {"type": "number"}, "q_left": {"type": "number", "minimum": 0}, "q_right": {"type": "number", "minimum": 0}}}]},
                    "boundary": nullable_number, "shared_mu": nullable_number, "membership": nullable_string,
                    "membership_cell": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/tie_safe_cell"}]},
                    "domain": domain, "direction": nullable_string, "continuity_residual": nullable_number,
                    "certificate": nullable_string, "derivative_certificate_left": {"$ref": "#/$defs/derivative_certificate"}, "derivative_certificate_right": {"$ref": "#/$defs/derivative_certificate"},
                    "identifiability_status": {"enum": ["IDENTIFIED", "WEAK_IDENTIFIABILITY", "UNAVAILABLE"]},
                    "bound_status": {"enum": ["NO_ACTIVE_BOUNDS", "PARAMETER_BOUND_HIT", "UNAVAILABLE"]},
                    "collapse_status": {"enum": ["NO_COLLAPSE", "COLLAPSED_SEGMENTS", "UNAVAILABLE"]},
                    "solver_status": {"enum": ["SUCCEEDED", "FAILED", "NOT_RUN"]}, "stability_status": {"enum": ["STABLE", "UNSTABLE", "UNAVAILABLE"]},
                    "optimizer": {"$ref": "#/$defs/optimizer"},
                    "model_structure_hash": nullable_string, "model_instance_hash": nullable_string,
                    "model_path": {"enum": ["models/two-segment-model.json", None]},
                    "left": {"$ref": "#/$defs/segment"}, "right": {"$ref": "#/$defs/segment"},
                    "r2_fit_all": metric_ref, "rmse_fit_all": metric_ref, "mae_fit_all": metric_ref,
                    "n": {"type": ["integer", "null"], "minimum": 1}, "n_unique_x": {"type": ["integer", "null"], "minimum": 1},
                    "plot": {"$ref": "#/$defs/plot"},
                },
                "allOf": [
                    {"if": {"properties": {"status": {"const": "certified"}}}, "then": {"properties": {"reason": {"type": "null"}, "family_left": {"type": "string"}, "family_right": {"type": "string"}, "ordered_family_pair": {"type": "string"}, "canonical_ast_left": {"type": "object"}, "canonical_ast_right": {"type": "object"}, "formula_left": {"type": "string"}, "formula_right": {"type": "string"}, "transforms": {"type": "object"}, "parameters": {"type": "object"}, "boundary": {"type": "number"}, "shared_mu": {"type": "number"}, "membership": {"type": "string"}, "membership_cell": {"type": "object"}, "domain": {"type": "array"}, "direction": {"type": "string"}, "continuity_residual": {"const": 0.0}, "certificate": {"const": "PASS"}, "derivative_certificate_left": {"properties": {"status": {"const": "PASS"}}}, "derivative_certificate_right": {"properties": {"status": {"const": "PASS"}}}, "identifiability_status": {"enum": ["IDENTIFIED", "WEAK_IDENTIFIABILITY"]}, "bound_status": {"enum": ["NO_ACTIVE_BOUNDS", "PARAMETER_BOUND_HIT"]}, "collapse_status": {"const": "NO_COLLAPSE"}, "solver_status": {"const": "SUCCEEDED"}, "stability_status": {"enum": ["STABLE", "UNSTABLE"]}, "optimizer": {"properties": {"status": {"const": "SUCCEEDED"}, "certificate_result": {"const": "PASS"}}}, "model_structure_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"}, "model_instance_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"}, "model_path": {"const": "models/two-segment-model.json"}, "rmse_fit_all": {"properties": {"value": {"type": "number", "minimum": 0}}}, "mae_fit_all": {"properties": {"value": {"type": "number", "minimum": 0}}}, "n": {"type": "integer"}, "n_unique_x": {"type": "integer"}, "plot": {"properties": {"line_present": {"const": True}, "boundary_present": {"const": True}}}}}},
                    {"if": {"properties": {"status": {"const": "unavailable"}}}, "then": {"properties": {"reason": {"type": "string"}, "family_left": {"type": "null"}, "family_right": {"type": "null"}, "ordered_family_pair": {"type": "null"}, "canonical_ast_left": {"type": "null"}, "canonical_ast_right": {"type": "null"}, "formula_left": {"type": "null"}, "formula_right": {"type": "null"}, "transforms": {"type": "null"}, "parameters": {"type": "null"}, "boundary": {"type": "null"}, "shared_mu": {"type": "null"}, "membership": {"type": "null"}, "membership_cell": {"type": "null"}, "domain": {"type": "null"}, "direction": {"type": "null"}, "continuity_residual": {"type": "null"}, "derivative_certificate_left": {"properties": {"status": {"const": "UNAVAILABLE"}}}, "derivative_certificate_right": {"properties": {"status": {"const": "UNAVAILABLE"}}}, "identifiability_status": {"const": "UNAVAILABLE"}, "bound_status": {"const": "UNAVAILABLE"}, "stability_status": {"const": "UNAVAILABLE"}, "model_structure_hash": {"type": "null"}, "model_instance_hash": {"type": "null"}, "model_path": {"type": "null"}, "left": {"properties": {"n": {"type": "null"}}}, "right": {"properties": {"n": {"type": "null"}}}, "r2_fit_all": {"properties": {"value": {"type": "null"}}}, "rmse_fit_all": {"properties": {"value": {"type": "null"}}}, "mae_fit_all": {"properties": {"value": {"type": "null"}}}, "n": {"type": "null"}, "n_unique_x": {"type": "null"}, "plot": {"properties": {"line_present": {"const": False}, "boundary_present": {"const": False}}}}}},
                ],
            },
            "diagnostic_flag": {
                "type": "object", "additionalProperties": False,
                "required": ["code", "severity", "reason", "threshold", "policy_version", "explanation"],
                "properties": {
                    "code": {"enum": ["LARGE_OOF_RESIDUAL", "HIGH_REFIT_INFLUENCE"]},
                    "severity": {"enum": ["review", "extreme"]}, "reason": {"type": "string", "minLength": 1},
                    "threshold": {"type": "number", "minimum": 0}, "policy_version": {"const": "prototype-policy-v1"},
                    "explanation": {"type": "string", "minLength": 1},
                },
            },
            "diagnostic_scale": {
                "type": "object", "additionalProperties": False,
                "required": ["value", "status", "reason", "method", "scope"],
                "properties": {
                    "value": nullable_number, "status": {"enum": ["defined", "undefined"]}, "reason": nullable_string,
                    "method": {"enum": ["qn_finite_sample", "finite_sample_s_mad"]}, "scope": {"const": "outer_train_inner_oof"},
                },
                "allOf": [
                    {"if": {"properties": {"value": {"type": "number"}}}, "then": {"properties": {"value": {"exclusiveMinimum": 0}, "status": {"const": "defined"}, "reason": {"type": "null"}}}},
                    {"if": {"properties": {"value": {"type": "null"}}}, "then": {"properties": {"status": {"const": "undefined"}, "reason": {"type": "string", "minLength": 1}}}},
                ],
            },
            "influence_group": {
                "type": "object", "additionalProperties": False,
                "required": ["x_group_id", "x", "row_ids", "status", "reason", "dmax", "drms", "delta_c", "segment_share_delta", "oof_rmse_relative_delta", "rel_mse_uplift_delta", "full_state", "without_group_state", "recommendation_changed", "decision_changed", "p2_status_changed", "family_changed", "direction_changed", "certificate_changed", "high_refit_influence", "action"],
                "properties": {
                    "x_group_id": {"type": "string"}, "x": {"type": "number"}, "row_ids": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string"}},
                    "status": {"enum": ["sensitivity_only", "unassessable"]}, "reason": nullable_string,
                    "dmax": nullable_number, "drms": nullable_number, "delta_c": nullable_number,
                    "segment_share_delta": nullable_number, "oof_rmse_relative_delta": nullable_number, "rel_mse_uplift_delta": nullable_number,
                    "full_state": {"$ref": "#/$defs/influence_state"}, "without_group_state": {"$ref": "#/$defs/influence_state"},
                    "recommendation_changed": {"type": "boolean"}, "decision_changed": {"type": "boolean"}, "p2_status_changed": {"type": "boolean"}, "family_changed": {"type": "boolean"},
                    "direction_changed": {"type": "boolean"}, "certificate_changed": {"type": "boolean"}, "high_refit_influence": {"type": "boolean"},
                    "action": {"const": "review_only"},
                },
                "allOf": [
                    {"if": {"properties": {"status": {"const": "sensitivity_only"}}}, "then": {"properties": {"reason": {"type": "null"}, "dmax": {"type": "number", "minimum": 0}, "drms": {"type": "number", "minimum": 0}}}},
                    {"if": {"properties": {"status": {"const": "unassessable"}}}, "then": {"properties": {"reason": {"type": "string", "minLength": 1}, "dmax": {"type": "null"}, "drms": {"type": "null"}, "delta_c": {"type": "null"}, "segment_share_delta": {"type": "null"}, "oof_rmse_relative_delta": {"type": "null"}, "rel_mse_uplift_delta": {"type": "null"}, "high_refit_influence": {"const": False}}}},
                ],
            },
            "influence_state": {
                "type": "object", "additionalProperties": False,
                "required": ["recommendation", "decision_code", "p2_status", "direction", "family", "certificate"],
                "properties": {
                    "recommendation": {"enum": ["one", "two", None]}, "decision_code": {"type": "string"},
                    "p2_status": {"enum": ["certified", "unavailable"]}, "direction": {"enum": ["flat", "nondecreasing", None]},
                    "family": {"type": ["string", "null"]}, "certificate": {"type": "string"},
                },
            },
            "diagnostic_row": {
                "type": "object", "additionalProperties": False,
                "required": ["row_id", "source_row_id", "x", "y", "x_group_id", "segment_refit", "procedure", "oof_prediction_median", "oof_prediction_mean", "oof_prediction_spread", "oof_residual_median", "oof_residual_mean", "oof_residual_spread", "appearance_count", "outer_fold_ids", "robust_scale", "scale_trace_index", "z_median", "z_spread", "score_median_abs_z", "threshold", "flag_rate", "refit_prediction", "refit_residual", "refit_label", "influence_group", "flags", "action"],
                "properties": {
                    "row_id": {"type": "string"}, "source_row_id": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}, "x_group_id": {"type": "string"},
                    "segment_refit": {"enum": ["left", "right", None]}, "procedure": {"enum": ["one", "two", None]},
                    "oof_prediction_median": nullable_number, "oof_prediction_mean": nullable_number, "oof_prediction_spread": nullable_number,
                    "oof_residual_median": nullable_number, "oof_residual_mean": nullable_number, "oof_residual_spread": nullable_number,
                    "appearance_count": {"type": "integer", "minimum": 0}, "outer_fold_ids": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
                    "robust_scale": {"$ref": "#/$defs/diagnostic_scale"}, "scale_trace_index": {"type": "integer", "minimum": 0}, "z_median": nullable_number, "z_spread": nullable_number,
                    "score_median_abs_z": nullable_number, "threshold": {"const": 3.5}, "flag_rate": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                    "refit_prediction": nullable_number, "refit_residual": nullable_number, "refit_label": {"enum": ["descriptive_final_refit", None]},
                    "influence_group": {"$ref": "#/$defs/influence_group"},
                    "flags": {"type": "array", "uniqueItems": True, "items": {"$ref": "#/$defs/diagnostic_flag"}}, "action": {"const": "review_only"},
                },
            },
            "repeated_group": {
                "type": "object", "additionalProperties": False,
                "required": ["x_group_id", "x", "n", "mean_y", "median_y", "mean_oof_residual", "median_oof_residual", "within_group_mad_y", "positive_residual_share", "negative_residual_share", "group_systematic_residual", "action"],
                "properties": {
                    "x_group_id": {"type": "string"}, "x": {"type": "number"}, "n": {"type": "integer", "minimum": 1},
                    "mean_y": {"type": "number"}, "median_y": {"type": "number"}, "mean_oof_residual": nullable_number, "median_oof_residual": nullable_number,
                    "within_group_mad_y": {"type": "number", "minimum": 0}, "positive_residual_share": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                    "negative_residual_share": {"type": ["number", "null"], "minimum": 0, "maximum": 1}, "group_systematic_residual": {"type": "boolean"},
                    "action": {"const": "review_only"},
                },
            },
            "diagnostic_scale_trace": {
                "type": "object", "additionalProperties": False,
                "required": ["row_id", "x_group_id", "procedure", "status", "reason", "estimator", "scope", "global_scale", "bin_count", "target_bin_id", "target_bin_group_ids", "raw_local_scale", "local_floor", "final_scale", "zero_tolerance", "training_sample_count", "local_sample_count"],
                "properties": {
                    "row_id": {"type": "string"}, "x_group_id": {"type": "string"}, "procedure": {"enum": ["one", "two"]},
                    "status": {"enum": ["defined", "undefined", "unavailable"]}, "reason": nullable_string,
                    "estimator": {"enum": ["qn_finite_sample", "finite_sample_s_mad"]}, "scope": {"const": "outer_train_inner_oof"},
                    "global_scale": nullable_number, "bin_count": {"type": "integer", "minimum": 0, "maximum": 4},
                    "target_bin_id": nullable_string, "target_bin_group_ids": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
                    "raw_local_scale": nullable_number, "local_floor": nullable_number, "final_scale": nullable_number,
                    "zero_tolerance": {"type": "number", "exclusiveMinimum": 0}, "training_sample_count": {"type": "integer", "minimum": 0},
                    "local_sample_count": {"type": "integer", "minimum": 0},
                },
            },
            "diagnostic_pattern": {
                "type": "object", "additionalProperties": False,
                "required": ["code", "status", "observed", "threshold", "reason", "action"],
                "properties": {
                    "code": {"enum": ["SYSTEMATIC_OOF_RESIDUAL", "HETEROSCEDASTIC_PATTERN", "BREAKPOINT_LOCAL_BIAS"]},
                    "status": {"enum": ["triggered", "not_triggered", "unavailable"]}, "observed": {"type": ["object", "null"]},
                    "threshold": {"type": "string"}, "reason": nullable_string, "action": {"const": "review_only"},
                },
            },
            "diagnostics": {
                "type": "object", "additionalProperties": False,
                "required": ["policy", "oof", "rows", "scale_traces", "repeated_x_groups", "pure_error_lack_of_fit", "patterns", "influence", "influence_groups", "flagged_rows"],
                "properties": {
                    "policy": {"type": "object", "additionalProperties": False, "required": ["version", "tau_z", "extreme_tau_z", "persistent_flag_rate", "scale_estimators", "local_bin_rule", "local_floor_factor", "zero_scale_tolerance_rule", "influence_thresholds"], "properties": {"version": {"const": "prototype-policy-v1"}, "tau_z": {"const": 3.5}, "extreme_tau_z": {"const": 5.25}, "persistent_flag_rate": {"const": 0.5}, "scale_estimators": {"type": "array", "minItems": 2, "items": {"type": "string"}}, "local_bin_rule": {"type": "string"}, "local_floor_factor": {"const": 0.5}, "zero_scale_tolerance_rule": {"type": "string"}, "influence_thresholds": {"type": "object", "additionalProperties": {"type": "number", "minimum": 0}}}},
                    "oof": {
                        "type": "object", "additionalProperties": False,
                        "required": ["status", "reason", "procedure", "residual_definition", "prediction_count", "appearance_export", "scale_trace_export", "threshold", "scale_method", "scale_scope", "separate_from_refit_influence", "plot"],
                        "properties": {
                            "status": {"enum": ["available", "unavailable"]}, "reason": nullable_string, "procedure": {"enum": ["one", "two", None]},
                            "residual_definition": {"type": "string"}, "prediction_count": {"type": "integer", "minimum": 0},
                            "appearance_export": {"const": "diagnostic-appearances.csv"}, "scale_trace_export": {"const": "diagnostic-scale-trace.csv"}, "threshold": {"const": 3.5},
                            "scale_method": {"const": "qn_then_mad_fallback"}, "scale_scope": {"const": "outer_train_inner_oof"}, "separate_from_refit_influence": {"const": True},
                            "plot": {"type": "object", "required": ["path", "status", "panels"], "additionalProperties": False, "properties": {"path": {"const": "figures/residuals.svg"}, "status": {"enum": ["four_separate_panels", "typed_unavailable_with_descriptive_refit"]}, "panels": {"type": "array", "minItems": 4, "maxItems": 4, "uniqueItems": True, "items": {"type": "string"}}}},
                        },
                        "allOf": [
                            {"if": {"properties": {"status": {"const": "available"}}}, "then": {"properties": {"reason": {"type": "null"}, "procedure": {"enum": ["one", "two"]}, "prediction_count": {"minimum": 1}}}},
                            {"if": {"properties": {"status": {"const": "unavailable"}}}, "then": {"properties": {"reason": {"type": "string", "minLength": 1}, "procedure": {"type": "null"}, "prediction_count": {"const": 0}}}},
                        ],
                    },
                    "rows": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/diagnostic_row"}},
                    "scale_traces": {"type": "array", "minItems": 2, "items": {"$ref": "#/$defs/diagnostic_scale_trace"}},
                    "repeated_x_groups": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/repeated_group"}},
                    "pure_error_lack_of_fit": {"type": "object", "additionalProperties": False, "required": ["status", "reason", "procedure", "sse_total", "sse_pure_error", "sse_lack_of_fit", "test_performed"], "properties": {"status": {"enum": ["descriptive", "unavailable"]}, "reason": nullable_string, "procedure": {"enum": ["one", "two", None]}, "sse_total": nullable_number, "sse_pure_error": nullable_number, "sse_lack_of_fit": nullable_number, "test_performed": {"const": False}}},
                    "patterns": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"$ref": "#/$defs/diagnostic_pattern"}},
                    "influence": {"type": "object", "additionalProperties": False, "required": ["status", "method", "scope", "grid_export", "evaluation_grid_size", "thresholds", "group_count", "flagged_group_count", "reason"], "properties": {"status": {"enum": ["available", "unavailable"]}, "method": {"type": "string"}, "scope": {"const": "sensitivity_only"}, "grid_export": {"const": "influence-grid.csv"}, "evaluation_grid_size": {"type": "integer", "minimum": 1}, "thresholds": {"type": "object", "additionalProperties": {"type": "number", "minimum": 0}}, "group_count": {"type": "integer", "minimum": 1}, "flagged_group_count": {"type": "integer", "minimum": 0}, "reason": nullable_string}},
                    "influence_groups": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/influence_group"}},
                    "flagged_rows": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["row_id", "flag_codes", "flags", "action", "diagnostic_row_index"], "properties": {"row_id": {"type": "string"}, "flag_codes": {"type": "array", "uniqueItems": True, "items": {"enum": ["LARGE_OOF_RESIDUAL", "HIGH_REFIT_INFLUENCE"]}}, "flags": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/diagnostic_flag"}}, "action": {"const": "review_only"}, "diagnostic_row_index": {"type": "integer", "minimum": 0}}}},
                },
            },
            "visualization": {
                "type": "object", "additionalProperties": False, "required": ["geometry", "palette"],
                "properties": {
                    "geometry": {"type": "object", "additionalProperties": False, "required": ["view_box", "plot_rect", "xlim", "ylim", "x_ticks", "y_ticks"], "properties": {"view_box": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}, "plot_rect": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}, "xlim": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}, "ylim": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}, "x_ticks": {"type": "integer", "minimum": 2}, "y_ticks": {"type": "integer", "minimum": 2}}},
                    "palette": {"type": "object", "additionalProperties": False, "required": ["model", "boundary", "point", "text", "grid"], "properties": {"model": {"type": "string"}, "boundary": {"type": "string"}, "point": {"type": "string"}, "text": {"type": "string"}, "grid": {"type": "string"}}},
                },
            },
        },
        "allOf": [
            {
                "if": {"properties": {"recommendation": {"properties": {"role": {"type": "null"}}}}},
                "then": {"properties": {"status": {"properties": {"recommendation_status": {"const": "NO_VALIDATED_RECOMMENDATION"}}}, "decision": {"properties": {"recommended_procedure": {"type": "null"}}}, "recommendation": {"properties": {"model_preview_path": {"type": "null"}, "formula_display": {"type": "null"}, "domain": {"type": "null"}, "extrapolation": {"type": "null"}, "global_primary_r2_oos": {"properties": {"value": {"type": "null"}}}, "global_primary_rmse_oof": {"properties": {"value": {"type": "null"}}}, "global_primary_mae_oof": {"properties": {"value": {"type": "null"}}}}}}},
            },
            {
                "if": {"properties": {"recommendation": {"properties": {"role": {"const": "one"}}}}},
                "then": {"properties": {
                    "status": {"properties": {"recommendation_status": {"const": "ONE_RECOMMENDED"}}},
                    "decision": {"properties": {"recommended_procedure": {"const": "one"}}},
                    "recommendation": {"properties": {
                        "model_preview_path": {"const": "models/one-model-preview.json"},
                        "formula_display": {"type": "string"}, "domain": {"type": "array"},
                        "extrapolation": {"const": "forbidden"},
                        "global_primary_rmse_oof": {"properties": {"value": {"type": "number", "minimum": 0}}},
                        "global_primary_mae_oof": {"properties": {"value": {"type": "number", "minimum": 0}}},
                    }},
                    "validation": {"properties": {
                        "repetitions": {"minimum": 1},
                        "procedures": {"properties": {"p1": {"properties": {"status": {"const": "valid"}}}}},
                        "decision_gates": {
                            "contains": {"type": "object", "required": ["status"], "properties": {"status": {"enum": ["fail", "unavailable"]}}},
                            "minContains": 1,
                        },
                    }},
                    "refit": {"properties": {"p1": {"properties": {"status": {"const": "certified"}}}}},
                }},
            },
            {
                "if": {"properties": {"recommendation": {"properties": {"role": {"const": "two"}}}}},
                "then": {"properties": {
                    "status": {"properties": {"recommendation_status": {"const": "TWO_RECOMMENDED"}}},
                    "decision": {"properties": {"recommended_procedure": {"const": "two"}}},
                    "recommendation": {"properties": {
                        "model_preview_path": {"const": "models/two-model-preview.json"},
                        "formula_display": {"type": "string"}, "domain": {"type": "array"},
                        "extrapolation": {"const": "forbidden"},
                        "global_primary_r2_oos": {"properties": {"value": {"type": "number"}}},
                        "global_primary_rmse_oof": {"properties": {"value": {"type": "number", "minimum": 0}}},
                        "global_primary_mae_oof": {"properties": {"value": {"type": "number", "minimum": 0}}},
                    }},
                    "validation": {"properties": {
                        "repetitions": {"minimum": 1},
                        "procedures": {"properties": {
                            "p1": {"properties": {"status": {"const": "valid"}}},
                            "p2": {"properties": {
                                "status": {"enum": ["valid", "fallback"]},
                                "full_data_refit_status": {"const": "certified"},
                                "fallback_rate": {"properties": {"value": {"type": "number", "minimum": 0, "maximum": 0.10}}},
                            }},
                        }},
                        "decision_gates": {"prefixItems": pass_gate_schemas, "items": False},
                    }},
                    "refit": {"properties": {
                        "p1": {"properties": {"status": {"const": "certified"}}},
                        "p2": {"properties": {"status": {"const": "certified"}}},
                    }},
                }},
            },
            {
                "if": {"properties": {"status": {"properties": {"analysis_status": {"const": "FAILED"}}}}},
                "then": {"properties": {"recommendation": {"properties": {"role": {"type": "null"}}}, "refit": {"properties": {"p1": {"properties": {"status": {"const": "failed"}}}, "p2": {"properties": {"status": {"const": "unavailable"}}}}}, "validation": {"properties": {"repetitions": {"const": 0}, "procedures": {"properties": {"p1": {"properties": {"status": {"const": "failed"}}}, "p2": {"properties": {"status": {"const": "failed"}}}}}}}}},
            },
        ],
    }
    return schema


def model_preview(report: dict, role: str) -> dict:
    refit = report["refit"]["p2" if role == "two" else "p1"]
    plot_series = series_for(report)
    selected_series = (
        {"p1": plot_series["p1"]}
        if role == "one"
        else {"p2_left": plot_series["p2_left"], "p2_right": plot_series["p2_right"]}
    )
    return {
        "prototype_only": True,
        "usable_for_prediction": False,
        "notice": "SYNTHETIC PREVIEW; not recommended-model.json",
        "role_preview": role,
        "report_id": report["report_id"],
        "family": refit.get("family_id") or [refit.get("family_left"), refit.get("family_right")],
        "formula_display": report["recommendation"]["formula_display"],
        "model_structure_hash": refit["model_structure_hash"],
        "model_instance_hash": refit["model_instance_hash"],
        "domain": report["recommendation"]["domain"],
        "direction": refit["direction"],
        "boundary": refit.get("boundary"),
        "membership": refit.get("membership"),
        "prediction_grid": selected_series,
        "extrapolation": "forbidden",
    }


def final_refit_model_dossier(report: dict, role: str) -> dict:
    """Return a non-executable prototype dossier for one final-refit candidate."""
    key = "p1" if role == "one" else "p2"
    return {
        "model_schema_version": "prototype-final-refit-dossier-1.0.0",
        "prototype_only": True,
        "usable_for_prediction": False,
        "notice": "SYNTHETIC FINAL-REFIT DOSSIER; not recommended-model.json",
        "report_id": report["report_id"],
        "role": role,
        "registry_version": report["refit"][key]["registry_version"],
        "refit": report["refit"][key],
        "extrapolation": "forbidden",
    }


LAB_CSS = REPORT_CSS + r"""
.labbar{position:fixed;z-index:9;left:50%;bottom:1rem;transform:translateX(-50%);display:flex;gap:.4rem;align-items:center;background:#111827;color:white;border-radius:999px;padding:.5rem .7rem;box-shadow:0 8px 30px #0008}.labbar a{color:white;padding:.25rem .55rem;border-radius:999px}.labbar a[aria-current=page]{background:#fff;color:#111827}.labbar .name{min-width:12rem;text-align:center}.dossiers{display:grid;grid-template-columns:1fr 1fr;gap:1rem}.dossier{border:3px solid #c7cdd4;padding:1rem}.ledger table{min-width:900px}.chart-first{display:grid;grid-template-columns:minmax(0,2fr) minmax(260px,1fr);gap:1rem}.chart-first .plot-grid{grid-template-columns:1fr}.claim{border-left:5px solid #005a9c;padding-left:1rem}
@media(max-width:760px){.dossiers,.chart-first{grid-template-columns:1fr}.labbar{width:calc(100% - 1rem);justify-content:center;flex-wrap:wrap;border-radius:1rem}.labbar .name{width:100%}}
"""


def lab_switcher(variant: str, fixture: str) -> str:
    variants = {"A": "Decision narrative", "B": "Candidate dossiers", "C": "Audit ledger"}
    links = "".join(
        f'<a href="/prototype?variant={key}&amp;fixture={h(fixture)}" data-variant-link="{key}"'
        + (' aria-current="page"' if key == variant else "") + f'>{key}</a>'
        for key in variants
    )
    return f'''<nav class="labbar" aria-label="Prototype variants"><a href="/prototype?variant={chr(((ord(variant)-65-1)%3)+65)}&amp;fixture={h(fixture)}" aria-label="Previous variant">←</a><span class="name">{variant} — {variants[variant]}</span>{links}<a href="/prototype?variant={chr(((ord(variant)-65+1)%3)+65)}&amp;fixture={h(fixture)}" aria-label="Next variant">→</a></nav>
<script>(function(){{const vs=['A','B','C'],cur='{variant}';addEventListener('keydown',e=>{{if(e.target.matches('input,textarea,[contenteditable]'))return;if(e.key==='ArrowLeft'||e.key==='ArrowRight'){{const d=e.key==='ArrowRight'?1:-1;const v=vs[(vs.indexOf(cur)+d+3)%3];location.replace('/prototype?variant='+v+'&fixture={h(fixture)}')}}}})}})();</script>'''


def lab_page(report: dict, variant: str, fixture: str) -> str:
    if variant == "A":
        accepted = render_report_html(report)
        accepted = accepted.replace("script-src &#x27;none&#x27;", "script-src &#x27;unsafe-inline&#x27;")
        accepted = accepted.replace("</head>", f"<style>{LAB_CSS}</style></head>")
        return accepted.replace("</body>", lab_switcher(variant, fixture) + "</body>")
    p1, p2 = report["refit"]["p1"], report["refit"]["p2"]
    rec = report["status"]["recommendation_status"]
    r1 = r2_cell(report["validation"]["procedures"]["p1"]["r2_oos"], "/validation/procedures/p1/r2_oos")
    r2 = r2_cell(report["validation"]["procedures"]["p2"]["r2_oos"], "/validation/procedures/p2/r2_oos")
    if variant == "B":
        content = f'''<header><p class="eyebrow">Variant B · symmetric dossiers</p><h1>Два кандидата как равные досье</h1><p>Радикально другая иерархия: модели сначала, вердикт после.</p></header><main><div class="dossiers"><article class="dossier"><h2>P1</h2><img src="/generated/{h(fixture)}/figures/one-function.svg" alt="P1"><p><code>{h(p1['formula_display'])}</code></p><p>OOF R²={r1}</p><p>certificate={h(p1['certificate'])}</p></article><article class="dossier"><h2>P2</h2><img src="/generated/{h(fixture)}/figures/two-segment.svg" alt="P2"><p>{h(p2.get('formula_left') or p2.get('reason'))}</p><p>OOF R²={r2}</p><p>status={h(p2['status'])}</p></article></div><section><h2>Мост решения</h2><div class="card decision"><strong>{h(rec)}</strong><p><code>{h(report['decision']['code'])}</code></p></div>{warning_blocks(report)}{failure_blocks(report)}</section></main>'''
    else:
        content = f'''<header><p class="eyebrow">Variant C · audit ledger</p><h1>Отчёт как матрица утверждений</h1><p>Графики после проверяемых claims; JSON pointer в каждой строке.</p></header><main class="ledger"><div class="table-wrap"><table><caption>Claim × P1 × P2 × verdict × source</caption><thead><tr><th>Claim</th><th>P1</th><th>P2</th><th>Вердикт</th><th>JSON pointer</th></tr></thead><tbody><tr><th>OOF quality</th><td>{r1}</td><td>{r2}</td><td>{h(report['decision']['code'])}</td><td><code>/validation/procedures</code></td></tr><tr><th>Certified refit</th><td>{h(p1['status'])}</td><td>{h(p2['status'])}</td><td>{h(rec)}</td><td><code>/refit</code></td></tr><tr><th>Warning</th><td colspan="2">{h(', '.join(w['code'] for w in report['warnings']) or '—')}</td><td>{h(rec)}</td><td><code>/warnings</code></td></tr></tbody></table></div><section><h2>Evidence figures</h2><div class="plot-grid"><figure><img src="/generated/{h(fixture)}/figures/one-function.svg" alt="P1 evidence"><figcaption>/refit/p1/plot</figcaption></figure><figure><img src="/generated/{h(fixture)}/figures/two-segment.svg" alt="P2 evidence"><figcaption>/refit/p2/plot</figcaption></figure></div></section>{warning_blocks(report)}{failure_blocks(report)}</main>'''
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PROTOTYPE variant {variant}</title><style>{LAB_CSS}</style></head><body><div class="prototype">PROTOTYPE UI LAB · synthetic fixture · variant {variant}</div>{content}{lab_switcher(variant, fixture)}</body></html>'''


def fixture_bundle_path(fixture_id: object) -> Path:
    if not isinstance(fixture_id, str) or not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", fixture_id):
        raise ValueError(f"unsafe fixture id: {fixture_id!r}")
    bundle = (GENERATED / fixture_id).resolve()
    if bundle.parent != GENERATED.resolve():
        raise ValueError(f"fixture path escaped generated root: {fixture_id!r}")
    return bundle


def build_bundle(spec: dict) -> None:
    bundle = fixture_bundle_path(spec.get("id"))
    (bundle / "figures").mkdir(parents=True, exist_ok=True)
    (bundle / "models").mkdir(parents=True, exist_ok=True)
    report = build_report(spec)
    dump_json(bundle / "report.json", report)
    report = json.loads((bundle / "report.json").read_text(encoding="utf-8"))
    canonical_schema = json.loads(CANONICAL_SCHEMA.read_text(encoding="utf-8"))
    dump_json(bundle / "report.schema.json", canonical_schema)
    write_csv_artifacts(bundle, report)
    write_fit_attempt_trace(bundle, report)
    (bundle / "figures" / "one-function.svg").write_text(svg_figure(report, "one"), encoding="utf-8")
    (bundle / "figures" / "two-segment.svg").write_text(svg_figure(report, "two"), encoding="utf-8")
    (bundle / "figures" / "residuals.svg").write_text(diagnostics_svg(report), encoding="utf-8")
    role = report["decision"]["recommended_procedure"]
    if role:
        dump_json(bundle / "models" / f"{role}-model-preview.json", model_preview(report, role))
    if report["refit"]["p1"]["status"] == "certified":
        dump_json(bundle / "models" / "one-model.json", final_refit_model_dossier(report, "one"))
    if report["refit"]["p2"]["status"] == "certified":
        dump_json(bundle / "models" / "two-segment-model.json", final_refit_model_dossier(report, "two"))
    dump_json(bundle / "provenance.json", report["provenance"])
    provenance_dir = bundle / "provenance"
    provenance_dir.mkdir(exist_ok=True)
    dump_json(provenance_dir / "request-metadata.json", report["provenance"]["hash_payloads"]["request_metadata"])
    dump_json(provenance_dir / "resolved-policy.json", report["provenance"]["hash_payloads"]["policy"])
    dump_json(provenance_dir / "registry.json", report["provenance"]["hash_payloads"]["registry"])
    dump_json(provenance_dir / "source-manifest.json", report["provenance"]["hash_payloads"]["source_manifest"])
    dump_json(provenance_dir / "dependency-manifest.json", report["provenance"]["hash_payloads"]["dependency_manifest"])
    dump_json(provenance_dir / "environment.json", report["provenance"]["environment"])
    (bundle / "report.html").write_text(render_report_html(report), encoding="utf-8")
    content_paths = sorted(
        path for path in bundle.rglob("*")
        if path.is_file() and path.name not in {"report.json", "report.html", "prototype-manifest.json"}
    )
    report["artifacts"] = [
        {
            "path": str(path.relative_to(bundle)),
            "media_type": artifact_media_type(path),
            "size": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in content_paths
    ]
    dump_json(bundle / "report.json", report)
    report = json.loads((bundle / "report.json").read_text(encoding="utf-8"))
    (bundle / "report.html").write_text(render_report_html(report), encoding="utf-8")
    manifest_paths = sorted(
        path for path in bundle.rglob("*")
        if path.is_file() and path.name != "prototype-manifest.json"
    )
    dump_json(bundle / "prototype-manifest.json", {
        "schema_version": "prototype-manifest-1.0.0",
        "prototype_only": True,
        "report_id": report["report_id"],
        "files": [
            {
                "path": str(path.relative_to(bundle)),
                "media_type": artifact_media_type(path),
                "size": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in manifest_paths
        ],
    })
    variants = GENERATED / "_variants"
    variants.mkdir(exist_ok=True)
    for variant in "ABC":
        (variants / f"{spec['id']}-{variant}.html").write_text(lab_page(report, variant, spec["id"]), encoding="utf-8")


def build_all() -> list[str]:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    canonical_schema = json.loads(CANONICAL_SCHEMA.read_text(encoding="utf-8"))
    if report_schema() != canonical_schema:
        raise RuntimeError("docs/specification/report.schema.json drifted from the executable report schema")
    if GENERATED.exists():
        shutil.rmtree(GENERATED)
    GENERATED.mkdir(parents=True)
    for spec in matrix["fixtures"]:
        build_bundle(spec)
    for directory in [GENERATED, *(path for path in GENERATED.rglob("*") if path.is_dir())]:
        os.chmod(directory, 0o700)
    for path in (path for path in GENERATED.rglob("*") if path.is_file()):
        os.chmod(path, 0o600)
    return [item["id"] for item in matrix["fixtures"]]


class PrototypeHandler(SimpleHTTPRequestHandler):
    fixture_ids: set[str] = set()

    def translate_path(self, path: str) -> str:
        relative = super().translate_path(path)
        return relative

    def do_GET(self) -> None:
        split = urlsplit(self.path)
        if split.path.rstrip("/") == "/prototype":
            query = parse_qs(split.query)
            variant = query.get("variant", ["A"])[0]
            fixture = query.get("fixture", [sorted(self.fixture_ids)[0]])[0]
            if variant not in {"A", "B", "C"} or fixture not in self.fixture_ids:
                self.send_error(404, "Unknown prototype variant or fixture")
                return
            data = (GENERATED / "_variants" / f"{fixture}-{variant}.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        return super().do_GET()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="serve the prototype lab after building")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    fixtures = build_all()
    print(json.dumps({"status": "BUILT", "prototype_only": True, "fixtures": fixtures, "generated": str(GENERATED)}, ensure_ascii=False))
    if args.serve:
        PrototypeHandler.fixture_ids = set(fixtures)
        os.chdir(HERE)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), PrototypeHandler)
        print(f"PROTOTYPE ONLY: http://127.0.0.1:{args.port}/prototype?variant=A&fixture={fixtures[0]}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
