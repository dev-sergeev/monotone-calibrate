from __future__ import annotations

import numpy as np

import monotone_calibrate.validation as validation_module
from monotone_calibrate.engine import FitOptions, SearchPolicy, fit_candidates
from monotone_calibrate.hypotheses import HypothesisSpace
from monotone_calibrate.validation import ValidationOptions, validate_candidates


def test_strong_two_segment_signal_earns_recommendation_from_grouped_oof_uplift() -> None:
    x = np.arange(60, dtype=float)
    c = 23.5
    join = 2.0 + 0.15 * c
    y = np.where(x <= 23.0, join + 0.15 * (x - c), join + 1.20 * (x - c))
    full = fit_candidates(x, y)

    result = validate_candidates(
        x,
        y,
        full,
        ValidationOptions(repetitions=4, bootstrap_resamples=80),
    )

    assert result.decision_state == "CLEAR_PRACTICAL_UPLIFT"
    assert result.recommended_structure == "P2"
    assert result.one.r2_oos is not None
    assert result.two.r2_oos is not None and result.two.r2_oos > result.one.r2_oos
    assert result.uplift.relative_mse is not None and result.uplift.relative_mse >= 0.10
    assert result.uplift.positive_repetition_share >= 0.90
    assert result.oof_appearances == 4 * len(x)


def test_low_quality_data_keeps_p1_and_emits_the_mandatory_global_r2_warning() -> None:
    x = np.arange(20, dtype=float)
    y = np.asarray(
        [0.2, 3.1, -2.4, 1.5, -1.8, 4.0, -3.0, 2.7, -2.2, 3.8,
         -1.4, 3.2, -2.8, 4.1, -1.1, 2.9, -2.5, 3.5, -1.7, 3.0]
    )
    full = fit_candidates(x, y)

    result = validate_candidates(
        x,
        y,
        full,
        ValidationOptions(repetitions=3, bootstrap_resamples=40),
    )

    assert result.recommended_structure == "P1"
    assert result.decision_state != "CLEAR_PRACTICAL_UPLIFT"
    assert result.one.r2_oos is not None and result.one.r2_oos < 0.60
    assert "BELOW_PRODUCT_R2" in result.warning_codes


def test_equal_x_observations_are_atomic_in_every_outer_split() -> None:
    x = np.repeat(np.arange(10, dtype=float), 2)
    y = 1.0 + 0.4 * x + np.tile(np.asarray([-0.03, 0.03]), 10)
    full = fit_candidates(x, y)

    result = validate_candidates(
        x,
        y,
        full,
        ValidationOptions(repetitions=2, bootstrap_resamples=0),
    )

    for repetition in range(2):
        for value in np.unique(x):
            folds = {
                row.fold
                for row in result.oof
                if row.repetition == repetition and row.x == value
            }
            assert len(folds) == 1


def test_validation_replays_the_full_fit_search_policy(monkeypatch) -> None:
    x = np.arange(20, dtype=float)
    y = 1.0 + 0.4 * x
    hypothesis_space = HypothesisSpace.linear_seeds()
    full = fit_candidates(
        x,
        y,
        FitOptions(
            min_segment_share=0.45,
            max_elementary_starts=1,
            search_policy=SearchPolicy("balanced"),
            hypothesis_space=hypothesis_space,
        ),
    )
    seen: list[FitOptions] = []

    def record_options(_x, _y, options):
        seen.append(options)
        return full

    monkeypatch.setattr(validation_module, "fit_candidates", record_options)

    validate_candidates(
        x,
        y,
        full,
        ValidationOptions(repetitions=1, bootstrap_resamples=0),
    )

    assert seen
    assert all(options.min_segment_share == 0.45 for options in seen)
    assert all(options.max_elementary_starts == 1 for options in seen)
    assert all(options.search_policy.profile == "balanced" for options in seen)
    assert all(options.hypothesis_space == hypothesis_space for options in seen)
