"""Experiment orchestration and machine gates for prototype issue 11."""

from __future__ import annotations

from dataclasses import replace
from time import perf_counter
from typing import Any

import numpy as np
from scipy.special import expit

from profiled_logic import (
    Problem,
    certificate_fault_probes,
    cubic_grid_trap,
    fit_p1_profiled,
    fit_problem,
    make_scenarios,
    scale_problem,
    x_scale_diagnostics,
)


SEED = 20260716
IDENTIFIED = ("hinge_identified", "exp_identified", "exp_negative_rate", "logistic_identified")


def run_experiment() -> dict[str, Any]:
    scenarios = {scenario.name: scenario for scenario in make_scenarios()}
    runs: dict[str, dict[str, Any]] = {}
    raw: dict[str, dict[str, Any]] = {}

    for name, problem in scenarios.items():
        raw[name] = {}
        profile, profile_ms = _timed_fit(problem, "profile_cells", SEED)
        raw[name]["profile"] = profile
        runs[name] = {"profile": _trace_summary(profile, profile_ms)}

        if name in IDENTIFIED:
            joint, joint_ms = _timed_fit(problem, "joint_cells", SEED)
            raw[name]["joint"] = joint
            runs[name]["joint"] = _trace_summary(joint, joint_ms)
            references = []
            reference_raw = []
            for offset in (0, 1, 2):
                reference, reference_ms = _timed_fit(problem, "global_profiled_reference", SEED + offset)
                reference_raw.append(reference)
                references.append(_trace_summary(reference, reference_ms))
            raw[name]["references"] = reference_raw
            runs[name]["global_references"] = references

    p1 = _p1_probes()
    direction_symmetry = _direction_probes(scenarios, raw)
    reproducibility = _reproducibility_probes(scenarios)
    scale = _scale_probes(scenarios["hinge_identified"])
    forced = fit_problem(scenarios["hinge_identified"], "profile_cells", budget="forced_failure")
    certificate = cubic_grid_trap()
    certificate_faults = certificate_fault_probes()
    gates = _evaluate_gates(
        raw,
        runs,
        p1,
        direction_symmetry,
        reproducibility,
        scale,
        forced,
        certificate,
        certificate_faults,
    )
    required = [item["pass"] for item in gates.values() if item["required"]]
    decision = "PROFILE_CELLS" if required and all(required) else "INCONCLUSIVE"

    return {
        "prototype": "fitting_strategy_profiled_v2.2",
        "question": "profiled tie-cell search vs joint local search, checked against a stochastic profiled reference",
        "decision": decision,
        "seed": SEED,
        "scope": "representative poly1/exp k+/exp k-/logistic; no validation or product recommendation",
        "runs": runs,
        "p1_probes": p1,
        "direction_symmetry": direction_symmetry,
        "reproducibility": reproducibility,
        "scale": scale,
        "forced_failure": forced.public(include_attempts=True),
        "cubic_grid_trap": certificate,
        "certificate_fault_probes": certificate_faults,
        "gates": gates,
    }


def _timed_fit(problem: Problem, strategy: str, seed: int):
    started = perf_counter()
    trace = fit_problem(problem, strategy, seed=seed, budget="small" if strategy != "global_profiled_reference" else "normal")
    return trace, (perf_counter() - started) * 1000.0


def _trace_summary(trace, wall_ms: float) -> dict[str, Any]:
    data = trace.public()
    data["wall_ms"] = wall_ms
    if data["best"] is not None:
        prediction = data["best"].pop("prediction_scaled")
        data["best"]["prediction_min_scaled"] = min(prediction)
        data["best"]["prediction_max_scaled"] = max(prediction)
    return data


