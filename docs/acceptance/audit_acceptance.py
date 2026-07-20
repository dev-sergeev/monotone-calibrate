#!/usr/bin/env python3
"""Read-only design-time audit for acceptance manifest v1."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import struct
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE = ROOT / "docs" / "acceptance"
SPECIFICATION = ROOT / "docs" / "specification"
MANIFEST_PATH = ACCEPTANCE / "acceptance-manifest-v1.json"
SCHEMA_PATH = ACCEPTANCE / "acceptance-manifest.schema.json"
RESULTS_SCHEMA_PATH = ACCEPTANCE / "acceptance-results.schema.json"
EVIDENCE_SCHEMA_PATH = ACCEPTANCE / "acceptance-evidence.schema.json"
PLATFORM_RUN_SCHEMA_PATH = ACCEPTANCE / "acceptance-platform-run.schema.json"
ENVIRONMENT_SCHEMA_PATH = ACCEPTANCE / "acceptance-environment.schema.json"
ENVIRONMENT_PROBE_SCHEMA_PATH = ACCEPTANCE / "acceptance-environment-probe.schema.json"
ORACLE_RESULT_SCHEMA_PATH = ACCEPTANCE / "acceptance-oracle-result.schema.json"
REVIEW_SCHEMA_PATH = ACCEPTANCE / "acceptance-review.schema.json"
SOURCE_MANIFEST_SCHEMA_PATH = ACCEPTANCE / "source-manifest.schema.json"
SOURCE_INVENTORY_POLICY_PATH = ACCEPTANCE / "source-inventory-policy-v1.json"
SOURCE_INVENTORY_SCHEMA_PATH = ACCEPTANCE / "source-inventory-policy.schema.json"
GATE_EXECUTION_POLICY_PATH = ACCEPTANCE / "gate-execution-policy-v1.json"
GATE_EXECUTION_SCHEMA_PATH = ACCEPTANCE / "gate-execution-policy.schema.json"
VERIFIER_RUNTIME_PROJECT_PATH = ACCEPTANCE / "verifier-runtime" / "pyproject.toml"
VERIFIER_RUNTIME_LOCK_PATH = ACCEPTANCE / "verifier-runtime" / "uv.lock"
MODEL_SCHEMA_PATH = SPECIFICATION / "model.schema.json"
REPORT_SCHEMA_PATH = SPECIFICATION / "production-report.schema.json"
BUNDLE_MANIFEST_SCHEMA_PATH = SPECIFICATION / "manifest.schema.json"
LLM_ADVICE_SCHEMA_PATH = SPECIFICATION / "llm-start-advice.schema.json"
RESOLVED_POLICY_SCHEMA_PATH = SPECIFICATION / "resolved-policy.schema.json"
REGISTRY_SCHEMA_PATH = SPECIFICATION / "registry.schema.json"
WORK_POLICY_PATH = ACCEPTANCE / "work-policy-v1.json"
START_SLOT_POLICY_PATH = ACCEPTANCE / "start-slot-policy-v1.json"
IDENTITY_POLICY_PATH = ACCEPTANCE / "identity-policy-v1.json"
SPLIT_POLICY_PATH = ACCEPTANCE / "split-policy-v1.json"
REPORT_TREE = ROOT / "docs" / "prototypes" / "report-layout" / "generated"
MODEL_REPORT_FUZZ_PATH = ACCEPTANCE / "fuzz_model_report_schemas.py"
SOURCE_ARCHIVE_FUZZ_PATH = ACCEPTANCE / "fuzz_source_archive.py"


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_constant,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_digest(path: Path) -> str:
    ledger = bytearray()
    for item in sorted(
        (p for p in path.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(path).as_posix().encode(),
    ):
        relative = item.relative_to(path).as_posix()
        ledger.extend(f"{relative}\t{item.stat().st_size}\t{sha256_file(item)}\n".encode())
    return hashlib.sha256(ledger).hexdigest()


def normative_snapshot_digest() -> tuple[str, int]:
    candidates = list(ACCEPTANCE.rglob("*"))
    forbidden_symlinks = [
        path
        for path in candidates
        if path.is_symlink()
        and "evidence" not in path.relative_to(ACCEPTANCE).parts
        and "__pycache__" not in path.relative_to(ACCEPTANCE).parts
    ]
    if forbidden_symlinks:
        relative = forbidden_symlinks[0].relative_to(ROOT).as_posix()
        raise ValueError(f"normative snapshot contains a symlink: {relative}")
    paths = {
        path
        for path in candidates
        if path.is_file()
        and "evidence" not in path.relative_to(ACCEPTANCE).parts
        and "__pycache__" not in path.relative_to(ACCEPTANCE).parts
        and path.suffix != ".pyc"
    }
    paths.add(ROOT / ".scratch" / "monotone-curve-approximation" / "spec.md")
    paths.update(SPECIFICATION.glob("0[1-8]-*.md"))
    paths.update(
        {
            MODEL_SCHEMA_PATH,
            REPORT_SCHEMA_PATH,
            BUNDLE_MANIFEST_SCHEMA_PATH,
            LLM_ADVICE_SCHEMA_PATH,
            RESOLVED_POLICY_SCHEMA_PATH,
            REGISTRY_SCHEMA_PATH,
        }
    )
    for path in paths:
        current = path
        while current != ROOT:
            if current.is_symlink():
                relative = current.relative_to(ROOT).as_posix()
                raise ValueError(f"normative snapshot path has a symlink component: {relative}")
            current = current.parent
        if not path.is_file():
            relative = path.relative_to(ROOT).as_posix()
            raise ValueError(f"normative snapshot path is not a regular file: {relative}")
    ledger = bytearray()
    for path in sorted(paths, key=lambda p: p.relative_to(ROOT).as_posix().encode()):
        relative = path.relative_to(ROOT).as_posix()
        ledger.extend(f"{relative}\t{path.stat().st_size}\t{sha256_file(path)}\n".encode())
    return hashlib.sha256(ledger).hexdigest(), len(paths)


def require(condition: bool, code: str, failures: list[str]) -> None:
    if not condition:
        failures.append(code)


def balanced_cells(group_sizes: list[int]) -> int:
    """Count tie-safe gaps whose cumulative row share is in closed [0.40, 0.60]."""
    if len(group_sizes) < 2:
        return 0
    row_count = sum(group_sizes)
    lower = math.ceil(0.40 * row_count)
    upper = math.floor(0.60 * row_count)
    cumulative = 0
    count = 0
    for size in group_sizes[:-1]:
        cumulative += size
        count += lower <= cumulative <= upper
    return count


def canonical_json_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def recompute_split_golden(policy: dict[str, Any]) -> dict[str, Any]:
    golden = policy["golden"]
    base_seed = policy["base_seed"]
    repetition = golden["repetition"]
    folds = golden["folds"]
    sizes = golden["group_sizes"]
    x_bits = [
        int.from_bytes(struct.pack(">d", float(value)), "big")
        for value in golden["x_values"]
    ]
    loads = [0] * folds
    assignments: list[list[int]] = [[] for _ in range(folds)]
    for stratum, start in enumerate(range(0, len(x_bits), folds)):
        indices = list(range(start, min(start + folds, len(x_bits))))

        def group_rank(index: int) -> bytes:
            preimage = (
                b"outer-group-rank-v1\0"
                + base_seed.to_bytes(8, "big")
                + repetition.to_bytes(4, "big")
                + stratum.to_bytes(4, "big")
                + x_bits[index].to_bytes(8, "big")
            )
            return hashlib.sha256(preimage).digest()

        ordered_groups = sorted(indices, key=lambda index: (group_rank(index), x_bits[index]))
        ordered_folds = sorted(
            range(folds),
            key=lambda fold: (
                hashlib.sha256(
                    b"outer-fold-priority-v1\0"
                    + base_seed.to_bytes(8, "big")
                    + repetition.to_bytes(4, "big")
                    + stratum.to_bytes(4, "big")
                    + fold.to_bytes(4, "big")
                ).digest(),
                fold,
            ),
        )
        priority = {fold: rank for rank, fold in enumerate(ordered_folds)}
        used: set[int] = set()
        for index in ordered_groups:
            fold = min(
                (candidate for candidate in range(folds) if candidate not in used),
                key=lambda candidate: (loads[candidate], priority[candidate], candidate),
            )
            assignments[fold].append(index)
            loads[fold] += sizes[index]
            used.add(fold)

    entries = [
        {
            "row_ids": [f"g{index}-r{row}" for row in range(sizes[index])],
            "x_bits": f"f64:{bits:016x}",
        }
        for index, bits in enumerate(x_bits)
    ]
    membership_hash = canonical_json_hash(entries)
    full_scope = {
        "base_seed": base_seed,
        "fold": None,
        "kind": "full",
        "membership_sha256": membership_hash,
        "repetition": None,
        "version": "training-scope-v1",
    }
    fold0 = set(assignments[0])
    outer_entries = [entry for index, entry in enumerate(entries) if index not in fold0]
    outer_membership = canonical_json_hash(outer_entries)
    outer_scope = {
        "base_seed": base_seed,
        "fold": 0,
        "kind": "outer_train",
        "membership_sha256": outer_membership,
        "repetition": repetition,
        "version": "training-scope-v1",
    }
    split = {
        "fold": 0,
        "groups": [entries[index] for index in assignments[0]],
        "kind": "outer_test",
        "repetition": repetition,
        "version": "outer-split-v1",
    }
    return {
        "fold_group_indices": assignments,
        "fold_row_loads": loads,
        "full_membership_sha256": membership_hash,
        "full_scope_id": canonical_json_hash(full_scope),
        "fold0_outer_train_membership_sha256": outer_membership,
        "fold0_outer_train_scope_id": canonical_json_hash(outer_scope),
        "fold0_split_id": canonical_json_hash(split),
    }


def recompute_demo_forecast(
    work: dict[str, Any], manifest: dict[str, Any], demo_path: Path
) -> dict[str, int]:
    with demo_path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    n = len(rows)
    group_counts: dict[float, int] = {}
    for row in rows:
        x = float(row["x"])
        group_counts[x] = group_counts.get(x, 0) + 1
    group_sizes = [group_counts[x] for x in sorted(group_counts)]
    g = len(group_sizes)
    validation = work["validation_policy"]["g_at_least_10"]
    folds = validation["outer_folds"]
    repetitions = validation["repetitions"]
    outer_scopes = folds * repetitions
    # The frozen demo has one row per group and exactly balanced folds.
    outer_n = n - n // folds
    outer_g = g - g // folds
    if any(size != 1 for size in group_sizes):
        raise ValueError("frozen demo forecast assumes one row per x group")
    full_cells = balanced_cells(group_sizes)
    outer_cells = balanced_cells([1] * outer_g)

    p1 = work["fit_budget"]["p1_scope_totals"]
    p2 = work["fit_budget"]["p2_branch_expansion"]
    p2_attempts_per_cell = p2["fit_attempts_per_cell_per_direction"]
    p2_structures_per_cell = p2["certified_structures_per_cell_per_direction"]
    directions = p2["directions"]

    registry_fit_attempts = p1["fit_attempts"] * (1 + outer_scopes)
    registry_fit_attempts += p2_attempts_per_cell * directions * (
        full_cells + outer_scopes * outer_cells
    )
    registry_certificates = p1["certified_structures"] * (1 + outer_scopes)
    registry_certificates += p2_structures_per_cell * directions * (
        full_cells + outer_scopes * outer_cells
    )

    objective_weight = work["fit_budget"]["objective_row_weight"]
    certificate_weight = work["fit_budget"]["certificate_row_weight"]
    grid = work["fit_budget"]["profile_grid_rows"]

    def p1_work(scope_n: int) -> int:
        return (
            p1["objective_row_evaluation_factor"] * scope_n * objective_weight
            + p1["certified_structures"] * certificate_weight * (scope_n + grid)
        )

    def p2_work(scope_n: int, cells: int) -> int:
        return cells * directions * (
            p2["objective_row_evaluation_factor_per_cell_per_direction"]
            * scope_n
            * objective_weight
            + p2_structures_per_cell * certificate_weight * (scope_n + grid)
        )

    validation_work = p1_work(n) + outer_scopes * p1_work(outer_n)
    validation_work += p2_work(n, full_cells) + outer_scopes * p2_work(outer_n, outer_cells)

    n_minus = n - 1
    cells_minus = balanced_cells([1] * (g - 1))
    logistic = work["fit_budget"]["p1"]["logistic_v1"]
    two_atom = p2["budget_per_structure"]["two_nonlinear_atoms"]
    influence_fit_attempts = g * (logistic["starts"] + cells_minus * two_atom["starts"])
    influence_certificates = g * (1 + cells_minus)
    influence_work_per_group = (
        logistic["starts"]
        * logistic["outer_evaluations_per_start"]
        * n_minus
        * objective_weight
        + certificate_weight * (n_minus + grid)
        + cells_minus
        * (
            two_atom["starts"]
            * two_atom["outer_evaluations_per_start"]
            * n_minus
            * objective_weight
            + certificate_weight * (n_minus + grid)
        )
    )
    influence_work = g * influence_work_per_group

    bootstrap_count = 200
    oof_appearances = n * repetitions
    bootstrap_work = bootstrap_count * g * repetitions * 8
    pooled_oof_work = oof_appearances * 2 * 8
    # Conservative union: uniform grid + every observed row + c_full + c_-g.
    plot_rows = grid + n + 2
    influence_grid_work = g * plot_rows * 2 * 2
    work_units = (
        validation_work
        + influence_work
        + bootstrap_work
        + pooled_oof_work
        + influence_grid_work
    )

    declared_artifacts = len(manifest["bundle_contract"]["base_complete_artifacts"])
    # Frozen demo runs with the default advisor-off policy and emits all three
    # model artifacts, but not the conditional LLM ledger.
    declared_artifacts += sum(
        path.startswith("models/")
        for path in manifest["bundle_contract"]["conditional_complete_artifacts"]
    )
    metric_nodes = 2 * repetitions + 2 + bootstrap_count + g
    fixed_nodes = 1 + 2 + declared_artifacts + 2
    task_nodes = (
        registry_fit_attempts
        + registry_certificates
        + influence_fit_attempts
        + influence_certificates
        + outer_scopes
        + metric_nodes
        + fixed_nodes
    )

    directory = work["workdir_forecast"]
    total_fit_attempts = registry_fit_attempts + influence_fit_attempts
    workdir_bytes = (
        directory["fixed_private_metadata_bytes"]
        + directory["publication_staging_bytes"]
        + directory["input_snapshot_expansion_multiplier"] * demo_path.stat().st_size
        + 3 * demo_path.stat().st_size * (2 * repetitions + 4)
        + directory["bytes_per_task_node"] * task_nodes
        + directory["bytes_per_fit_attempt"] * total_fit_attempts
        + directory["bytes_per_procedure_oof_appearance"] * 2 * oof_appearances
        + directory["bytes_per_p2_outer_fit"] * outer_scopes
        + directory["bytes_per_bootstrap_resample"] * bootstrap_count
        + directory["bytes_per_bootstrap_group_entry"] * bootstrap_count * g
        + directory["bytes_per_influence_group"] * g
        + directory["bytes_per_plot_row"] * min(plot_rows, directory["plot_rows_max"])
    )
    return {
        "input_snapshot_bytes": demo_path.stat().st_size,
        "n": n,
        "g": g,
        "outer_folds": folds,
        "repetitions": repetitions,
        "full_balanced_cells": full_cells,
        "outer_training_n": outer_n,
        "outer_training_g": outer_g,
        "outer_balanced_cells": outer_cells,
        "registry_fit_attempts": registry_fit_attempts,
        "registry_certificates": registry_certificates,
        "influence_fit_attempts": influence_fit_attempts,
        "influence_certificates": influence_certificates,
        "task_nodes": task_nodes,
        "work_units": work_units,
        "workdir_bytes": workdir_bytes,
        "declared_success_artifacts": declared_artifacts,
    }


def main() -> int:
    failures: list[str] = []
    try:
        manifest = load_json(MANIFEST_PATH)
        schema = load_json(SCHEMA_PATH)
        results_schema = load_json(RESULTS_SCHEMA_PATH)
        evidence_schema = load_json(EVIDENCE_SCHEMA_PATH)
        platform_run_schema = load_json(PLATFORM_RUN_SCHEMA_PATH)
        environment_schema = load_json(ENVIRONMENT_SCHEMA_PATH)
        environment_probe_schema = load_json(ENVIRONMENT_PROBE_SCHEMA_PATH)
        oracle_result_schema = load_json(ORACLE_RESULT_SCHEMA_PATH)
        review_schema = load_json(REVIEW_SCHEMA_PATH)
        source_manifest_schema = load_json(SOURCE_MANIFEST_SCHEMA_PATH)
        source_inventory_schema = load_json(SOURCE_INVENTORY_SCHEMA_PATH)
        source_inventory_policy = load_json(SOURCE_INVENTORY_POLICY_PATH)
        gate_execution_schema = load_json(GATE_EXECUTION_SCHEMA_PATH)
        gate_execution_policy = load_json(GATE_EXECUTION_POLICY_PATH)
        model_schema = load_json(MODEL_SCHEMA_PATH)
        report_schema = load_json(REPORT_SCHEMA_PATH)
        bundle_manifest_schema = load_json(BUNDLE_MANIFEST_SCHEMA_PATH)
        llm_advice_schema = load_json(LLM_ADVICE_SCHEMA_PATH)
        resolved_policy_schema = load_json(RESOLVED_POLICY_SCHEMA_PATH)
        registry_schema = load_json(REGISTRY_SCHEMA_PATH)
        work = load_json(WORK_POLICY_PATH)
        start_slots = load_json(START_SLOT_POLICY_PATH)
        identity_policy = load_json(IDENTITY_POLICY_PATH)
        split_policy = load_json(SPLIT_POLICY_PATH)
        verifier_runtime_project = tomllib.loads(VERIFIER_RUNTIME_PROJECT_PATH.read_text(encoding="utf-8"))
        verifier_runtime_lock = tomllib.loads(VERIFIER_RUNTIME_LOCK_PATH.read_text(encoding="utf-8"))
    except Exception as error:
        print(json.dumps({"status": "FAIL", "failures": [f"JSON:{error}"]}, indent=2))
        return 1

    try:
        import jsonschema

        for candidate in (
            schema,
            results_schema,
            evidence_schema,
            platform_run_schema,
            environment_schema,
            environment_probe_schema,
            oracle_result_schema,
            review_schema,
            source_manifest_schema,
            source_inventory_schema,
            gate_execution_schema,
            model_schema,
            report_schema,
            bundle_manifest_schema,
            llm_advice_schema,
            resolved_policy_schema,
            registry_schema,
        ):
            jsonschema.Draft202012Validator.check_schema(candidate)
        jsonschema.Draft202012Validator(schema).validate(manifest)
        jsonschema.Draft202012Validator(source_inventory_schema).validate(source_inventory_policy)
        jsonschema.Draft202012Validator(gate_execution_schema).validate(gate_execution_policy)
        schema_status = "PASS"
    except Exception as error:  # pragma: no cover - exact dependency failure is evidence
        failures.append(f"SCHEMA:{type(error).__name__}:{error}")
        schema_status = "FAIL"

    try:
        fuzz_completed = subprocess.run(
            [sys.executable, "-I", str(MODEL_REPORT_FUZZ_PATH)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        fuzz_result = json.loads(fuzz_completed.stdout)
        require(
            fuzz_completed.returncode == 0
            and fuzz_result.get("status") == "PASS"
            and fuzz_result.get("schema_reject_cases") == 23
            and fuzz_result.get("schema_accept_cases") == 15
            and len(fuzz_result.get("semantic_validator_requirements", [])) == 13,
            "MODEL_REPORT_NEGATIVE_FUZZ",
            failures,
        )
    except Exception as error:
        failures.append(f"MODEL_REPORT_NEGATIVE_FUZZ:{type(error).__name__}:{error}")

    try:
        archive_fuzz_completed = subprocess.run(
            [sys.executable, "-I", str(SOURCE_ARCHIVE_FUZZ_PATH)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        archive_fuzz_result = json.loads(archive_fuzz_completed.stdout)
        require(
            archive_fuzz_completed.returncode == 0
            and archive_fuzz_result.get("status") == "PASS"
            and archive_fuzz_result.get("accepted_canonical_archives") == 3
            and archive_fuzz_result.get("rejected_mutations") == 12,
            "SOURCE_ARCHIVE_NEGATIVE_FUZZ",
            failures,
        )
    except Exception as error:
        failures.append(f"SOURCE_ARCHIVE_NEGATIVE_FUZZ:{type(error).__name__}:{error}")

    require(report_schema.get("$id", "").startswith("urn:monotone-calibrate"), "PRODUCTION_REPORT_ID", failures)
    require(report_schema.get("properties", {}).get("schema_version", {}).get("const") == "report-v1", "PRODUCTION_REPORT_VERSION", failures)
    require({"report_id", "generated_at"} <= set(report_schema.get("required", [])), "REPORT_ID_TIME_REQUIRED", failures)
    generated_at = report_schema.get("properties", {}).get("generated_at", {})
    require(generated_at.get("format") == "date-time", "REPORT_TIME_FORMAT", failures)
    require(generated_at.get("pattern", "").endswith("Z$"), "REPORT_TIME_NOT_UTC_Z", failures)
    exclusion = report_schema.get("$defs", {}).get("exclusion", {})
    require(not ({"raw_x", "raw_y"} & set(exclusion.get("properties", {}))), "RAW_INVALID_IN_REPORT_SCHEMA", failures)
    model_decisions = set(model_schema.get("properties", {}).get("decision_state", {}).get("enum", []))
    model_recommendations = set(model_schema.get("properties", {}).get("recommendation_status", {}).get("enum", []))
    require("DESCRIPTIVE_ONLY" in model_decisions, "MODEL_DESCRIPTIVE_STATE", failures)
    require("NO_VALIDATED_RECOMMENDATION" in model_recommendations, "MODEL_NO_RECOMMENDATION_STATE", failures)
    require(
        any("constant_v1" in json.dumps(rule) and "not" in rule for rule in model_schema.get("allOf", [])),
        "MODEL_TWO_CONSTANT_COLLAPSE_RULE",
        failures,
    )

    def model_transition(status: str) -> dict[str, Any]:
        for rule in model_schema.get("allOf", []):
            candidate = (
                rule.get("if", {})
                .get("properties", {})
                .get("recommendation_status", {})
                .get("const")
            )
            if candidate == status:
                return rule.get("then", {}).get("properties", {})
        return {}

    require(
        set(model_transition("ONE_RECOMMENDED").get("decision_state", {}).get("enum", []))
        == {
            "NO_UPLIFT_OR_HARM",
            "STATISTICAL_ONLY_SMALL",
            "PRACTICALLY_PROMISING_UNCERTAIN",
            "UNSTABLE_SELECTION",
            "NO_VALID_TWO_SEGMENT",
        },
        "MODEL_ONE_TRANSITION",
        failures,
    )
    require(
        model_transition("TWO_RECOMMENDED").get("decision_state", {}).get("const")
        == "CLEAR_PRACTICAL_UPLIFT",
        "MODEL_TWO_TRANSITION",
        failures,
    )
    require(
        model_transition("NO_VALIDATED_RECOMMENDATION").get("decision_state", {}).get("const")
        == "DESCRIPTIVE_ONLY",
        "MODEL_NO_RECOMMENDATION_TRANSITION",
        failures,
    )
    constant_rules = model_schema.get("$defs", {}).get("segment", {}).get("allOf", [])
    require(
        any(
            rule.get("if", {}).get("properties", {}).get("family_id", {}).get("const") == "constant_v1"
            and rule.get("if", {}).get("properties", {}).get("segment_id", {}).get("enum") == ["left", "right"]
            and rule.get("then", {}).get("properties", {}).get("parameters", {}).get("maxProperties") == 0
            for rule in constant_rules
        ),
        "MODEL_P2_CONSTANT_EMPTY_PARAMETERS",
        failures,
    )

    def report_transition(if_field: str, if_value: str) -> dict[str, Any] | None:
        for rule in report_schema.get("allOf", []):
            status_if = rule.get("if", {}).get("properties", {}).get("status", {})
            candidate = status_if.get("properties", {}).get(if_field, {}).get("const")
            if candidate == if_value:
                return rule.get("then", {}).get("properties", {})
        return None

    succeeded = report_transition("analysis_status", "SUCCEEDED") or {}
    no_recommendation = report_transition("analysis_status", "NO_RECOMMENDATION") or {}
    failed = report_transition("analysis_status", "FAILED") or {}
    one_recommended = report_transition("recommendation_status", "ONE_RECOMMENDED") or {}
    two_recommended = report_transition("recommendation_status", "TWO_RECOMMENDED") or {}
    require(
        set(succeeded.get("status", {}).get("properties", {}).get("recommendation_status", {}).get("enum", []))
        == {"ONE_RECOMMENDED", "TWO_RECOMMENDED"},
        "REPORT_SUCCEEDED_TRANSITION",
        failures,
    )
    require(
        no_recommendation.get("decision", {}).get("properties", {}).get("code", {}).get("const")
        == "DESCRIPTIVE_ONLY",
        "REPORT_DESCRIPTIVE_TRANSITION",
        failures,
    )
    require(
        failed.get("decision", {}).get("properties", {}).get("code", {}).get("const")
        == "PIPELINE_FAILURE",
        "REPORT_FAILED_TRANSITION",
        failures,
    )
    require(
        set(one_recommended.get("decision", {}).get("properties", {}).get("code", {}).get("enum", []))
        == {
            "NO_UPLIFT_OR_HARM",
            "STATISTICAL_ONLY_SMALL",
            "PRACTICALLY_PROMISING_UNCERTAIN",
            "UNSTABLE_SELECTION",
            "NO_VALID_TWO_SEGMENT",
        },
        "REPORT_ONE_TRANSITION",
        failures,
    )
    require(
        two_recommended.get("decision", {}).get("properties", {}).get("code", {}).get("const")
        == "CLEAR_PRACTICAL_UPLIFT",
        "REPORT_TWO_TRANSITION",
        failures,
    )
    decision_validation = {}
    for rule in report_schema.get("allOf", []):
        code = rule.get("if", {}).get("properties", {}).get("decision", {}).get("properties", {}).get("code", {}).get("const")
        if code:
            decision_validation[code] = {
                "reason": rule.get("then", {}).get("properties", {}).get("status", {}).get("properties", {}).get("decision_reason", {}).get("const"),
                "validation": rule.get("then", {}).get("properties", {}).get("validation", {}).get("properties", {}).get("status", {}).get("const"),
            }
    expected_decision_validation = {
        "CLEAR_PRACTICAL_UPLIFT": "available",
        "NO_UPLIFT_OR_HARM": "available",
        "STATISTICAL_ONLY_SMALL": "available",
        "PRACTICALLY_PROMISING_UNCERTAIN": "available",
        "UNSTABLE_SELECTION": "available",
        "NO_VALID_TWO_SEGMENT": "available",
        "DESCRIPTIVE_ONLY": "descriptive_only",
        "PIPELINE_FAILURE": "failed",
    }
    require(
        decision_validation
        == {code: {"reason": code, "validation": status} for code, status in expected_decision_validation.items()},
        "REPORT_DECISION_VALIDATION_MATRIX",
        failures,
    )
    llm_advisor_schema = report_schema.get("$defs", {}).get("llm_advisor", {})
    require(
        {"endpoint_origin_sha256", "normalized_endpoint_sha256"}
        <= set(llm_advisor_schema.get("required", [])),
        "REPORT_LLM_ENDPOINT_PROVENANCE",
        failures,
    )
    require("complete_marker" in bundle_manifest_schema.get("$defs", {}), "COMPLETE_SCHEMA_MISSING", failures)
    require("artifacts" in bundle_manifest_schema.get("properties", {}), "MANIFEST_ARTIFACTS_MISSING", failures)
    llm_policy = manifest["llm_policy"]
    require(llm_policy["output_schema_path"] == LLM_ADVICE_SCHEMA_PATH.relative_to(ROOT).as_posix(), "LLM_SCHEMA_PATH", failures)
    require(llm_policy["output_schema_sha256"] == sha256_file(LLM_ADVICE_SCHEMA_PATH), "LLM_SCHEMA_HASH", failures)
    require(
        llm_policy["start_slot_policy_path"] == START_SLOT_POLICY_PATH.relative_to(ROOT).as_posix(),
        "START_SLOT_POLICY_PATH",
        failures,
    )
    require(
        llm_policy["start_slot_policy_sha256"] == sha256_file(START_SLOT_POLICY_PATH),
        "START_SLOT_POLICY_HASH",
        failures,
    )
    runtime_policy = manifest["verifier_runtime_policy"]
    require(
        runtime_policy["project_path"] == VERIFIER_RUNTIME_PROJECT_PATH.relative_to(ROOT).as_posix(),
        "VERIFIER_RUNTIME_PROJECT_PATH",
        failures,
    )
    require(
        runtime_policy["lock_path"] == VERIFIER_RUNTIME_LOCK_PATH.relative_to(ROOT).as_posix(),
        "VERIFIER_RUNTIME_LOCK_PATH",
        failures,
    )
    require(
        runtime_policy["lock_sha256"] == sha256_file(VERIFIER_RUNTIME_LOCK_PATH),
        "VERIFIER_RUNTIME_LOCK_HASH",
        failures,
    )
    require(
        set(verifier_runtime_project["project"]["dependencies"])
        == {"cryptography==49.0.0", "jsonschema==4.26.0"},
        "VERIFIER_RUNTIME_DIRECT_DEPENDENCIES",
        failures,
    )
    locked_packages = {
        package["name"]: package
        for package in verifier_runtime_lock.get("package", [])
    }
    require(
        locked_packages.get("cryptography", {}).get("version") == "49.0.0"
        and locked_packages.get("jsonschema", {}).get("version") == "4.26.0",
        "VERIFIER_RUNTIME_LOCKED_VERSIONS",
        failures,
    )
    require(
        all(
            package.get("source", {}).get("virtual") == "."
            or (
                str(package.get("sdist", {}).get("hash", "")).startswith("sha256:")
                and package.get("wheels")
                and all(str(wheel.get("hash", "")).startswith("sha256:") for wheel in package["wheels"])
            )
            for package in verifier_runtime_lock.get("package", [])
        ),
        "VERIFIER_RUNTIME_ARTIFACT_HASHES",
        failures,
    )
    source_reference = manifest["source_inventory_policy"]
    require(
        source_reference["path"] == SOURCE_INVENTORY_POLICY_PATH.relative_to(ROOT).as_posix(),
        "SOURCE_INVENTORY_POLICY_PATH",
        failures,
    )
    require(
        source_reference["sha256"] == sha256_file(SOURCE_INVENTORY_POLICY_PATH),
        "SOURCE_INVENTORY_POLICY_HASH",
        failures,
    )
    require(
        source_reference["schema_path"] == SOURCE_INVENTORY_SCHEMA_PATH.relative_to(ROOT).as_posix(),
        "SOURCE_INVENTORY_SCHEMA_PATH",
        failures,
    )
    require(
        source_reference["schema_sha256"] == sha256_file(SOURCE_INVENTORY_SCHEMA_PATH),
        "SOURCE_INVENTORY_SCHEMA_HASH",
        failures,
    )
    source_snapshot_policy = manifest["source_snapshot_policy"]
    require(
        source_snapshot_policy["path"] == "evidence/acceptance/source-snapshot.zip"
        and source_snapshot_policy["builder"] == "tools/build_source_snapshot.py"
        and source_snapshot_policy["builder"] in source_inventory_policy["required_files"],
        "SOURCE_SNAPSHOT_POLICY",
        failures,
    )
    gate_execution_reference = manifest["gate_execution_policy"]
    require(
        gate_execution_reference["path"] == GATE_EXECUTION_POLICY_PATH.relative_to(ROOT).as_posix(),
        "GATE_EXECUTION_POLICY_PATH",
        failures,
    )
    require(
        gate_execution_reference["sha256"] == sha256_file(GATE_EXECUTION_POLICY_PATH),
        "GATE_EXECUTION_POLICY_HASH",
        failures,
    )
    require(
        gate_execution_reference["schema_path"] == GATE_EXECUTION_SCHEMA_PATH.relative_to(ROOT).as_posix(),
        "GATE_EXECUTION_SCHEMA_PATH",
        failures,
    )
    require(
        gate_execution_reference["schema_sha256"] == sha256_file(GATE_EXECUTION_SCHEMA_PATH),
        "GATE_EXECUTION_SCHEMA_HASH",
        failures,
    )
    for semantic in source_inventory_policy["semantic_files"].values():
        semantic_schema = ROOT / semantic["schema_path"]
        require(semantic["schema_sha256"] == sha256_file(semantic_schema), f"SEMANTIC_SCHEMA_HASH:{semantic['schema_path']}", failures)
    require(
        set(source_inventory_policy["required_files"])
        >= {
            "src/monotone_calibrate/cli.py",
            "src/monotone_calibrate/engine.py",
            "tests/acceptance/test_data_contract.py",
            "tests/acceptance/test_model_registry.py",
            "docs/acceptance/verify_acceptance_results.py",
        },
        "SOURCE_REQUIRED_IMPLEMENTATION_FILES",
        failures,
    )
    require(
        {".agents", ".scratch", "docs", "prototypes", "src", "tests", "tools"}
        <= set(source_inventory_policy["source_roots"]),
        "SOURCE_ROOT_COVERAGE",
        failures,
    )
    require(
        {".git", ".hypothesis", ".pytest_cache", ".venv", "evidence"}
        <= set(source_inventory_policy["excluded_top_level_entries"]),
        "SOURCE_TOP_LEVEL_POLICY",
        failures,
    )
    require(
        resolved_policy_schema.get("properties", {}).get("validation", {}).get("properties", {}).get("r2_warning_threshold", {}).get("const") == 0.6,
        "RESOLVED_POLICY_R2_THRESHOLD",
        failures,
    )
    require(
        len(registry_schema.get("properties", {}).get("families", {}).get("prefixItems", [])) == 8,
        "REGISTRY_EXACT_FAMILY_ENTRIES",
        failures,
    )
    identity_reference = manifest["identity_policy"]
    require(
        identity_reference["projection_policy_path"] == IDENTITY_POLICY_PATH.relative_to(ROOT).as_posix(),
        "IDENTITY_POLICY_PATH",
        failures,
    )
    require(
        identity_reference["projection_policy_sha256"] == sha256_file(IDENTITY_POLICY_PATH),
        "IDENTITY_POLICY_HASH",
        failures,
    )
    require(
        identity_reference["split_policy_path"] == SPLIT_POLICY_PATH.relative_to(ROOT).as_posix(),
        "SPLIT_POLICY_PATH",
        failures,
    )
    require(
        identity_reference["split_policy_sha256"] == sha256_file(SPLIT_POLICY_PATH),
        "SPLIT_POLICY_HASH",
        failures,
    )
    recomputed_split = recompute_split_golden(split_policy)
    for key, actual in recomputed_split.items():
        require(split_policy["golden"].get(key) == actual, f"SPLIT_GOLDEN:{key}", failures)
    identity_goldens = identity_policy.get("goldens", [])
    for golden in identity_goldens:
        preimage = golden.get("canonical_bytes_utf8", "").encode("utf-8")
        require(hashlib.sha256(preimage).hexdigest() == golden.get("sha256"), f"IDENTITY_GOLDEN:{golden.get('id')}", failures)
        endpoint = golden.get("normalized_endpoint")
        if endpoint is not None:
            require(
                hashlib.sha256(endpoint.encode("utf-8")).hexdigest() == golden.get("normalized_endpoint_sha256"),
                f"ENDPOINT_GOLDEN:{golden.get('id')}",
                failures,
            )
    require(len(identity_policy.get("base_url_rejection_goldens", [])) >= 8, "URL_REJECTION_GOLDENS", failures)
    require(
        {
            "structure-poly1-v1",
            "instance-poly1-v1",
            "structure-p2-poly1-poly1-v1",
            "instance-p2-poly1-poly1-v1",
        }
        <= {golden.get("id") for golden in identity_goldens},
        "MODEL_IDENTITY_P1_P2_GOLDENS",
        failures,
    )

    variants = start_slots.get("nonlinear_variants", [])
    variant_ids = [variant.get("variant_id") for variant in variants]
    require(len(variant_ids) == 5 and len(set(variant_ids)) == 5, "START_SLOT_VARIANTS", failures)
    variants_by_id = {variant.get("variant_id"): variant for variant in variants}
    for variant in variants:
        names = variant.get("parameter_names", [])
        bounds = variant.get("bounds", [])
        require(len(names) == len(bounds) > 0, f"START_SLOT_BOUNDS:{variant.get('variant_id')}", failures)
        for bound in bounds:
            require(
                len(bound) == 2
                and all(isinstance(value, (int, float)) and math.isfinite(value) for value in bound)
                and bound[0] < bound[1],
                f"START_SLOT_BOUND:{variant.get('variant_id')}",
                failures,
            )
        for schedule_name in ("p1_three", "p1_five", "p2_three", "p2_five"):
            if schedule_name not in variant:
                continue
            schedule = variant[schedule_name]
            expected_count = 3 if schedule_name.endswith("three") else 5
            require(len(schedule) == expected_count, f"START_SLOT_COUNT:{variant.get('variant_id')}:{schedule_name}", failures)
            for vector in schedule:
                valid = len(vector) == len(bounds)
                if valid:
                    valid = all(
                        isinstance(value, (int, float))
                        and math.isfinite(value)
                        and bounds[index][0] <= value <= bounds[index][1]
                        for index, value in enumerate(vector)
                    )
                require(valid, f"START_SLOT_VECTOR:{variant.get('variant_id')}:{schedule_name}", failures)

    for variant_id, rule in start_slots.get("p1_rule", {}).items():
        variant = variants_by_id.get(variant_id, {})
        schedule = variant.get(rule.get("schedule"), [])
        ordinals = rule.get("anchor_ordinals", []) + rule.get("replaceable_ordinals", [])
        require(
            len(schedule) == rule.get("start_count")
            and sorted(ordinals) == list(range(rule.get("start_count", -1)))
            and not (set(rule.get("anchor_ordinals", [])) & set(rule.get("replaceable_ordinals", []))),
            f"P1_START_RULE:{variant_id}",
            failures,
        )
    p2_rules = start_slots.get("p2_rule", {})
    require(p2_rules.get("zero_nonlinear_atoms", {}).get("replaceable_ordinals") == [], "P2_ZERO_START_RULE", failures)
    require(p2_rules.get("one_nonlinear_atom", {}).get("replaceable_ordinals") == [2], "P2_ONE_START_RULE", failures)
    require(p2_rules.get("two_nonlinear_atoms", {}).get("replaceable_ordinals") == [3, 4], "P2_TWO_START_RULE", failures)
    require(balanced_cells([40, 1, 1, 1, 1, 1, 1, 1, 1, 52]) == 9, "ROW_WEIGHTED_BALANCED_CELLS", failures)

    fixtures = manifest["fixture_sets"]
    gates = manifest["gates"]
    fixture_ids = [item["id"] for item in fixtures]
    gate_ids = [item["id"] for item in gates]
    require(len(fixture_ids) == len(set(fixture_ids)), "DUPLICATE_FIXTURE_ID", failures)
    require(len(gate_ids) == len(set(gate_ids)), "DUPLICATE_GATE_ID", failures)
    require(len(gates) == 20, "GATE_COUNT_NOT_20", failures)
    require(all(item["status"] == "NOT_RUN" for item in gates), "IMPLEMENTATION_STATUS_NOT_NOT_RUN", failures)
    execution_gates = gate_execution_policy["gates"]
    require(
        gate_execution_policy["required_platforms"] == manifest["platform_policy"]["required"],
        "GATE_EXECUTION_PLATFORMS",
        failures,
    )
    require(
        [item["gate_id"] for item in execution_gates] == gate_ids,
        "GATE_EXECUTION_ORDER",
        failures,
    )
    require(
        gate_execution_policy.get("execution_context", {}).get("runner") == ".venv/bin/python"
        and gate_execution_policy.get("execution_context", {}).get("working_directory") == "."
        and gate_execution_policy.get("execution_context", {}).get("source_materialization") == "canonical-source-zip-v1"
        and gate_execution_policy.get("execution_context", {}).get("environment", {}).get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") == "1",
        "GATE_EXECUTION_CONTEXT",
        failures,
    )
    for design_gate, execution_gate in zip(gates, execution_gates, strict=False):
        require(
            execution_gate["test_ids"] == [f"{design_gate['id']}::contract-v1"],
            f"GATE_EXECUTION_TEST_ID:{design_gate['id']}",
            failures,
        )
        require(
            execution_gate["tolerance_policy"] == design_gate.get("tolerance_policy"),
            f"GATE_EXECUTION_TOLERANCE:{design_gate['id']}",
            failures,
        )
        require(
            execution_gate["argv"][:2] == [".venv/bin/python", "-I"],
            f"GATE_EXECUTION_ARGV:{design_gate['id']}",
            failures,
        )
        require(
            bool(execution_gate["source_targets"])
            and set(execution_gate["source_targets"]) <= set(source_inventory_policy["required_files"]),
            f"GATE_EXECUTION_SOURCE_TARGET:{design_gate['id']}",
            failures,
        )
    require(
        results_schema.get("properties", {}).get("acceptance_id", {}).get("const")
        == manifest["acceptance_id"],
        "RESULTS_ACCEPTANCE_ID",
        failures,
    )
    result_gate_schemas = results_schema.get("properties", {}).get("gates", {}).get("prefixItems", [])
    require(len(result_gate_schemas) == len(gates), "RESULTS_GATE_SCHEMA_COUNT", failures)
    for design_gate, result_gate_schema in zip(gates, result_gate_schemas, strict=False):
        fixed = result_gate_schema.get("allOf", [{}, {}])[1].get("properties", {})
        require(fixed.get("gate_id", {}).get("const") == design_gate["id"], f"RESULT_GATE_ID:{design_gate['id']}", failures)
        require(fixed.get("priority", {}).get("const") == design_gate["priority"], f"RESULT_GATE_PRIORITY:{design_gate['id']}", failures)
        require(fixed.get("evidence_path", {}).get("const") == design_gate["evidence_path"], f"RESULT_GATE_PATH:{design_gate['id']}", failures)
        require(fixed.get("declared_executor", {}).get("const") == design_gate["command_or_executor"], f"RESULT_GATE_EXECUTOR:{design_gate['id']}", failures)
        require(
            fixed.get("design_gate_sha256", {}).get("const") == canonical_json_hash(design_gate),
            f"RESULT_GATE_HASH:{design_gate['id']}",
            failures,
        )
    require((ACCEPTANCE / "verify_acceptance_results.py").is_file(), "RESULTS_VERIFIER_MISSING", failures)
    verifier_source = (ACCEPTANCE / "verify_acceptance_results.py").read_text(encoding="utf-8")
    require("--reviewer-public-key" in verifier_source, "RESULTS_EXTERNAL_REVIEW_KEYS", failures)
    require("Ed25519" in verifier_source, "RESULTS_REVIEW_SIGNATURES", failures)
    require("enumerate_source_inventory" in verifier_source, "RESULTS_EXACT_SOURCE_INVENTORY", failures)
    require("MAX_HASHED_TOTAL_BYTES" in verifier_source, "RESULTS_BOUNDED_HASHING", failures)
    require("--trusted-release-digest" in verifier_source, "RESULTS_EXTERNAL_RELEASE_ANCHOR", failures)
    require("release_snapshot_digest" in verifier_source, "RESULTS_RELEASE_SNAPSHOT_RECOMPUTE", failures)
    require("verifier_runtime_lock_digest" in verifier_source, "RESULTS_FROZEN_VERIFIER_RUNTIME", failures)
    require("validate_source_archive" in verifier_source and "ZIP_STORED" in verifier_source, "RESULTS_AUTHORITATIVE_SOURCE_ARCHIVE", failures)
    design_ref_schema = results_schema.get("properties", {}).get("design_manifest", {})
    design_ref_rules = design_ref_schema.get("allOf", [])
    design_ref_constants = (
        design_ref_rules[1].get("properties", {})
        if len(design_ref_rules) == 2
        else {}
    )
    manifest_sha256 = sha256_file(MANIFEST_PATH)
    require(
        design_ref_constants.get("path", {}).get("const") == MANIFEST_PATH.relative_to(ROOT).as_posix(),
        "RESULTS_DESIGN_MANIFEST_PATH_PIN",
        failures,
    )
    require(
        design_ref_constants.get("sha256", {}).get("const") == manifest_sha256,
        "RESULTS_DESIGN_MANIFEST_HASH_PIN",
        failures,
    )
    require(
        f'EXPECTED_DESIGN_MANIFEST_SHA256 = "{manifest_sha256}"' in verifier_source,
        "VERIFIER_DESIGN_MANIFEST_HASH_PIN",
        failures,
    )
    require(
        evidence_schema.get("properties", {}).get("gate_id", {}).get("type") == "string",
        "EVIDENCE_GATE_ID_TYPE",
        failures,
    )
    require(
        evidence_schema.get("properties", {}).get("schema_version", {}).get("const")
        == "acceptance-evidence-v2",
        "EVIDENCE_SCHEMA_VERSION",
        failures,
    )
    require(
        {"fixture_ids", "fixture_ledger_sha256", "platform_runs", "oracle_results", "artifact_hashes"}
        <= set(evidence_schema.get("required", [])),
        "EVIDENCE_SEMANTIC_BINDINGS",
        failures,
    )
    evidence_platform = evidence_schema.get("$defs", {}).get("platform_run", {})
    require(
        {"environment", "execution_context", "argv", "source_targets", "raw_result", "log"}
        <= set(evidence_platform.get("required", [])),
        "EVIDENCE_PLATFORM_EXECUTION_BINDINGS",
        failures,
    )
    require(
        {"environment_id", "identity_payload", "raw_probe"}
        <= set(environment_schema.get("required", []))
        and len(environment_schema.get("$defs", {}).get("identity_payload", {}).get("oneOf", [])) == 2,
        "ENVIRONMENT_EVIDENCE_BINDINGS",
        failures,
    )
    environment_identity_required = set(
        environment_schema.get("$defs", {}).get("identity_payload", {}).get("required", [])
    )
    require(
        {
            "python_build",
            "python_compiler",
            "python_executable_sha256",
            "solver_backend",
            "cpu_numerical_features",
            "blas_lapack",
            "thread_policy",
            "floating_point_mode",
        }
        <= environment_identity_required,
        "ENVIRONMENT_NUMERICAL_IDENTITY",
        failures,
    )
    require(
        environment_probe_schema.get("properties", {}).get("schema_version", {}).get("const")
        == "acceptance-environment-probe-v1",
        "ENVIRONMENT_PROBE_SCHEMA",
        failures,
    )
    require(
        "release_snapshot_digest" in results_schema.get("required", [])
        and "verifier_runtime_lock_sha256" in results_schema.get("required", [])
        and "source_archive" in results_schema.get("required", [])
        and "release_snapshot_digest" in evidence_schema.get("required", [])
        and "verifier_runtime_lock_sha256" in evidence_schema.get("required", [])
        and "source_archive_sha256" in evidence_schema.get("required", [])
        and "release_snapshot_digest"
        in review_schema.get("properties", {}).get("signed_payload", {}).get("required", []),
        "RELEASE_DIGEST_SIGNED_BINDING",
        failures,
    )
    require(
        "verifier_runtime_lock_sha256"
        in review_schema.get("properties", {}).get("signed_payload", {}).get("required", [])
        and "source_archive_sha256" in source_manifest_schema.get("required", [])
        and "source_archive_sha256" in environment_identity_required,
        "SOURCE_ARCHIVE_RUNTIME_SIGNED_BINDING",
        failures,
    )
    require(
        "tolerance_policy" in oracle_result_schema.get("required", [])
        and oracle_result_schema.get("properties", {}).get("observations", {}).get("maxItems") == 1,
        "ORACLE_EXACT_TOLERANCE_CLAIM",
        failures,
    )
    require(
        results_schema.get("properties", {}).get("independence_attestation", {}).get("const")
        == "distinct-external-ed25519-trust-roots-on-one-evidence-digest-v1",
        "RESULTS_REVIEW_AUTHENTICATION",
        failures,
    )

    fixture_set = set(fixture_ids)
    for gate in gates:
        require(set(gate["fixture_ids"]) <= fixture_set, f"UNKNOWN_FIXTURE:{gate['id']}", failures)
        require(gate["evidence_path"].startswith("evidence/acceptance/"), f"BAD_EVIDENCE_PATH:{gate['id']}", failures)

    empirical = {item["id"]: item for item in fixtures if item["kind"] == "empirical"}
    require(len(empirical) >= 2, "EMPIRICAL_SENTINELS_LT_2", failures)
    for item in empirical.values():
        for relative, expected in zip(item.get("files", []), item.get("sha256", []), strict=True):
            path = ROOT / relative
            require(path.is_file(), f"MISSING_FILE:{relative}", failures)
            if path.is_file():
                require(sha256_file(path) == expected, f"HASH_MISMATCH:{relative}", failures)

    demo = next(item for item in fixtures if item["id"] == "DEMO_STRONG_P2_V1")
    demo_path = ROOT / demo["files"][0]
    require(sha256_file(demo_path) == demo["sha256"][0], "DEMO_HASH_MISMATCH", failures)

    work_reference = manifest["work_policy"]
    require(work_reference["path"] == WORK_POLICY_PATH.relative_to(ROOT).as_posix(), "WORK_POLICY_PATH", failures)
    require(work_reference["sha256"] == sha256_file(WORK_POLICY_PATH), "WORK_POLICY_HASH", failures)
    require(
        work["validation_policy"]["split_policy_path"] == SPLIT_POLICY_PATH.relative_to(ROOT).as_posix(),
        "WORK_SPLIT_POLICY_PATH",
        failures,
    )
    require(
        work["validation_policy"]["split_policy_sha256"] == sha256_file(SPLIT_POLICY_PATH),
        "WORK_SPLIT_POLICY_HASH",
        failures,
    )
    computed_forecast = recompute_demo_forecast(work, manifest, demo_path)
    frozen_forecast = work["demo_forecast"]
    for key, actual in computed_forecast.items():
        require(frozen_forecast.get(key) == actual, f"DEMO_FORECAST:{key}", failures)
    advisor_calls = 1 + computed_forecast["outer_folds"] * computed_forecast["repetitions"]
    advisor_policy = work["llm_start_advisor"]
    require(
        advisor_policy["start_slot_policy_path"] == START_SLOT_POLICY_PATH.relative_to(ROOT).as_posix(),
        "WORK_START_SLOT_POLICY_PATH",
        failures,
    )
    require(
        advisor_policy["start_slot_policy_sha256"] == sha256_file(START_SLOT_POLICY_PATH),
        "WORK_START_SLOT_POLICY_HASH",
        failures,
    )
    enabled_forecast = {
        "advisor_calls": advisor_calls,
        "additional_conditional_artifacts": 1,
        "task_nodes": computed_forecast["task_nodes"] + advisor_calls + 1,
        "work_units": computed_forecast["work_units"],
        "workdir_bytes": (
            computed_forecast["workdir_bytes"]
            + (advisor_calls + 1) * work["workdir_forecast"]["bytes_per_task_node"]
            + advisor_calls * advisor_policy["reserved_workdir_bytes_per_call"]
        ),
    }
    for key, actual in enabled_forecast.items():
        require(
            work["demo_advisor_enabled_forecast"].get(key) == actual,
            f"DEMO_ADVISOR_FORECAST:{key}",
            failures,
        )
    # A parser-valid near-10 MiB file can spend most bytes on unique non-ASCII
    # row IDs. Every ID then expands up to 3x and is repeated for both
    # procedures across 20 repetitions. This lower bound caught an earlier
    # non-conservative constant-per-row forecast.
    hostile_input_bytes = 10 * 1024 * 1024
    hostile_repetitions = 20
    hostile_n = 10_000
    hostile_g = 8
    hostile_paired_oof = hostile_n * hostile_repetitions
    hostile_bootstrap = 200
    hostile_variable_reserve = (
        3 * hostile_input_bytes * (2 * hostile_repetitions + 4)
        + work["workdir_forecast"]["bytes_per_procedure_oof_appearance"]
        * 2
        * hostile_paired_oof
        + work["workdir_forecast"]["bytes_per_bootstrap_group_entry"]
        * hostile_bootstrap
        * hostile_g
    )
    hostile_required_lower_bound = (
        3 * hostile_input_bytes * 2 * hostile_repetitions
        + 32 * hostile_bootstrap * hostile_g
    )
    require(
        hostile_variable_reserve >= hostile_required_lower_bound,
        "HOSTILE_IDENTIFIER_WORKDIR_BOUND",
        failures,
    )
    limits = work["limits"]
    require(computed_forecast["work_units"] <= limits["max_work_units"], "DEMO_WORK_LIMIT", failures)
    require(computed_forecast["task_nodes"] <= limits["max_task_nodes"], "DEMO_TASK_LIMIT", failures)
    require(computed_forecast["workdir_bytes"] <= limits["max_workdir_bytes"], "DEMO_WORKDIR_LIMIT", failures)
    require(enabled_forecast["task_nodes"] <= limits["max_task_nodes"], "DEMO_ADVISOR_TASK_LIMIT", failures)
    require(enabled_forecast["work_units"] <= limits["max_work_units"], "DEMO_ADVISOR_WORK_LIMIT", failures)
    require(enabled_forecast["workdir_bytes"] <= limits["max_workdir_bytes"], "DEMO_ADVISOR_WORKDIR_LIMIT", failures)

    report_fixture = next(item for item in fixtures if item["id"] == "REPORT_STATIC_GOLDEN_V1")
    report_digest = tree_digest(REPORT_TREE)
    require(report_digest == report_fixture["sha256"][0], "REPORT_TREE_DIGEST_MISMATCH", failures)

    required_contracts = {f"0{index}" for index in range(1, 8)}
    covered_contracts: set[str] = set()
    for gate in gates:
        for source in gate["requirement_sources"]:
            match = re.match(r"^(0[1-7])\b", source)
            if match:
                covered_contracts.add(match.group(1))
    require(covered_contracts == required_contracts, "CONTRACT_COVERAGE_INCOMPLETE", failures)

    bundle_contract = manifest["bundle_contract"]
    bundle = bundle_contract["base_complete_artifacts"]
    conditional = list(bundle_contract["conditional_complete_artifacts"])
    all_paths = bundle + conditional
    require(len(all_paths) == len(set(all_paths)), "DUPLICATE_BUNDLE_PATH", failures)
    path_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,127})*$")
    require(all(len(path) <= 512 and path_pattern.fullmatch(path) for path in all_paths), "BAD_BUNDLE_PATH_GRAMMAR", failures)
    require("models/two-segment-model.json" not in bundle, "P2_MODEL_UNCONDITIONAL", failures)

    csvw = load_json(ACCEPTANCE / "csvw-contract-v1.json")
    required_csv = {path for path in bundle if path.endswith(".csv") or path.endswith(".csv.gz")}
    csvw_tables = set(csvw["tables"])
    require(required_csv <= csvw_tables, "CSVW_SUCCESS_TABLE_COVERAGE", failures)
    require("predictions.csv" in csvw_tables, "CSVW_PREDICTION_MISSING", failures)
    for table_name, table in csvw["tables"].items():
        names = [column["name"] for column in table["columns"]]
        require(len(names) == len(set(names)), f"CSVW_DUPLICATE_COLUMN:{table_name}", failures)
        require(set(table["primaryKey"]) <= set(names), f"CSVW_BAD_PRIMARY_KEY:{table_name}", failures)
    bootstrap_columns = {
        column["name"] for column in csvw["tables"]["bootstrap-resamples.csv.gz"]["columns"]
    }
    influence_columns = {
        column["name"] for column in csvw["tables"]["influence-groups.csv"]["columns"]
    }
    require("sampled_x_group_multiplicities_json" in bootstrap_columns, "BOOTSTRAP_MULTIPLICITY_EXPORT", failures)
    require(
        not ({"delta_oof_rmse_relative", "delta_rel_mse_uplift"} & influence_columns),
        "INFLUENCE_FULL_PIPELINE_COLUMNS",
        failures,
    )
    require(
        {
            "parameter_delta_status",
            "parameter_delta_linf_relative",
            "parameter_deltas_json",
            "certificate_changes",
        }
        <= influence_columns,
        "INFLUENCE_PARAMETER_DELTA_EXPORT",
        failures,
    )

    normative_files = [
        ROOT / ".scratch" / "monotone-curve-approximation" / "spec.md",
        *SPECIFICATION.glob("0[1-8]-*.md"),
        ACCEPTANCE / "synthetic-fixtures-v1.md",
        ACCEPTANCE / "traceability.md",
    ]
    for path in normative_files:
        content = path.read_text(encoding="utf-8")
        require(re.search(r"\bTBD\b", content, flags=re.IGNORECASE) is None, f"TBD:{path.relative_to(ROOT)}", failures)

    snapshot_digest, snapshot_files = normative_snapshot_digest()
    result = {
        "status": "PASS" if not failures else "FAIL",
        "manifest_schema": schema_status,
        "gates": len(gates),
        "fixtures": len(fixtures),
        "empirical_sentinels": len(empirical),
        "contract_groups_covered": sorted(covered_contracts),
        "report_tree_digest": report_digest,
        "demo_forecast": computed_forecast,
        "demo_advisor_enabled_forecast": enabled_forecast,
        "normative_snapshot_digest": snapshot_digest,
        "normative_snapshot_files": snapshot_files,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
