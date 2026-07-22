"""Command-line interface for the local calibration workflow."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys

from . import __version__
from .application import CalibrationRunError, RunRequest, run_calibration
from .bundle_runtime import BundleRuntimeError, verify_bundle, write_predictions
from .data import DataContractError
from .engine import FitOptions, SearchPolicy
from .validation import ValidationOptions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monotone-calibrate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Fit and compare monotone one- and two-function approximations.\n"
            "LLM-SR symbolic search: disabled by default."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    run = subparsers.add_parser(
        "run",
        help="calibrate one x,y CSV and write a report bundle",
        description=(
            "Run the blocking calibration batch. Progress is not streamed; "
            "wait for the final JSON before invoking verify."
        ),
    )
    run.add_argument("input", type=Path, help="UTF-8 CSV containing x and y")
    run.add_argument("--output", type=Path, required=True, help="new report-bundle directory")
    run.add_argument(
        "--llm-symbolic-search",
        "--llm-start-advisor",
        dest="llm_symbolic_search",
        action="store_true",
        help="enable configured OpenAI-compatible LLM-SR equation-skeleton search",
    )
    dotenv = run.add_mutually_exclusive_group()
    dotenv.add_argument("--dotenv", type=Path, default=Path(".env"), help="dotenv path (default: .env)")
    dotenv.add_argument("--no-dotenv", action="store_const", const=None, dest="dotenv")
    run.add_argument(
        "--validation-repetitions",
        type=int,
        default=10,
        metavar="N",
        help="grouped validation repetitions; lower values are faster but less stable (default: 10)",
    )
    run.add_argument(
        "--bootstrap-resamples",
        type=int,
        default=200,
        metavar="N",
        help="paired OOF bootstrap resamples (default: 200)",
    )
    run.add_argument(
        "--search-profile",
        choices=("fast", "balanced", "quality", "exhaustive"),
        default="fast",
        help="candidate-search budget (default: fast)",
    )

    predict = subparsers.add_parser("predict", help="apply a recommended model to an x CSV")
    predict.add_argument("model", type=Path, help="recommended-model.json")
    predict.add_argument("input", type=Path, help="UTF-8 CSV containing x")
    predict.add_argument("--output", type=Path, required=True, help="prediction CSV")

    verify = subparsers.add_parser("verify", help="verify a completed report bundle")
    verify.add_argument("bundle", type=Path, help="report-bundle directory")
    return parser


def _emit(value: object, *, error: bool = False) -> None:
    print(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False),
        file=sys.stderr if error else sys.stdout,
    )


def _run(arguments: argparse.Namespace) -> dict[str, object]:
    result = run_calibration(
        RunRequest(
            input_path=arguments.input,
            output_dir=arguments.output,
            dotenv_path=arguments.dotenv,
            llm_symbolic_search=arguments.llm_symbolic_search,
            fit_options=FitOptions(
                search_policy=SearchPolicy(arguments.search_profile),
            ),
            validation_options=ValidationOptions(
                repetitions=arguments.validation_repetitions,
                bootstrap_resamples=arguments.bootstrap_resamples,
            ),
        )
    )
    structure = result.validation.recommended_structure
    candidate = (
        result.candidates.two
        if structure == "P2"
        else result.candidates.one if structure == "P1" else None
    )
    validation_metrics = (
        result.validation.two
        if structure == "P2"
        else result.validation.one if structure == "P1" else None
    )
    trace = result.candidates.search_trace
    return {
        "status": "ok",
        "output_dir": str(result.bundle.root),
        "report_html": str(result.bundle.report_html),
        "recommended_model": None
        if result.bundle.recommended_model is None
        else str(result.bundle.recommended_model),
        "recommendation": structure,
        "decision_state": result.validation.decision_state,
        "formula": None if candidate is None else candidate.model.formula,
        "r2_refit": None if candidate is None else candidate.r2_refit,
        "r2_oos": None if validation_metrics is None else validation_metrics.r2_oos,
        "warnings": list(result.warning_codes),
        "llm_status": result.llm_advisor["status"],
        "search": {
            "policy_id": trace.policy_id,
            "profile": trace.profile,
            "approximate": trace.approximate,
            "variant_count": trace.variant_count,
            "min_segment_share": trace.min_segment_share,
            "max_elementary_starts": trace.max_elementary_starts,
            "eligible_cells": trace.eligible_cells,
            "coarse_cells": trace.coarse_cells,
            "evaluated_cells": trace.evaluated_cells,
            "evaluated_candidates": trace.evaluated_candidates,
            "refinement_pairs": trace.refinement_pairs,
            "termination": trace.termination,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 0
    try:
        if arguments.command == "run":
            payload = _run(arguments)
        elif arguments.command == "predict":
            payload = write_predictions(
                arguments.model,
                arguments.input,
                arguments.output,
            ).to_dict()
        elif arguments.command == "verify":
            payload = verify_bundle(arguments.bundle).to_dict()
        else:  # pragma: no cover - argparse owns the finite command set
            raise AssertionError("unreachable command")
    except (CalibrationRunError, BundleRuntimeError, DataContractError, OSError, ValueError) as error:
        code = getattr(error, "code", type(error).__name__)
        _emit(
            {
                "status": "error",
                "code": code,
                "message": str(error),
            },
            error=True,
        )
        return 1
    _emit(payload)
    return 0
