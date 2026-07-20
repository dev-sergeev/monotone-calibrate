from __future__ import annotations

import numpy as np
import pytest

from monotone_calibrate.model_runtime import FittedModel, SegmentModel, model_from_dict


@pytest.mark.parametrize(
    ("family_id", "parameters", "expected"),
    (
        ("constant_v1", {"a": 1.23456}, "1.235"),
        ("poly1_v1", {"a": 1.23456, "b": 2.34567}, "1.235+2.346*((x-0.000)/1.000)"),
        (
            "poly2_v1",
            {"a": 1.23456, "b": 2.34567, "c": 3.45678},
            "1.235+2.346*((x-0.000)/1.000)+3.457*((x-0.000)/1.000)^2",
        ),
        (
            "poly3_v1",
            {"a": 1.23456, "b": 2.34567, "c": 3.45678, "d": 4.56789},
            "1.235+2.346*((x-0.000)/1.000)+3.457*((x-0.000)/1.000)^2+4.568*((x-0.000)/1.000)^3",
        ),
        (
            "exp_affine_v1",
            {"a": 1.23456, "b": 2.34567, "k": 3.45678},
            "1.235+2.346*exp(3.457*((x-0.000)/1.000))",
        ),
        (
            "log_shift_v1",
            {"a": 1.23456, "b": 2.34567, "d": 3.45678},
            "1.235+2.346*log(((x-0.000)/1.000)+3.457)",
        ),
        (
            "reciprocal_shift_pos_v1",
            {"a": 1.23456, "b": 2.34567, "d": 3.45678},
            "1.235+2.346/(((x-0.000)/1.000)+3.457)",
        ),
        (
            "logistic_v1",
            {"a": 1.23456, "b": 2.34567, "k": 3.45678, "m": 0.45678},
            "1.235+2.346/(1+exp(-3.457*(((x-0.000)/1.000)-0.457)))",
        ),
    ),
)
def test_every_family_displays_coefficients_to_thousandths(
    family_id: str,
    parameters: dict[str, float],
    expected: str,
) -> None:
    segment = SegmentModel(family_id, 0.0, 1.0, parameters)

    assert segment.formula() == expected


def test_thousandth_grid_parameters_are_the_executable_serialized_parameters() -> None:
    model = FittedModel(
        (
            SegmentModel(
                "poly1_v1",
                0.0,
                2.0,
                {"a": 1.235, "b": 2.346},
            ),
        ),
        "increasing",
        coefficient_decimal_places=3,
    )
    prediction = np.asarray(model.predict(np.asarray([0.0, 1.0, 2.0])))

    payload = model.to_dict()
    restored = model_from_dict(payload)

    assert payload["schema_version"] == "model-runtime-v2"
    assert payload["coefficient_decimal_places"] == 3
    assert payload["segments"][0]["parameters"] == {"a": 1.235, "b": 2.346}
    assert payload["formula"] == "f(x)=1.235+2.346*((x-0.000)/2.000)"
    assert np.array_equal(restored.predict(np.asarray([0.0, 1.0, 2.0])), prediction)


def test_thousandth_grid_model_rejects_a_more_precise_parameter() -> None:
    with pytest.raises(ValueError, match="coefficient grid"):
        FittedModel(
            (
                SegmentModel(
                    "poly1_v1",
                    0.0,
                    2.0,
                    {"a": 1.23456, "b": 2.346},
                ),
            ),
            "increasing",
            coefficient_decimal_places=3,
        )


def test_thousandth_grid_model_rejects_a_large_more_precise_parameter() -> None:
    with pytest.raises(ValueError, match="coefficient grid"):
        FittedModel(
            (
                SegmentModel(
                    "constant_v1",
                    0.0,
                    2.0,
                    {"a": 1_000_000_000_000.1234},
                ),
            ),
            "flat",
            coefficient_decimal_places=3,
        )


def test_thousandth_grid_p2_rejects_a_more_precise_breakpoint() -> None:
    with pytest.raises(ValueError, match="breakpoint.*coefficient grid"):
        FittedModel(
            (
                SegmentModel("constant_v1", 0.0, 0.5004, {"a": 1.0}, "left"),
                SegmentModel(
                    "poly1_v1",
                    0.5004,
                    1.0,
                    {"a": 1.0, "b": 1.0},
                    "right",
                ),
            ),
            "increasing",
            coefficient_decimal_places=3,
        )


def test_quantized_p2_allows_only_one_grid_step_of_join_mismatch() -> None:
    left = SegmentModel("constant_v1", 0.0, 1.0, {"a": 1.0}, "left")
    within_grid = SegmentModel(
        "poly1_v1",
        1.0,
        2.0,
        {"a": 1.001, "b": 1.0},
        "right",
    )
    outside_grid = SegmentModel(
        "poly1_v1",
        1.0,
        2.0,
        {"a": 1.002, "b": 1.0},
        "right",
    )

    accepted = FittedModel(
        (left, within_grid),
        "increasing",
        coefficient_decimal_places=3,
    )

    assert accepted.monotonicity_certified is True
    with pytest.raises(ValueError, match="coefficient precision"):
        FittedModel(
            (left, outside_grid),
            "increasing",
            coefficient_decimal_places=3,
        )


def test_quantized_increasing_p2_rejects_a_downward_join_step() -> None:
    left = SegmentModel("constant_v1", 0.0, 1.0, {"a": 1.001}, "left")
    right = SegmentModel(
        "poly1_v1",
        1.0,
        2.0,
        {"a": 1.0, "b": 1.0},
        "right",
    )

    with pytest.raises(ValueError, match="globally monotone"):
        FittedModel(
            (left, right),
            "increasing",
            coefficient_decimal_places=3,
        )


def test_rounded_formula_renders_negative_coefficients_without_plus_minus() -> None:
    segment = SegmentModel(
        "poly2_v1",
        0.0,
        1.0,
        {"a": 1.23456, "b": -2.34567, "c": -0.0004},
    )

    assert segment.formula() == (
        "1.235-2.346*((x-0.000)/1.000)+0.000*((x-0.000)/1.000)^2"
    )


def test_legacy_full_precision_model_runtime_remains_loadable() -> None:
    model = FittedModel(
        (
            SegmentModel(
                "poly1_v1",
                0.0,
                2.0,
                {"a": 1.23456, "b": 2.34567},
            ),
        ),
        "increasing",
    )
    legacy = model.to_dict(schema_version="model-runtime-v1")

    restored = model_from_dict(legacy)

    assert legacy["formula"] == "f(x)=1.2345600000000001+2.3456700000000001*((x-0)/2)"
    assert restored.model_instance_hash == model.model_instance_hash