def _p1_probes() -> dict[str, Any]:
    x = np.linspace(0.0, 1.0, 31)
    logistic_y = 0.2 + 1.3 * expit(7.0 * (x - 0.4))
    local = fit_p1_profiled(x, logistic_y, "logistic_v1", 1, seed=SEED)
    reference = fit_p1_profiled(x, logistic_y, "logistic_v1", 1, seed=SEED, global_reference=True)
    decreasing = fit_p1_profiled(x, -logistic_y, "logistic_v1", -1, seed=SEED)
    linear = fit_p1_profiled(x, 0.3 + 0.8 * x, "poly1_v1", 1, seed=SEED)
    constant = fit_p1_profiled(x, np.full_like(x, 2.75), "poly1_v1", 1, seed=SEED)
    insufficient = fit_p1_profiled(np.array([0.0, 0.5, 1.0]), np.array([0.0, 0.5, 1.0]), "poly1_v1", 1, seed=SEED)
    return {
        "logistic_local": local,
        "logistic_global_reference": reference,
        "logistic_decreasing": decreasing,
        "linear": linear,
        "constant": constant,
        "insufficient_3_rows": insufficient,
    }


def _reproducibility_probes(scenarios: dict[str, Problem]) -> dict[str, Any]:
    problem = scenarios["logistic_identified"]
    normal = fit_problem(problem, "profile_cells", seed=SEED, start_order="normal", budget="small")
    reversed_order = fit_problem(problem, "profile_cells", seed=SEED, start_order="reversed", budget="small")
    permutation = np.random.default_rng(SEED + 77).permutation(len(problem.x))
    permuted_problem = replace(problem, name="logistic_permuted", x=problem.x[permutation], y=problem.y[permutation])
    permuted = fit_problem(permuted_problem, "profile_cells", seed=SEED, start_order="normal", budget="small")
    if normal.best is None or reversed_order.best is None or permuted.best is None:
        return {"status": "UNAVAILABLE", "pass": False}
    return {
        "status": "OK",
        "normal_hash": normal.best.model_hash,
        "reversed_hash": reversed_order.best.model_hash,
        "permuted_hash": permuted.best.model_hash,
        "start_order_prediction_drift": float(np.max(np.abs(normal.best.prediction_scaled - reversed_order.best.prediction_scaled))),
        "permutation_prediction_drift": float(np.max(np.abs(normal.best.prediction_scaled - permuted.best.prediction_scaled))),
        "start_order_c_drift": abs(normal.best.c_t - reversed_order.best.c_t),
        "permutation_c_drift": abs(normal.best.c_t - permuted.best.c_t),
        "pass": (
            normal.best.model_hash == reversed_order.best.model_hash == permuted.best.model_hash
            and np.max(np.abs(normal.best.prediction_scaled - reversed_order.best.prediction_scaled)) <= 1e-9
            and np.max(np.abs(normal.best.prediction_scaled - permuted.best.prediction_scaled)) <= 1e-9
            and abs(normal.best.c_t - reversed_order.best.c_t) <= 1e-8
            and abs(normal.best.c_t - permuted.best.c_t) <= 1e-8
        ),
    }


