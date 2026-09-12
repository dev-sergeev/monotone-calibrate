"""Application orchestration for one immutable calibration run.

The complete registry baselines are always fitted independently. Optional
LLM-SR discovers a third formula via bounded expression trees and numerical
optimization, with a common untouched holdout for all three procedures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import os
from pathlib import Path
import shutil
import tempfile
from typing import Mapping

import numpy as np

from .config import LLMConfig, LLMConfigError
from .data import ObservationSet, read_xy_csv
from .engine import (
    CandidateSet,
    FitOptions,
    fit_candidates,
)
from .hypotheses import HypothesisSpace
from .comparison import FormulaComparison, compare_formula_discovery
from .formula_search import FormulaSearchResult, OUTPUT_SCHEMA_VERSION, PROMPT_VERSION
from .reporting import ReportBundle, write_report_bundle
from .validation import (
    ValidationOptions,
    ValidationResult,
    validate_candidates,
)


class CalibrationRunError(RuntimeError):
    """A typed application-level failure safe to expose at the CLI boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RunRequest:
    """Inputs and policy knobs for one calibration run."""

    input_path: str | Path
    output_dir: str | Path
    dotenv_path: str | Path | None = ".env"
    llm_symbolic_search: bool = False
    # Backward-compatible spelling: the old flag now enables symbolic search.
    llm_start_advisor: bool = False
    fit_options: FitOptions = field(default_factory=FitOptions)
    validation_options: ValidationOptions = field(default_factory=ValidationOptions)

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_path", Path(self.input_path))
        object.__setattr__(self, "output_dir", Path(self.output_dir))
        if self.dotenv_path is not None:
            object.__setattr__(self, "dotenv_path", Path(self.dotenv_path))
        if (
            self.fit_options.start_overrides
            or self.fit_options.hypothesis_space is not None
        ):
            raise ValueError(
                "RunRequest.fit_options cannot contain externally supplied starts or "
                "a hypothesis space; application orchestration owns those seams"
            )


@dataclass(frozen=True, slots=True)
class RunResult:
    """Frozen result of a successfully published run."""

    bundle: ReportBundle
    dataset: ObservationSet
    candidates: CandidateSet
    validation: ValidationResult
    llm_advisor: Mapping[str, object]
    warning_codes: tuple[str, ...]
    formula_comparison: FormulaComparison


def _resolve_formula_comparison(x, y, request: RunRequest):
    config = None
    try:
        config = LLMConfig.load(
            request.dotenv_path,
            force_enable=request.llm_symbolic_search or request.llm_start_advisor,
        )
        comparison = compare_formula_discovery(x, y, config, request.fit_options)
    except LLMConfigError as error:
        comparison = FormulaComparison(
            FormulaSearchResult("CONFIG_INVALID", warning_codes=(error.code,))
        )
    space = HypothesisSpace.full_registry()
    search = comparison.search
    provenance = {
        "status": search.status,
        "mode": "llm_formula_discovery",
        "provider_model": None if config is None else config.model,
        "max_output_tokens": None if config is None else config.max_output_tokens,
        "reasoning_effort": None if config is None else config.reasoning_effort,
        "endpoint_origin_sha256": None
        if config is None or config.base_url is None
        else sha256(config.base_url.encode()).hexdigest(),
        "prompt_version": PROMPT_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "calls_requested": search.calls_succeeded + search.calls_failed,
        "calls_succeeded": search.calls_succeeded,
        "calls_failed": search.calls_failed,
        "hypotheses_evaluated": len(search.trace),
        "hypotheses_accepted": sum(r["status"] == "VALID" for r in search.trace),
        "portfolio_size": len(space.hypotheses),
        "p1_hypotheses": len(space.p1_family_ids),
        "p2_hypotheses": len(space.p2_family_pairs),
        "scope": "independent_formula_discovery"
        if config is not None and config.enabled
        else "deterministic_full_registry",
        "used_in_validation": False,
        "family_search_space_changed": False,
        "selection_policy_changed": False,
        "certificate_policy_changed": False,
        "hypothesis_space_hash": space.space_hash,
        "formula_source": "bounded_expression_tree_plus_numeric_optimizer",
    }
    return comparison, provenance, comparison.warning_codes


def _final_bundle(staged: ReportBundle, output: Path) -> ReportBundle:
    """Rebase paths returned by the writer after the atomic directory move."""

    def moved(path: Path | None) -> Path | None:
        return None if path is None else output / path.name

    return ReportBundle(
        root=output,
        report_html=output / staged.report_html.name,
        report_json=output / staged.report_json.name,
        plot_one_svg=output / staged.plot_one_svg.name,
        plot_two_svg=moved(staged.plot_two_svg),
        observations_csv=output / staged.observations_csv.name,
        recommended_model=moved(staged.recommended_model),
        manifest_json=output / staged.manifest_json.name,
    )


def _publish_report(
    dataset: ObservationSet,
    candidates: CandidateSet,
    validation: ValidationResult,
    output: Path,
    *,
    llm_advisor: Mapping[str, object],
    warning_codes: tuple[str, ...],
    formula_comparison: FormulaComparison,
) -> ReportBundle:
    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=parent))
    staged_output = staging_root / "bundle"
    try:
        staged = write_report_bundle(
            dataset,
            candidates,
            validation,
            staged_output,
            llm_advisor=llm_advisor,
            extra_warning_codes=warning_codes,
            formula_comparison=formula_comparison,
        )
        if output.exists():
            raise CalibrationRunError(
                "OUTPUT_EXISTS",
                f"output already exists: {output}",
            )
        os.replace(staged_output, output)
        return _final_bundle(staged, output)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def run_calibration(request: RunRequest) -> RunResult:
    """Select hypotheses, fit, validate, and atomically publish one report."""

    output = Path(request.output_dir)
    if output.exists():
        raise CalibrationRunError("OUTPUT_EXISTS", f"output already exists: {output}")

    dataset = read_xy_csv(request.input_path)
    if dataset.fit_readiness != "READY":
        raise CalibrationRunError(
            "FIT_NOT_READY",
            f"input is not ready for fitting: {dataset.fit_readiness}",
        )
    x = np.asarray([row.x for row in dataset.observations], dtype=np.float64)
    y = np.asarray([row.y for row in dataset.observations], dtype=np.float64)

    # The LLM can never narrow, replace, or tune either registry baseline.
    candidates = fit_candidates(x, y, request.fit_options)
    formula_comparison, provenance, application_warnings = _resolve_formula_comparison(
        x, y, request
    )
    validation = validate_candidates(
        x,
        y,
        candidates,
        request.validation_options,
    )
    bundle = _publish_report(
        dataset,
        candidates,
        validation,
        output,
        llm_advisor=provenance,
        warning_codes=application_warnings,
        formula_comparison=formula_comparison,
    )
    warnings = tuple(
        sorted(
            set(dataset.warning_codes)
            | set(validation.warning_codes)
            | set(application_warnings)
        )
    )
    return RunResult(
        bundle=bundle,
        dataset=dataset,
        candidates=candidates,
        validation=validation,
        llm_advisor=provenance,
        warning_codes=warnings,
        formula_comparison=formula_comparison,
    )
