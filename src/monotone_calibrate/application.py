"""Application orchestration for one immutable calibration run.

The optional LLM boundary is deliberately downstream of validation.  It may
replace only predeclared nonlinear P1 optimizer starts for one final full-data
refit; it never participates in model-family search, P2 fitting, validation,
selection policy, or mathematical certification.
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
    StartOverride,
    fit_candidates,
    p1_start_slots,
)
from .llm_advisor import (
    AdviceResult,
    ChatOpenAIStartAdvisor,
    ReplaceableStartSlot,
    summarize_training,
)
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
    llm_start_advisor: bool = False
    fit_options: FitOptions = field(default_factory=FitOptions)
    validation_options: ValidationOptions = field(default_factory=ValidationOptions)

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_path", Path(self.input_path))
        object.__setattr__(self, "output_dir", Path(self.output_dir))
        if self.dotenv_path is not None:
            object.__setattr__(self, "dotenv_path", Path(self.dotenv_path))
        if self.fit_options.start_overrides:
            raise ValueError(
                "RunRequest.fit_options cannot contain start_overrides; "
                "the optional advisor owns that boundary"
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
    baseline_hash: str,
    final_hash: str,
    config: LLMConfig | None,
    calls_requested: int,
    accepted_slot_count: int,
    influenced_final_refit: bool,
) -> dict[str, object]:
    endpoint_digest = (
        None
        if config is None or config.base_url is None
        else sha256(config.base_url.encode("utf-8")).hexdigest()
    )
    return {
        "status": status,
        "mode": "langchain_openai_start_advisor",
        "provider_model": None if config is None else config.model,
        "endpoint_origin_sha256": endpoint_digest,
        "prompt_version": "llm-start-prompt-v1",
        "output_schema_version": "llm-start-advice-v1",
        "calls_requested": calls_requested,
        "accepted_slot_count": accepted_slot_count,
        "scope": "full_data_p1_refit_only",
        "used_in_validation": False,
        "family_search_space_changed": False,
        "selection_policy_changed": False,
        "certificate_policy_changed": False,
        "baseline_p1_model_hash": baseline_hash,
        "final_p1_model_hash": final_hash,
        "influenced_final_refit": influenced_final_refit,
        "formula_source": "certified_registry_solver",
    }


def _advisor_refit(
    x: np.ndarray,
    y: np.ndarray,
    baseline: CandidateSet,
    request: RunRequest,
) -> tuple[CandidateSet, dict[str, object], tuple[str, ...]]:
    """Optionally advise one P1 refit after validation has already finished."""

    baseline_hash = baseline.one.model.model_instance_hash
    try:
        config = LLMConfig.load(
            request.dotenv_path,
            force_enable=request.llm_start_advisor,
        )
    except LLMConfigError as error:
        return (
            baseline,
            _base_provenance(
                status="CONFIG_INVALID",
                baseline_hash=baseline_hash,
                final_hash=baseline_hash,
                config=None,
                calls_requested=0,
                accepted_slot_count=0,
                influenced_final_refit=False,
            ),
            (error.code,),
        )

    if not config.enabled:
        return (
            baseline,
            _base_provenance(
                status="DISABLED",
                baseline_hash=baseline_hash,
                final_hash=baseline_hash,
                config=config,
                calls_requested=0,
                accepted_slot_count=0,
                influenced_final_refit=False,
            ),
            (),
        )

    slots = tuple(
        ReplaceableStartSlot(
            slot_id=slot.slot_id,
            bounds=slot.bounds,
            default_vector=slot.default_vector,
        )
        for slot in p1_start_slots()
    )
    try:
        advice = ChatOpenAIStartAdvisor(config).advise(
            summarize_training(x, y, replaceable_slots=slots)
        )
    except Exception:
        # This is an external boundary.  Provider and transport details are
        # intentionally discarded; deterministic fitting remains complete.
        advice = AdviceResult(
            status="FALLBACK",
            warning_code="LLM_ADVISOR_UNAVAILABLE",
        )

    if advice.status == "FALLBACK":
        return (
            baseline,
            _base_provenance(
                status="FALLBACK",
                baseline_hash=baseline_hash,
                final_hash=baseline_hash,
                config=config,
                calls_requested=1,
                accepted_slot_count=0,
                influenced_final_refit=False,
            ),
            (advice.warning_code or "LLM_ADVISOR_UNAVAILABLE",),
        )

    overrides = tuple(
        StartOverride(item.slot_id, item.parameter_vector)
        for item in advice.suggestions
    )
    if not overrides:
        return (
            baseline,
            _base_provenance(
                status="ACCEPTED",
                baseline_hash=baseline_hash,
                final_hash=baseline_hash,
                config=config,
                calls_requested=1,
                accepted_slot_count=0,
                influenced_final_refit=False,
            ),
            (),
        )

    advised_options = FitOptions(
        min_segment_share=request.fit_options.min_segment_share,
        max_elementary_starts=request.fit_options.max_elementary_starts,
        start_overrides=overrides,
    )
    advised = fit_candidates(x, y, advised_options)
    retained = advised.one.sse <= baseline.one.sse
    final_one = advised.one if retained else baseline.one
    final = CandidateSet(
        one=final_one,
        two=baseline.two,
        two_status=baseline.two_status,
    )
    final_hash = final_one.model.model_instance_hash
    influenced = retained and final_hash != baseline_hash
    return (
        final,
        _base_provenance(
            status="ACCEPTED" if retained else "NO_IMPROVEMENT",
            baseline_hash=baseline_hash,
            final_hash=final_hash,
            config=config,
            calls_requested=1,
            accepted_slot_count=len(overrides),
            influenced_final_refit=influenced,
        ),
        (),
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
    """Fit, validate, optionally advise one refit, and atomically publish."""

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

    baseline = fit_candidates(x, y, request.fit_options)
    # Validation is intentionally completed before configuration loading or
    # construction of the optional external advisor.
    validation = validate_candidates(
        x,
        y,
        baseline,
        request.validation_options,
    )
    final_candidates, provenance, application_warnings = _advisor_refit(
        x,
        y,
        baseline,
        request,
    )
    bundle = _publish_report(
        dataset,
        final_candidates,
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
        candidates=final_candidates,
        validation=validation,
        llm_advisor=provenance,
        warning_codes=warnings,
    )
