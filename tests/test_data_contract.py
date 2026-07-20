from __future__ import annotations

import pytest

from monotone_calibrate.data import DataContractError, read_xy_csv


def test_invalid_numeric_rows_are_audited_while_duplicate_x_rows_remain_observations(
    tmp_path,
) -> None:
    source = tmp_path / "curve.csv"
    source.write_text(
        "x,y,row_id\n"
        "1,10,a\n"
        "1,12,b\n"
        "2,,missing-y\n"
        "oops,4,bad-x\n"
        "3,nan,bad-y\n",
        encoding="utf-8",
    )

    dataset = read_xy_csv(source)

    assert dataset.n_input == 5
    assert [(row.x, row.y, row.row_id) for row in dataset.observations] == [
        (1.0, 10.0, "a"),
        (1.0, 12.0, "b"),
    ]
    assert [row.reason_codes for row in dataset.exclusions] == [
        ("MISSING_Y",),
        ("INVALID_X",),
        ("NONFINITE_Y",),
    ]
    assert "INVALID_ROWS_SKIPPED" in dataset.warning_codes


def test_duplicate_and_blank_external_row_ids_are_replaced_without_renumbering(
    tmp_path,
) -> None:
    source = tmp_path / "ids.csv"
    source.write_text(
        "x,y,row_id\n"
        "0,1,same\n"
        "1,2,same\n"
        "2,3,\n"
        "3,4,unique\n",
        encoding="utf-8",
    )

    dataset = read_xy_csv(source)

    assert [row.row_id for row in dataset.observations] == [
        "row-000001",
        "row-000002",
        "row-000003",
        "unique",
    ]
    assert "ROW_ID_REPLACED" in dataset.warning_codes


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("x,z\n1,2\n", "SCHEMA_ERROR"),
        ("x,y,x\n1,2,3\n", "SCHEMA_ERROR"),
        ("x,y,weight\n1,2,1\n", "UNSUPPORTED_MODEL_COLUMN"),
    ],
)
def test_file_level_schema_errors_do_not_enter_row_processing(
    tmp_path,
    content: str,
    code: str,
) -> None:
    source = tmp_path / "bad-schema.csv"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(DataContractError) as captured:
        read_xy_csv(source)

    assert captured.value.code == code


@pytest.mark.parametrize(
    ("content", "status"),
    [
        ("x,y\n,\nno,no\n", "NO_VALID_ROWS"),
        ("x,y\n1,1\n1,2\n1,3\n1,4\n", "CONSTANT_X"),
        ("x,y\n1,1\n2,2\n3,3\n", "INSUFFICIENT_DATA_FOR_P1"),
        ("x,y\n1,1\n2,2\n3,3\n4,4\n", "READY"),
    ],
)
def test_fit_readiness_is_typed_instead_of_silently_attempting_an_invalid_fit(
    tmp_path,
    content: str,
    status: str,
) -> None:
    source = tmp_path / "readiness.csv"
    source.write_text(content, encoding="utf-8")

    assert read_xy_csv(source).fit_readiness == status
