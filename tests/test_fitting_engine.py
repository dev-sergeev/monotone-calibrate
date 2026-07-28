from __future__ import annotations

import numpy as np
import pytest

from monotone_calibrate import engine
from monotone_calibrate.engine import (
    FitOptions,
    SearchPolicy,
    StartOverride,
    fit_candidates,
    p1_start_slots,
)
from monotone_calibrate.hypotheses import EquationHypothesis, HypothesisSpace
from monotone_calibrate.model_runtime import FittedModel, SegmentModel


def _logistic_mix_fixture() -> tuple[np.ndarray, np.ndarray]:
    x = np.linspace(0.0, 100.0, 60)
    z = x / 100.0
    boundary = 0.48

    def expit(value):
        return 1.0 / (1.0 + np.exp(-value))

    left = 20.0 + 55.0 * (expit(8.0 * (z / boundary - 0.55)) - expit(-4.4)) / (
        expit(3.6) - expit(-4.4)
    )
    right = 75.0 + 70.0 * np.log1p(8.0 * np.maximum((z - boundary) / (1.0 - boundary), 0.0)) / np.log(9.0)
    mean = np.where(z <= boundary, left, right)
    return x, mean + np.random.default_rng(3).normal(0.0, 0.6, x.size)


def _high_noise_fixture() -> tuple[np.ndarray, np.ndarray]:
    x = np.linspace(0.0, 100.0, 30)
    z = x / 100.0
    boundary, left_slope, right_slope = 0.58, 0.40, 0.80
    mean = np.where(
        z <= boundary,
        3.0 + left_slope * 100.0 * z,
        3.0 + left_slope * 100.0 * boundary + right_slope * 100.0 * (z - boundary),
    )
    return x, mean + np.random.default_rng(6).normal(0.0, 15.0, x.size)


def test_one_function_recovers_a_monotone_cubic_with_a_global_certificate() -> None:
    x = np.linspace(-2.0, 3.0, 31)
    t = (x + 2.0) / 5.0
    y = 1.0 + 2.0 * t + 3.0 * t**2 + 4.0 * t**3

    candidates = fit_candidates(x, y)
    grid = np.linspace(x.min(), x.max(), 501)
    predicted = candidates.one.model.predict(grid)

    assert candidates.one.status == "VALID"
    assert candidates.one.r2_refit > 0.999999
    assert candidates.one.model.segment_count == 1
    assert candidates.one.model.polynomial_degree <= 3
    assert np.all(np.isfinite(predicted))
    assert np.all(np.diff(predicted) >= -1e-10)
    assert candidates.one.model.monotonicity_certified is True
    assert candidates.one.model.coefficient_decimal_places == 3
    assert all(
        value == round(value, 3)
        for segment in candidates.one.model.segments
        for value in segment.parameters.values()
    )


def test_candidate_sse_is_scored_from_the_thousandth_grid_model() -> None:
    x = np.linspace(0.0, 10.0, 21)
    t = x / 10.0
    y = 1.23456 + 2.34567 * t

    candidate = fit_candidates(x, y).one
    prediction = np.asarray(candidate.model.predict(x))

    assert candidate.model.coefficient_decimal_places == 3
    assert all(
        value == round(value, 3)
        for segment in candidate.model.segments
        for value in segment.parameters.values()
    )
    assert candidate.sse == pytest.approx(float(np.sum((y - prediction) ** 2)))
    assert candidate.sse > 0.0


def test_two_function_candidate_is_continuous_monotone_and_balanced_at_raw_x_gap() -> None:
    x = np.arange(30, dtype=float)
    breakpoint = 11.5
    join = 2.0 + 0.15 * breakpoint
    y = np.where(
        x <= 11.0,
        join + 0.15 * (x - breakpoint),
        join + 1.20 * (x - breakpoint),
    )

    candidates = fit_candidates(x, y)

    assert candidates.two_status == "VALID"
    assert candidates.two is not None
    assert candidates.two.segment_shares == (0.4, 0.6)
    assert candidates.two.r2_refit is not None and candidates.two.r2_refit > 0.999999
    assert candidates.two.r2_refit > candidates.one.r2_refit
    assert candidates.two.model.segment_count == 2
    assert candidates.two.model.breakpoint == breakpoint
    assert candidates.two.model.coefficient_decimal_places == 3
    assert all(
        value == round(value, 3)
        for segment in candidates.two.model.segments
        for value in segment.parameters.values()
    )
    left, right = candidates.two.model.segments
    assert float(left.predict_unchecked(breakpoint)) == pytest.approx(
        float(right.predict_unchecked(breakpoint)),
        abs=1e-10,
    )
    grid = np.linspace(x.min(), x.max(), 1001)
    assert np.all(np.diff(candidates.two.model.predict(grid)) >= -1e-10)


