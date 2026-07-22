"""Offline report bundle writer for one completed calibration."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import html
import json
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import quote

from jinja2 import Environment, StrictUndefined
import numpy as np

from .data import ObservationSet
from .engine import CandidateSet, FitCandidate
from .model_runtime import FittedModel
from .validation import ValidationResult


_LLM_PROVENANCE_FIELDS = frozenset(
    {
        "status",
        "mode",
        "provider_model",
        "endpoint_origin_sha256",
        "prompt_version",
        "output_schema_version",
        "calls_requested",
        "calls_succeeded",
        "calls_failed",
        "accepted_slot_count",
        "iterations_requested",
        "hypotheses_proposed",
        "hypotheses_evaluated",
        "hypotheses_accepted",
        "hypotheses_buffered",
        "portfolio_size",
        "p1_hypotheses",
        "p2_hypotheses",
        "island_count",
        "experiences_per_prompt",
        "samples_per_prompt",
        "scope",
        "used_in_validation",
        "family_search_space_changed",
        "selection_policy_changed",
        "certificate_policy_changed",
        "baseline_p1_model_hash",
        "final_p1_model_hash",
        "hypothesis_space_hash",
        "influenced_final_refit",
        "formula_source",
    }
)
_WARNING_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ReportBundle:
    root: Path
    report_html: Path
    report_json: Path
    plot_one_svg: Path
    plot_two_svg: Path | None
    observations_csv: Path
    recommended_model: Path | None
    manifest_json: Path


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_json_bytes(value))


def _safe_llm_provenance(value: Mapping[str, object] | None) -> dict[str, object]:
    if value is None:
        return {
            "status": "DISABLED",
            "scope": "deterministic_full_registry",
            "used_in_validation": False,
            "influenced_final_refit": False,
            "formula_source": "certified_registry_solver",
        }
    if not isinstance(value, Mapping) or not value or not set(value) <= _LLM_PROVENANCE_FIELDS:
        raise ValueError("llm_advisor provenance contains an unsupported or secret-bearing field")
    normalized: dict[str, object] = {}
    for name, raw in value.items():
        if raw is None:
            normalized[name] = None
        elif isinstance(raw, bool):
            normalized[name] = raw
        elif isinstance(raw, int) and raw >= 0:
            normalized[name] = raw
        elif isinstance(raw, str) and len(raw) <= 256:
            normalized[name] = raw
        else:
            raise ValueError(f"llm_advisor provenance field {name!r} has an invalid value")
    status = normalized.get("status")
    if status not in {
        "DISABLED",
        "CONFIG_INVALID",
        "FALLBACK",
        "ACCEPTED",
        "NO_IMPROVEMENT",
    }:
        raise ValueError("llm_advisor provenance has an invalid status")
    for name in (
        "endpoint_origin_sha256",
        "baseline_p1_model_hash",
        "final_p1_model_hash",
        "hypothesis_space_hash",
    ):
        digest = normalized.get(name)
        if digest is not None and (not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
            raise ValueError(f"llm_advisor provenance field {name!r} must be SHA-256")
    return normalized


def _candidate_dict(candidate: FitCandidate | None, status: str) -> dict[str, object]:
    if candidate is None:
        return {"status": status, "available": False}
    return {
        "status": candidate.status,
        "available": True,
        "metrics": {
            "r2_refit": candidate.r2_refit,
            "rmse_refit": candidate.rmse_refit,
            "mae_refit": candidate.mae_refit,
            "sse_refit": candidate.sse,
        },
        "segment_metrics": [
            {
                "segment_id": metric.segment_id,
                "n": metric.n,
                "n_unique_x": metric.n_unique_x,
                "share": metric.share,
                "r2_refit": metric.r2,
                "rmse_refit": metric.rmse,
                "mae_refit": metric.mae,
            }
            for metric in candidate.segment_metrics
        ],
        "model": candidate.model.to_dict(),
    }


def _recommended_candidate(
    candidates: CandidateSet,
    validation: ValidationResult,
) -> FitCandidate | None:
    if validation.recommended_structure == "P2":
        return candidates.two
    if validation.recommended_structure == "P1":
        return candidates.one
    return None


def _oof_residual_flags(
    dataset: ObservationSet,
    validation: ValidationResult,
) -> tuple[np.ndarray, float | None, str, np.ndarray, np.ndarray]:
    y = np.asarray([row.y for row in dataset.observations], dtype=np.float64)
    unavailable_values = np.full(y.shape, np.nan, dtype=np.float64)
    if not validation.oof or validation.recommended_structure not in {"P1", "P2"}:
        return (
            np.zeros(y.shape, dtype=bool),
            None,
            "UNAVAILABLE_NO_OOF",
            unavailable_values,
            unavailable_values.copy(),
        )
    x = np.asarray([row.x for row in dataset.observations], dtype=np.float64)
    canonical_to_original = np.lexsort((y, x))
    residuals_by_original: list[list[float]] = [[] for _ in dataset.observations]
    all_residuals: list[float] = []
    for appearance in validation.oof:
        prediction = (
            appearance.prediction_two
            if validation.recommended_structure == "P2"
            else appearance.prediction_one
        )
        residual = appearance.y - prediction
        original_index = int(canonical_to_original[appearance.canonical_index])
        residuals_by_original[original_index].append(residual)
        all_residuals.append(residual)
    residual = np.asarray(all_residuals, dtype=np.float64)
    center = float(np.median(residual))
    mad = float(np.median(np.abs(residual - center)))
    scale = 1.4826 * mad
    numeric_floor = 1e-10 * max(1.0, float(np.ptp(y)), float(np.max(np.abs(y))))
    row_residual = np.asarray(
        [float(np.median(values)) if values else np.nan for values in residuals_by_original]
    )
    row_prediction = y - row_residual
    deviation = np.abs(row_residual - center)
    if not np.isfinite(scale) or scale <= numeric_floor:
        flagged = np.isfinite(deviation) & (deviation > numeric_floor)
        return flagged, max(scale, 0.0), "AVAILABLE_OOF", row_prediction, row_residual
    return deviation > 3.5 * scale, scale, "AVAILABLE_OOF", row_prediction, row_residual


def _bounds(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float, float]:
    x_min, x_max = float(np.min(x)), float(np.max(x))
    y_min, y_max = float(np.min(y)), float(np.max(y))
    y_span = y_max - y_min
    if y_span == 0.0:
        y_span = max(1.0, abs(y_min) * 0.1)
    return x_min, x_max, y_min - 0.08 * y_span, y_max + 0.08 * y_span


def _metric_text(value: object) -> str:
    return "R² не определён" if value is None else f"{float(value):.3f}"


def _svg_plot(
    x: np.ndarray,
    y: np.ndarray,
    model: FittedModel | None,
    flagged: np.ndarray,
    title: str,
    unavailable_status: str | None = None,
) -> str:
    width, height = 900.0, 520.0
    left, right, top, bottom = 76.0, 28.0, 50.0, 66.0
    x_min, x_max, y_min, y_max = _bounds(x, y)
    plot_w = width - left - right
    plot_h = height - top - bottom

    def sx(value: float) -> float:
        return left + (value - x_min) / (x_max - x_min) * plot_w

    def sy(value: float) -> float:
        return top + (y_max - value) / (y_max - y_min) * plot_h

    boundary_description = (
        "Модель недоступна, показаны только наблюдения."
        if model is None
        else (
            "Одна функция на полном интервале."
            if model.segment_count == 1
            else f"Две интервальные функции; красная граница x={model.breakpoint:.8g}."
        )
    )
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="520" viewBox="0 0 900 520" role="img">',
        f"<title>{html.escape(title)}</title>",
        f"<desc>{html.escape(boundary_description)} Отмеченных наблюдений: {int(np.sum(flagged))}.</desc>",
        '<rect width="900" height="520" fill="#ffffff"/>',
        f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="#fafafa" stroke="#68737d"/>',
        f'<text x="{width / 2}" y="28" text-anchor="middle" font-family="sans-serif" font-size="18">{html.escape(title)}</text>',
        f'<text x="{width / 2}" y="505" text-anchor="middle" font-family="sans-serif">x</text>',
        f'<text x="18" y="{height / 2}" transform="rotate(-90 18 {height / 2})" text-anchor="middle" font-family="sans-serif">y</text>',
    ]
    for index, (x_value, y_value) in enumerate(zip(x, y, strict=True)):
        fill = "#ef6c00" if bool(flagged[index]) else "#263238"
        radius = 5.2 if bool(flagged[index]) else 3.8
        lines.append(
            f'<circle data-role="observation" cx="{sx(float(x_value)):.4f}" cy="{sy(float(y_value)):.4f}" '
            f'r="{radius}" fill="{fill}" stroke="#ffffff" stroke-width="0.8"><title>'
            f'x={float(x_value):.8g}, y={float(y_value):.8g}</title></circle>'
        )
        if bool(flagged[index]):
            cx, cy = sx(float(x_value)), sy(float(y_value))
            lines.append(
                f'<path data-role="diagnostic-marker" d="M {cx - 6:.4f},{cy - 6:.4f} L {cx + 6:.4f},{cy + 6:.4f} '
                f'M {cx - 6:.4f},{cy + 6:.4f} L {cx + 6:.4f},{cy - 6:.4f}" '
                'stroke="#111111" stroke-width="1.5"><title>Большой OOF-остаток; только review</title></path>'
            )
    colors = ("#1565c0", "#00897b")
    if model is not None:
        segment_labels = ("single",) if model.segment_count == 1 else ("left", "right")
        for index, (segment, label) in enumerate(zip(model.segments, segment_labels, strict=True)):
            grid = np.linspace(segment.x_lower, segment.x_upper, 240)
            predicted = np.asarray(segment.predict_unchecked(grid))
            points = " ".join(
                f"{sx(float(x_value)):.4f},{sy(float(y_value)):.4f}"
                for x_value, y_value in zip(grid, predicted, strict=True)
            )
            lines.append(
                f'<polyline data-role="approximation" data-segment="{label}" points="{points}" '
                f'fill="none" stroke="{colors[index]}" stroke-width="3"/>'
            )
    else:
        lines.append(
            f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" font-family="sans-serif" '
            f'font-size="18" fill="#59636e">P2 недоступна: {html.escape(unavailable_status or "UNKNOWN")}</text>'
        )
    if model is not None and model.breakpoint is not None:
        boundary_x = sx(model.breakpoint)
        lines.append(
            f'<line data-role="interval-boundary" x1="{boundary_x:.4f}" x2="{boundary_x:.4f}" '
            f'y1="{top}" y2="{top + plot_h}" stroke="#c62828" stroke-width="3" stroke-dasharray="8 5"/>'
        )
        lines.append(
            f'<text x="{boundary_x + 7:.4f}" y="{top + 18}" fill="#c62828" font-family="sans-serif">'
            f'граница x={model.breakpoint:.6g}</text>'
        )
    lines.append("</svg>\n")
    return "\n".join(lines)


_HTML_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self'; style-src 'unsafe-inline'">
  <title>Отчёт о монотонной калибровке</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, sans-serif; line-height: 1.45; }
    body { margin: 0 auto; max-width: 1120px; padding: 24px; color: #17212b; }
    .decision { border-left: 7px solid #1565c0; background: #eef5ff; padding: 16px 20px; }
    .warning { border-left-color: #c62828; background: #fff0f0; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(250px,1fr)); gap: 12px; }
    .card { border: 1px solid #ccd4dc; border-radius: 8px; padding: 14px; }
    img { width: 100%; height: auto; border: 1px solid #d6dde3; }
    code { overflow-wrap: anywhere; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border-bottom: 1px solid #d6dde3; text-align: left; padding: 7px; }
  </style>
</head>
<body>
<h1>Отчёт о монотонной калибровке</h1>
<section class="decision{% if warnings %} warning{% endif %}">
  <h2>Рекомендация: {{ recommendation }}</h2>
  <p>Состояние решения: <code>{{ decision_state }}</code>.</p>
  {% if warnings %}<p><strong>Предупреждения:</strong> {{ warnings|join(', ') }}</p>{% endif %}
  {% if below_quality %}<p><strong>Проверочный R² рекомендуемой процедуры ниже 0.60.</strong>
  Результат сохранён, но качество обязательно требует внимания.</p>{% endif %}
  {% if not validation_available %}<p><strong>Проверочная оценка недоступна:</strong>
  данных недостаточно для grouped OOF. Формулы ниже являются только описательными.</p>{% endif %}
</section>
<h2>Вход</h2>
<p>Использовано {{ input.n_used }} из {{ input.n_input }} строк; пропущено {{ input.n_skipped }}.
Уникальных x: {{ input.n_unique_x }}.</p>
<h2>Выбор калибровочной функции</h2>
<p>Режим: <code>{{ llm_mode }}</code>; статус: <code>{{ llm_status }}</code>.
{% if llm_status == "ACCEPTED" %}В численный fit передано {{ llm_portfolio_size }}
типизированных гипотез (P1: {{ llm_p1_hypotheses }}, P2: {{ llm_p2_hypotheses }}).
{% else %}Использован полный детерминированный реестр
(P1: {{ llm_p1_hypotheses }}, P2: {{ llm_p2_hypotheses }}).{% endif %}</p>
{% if llm_status == "ACCEPTED" %}<p>LLM-SR итеративно предлагал структуры; коэффициенты,
граница P2 и fitness вычислялись локальным solver-ом. Произвольный код модели не исполнялся.
OOF-метрики условны относительно portfolio, найденного на полном наборе данных, и не являются
независимой проверкой самой LLM-стадии discovery.</p>{% endif %}
<div class="grid">
  <section class="card"><h3>P1 — одна функция</h3>
    <p>Refit R²: {{ p1.metrics.r2_refit|metric }}</p>
    <p>OOF R²: {% if validation_available %}{{ validation.one.r2_oos|metric }}{% else %}недоступен{% endif %}</p>
    <p><code>{{ p1.model.formula }}</code></p>
  </section>
  <section class="card"><h3>P2 — две функции</h3>
  {% if p2.available %}
    <p>Refit global R²: {{ p2.metrics.r2_refit|metric }}</p>
    <p>OOF R²: {% if validation_available %}{{ validation.two.r2_oos|metric }}{% else %}недоступен{% endif %}</p>
    <p><code>{{ p2.model.formula }}</code></p>
    <table><caption>Локальные refit-метрики P2</caption><thead><tr><th scope="col">Интервал</th><th scope="col">n</th><th scope="col">доля</th><th scope="col">R²</th></tr></thead><tbody>
    {% for item in p2.segment_metrics %}<tr><td>{{ item.segment_id }}</td><td>{{ item.n }}</td><td>{{ item.share|metric }}</td><td>{{ item.r2_refit|metric }}</td></tr>{% endfor %}
    </tbody></table>
  {% else %}<p>Недоступна: <code>{{ p2.status }}</code>.</p>{% endif %}
  </section>
</div>
<h2>Графики</h2>
<h3>P1</h3><img src="plot-one.svg" alt="Scatterplot и односегментная аппроксимация">
<h3>P2</h3><img src="plot-two.svg" alt="Scatterplot; две интервальные функции и красная граница либо явное состояние недоступности">
<h2>Проверочный uplift</h2>
{% if validation_available and validation.uplift.relative_mse is not none %}
<p>Relative MSE uplift: {{ validation.uplift.relative_mse|metric }};
bootstrap 90% interval:
{% if validation.uplift.bootstrap_interval.lower is not none and validation.uplift.bootstrap_interval.upper is not none %}
[{{ validation.uplift.bootstrap_interval.lower|metric }}, {{ validation.uplift.bootstrap_interval.upper|metric }}].
{% else %}недоступен.{% endif %}</p>
{% elif validation_available %}<p>Uplift не определён для этих данных.</p>
{% else %}<p>Uplift не вычислялся без grouped OOF.</p>{% endif %}
<h2>Наблюдения для проверки</h2>
{% if diagnostic_available %}
<p>Флаги основаны на grouped OOF-остатках рекомендуемой процедуры; они ничего не удаляют.</p>
{% if flagged_rows %}<table><caption>Наблюдения с большим OOF-остатком</caption><thead><tr><th scope="col">row_id</th><th scope="col">OOF-прогноз</th><th scope="col">OOF-остаток</th><th scope="col">действие</th></tr></thead><tbody>
{% for item in flagged_rows %}<tr><td><code>{{ item.row_id }}</code></td><td>{{ item.oof_prediction }}</td><td>{{ item.oof_residual }}</td><td>review_only</td></tr>{% endfor %}
</tbody></table>{% else %}<p>Больших OOF-остатков по текущему robust rule не обнаружено.</p>{% endif %}
{% else %}<p>OOF residual diagnostics недоступна; refit-остатки не выдаются за проверочные.</p>{% endif %}
</body></html>
"""


