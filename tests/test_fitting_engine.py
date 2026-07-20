from __future__ import annotations

import numpy as np
import pytest

from monotone_calibrate import engine
from monotone_calibrate.engine import FitOptions, StartOverride, fit_candidates, p1_start_slots
from monotone_calibrate.model_runtime import FittedModel, SegmentModel


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
    left, right = candidates.two.model.segments
    assert float(left.predict_unchecked(breakpoint)) == pytest.approx(
        float(right.predict_unchecked(breakpoint)),
        abs=1e-10,
    )
    grid = np.linspace(x.min(), x.max(), 1001)
    assert np.all(np.diff(candidates.two.model.predict(grid)) >= -1e-10)


def test_repeated_x_group_cannot_be_split_to_manufacture_a_small_segment() -> None:
    x = np.asarray([0.0] * 3 + [1.0] * 14 + [2.0] * 3)
    y = 1.0 + x

    candidates = fit_candidates(x, y)

    assert candidates.one.status == "VALID"
    assert candidates.two is None
    assert candidates.two_status == "NO_BALANCED_SPLIT"


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