def test_practically_tied_p2_candidates_choose_simpler_families() -> None:
    x = np.arange(30, dtype=float)
    breakpoint = 11.5
    join = 2.0 + 0.15 * breakpoint
    mean = np.where(
        x <= 11.0,
        join + 0.15 * (x - breakpoint),
        join + 1.20 * (x - breakpoint),
    )
    y = mean + 0.03 * np.where(np.arange(x.size) % 2 == 0, -1.0, 1.0)
    portfolio = HypothesisSpace(
        (
            EquationHypothesis("P1", ("poly1_v1",)),
            EquationHypothesis("P2", ("poly1_v1", "poly1_v1")),
            EquationHypothesis("P2", ("poly2_v1", "poly2_v1")),
        ),
        "llm_sr",
    )

    candidates = fit_candidates(x, y, FitOptions(hypothesis_space=portfolio))

    assert candidates.two is not None
    assert candidates.two.model.family_ids == ("poly1_v1", "poly1_v1")


def test_practically_tied_p1_candidates_choose_simpler_family() -> None:
    x = np.linspace(0.0, 10.0, 40)
    y = 2.0 + 0.5 * x + np.random.default_rng(2).normal(0.0, 0.1, x.size)
    portfolio = HypothesisSpace(
        (
            EquationHypothesis("P1", ("poly1_v1",)),
            EquationHypothesis("P1", ("poly2_v1",)),
            EquationHypothesis("P2", ("poly1_v1", "poly1_v1")),
        ),
        "llm_sr",
    )

    candidates = fit_candidates(x, y, FitOptions(hypothesis_space=portfolio))

    assert candidates.one.model.family_ids == ("poly1_v1",)


def test_p2_parsimony_tolerance_prevents_fold_level_family_flapping() -> None:
    x = np.linspace(0.0, 10.0, 100)
    mean = np.where(x <= 5.0, 10.0 + 0.5 * x, 12.5 + 2.0 * (x - 5.0))
    rng = np.random.default_rng(6)
    innovation = rng.normal(0.0, 0.12, x.size)
    noise = np.empty_like(innovation)
    noise[0] = innovation[0]
    for index in range(1, noise.size):
        noise[index] = 0.65 * noise[index - 1] + innovation[index]
    portfolio = HypothesisSpace(
        (
            EquationHypothesis("P1", ("poly1_v1",)),
            EquationHypothesis("P2", ("poly1_v1", "poly1_v1")),
            EquationHypothesis("P2", ("poly1_v1", "poly2_v1")),
        ),
        "llm_sr",
    )

    candidates = fit_candidates(
        x,
        mean + noise,
        FitOptions(hypothesis_space=portfolio),
    )

    assert candidates.two is not None
    assert candidates.two.model.family_ids == ("poly1_v1", "poly1_v1")


def test_budgeted_search_profiles_are_reproducible_and_close_to_exhaustive() -> None:
    x = np.arange(60, dtype=float)
    breakpoint = 23.5
    join = 2.0 + 0.15 * breakpoint
    y = np.where(
        x <= 23.0,
        join + 0.15 * (x - breakpoint),
        join + 1.20 * (x - breakpoint),
    )

    exhaustive = fit_candidates(
        x,
        y,
        FitOptions(search_policy=SearchPolicy("exhaustive")),
    )
    fast = fit_candidates(
        x,
        y,
        FitOptions(search_policy=SearchPolicy("fast")),
    )
    repeated = fit_candidates(
        x,
        y,
        FitOptions(search_policy=SearchPolicy("fast")),
    )

    assert exhaustive.two is not None
    assert fast.two is not None
    assert fast.two.sse <= exhaustive.two.sse * 1.001 + 1e-10
    assert fast.two.model.model_instance_hash == repeated.two.model.model_instance_hash
    assert fast.search_trace.profile == "fast"
    assert fast.search_trace.approximate is True
    assert fast.search_trace.evaluated_candidates < exhaustive.search_trace.evaluated_candidates


def test_fast_profile_keeps_the_full_nonlinear_shape_registry() -> None:
    x, y = _high_noise_fixture()

    exhaustive = fit_candidates(x, y, FitOptions(search_policy=SearchPolicy("exhaustive")))
    fast = fit_candidates(x, y, FitOptions(search_policy=SearchPolicy("fast")))

    assert exhaustive.two is not None
    assert fast.two is not None
    assert fast.two.sse == pytest.approx(exhaustive.two.sse, abs=1e-10)
    assert fast.two.breakpoint == pytest.approx(exhaustive.two.breakpoint, abs=1e-12)
    assert fast.two.model.model_instance_hash == exhaustive.two.model.model_instance_hash