def _direction_probes(scenarios: dict[str, Problem], raw: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = []
    passed = True
    for name in IDENTIFIED:
        source = scenarios[name]
        mirrored_problem = replace(source, name=f"{name}_decreasing", y=-source.y, direction=-1)
        mirrored = fit_problem(mirrored_problem, "profile_cells", seed=SEED, budget="small")
        increasing = raw[name]["profile"].best
        if increasing is None or mirrored.best is None:
            row = {"scenario": name, "pass": False, "reason": "missing candidate"}
        else:
            prediction_symmetry_drift = float(np.max(np.abs(increasing.prediction_scaled + mirrored.best.prediction_scaled)))
            c_drift = abs(increasing.c_t - mirrored.best.c_t)
            row_pass = mirrored.best.certificate.valid and prediction_symmetry_drift <= 1e-7 and c_drift <= 1e-6
            row = {
                "scenario": name,
                "decreasing_status": mirrored.status,
                "prediction_symmetry_drift": prediction_symmetry_drift,
                "c_drift": c_drift,
                "pass": row_pass,
            }
        passed = passed and bool(row["pass"])
        rows.append(row)
    return {"pass": passed, "rows": rows}


def _scale_probes(problem: Problem) -> dict[str, Any]:
    dyadic_x = np.arange(33, dtype=float) / 32.0
    dyadic_y = 0.2 + 0.35 * dyadic_x + 1.25 * np.maximum(dyadic_x - 0.515625, 0.0)
    base_problem = replace(problem, name="dyadic_base", x=dyadic_x, y=dyadic_y, truth_c=0.515625)
    base = fit_problem(base_problem, "profile_cells", seed=SEED, budget="small")
    safe_problem = replace(
        base_problem,
        name="safe_affine",
        x=2.0**30 + 2.0**10 * dyadic_x,
        y=2.0**10 + 2.0**8 * dyadic_y,
    )
    safe = fit_problem(safe_problem, "profile_cells", seed=SEED, budget="small")
    _, base_t, _ = scale_problem(base_problem)
    _, safe_t, _ = scale_problem(safe_problem)
    hostile_problem = replace(base_problem, name="hostile_quantized", x=1e12 + 1e-2 * dyadic_x)
    _, hostile_t, _ = scale_problem(hostile_problem)
    hostile_span = float(np.max(hostile_problem.x) - np.min(hostile_problem.x))
    hostile_resolution_ratio = float(np.spacing(np.max(np.abs(hostile_problem.x))) / hostile_span)
    hostile_diagnostics = x_scale_diagnostics(hostile_problem)
    ulp_step = float(np.spacing(1e12))
    ulp_problem = replace(
        base_problem,
        name="distinct_one_ulp_steps",
        x=1e12 + ulp_step * np.arange(10, dtype=float),
        y=np.linspace(0.0, 1.0, 10),
    )
    ulp_trace = fit_problem(ulp_problem, "profile_cells", seed=SEED, budget="small")
    wide_x = np.r_[-1e16, np.arange(-6.0, 7.0), 1e16]
    wide_problem = replace(
        base_problem,
        name="distinct_x_normalized_collision",
        x=wide_x,
        y=np.linspace(0.0, 1.0, len(wide_x)),
    )
    _, wide_t, _ = scale_problem(wide_problem)
    wide_trace = fit_problem(wide_problem, "profile_cells", seed=SEED, budget="small")
    wide_cell_unique_totals = sorted(
        {cell.left_unique + cell.right_unique for cell in wide_trace.cells}
    )
    if base.best is None or safe.best is None:
        return {"status": "UNAVAILABLE", "pass": False}
    return {
        "status": "OK",
        "safe_normalized_design_drift": float(np.max(np.abs(base_t - safe_t))),
        "safe_prediction_drift": float(np.max(np.abs(base.best.prediction_scaled - safe.best.prediction_scaled))),
        "safe_c_drift": abs(base.best.c_t - safe.best.c_t),
        "safe_hash_equal": base.best.model_hash == safe.best.model_hash,
        "hostile_normalized_design_drift": float(np.max(np.abs(base_t - hostile_t))),
        "hostile_resolution_ratio": hostile_resolution_ratio,
        "hostile_core_warnings": list(hostile_diagnostics.warning_codes),
        "distinct_ulp_status": ulp_trace.status,
        "distinct_ulp_warnings": list(ulp_trace.warnings),
        "distinct_ulp_unique_x": None if ulp_trace.x_scale is None else ulp_trace.x_scale.unique_x,
        "distinct_ulp_unique_t": None if ulp_trace.x_scale is None else ulp_trace.x_scale.unique_t,
        "wide_unique_x": int(np.unique(wide_x).size),
        "wide_unique_t": int(np.unique(wide_t).size),
        "wide_status": wide_trace.status,
        "wide_warnings": list(wide_trace.warnings),
        "wide_attempt_count": len(wide_trace.attempts),
        "wide_cell_unique_totals": wide_cell_unique_totals,
        "wide_cells_use_raw_bounds": all(cell.lo_x < cell.hi_x for cell in wide_trace.cells),
        "pass": (
            np.max(np.abs(base_t - safe_t)) <= 1e-12
            and np.max(np.abs(base.best.prediction_scaled - safe.best.prediction_scaled)) <= 1e-7
            and abs(base.best.c_t - safe.best.c_t) <= 1e-8
            and base.best.model_hash == safe.best.model_hash
            and hostile_resolution_ratio > 1e-6
            and "LOW_X_RESOLUTION" in hostile_diagnostics.warning_codes
            and "LOW_X_RESOLUTION" in ulp_trace.warnings
            and ulp_trace.x_scale is not None
            and ulp_trace.x_scale.unique_x == 10
            and ulp_trace.x_scale.unique_t == 10
            and np.unique(wide_x).size == 15
            and np.unique(wide_t).size < np.unique(wide_x).size
            and wide_trace.status == "NONINVERTIBLE_X_SCALE"
            and "LOW_X_RESOLUTION" in wide_trace.warnings
            and "NORMALIZED_X_COLLISION" in wide_trace.warnings
            and not wide_trace.attempts
            and wide_cell_unique_totals == [15]
            and all(cell.lo_x < cell.hi_x for cell in wide_trace.cells)
        ),
    }


def _evaluate_gates(
    raw: dict[str, dict[str, Any]],
    runs: dict[str, dict[str, Any]],
    p1: dict[str, Any],
    direction_symmetry: dict[str, Any],
    reproducibility: dict[str, Any],
    scale: dict[str, Any],
    forced,
    certificate: dict[str, Any],
    certificate_faults: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    profile_candidates = [payload["profile"].best for payload in raw.values() if payload["profile"].best is not None]
    exact_ok = bool(profile_candidates) and all(candidate.certificate.valid for candidate in profile_candidates)

    objective_rows: list[dict[str, Any]] = []
    objective_ok = True
    for name in IDENTIFIED:
        profile = raw[name]["profile"].best
        joint = raw[name]["joint"].best
        references = [trace.best for trace in raw[name]["references"] if trace.best is not None]
        if profile is None or joint is None or not references:
            objective_ok = False
            objective_rows.append({"scenario": name, "pass": False, "reason": "missing finite candidate"})
            continue
        reference = min(references, key=lambda item: item.sse_scaled)
        allowed = max(1e-8, 1e-4 * max(reference.sse_scaled, 1e-8))
        loss_gap = profile.sse_scaled - reference.sse_scaled
        c_tolerance = 1e-5 if name == "hinge_identified" else 1e-3
        problem = next(problem for problem in make_scenarios() if problem.name == name)
        truth_c = problem.truth_c
        _, _, truth_scaled = scale_problem(problem)
        fit_error = float(np.max(np.abs(profile.prediction_scaled - truth_scaled)))
        c_error = abs(profile.c_t - float(truth_c))
        reference_errors = [float(np.max(np.abs(item.prediction_scaled - truth_scaled))) for item in references]
        reference_c_errors = [abs(item.c_t - float(truth_c)) for item in references]
        reference_quality = max(reference_errors) <= 1e-4 and max(reference_c_errors) <= 1e-3
        row_pass = loss_gap <= allowed and c_error <= c_tolerance and fit_error <= 1e-5 and reference_quality
        objective_ok = objective_ok and row_pass
        objective_rows.append({
            "scenario": name,
            "profile_sse": profile.sse_scaled,
            "best_reference_sse": reference.sse_scaled,
            "profile_minus_reference": loss_gap,
            "allowed_gap": allowed,
            "c_error": c_error,
            "c_tolerance": c_tolerance,
            "max_scaled_fit_error": fit_error,
            "max_reference_fit_error": max(reference_errors),
            "max_reference_c_error": max(reference_c_errors),
            "reference_quality": reference_quality,
            "pass": row_pass,
        })

    line = raw["line_collapse"]["profile"]
    constant = raw["constant_collapse"]["profile"]
    outside = raw["break_outside_balance"]["profile"]
    ties = raw["ties_exact_40_60"]["profile"]
    no_split = raw["ties_no_split"]["profile"]
    insufficient = raw["balanced_but_insufficient"]["profile"]
    multiple = raw["multiple_near_optima"]["profile"]
    saturation = raw["logistic_saturation"]["profile"]
    state_ok = (
        line.status == "COLLAPSED_SEGMENTS"
        and line.best is not None
        and line.best.canonical_replacement is not None
        and line.best.canonical_replacement["family"] == "poly1_v1"
        and "FLAT_PROFILE" in line.best.warnings
        and constant.status == "COLLAPSED_SEGMENTS"
        and constant.best is not None
        and constant.best.canonical_replacement is not None
        and constant.best.canonical_replacement["family"] == "constant_v1"
        and outside.best is not None
        and "BOUNDARY_HIT" in outside.best.warnings
        and ties.best is not None
        and ties.best.certificate.ties_atomic
        and any(cell.left_n == 6 and cell.right_n == 9 for cell in ties.cells)
        and no_split.status == "NO_BALANCED_SPLIT"
        and insufficient.status == "INSUFFICIENT_DATA_FOR_P2"
        and multiple.status == "MULTIPLE_NEAR_OPTIMA"
        and multiple.best is not None
        and "MULTIPLE_NEAR_OPTIMA" in multiple.best.warnings
        and saturation.status == "WEAK_IDENTIFIABILITY"
        and saturation.best is not None
        and "PARAMETER_BOUND_HIT" in saturation.best.warnings
    )

    fairness_rows = []
    fair_ok = True
    for name in IDENTIFIED:
        profile_trace = raw[name]["profile"]
        joint_trace = raw[name]["joint"]
        references_for_name = raw[name]["references"]
        if name == "hinge_identified":
            row_pass = (
                profile_trace.best is not None
                and len(profile_trace.attempts) == len(profile_trace.cells)
                and all(attempt.start_id.endswith(":scalar") for attempt in profile_trace.attempts)
                and joint_trace.best is not None
                and len([trace for trace in references_for_name if trace.best is not None]) == 3
            )
            mode = "declared scalar oracle specialization"
        else:
            profile_starts = [(item.cell_index, item.start_id) for item in profile_trace.attempts]
            joint_starts = [(item.cell_index, item.start_id) for item in joint_trace.attempts]
            same_reference_cells = all(trace.cells == profile_trace.cells for trace in references_for_name)
            row_pass = (
                profile_trace.best is not None
                and joint_trace.best is not None
                and profile_trace.cells == joint_trace.cells
                and profile_starts == joint_starts
                and len([trace for trace in references_for_name if trace.best is not None]) == 3
                and same_reference_cells
            )
            mode = "same cells/start IDs/local budget; shared profiled objective for global audit"
        fair_ok = fair_ok and row_pass
        fairness_rows.append({"scenario": name, "mode": mode, "pass": row_pass})
    efficiency_rows = []
    efficiency_ok = True
    for name in IDENTIFIED:
        profile_trace = raw[name]["profile"]
        joint_trace = raw[name]["joint"]
        reference_traces = raw[name]["references"]
        profile_evals = sum(item.outer_evaluations for item in profile_trace.attempts)
        profile_lsq = sum(item.inner_lsq_solves for item in profile_trace.attempts)
        joint_evals = sum(item.outer_evaluations for item in joint_trace.attempts)
        joint_lsq = sum(item.inner_lsq_solves for item in joint_trace.attempts)
        reference_work = [
            {
                "outer": sum(item.outer_evaluations for item in trace.attempts),
                "inner_lsq": sum(item.inner_lsq_solves for item in trace.attempts),
                "attempts": len(trace.attempts),
            }
            for trace in reference_traces
        ]
        reference_min = min(reference_work, key=lambda item: item["outer"])
        accounting_pass = (
            profile_lsq == profile_evals + len(profile_trace.attempts)
            and joint_lsq == len(joint_trace.attempts)
            and all(
                item["inner_lsq"] == item["outer"] + item["attempts"]
                for item in reference_work
            )
        )
        outer_advantage = profile_evals < joint_evals and profile_evals < reference_min["outer"]
        profile_ms = float(runs[name]["profile"]["wall_ms"])
        joint_ms = float(runs[name]["joint"]["wall_ms"])
        reference_ms = min(float(item["wall_ms"]) for item in runs[name]["global_references"])
        row_pass = outer_advantage and accounting_pass
        efficiency_ok = efficiency_ok and row_pass
        efficiency_rows.append({
            "scenario": name,
            "profile_outer": profile_evals,
            "profile_inner_lsq": profile_lsq,
            "joint_outer": joint_evals,
            "joint_initial_lsq": joint_lsq,
            "reference_outer_min": reference_min["outer"],
            "reference_inner_lsq_at_outer_min": reference_min["inner_lsq"],
            "outer_advantage": outer_advantage,
            "accounting_pass": accounting_pass,
            "wall_ms_diagnostic": {
                "profile": profile_ms,
                "joint": joint_ms,
                "reference_min": reference_ms,
            },
            "pass": row_pass,
        })

    p1_local, p1_reference = p1["logistic_local"], p1["logistic_global_reference"]
    p1_allowed = max(1e-8, 1e-4 * max(float(p1_reference.get("sse_scaled", 0.0)), 1e-8))
    p1_ok = (
        p1_local.get("status") == "OK"
        and p1["logistic_decreasing"].get("status") == "OK"
        and p1["linear"].get("status") == "OK"
        and p1["constant"].get("status") == "COLLAPSED_TO_CONSTANT"
        and p1["insufficient_3_rows"].get("status") == "INSUFFICIENT_DATA_FOR_P1"
        and float(p1_local.get("sse_scaled", np.inf)) - float(p1_reference.get("sse_scaled", np.inf)) <= p1_allowed
    )
    certificate_ok = (
        certificate["coarse_grid_passes"]
        and not certificate["analytic_certificate_passes"]
        and certificate["status"] == "SHAPE_FAILURE"
        and bool(certificate_faults.get("pass"))
    )
    failure_ok = (
        forced.status == "OPTIMIZER_FAILURE"
        and bool(forced.attempts)
        and sum(item.outer_evaluations for item in forced.attempts) > 0
        and all(not item.success and "OPTIMIZER_FAILURE" in item.reason_codes for item in forced.attempts)
    )

    return {
        "exact_validity": {"required": True, "pass": exact_ok, "candidate_count": len(profile_candidates)},
        "fair_search": {"required": True, "pass": fair_ok, "rows": fairness_rows},
        "objective_recovery": {"required": True, "pass": objective_ok, "rows": objective_rows},
        "state_truth": {"required": True, "pass": state_ok},
        "reproducibility": {"required": True, "pass": bool(reproducibility.get("pass")), "detail": reproducibility},
        "scale_equivariance": {"required": True, "pass": bool(scale.get("pass")), "detail": scale},
        "p1_profiled_seam": {"required": True, "pass": p1_ok, "allowed_gap": p1_allowed},
        "p2_both_directions": {"required": True, "pass": bool(direction_symmetry.get("pass")), "detail": direction_symmetry},
        "independent_certificate": {
            "required": True,
            "pass": certificate_ok,
            "cubic_grid_trap": certificate,
            "fault_probes": certificate_faults,
        },
        "failure_honesty": {"required": True, "pass": failure_ok, "forced_trace": forced.public(include_attempts=True)},
        "efficiency": {
            "required": True,
            "pass": efficiency_ok,
            "basis": "deterministic outer-evaluation advantage plus exact inner-work accounting; wall_ms is diagnostic only",
            "rows": efficiency_rows,
        },
        "policy_calibration": {"required": False, "pass": None, "status": "DEFERRED_TO_TICKET_14"},
    }
