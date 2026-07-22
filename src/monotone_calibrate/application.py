"""Application orchestration for one immutable calibration run.

An optional LLM-SR stage now selects a finite portfolio of typed equation
skeletons before fitting.  The numerical engine and outer validation replay
that frozen portfolio; generated code and numeric coefficients are never
accepted from the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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
from .reporting import ReportBundle, write_report_bundle
from .symbolic_search import (
    OUTPUT_SCHEMA_VERSION,
    PROMPT_VERSION,
    SymbolicSearchOptions,
    SymbolicSearchResult,
    run_symbolic_search,
)
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
        if self.fit_options.start_overrides or self.fit_options.hypothesis_space is not None:
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


def _base_provenance(
    *,
    status: str,
    config: LLMConfig | None,
    hypothesis_space: HypothesisSpace,
    search: SymbolicSearchResult | None = None,
    search_options: SymbolicSearchOptions | None = None,
) -> dict[str, object]:
    endpoint_digest = (
        None
        if config is None or config.base_url is None
        else sha256(config.base_url.encode("utf-8")).hexdigest()
    )
    calls_requested = 0 if search is None else search.calls_requested
    calls_succeeded = 0 if search is None else search.calls_succeeded
    calls_failed = 0 if search is None else search.calls_failed
    changed = hypothesis_space.source == "llm_sr"
    return {
        "status": status,
        "mode": "llm_sr_typed_symbolic_search",
        "provider_model": None if config is None else config.model,
        "endpoint_origin_sha256": endpoint_digest,
        "prompt_version": PROMPT_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "calls_requested": calls_requested,
        "calls_succeeded": calls_succeeded,
        "calls_failed": calls_failed,
        "iterations_requested": 0 if search is None else search.iterations_requested,
        "hypotheses_proposed": 0 if search is None else search.hypotheses_proposed,
        "hypotheses_evaluated": 0 if search is None else search.hypotheses_evaluated,
        "hypotheses_accepted": 0 if search is None else search.hypotheses_accepted,
        "hypotheses_buffered": 0 if search is None else search.hypotheses_buffered,
        "portfolio_size": len(hypothesis_space.hypotheses),
        "p1_hypotheses": len(hypothesis_space.p1_family_ids),
        "p2_hypotheses": len(hypothesis_space.p2_family_pairs),
        "island_count": 0 if search_options is None else search_options.num_islands,
        "experiences_per_prompt": 0
        if search_options is None
        else search_options.experiences_per_prompt,
        "samples_per_prompt": 0
        if search_options is None
        else search_options.samples_per_prompt,
        "scope": (
            "full_data_hypothesis_portfolio_replayed_in_validation"
            if changed
            else "deterministic_full_registry"
        ),
        "used_in_validation": changed,
        "family_search_space_changed": changed,
        "selection_policy_changed": changed,
        "certificate_policy_changed": False,
        "hypothesis_space_hash": hypothesis_space.space_hash,
        "formula_source": "typed_skeleton_plus_certified_registry_solver",
    }


def _resolve_hypothesis_space(
    x: np.ndarray,
    y: np.ndarray,
    request: RunRequest,
) -> tuple[HypothesisSpace, dict[str, object], tuple[str, ...]]:
    """Run typed LLM-SR search or choose the deterministic full registry."""

    deterministic = HypothesisSpace.full_registry()
    try:
        config = LLMConfig.load(
            request.dotenv_path,
            force_enable=request.llm_symbolic_search or request.llm_start_advisor,
        )
    except LLMConfigError as error:
        return (
            deterministic,
            _base_provenance(
                status="CONFIG_INVALID",
                config=None,
                hypothesis_space=deterministic,
            ),
            (error.code,),
        )

    if not config.enabled:
        return (
            deterministic,
            _base_provenance(
                status="DISABLED",
                config=config,
                hypothesis_space=deterministic,
            ),
            (),
        )

    search_options = SymbolicSearchOptions(iterations=config.search_iterations)
    evaluation_options = replace(
        request.fit_options,
        hypothesis_space=None,
        start_overrides=(),
    )
    try:
        search = run_symbolic_search(
            x,
            y,
            config,
            evaluation_options,
            options=search_options,
        )
    except Exception:
        # Provider, transport, and untrusted-output details never enter the
        # report.  A complete deterministic analysis remains available.
        return (
            deterministic,
            _base_provenance(
                status="FALLBACK",
                config=config,
                hypothesis_space=deterministic,
                search_options=search_options,
            ),
            (
                "LLM_TRAINING_SUMMARY_DISCLOSED",
                "LLM_SR_SEARCH_UNAVAILABLE",
            ),
        )

    return (
        search.hypothesis_space,
        _base_provenance(
            status=search.status,
            config=config,
            hypothesis_space=search.hypothesis_space,
            search=search,
            search_options=search_options,
        ),
        search.warning_codes,
    )


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
) -> ReportBundle:
    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=parent)
    )
    staged_output = staging_root / "bundle"
    try:
        staged = write_report_bundle(
            dataset,
            candidates,
            validation,
            staged_output,
            llm_advisor=llm_advisor,
            extra_warning_codes=warning_codes,
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

    hypothesis_space, provenance, application_warnings = _resolve_hypothesis_space(
        x,
        y,
        request,
    )
    fit_options = replace(request.fit_options, hypothesis_space=hypothesis_space)
    candidates = fit_candidates(x, y, fit_options)
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
    )