def test_fast_profile_refines_the_linear_baseline_before_parsimony_selection() -> None:
    rng = np.random.default_rng(101)
    x = np.sort(rng.uniform(0.0, 100.0, 140))
    for index in range(14, x.size, 29):
        x[index] = x[index - 1]
    join = 12.0 + 0.08 * 45.0
    mean = np.where(
        x <= 45.0,
        12.0 + 0.08 * x,
        join + 0.35 * (x - 45.0),
    )
    relative_position = (x - float(np.min(x))) / float(np.ptp(x))
    sigma = (0.004 + 0.006 * relative_position) * float(np.ptp(mean))
    y = mean + rng.normal(0.0, sigma)
    portfolio = HypothesisSpace(
        (
            EquationHypothesis("P1", ("poly1_v1",)),
            EquationHypothesis("P2", ("poly1_v1", "poly1_v1")),
            EquationHypothesis("P2", ("poly1_v1", "poly2_v1")),
            EquationHypothesis("P2", ("poly2_v1", "poly2_v1")),
        ),
        "llm_sr",
    )

    fast = fit_candidates(
        x,
        y,
        FitOptions(
            hypothesis_space=portfolio,
            search_policy=SearchPolicy("fast"),
        ),
    )
    exhaustive = fit_candidates(
        x,
        y,
        FitOptions(
            hypothesis_space=portfolio,
            search_policy=SearchPolicy("exhaustive"),
        ),
    )

    assert fast.two is not None
    assert exhaustive.two is not None
    assert fast.two.model.family_ids == exhaustive.two.model.family_ids
    assert fast.two.breakpoint == exhaustive.two.breakpoint


def test_finite_sample_complexity_guard_rejects_noise_fitting_polynomial_degrees() -> None:
    rng = np.random.default_rng(101)
    x = np.sort(rng.uniform(0.0, 100.0, 140))
    for index in range(14, x.size, 29):
        x[index] = x[index - 1]
    join = 12.0 + 0.08 * 45.0
    mean = np.where(
        x <= 45.0,
        12.0 + 0.08 * x,
        join + 0.35 * (x - 45.0),
    )
    relative_position = (x - float(np.min(x))) / float(np.ptp(x))
    sigma = (0.004 + 0.006 * relative_position) * float(np.ptp(mean))
    y = mean + rng.normal(0.0, sigma)
    polynomial_families = ("poly1_v1", "poly2_v1", "poly3_v1")
    hypotheses = [EquationHypothesis("P1", ("poly1_v1",))]
    hypotheses.extend(
        EquationHypothesis("P2", (left, right))
        for left in polynomial_families
        for right in polynomial_families
    )

    candidates = fit_candidates(
        x,
        y,
        FitOptions(
            hypothesis_space=HypothesisSpace(tuple(hypotheses), "llm_sr"),
            search_policy=SearchPolicy("exhaustive"),
        ),
    )

    assert candidates.two is not None
    assert candidates.two.model.family_ids == ("poly1_v1", "poly1_v1")


def test_fast_profile_adaptively_refines_a_nonlinear_breakpoint() -> None:
    x, y = _logistic_mix_fixture()

    exhaustive = fit_candidates(x, y, FitOptions(search_policy=SearchPolicy("exhaustive")))
    fast = fit_candidates(x, y, FitOptions(search_policy=SearchPolicy("fast")))

    assert exhaustive.two is not None
    assert fast.two is not None
    assert fast.two.sse == pytest.approx(exhaustive.two.sse, abs=1e-10)
    assert fast.two.breakpoint == pytest.approx(exhaustive.two.breakpoint, abs=1e-12)
    assert fast.two.model.model_instance_hash == exhaustive.two.model.model_instance_hash
    assert fast.search_trace.evaluated_cells < exhaustive.search_trace.evaluated_cells


def test_repeated_x_group_cannot_be_split_to_manufacture_a_small_segment() -> None:
    x = np.asarray([0.0] * 3 + [1.0] * 14 + [2.0] * 3)
    y = 1.0 + x

    candidates = fit_candidates(x, y)

    assert candidates.one.status == "VALID"
    assert candidates.two is None
    assert candidates.two_status == "NO_BALANCED_SPLIT"


def test_every_eligible_p2_breakpoint_is_on_the_thousandth_grid() -> None:
    x = np.linspace(0.000123, 1.000987, 30)

    cells = engine._eligible_cells(x, 0.40)

    assert cells
    assert all(breakpoint == round(breakpoint, 3) for _, breakpoint in cells)


