"""Independent formula discovery and a common untouched holdout for P1/P2/LLM."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import LLMConfig
from .engine import FitOptions, fit_candidates
from .formula_search import (
    FormulaFit,
    FormulaFitOptions,
    FormulaSampler,
    FormulaSearchResult,
    fit_formula,
    run_formula_search,
)


@dataclass(frozen=True, slots=True)
class FormulaComparison:
    search: FormulaSearchResult = field(
        default_factory=lambda: FormulaSearchResult("DISABLED")
    )
    fitted: FormulaFit | None = None
    holdout: dict = field(
        default_factory=lambda: {"status": "DISABLED", "candidates": {}}
    )

    @property
    def warning_codes(self) -> tuple[str, ...]:
        extra = (
            ()
            if self.holdout["status"] in {"AVAILABLE", "DISABLED"}
            else ("COMMON_HOLDOUT_UNAVAILABLE",)
        )
        if self.search.best is not None and self.fitted is None:
            extra += ("LLM_FORMULA_FULL_REFIT_FAILED",)
        llm_r2 = self.holdout.get("candidates", {}).get("LLM", {}).get("r2")
        if llm_r2 is not None and llm_r2 < 0.60:
            extra += ("LLM_BELOW_PRODUCT_R2",)
        return tuple(sorted(set(self.search.warning_codes + extra)))


def holdout_mask(x: np.ndarray, seed: int = 20260912) -> np.ndarray:
    """Split by raw x groups, independent of y; keep both domain endpoints in train."""
    unique = np.unique(x)
    mask = np.zeros(x.size, dtype=bool)
    if unique.size >= 15:
        groups = np.random.default_rng(seed).choice(
            unique[1:-1], size=max(2, unique.size // 5), replace=False
        )
        mask = np.isin(x, groups)
    return mask


def prediction_metrics(y, prediction, reference_mean: float) -> dict:
    y = np.asarray(y, dtype=float)
    prediction = np.asarray(prediction, dtype=float)
    if y.size == 0 or not np.all(np.isfinite(prediction)):
        return {
            "status": "UNAVAILABLE",
            "r2": None,
            "mse": None,
            "rmse": None,
            "mae": None,
        }
    residual = y - prediction
    mse = float(np.mean(residual**2))
    denominator = float(np.sum((y - reference_mean) ** 2))
    return {
        "status": "AVAILABLE",
        "r2": None
        if denominator == 0
        else 1 - float(residual @ residual) / denominator,
        "mse": mse,
        "rmse": float(np.sqrt(mse)),
        "mae": float(np.mean(np.abs(residual))),
    }


def compare_formula_discovery(
    x,
    y,
    config: LLMConfig,
    fit_options: FitOptions,
    *,
    sampler: FormulaSampler | None = None,
    formula_options: FormulaFitOptions | None = None,
) -> FormulaComparison:
    """Freeze the training-selected formula before touching test labels or refitting."""
    if not config.enabled:
        return FormulaComparison()
    xv, yv = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    test = holdout_mask(xv)
    train = ~test
    search = run_formula_search(
        xv[train], yv[train], config, sampler=sampler, fit_options=formula_options
    )
    heldout = {
        "status": "AVAILABLE" if np.any(test) else "INSUFFICIENT_GROUPS",
        "policy": "grouped-interpolation-holdout-v1",
        "seed": 20260912,
        "scope": "training_only_discovery_then_untouched_test_before_full_refit",
        "r2_reference": "discovery_training_mean",
        "train_mean": float(np.mean(yv[train])),
        "train_indices": np.flatnonzero(train).tolist(),
        "test_indices": np.flatnonzero(test).tolist(),
        "n_train": int(train.sum()),
        "n_test": int(test.sum()),
        "candidates": {},
    }
    if np.any(test):
        # Full registry baselines use the identical train/test split and budget.
        baseline = fit_candidates(xv[train], yv[train], fit_options)
        models = {
            "P1": baseline.one.model,
            "P2": None if baseline.two is None else baseline.two.model,
            "LLM": None if search.best is None else search.best.model,
        }
        for name, model in models.items():
            if model is None:
                heldout["candidates"][name] = {
                    "status": "MODEL_UNAVAILABLE",
                    "r2": None,
                    "mse": None,
                    "rmse": None,
                    "mae": None,
                    "model": None,
                    "predictions": None,
                }
                continue
            predictions = model.predict(xv[test])
            heldout["candidates"][name] = {
                **prediction_metrics(yv[test], predictions, heldout["train_mean"]),
                "model": model.to_dict(),
                "predictions": list(map(float, predictions)),
            }
    try:
        fitted = (
            None
            if search.best is None
            else fit_formula(xv, yv, search.best.hypothesis, formula_options)
        )
    except (ValueError, FloatingPointError, OverflowError, np.linalg.LinAlgError):
        fitted = None
    return FormulaComparison(search, fitted, heldout)