def write_report_bundle(
    dataset: ObservationSet,
    candidates: CandidateSet,
    validation: ValidationResult,
    output_dir: str | Path,
    *,
    llm_advisor: Mapping[str, object] | None = None,
    extra_warning_codes: tuple[str, ...] = (),
) -> ReportBundle:
    """Write one self-contained report tree from already frozen computations."""

    safe_llm_advisor = _safe_llm_provenance(llm_advisor)
    if not all(isinstance(code, str) and _WARNING_CODE.fullmatch(code) for code in extra_warning_codes):
        raise ValueError("extra_warning_codes must contain typed uppercase warning codes")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=False)
    x = np.asarray([row.x for row in dataset.observations], dtype=np.float64)
    y = np.asarray([row.y for row in dataset.observations], dtype=np.float64)
    recommendation = _recommended_candidate(candidates, validation)
    recommended_prediction = (
        np.full(x.shape, np.nan)
        if recommendation is None
        else np.asarray(recommendation.model.predict(x))
    )
    (
        flagged,
        robust_scale,
        diagnostic_status,
        oof_prediction,
        oof_residual,
    ) = _oof_residual_flags(dataset, validation)
    prediction_one = np.asarray(candidates.one.model.predict(x))
    prediction_two = (
        np.full(x.shape, np.nan)
        if candidates.two is None
        else np.asarray(candidates.two.model.predict(x))
    )

    p1 = _candidate_dict(candidates.one, candidates.one.status)
    p2 = _candidate_dict(candidates.two, candidates.two_status)
    trace = candidates.search_trace
    search = {
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
    }
    warnings = tuple(
        sorted(set(dataset.warning_codes) | set(validation.warning_codes) | set(extra_warning_codes))
    )
    problem_rows = [
        {
            "row_id": row.row_id,
            "oof_prediction": float(oof_prediction[index]),
            "oof_residual": float(oof_residual[index]),
        }
        for index, row in enumerate(dataset.observations)
        if bool(flagged[index])
    ]
    report_id = sha256(
        _json_bytes(
            {
                "input_sha256": dataset.input_sha256,
                "decision_state": validation.decision_state,
                "search": search,
                "recommended_model": None
                if recommendation is None
                else recommendation.model.model_instance_hash,
            }
        )
    ).hexdigest()
    report = {
        "schema_version": "monotone-report-v2",
        "report_id": report_id,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "input": {
            "sha256": dataset.input_sha256,
            "n_input": dataset.n_input,
            "n_used": dataset.n_used,
            "n_skipped": dataset.n_skipped,
            "n_unique_x": dataset.n_unique_x,
            "extra_columns_ignored": list(dataset.extra_columns),
        },
        "search": search,
        "candidates": {"P1": p1, "P2": p2},
        "validation": validation.to_dict(),
        "recommendation": {
            "structure": validation.recommended_structure,
            "decision_state": validation.decision_state,
            "formula": None if recommendation is None else recommendation.model.formula,
            "model_instance_hash": None
            if recommendation is None
            else recommendation.model.model_instance_hash,
        },
        "warnings": list(warnings),
        "diagnostics": {
            "status": diagnostic_status,
            "source": "grouped_oof" if diagnostic_status == "AVAILABLE_OOF" else None,
            "robust_residual_scale": robust_scale,
            "problem_observations": [
                item["row_id"] for item in problem_rows
            ],
            "problem_rows": problem_rows,
            "action": "review_only",
        },
        "llm_advisor": safe_llm_advisor,
    }
    report_json = root / "report.json"
    _write_json(report_json, report)
    _write_json(root / "model-comparison.json", {"P1": p1, "P2": p2, "validation": validation.to_dict()})
    _write_json(
        root / "input-audit.json",
        {
            "exclusions": [
                {
                    "source_row": row.source_row,
                    "row_id": row.row_id,
                    "external_row_id": row.external_row_id,
                    "raw_x": row.raw_x,
                    "raw_y": row.raw_y,
                    "reason_codes": list(row.reason_codes),
                }
                for row in dataset.exclusions
            ]
        },
    )
    observations_csv = root / "observations.csv"
    with observations_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            [
                "row_id",
                "x",
                "y",
                "prediction_p1_refit",
                "prediction_p2_refit",
                "prediction_recommended_refit",
                "residual_recommended_refit",
                "prediction_recommended_oof",
                "residual_recommended_oof",
                "diagnostic_flag",
                "action",
            ]
        )
        for index, row in enumerate(dataset.observations):
            writer.writerow(
                [
                    "id:" + quote(row.row_id, safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-"),
                    repr(row.x),
                    repr(row.y),
                    repr(float(prediction_one[index])),
                    "" if not np.isfinite(prediction_two[index]) else repr(float(prediction_two[index])),
                    ""
                    if not np.isfinite(recommended_prediction[index])
                    else repr(float(recommended_prediction[index])),
                    ""
                    if not np.isfinite(recommended_prediction[index])
                    else repr(float(y[index] - recommended_prediction[index])),
                    "" if not np.isfinite(oof_prediction[index]) else repr(float(oof_prediction[index])),
                    "" if not np.isfinite(oof_residual[index]) else repr(float(oof_residual[index])),
                    "LARGE_RESIDUAL" if bool(flagged[index]) else "",
                    "review_only" if bool(flagged[index]) else "",
                ]
            )

    plot_one_svg = root / "plot-one.svg"
    plot_one_svg.write_text(
        _svg_plot(x, y, candidates.one.model, flagged, "P1: одна монотонная функция"),
        encoding="utf-8",
    )
    plot_two_svg = root / "plot-two.svg"
    plot_two_svg.write_text(
        _svg_plot(
            x,
            y,
            None if candidates.two is None else candidates.two.model,
            flagged,
            "P2: две функции на интервалах",
            candidates.two_status,
        ),
        encoding="utf-8",
    )
    recommended_model: Path | None = None
    if recommendation is not None:
        recommended_model = root / "recommended-model.json"
        _write_json(recommended_model, recommendation.model.to_dict())

    environment = Environment(autoescape=True, undefined=StrictUndefined)
    environment.filters["metric"] = _metric_text
    rendered = environment.from_string(_HTML_TEMPLATE).render(
        recommendation=validation.recommended_structure or "нет validated recommendation",
        decision_state=validation.decision_state,
        warnings=warnings,
        input=report["input"],
        p1=p1,
        p2=p2,
        validation=validation.to_dict(),
        validation_available=validation.status == "VALIDATED",
        below_quality="BELOW_PRODUCT_R2" in warnings,
        diagnostic_available=diagnostic_status == "AVAILABLE_OOF",
        flagged_rows=problem_rows,
        llm_mode=safe_llm_advisor.get("mode", "off"),
        llm_status=safe_llm_advisor.get("status", "DISABLED"),
        llm_portfolio_size=safe_llm_advisor.get("portfolio_size"),
        llm_p1_hypotheses=safe_llm_advisor.get("p1_hypotheses", 0),
        llm_p2_hypotheses=safe_llm_advisor.get("p2_hypotheses", 0),
    )
    report_html = root / "report.html"
    report_html.write_text(rendered + "\n", encoding="utf-8")

    artifacts = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.encode("utf-8")):
        if path.name == "manifest.json" or not path.is_file():
            continue
        payload = path.read_bytes()
        artifacts.append({"path": path.name, "bytes": len(payload), "sha256": sha256(payload).hexdigest()})
    manifest_json = root / "manifest.json"
    _write_json(
        manifest_json,
        {
            "schema_version": "bundle-manifest-v1",
            "report_id": report_id,
            "artifacts": artifacts,
        },
    )
    return ReportBundle(
        root,
        report_html,
        report_json,
        plot_one_svg,
        plot_two_svg,
        observations_csv,
        recommended_model,
        manifest_json,
    )