def test_quantized_p2_join_preserves_the_global_direction() -> None:
    x = np.linspace(0.000123, 1.000987, 60)
    source_breakpoint = 0.483
    join = 1.23456 + 0.34567 * source_breakpoint
    y = np.where(
        x <= source_breakpoint,
        join + 0.34567 * (x - source_breakpoint),
        join + 1.23456 * (x - source_breakpoint),
    )

    candidate = fit_candidates(x, y).two

    assert candidate is not None
    assert candidate.model.direction == "increasing"
    breakpoint = candidate.model.breakpoint
    assert breakpoint is not None
    left, right = candidate.model.segments
    left_join = float(left.predict_unchecked(breakpoint))
    right_join = float(right.predict_unchecked(breakpoint))
    assert right_join >= left_join - 1e-12
    assert right_join - left_join <= 0.001 + 1e-12
    grid = np.unique(
        np.concatenate(
            (
                np.linspace(x.min(), x.max(), 1001),
                np.asarray([breakpoint, np.nextafter(breakpoint, np.inf)]),
            )
        )
    )
    assert np.all(np.diff(candidate.model.predict(grid)) >= -1e-12)


def test_constant_response_is_canonical_one_function_not_a_fake_two_segment_fit() -> None:
    x = np.arange(12, dtype=float)
    y = np.full(x.shape, 2.75)

    candidates = fit_candidates(x, y)

    assert candidates.one.model.direction == "flat"
    assert candidates.one.model.family_ids == ("constant_v1",)
    assert candidates.one.r2_refit is None
    assert candidates.two is None
    assert candidates.two_status == "COLLAPSED_TO_P1"


def test_constant_function_cannot_be_serialized_as_a_nonconstant_family_or_direction() -> None:
    with pytest.raises(ValueError, match="canonical"):
        FittedModel(
            (SegmentModel("poly3_v1", 0.0, 1.0, {"a": 2.0, "b": 0.0, "c": 0.0, "d": 0.0}),),
            "increasing",
        )
    with pytest.raises(ValueError, match="canonical"):
        FittedModel((SegmentModel("constant_v1", 0.0, 1.0, {"a": 2.0}),), "decreasing")
    with pytest.raises(ValueError, match="collapse"):
        FittedModel(
            (
                SegmentModel("constant_v1", 0.0, 0.5, {"a": 2.0}, "left"),
                SegmentModel("constant_v1", 0.5, 1.0, {"a": 2.0}, "right"),
            ),
            "flat",
        )


def test_decreasing_direction_is_searched_and_certified_globally() -> None:
    x = np.linspace(0.0, 4.0, 24)
    y = 5.0 - 2.0 * np.log1p(x)

    candidates = fit_candidates(x, y)
    grid = np.linspace(0.0, 4.0, 401)

    assert candidates.one.model.direction == "decreasing"
    assert candidates.one.r2_refit is not None and candidates.one.r2_refit > 0.999
    assert np.all(np.diff(candidates.one.model.predict(grid)) <= 1e-10)


def test_bounded_advice_replaces_one_declared_p1_start_without_changing_budget(
    monkeypatch,
) -> None:
    x = np.linspace(-3.0, 2.0, 18)
    y = 2.0 + 0.5 * x
    slot = next(
        item
        for item in p1_start_slots()
        if item.family_id == "exp_affine_v1"
        and item.direction == "increasing"
        and item.bounds == ((0.10, 12.0),)
    )
    advised_vector = (4.321,)
    seen: list[tuple[float, ...]] = []

    class NoOptimization:
        success = False
        x = np.asarray([0.0])

    def record_minimize(objective, x0, **kwargs):
        seen.append(tuple(map(float, x0)))
        return NoOptimization()

    monkeypatch.setattr(engine, "minimize", record_minimize)
    baseline = fit_candidates(x, y)
    baseline_starts = tuple(seen)
    seen.clear()
    advised = fit_candidates(
        x,
        y,
        FitOptions(start_overrides=(StartOverride(slot.slot_id, advised_vector),)),
    )

    assert len(seen) == len(baseline_starts)
    assert advised_vector in seen
    assert seen.count(slot.default_vector) == baseline_starts.count(slot.default_vector) - 1
    assert advised.one.model.monotonicity_certified is True
    assert baseline.two_status == advised.two_status


@pytest.mark.parametrize(
    "override_factory",
    [
        lambda slot: (StartOverride("unknown-slot", (1.0,)),),
        lambda slot: (StartOverride(slot.slot_id.replace("s02", "s00"), slot.default_vector),),
        lambda slot: (StartOverride(slot.slot_id, (1000.0,) * len(slot.default_vector)),),
        lambda slot: (StartOverride(slot.slot_id, (float("nan"),) * len(slot.default_vector)),),
        lambda slot: (
            StartOverride(slot.slot_id, slot.default_vector),
            StartOverride(slot.slot_id, slot.default_vector),
        ),
    ],
)
def test_advised_starts_cannot_escape_predeclared_replaceable_slots(override_factory) -> None:
    slot = p1_start_slots()[0]
    with pytest.raises(ValueError, match="start_overrides"):
        FitOptions(start_overrides=override_factory(slot))
