from __future__ import annotations

from hashlib import sha256
import csv
import json
from pathlib import Path

import pytest

from monotone_calibrate.bundle_runtime import (
    BundleRuntimeError,
    verify_bundle,
    write_predictions,
)
from monotone_calibrate.model_runtime import FittedModel, SegmentModel


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _linear_model() -> FittedModel:
    return FittedModel(
        (
            SegmentModel(
                family_id="poly1_v1",
                x_lower=0.0,
                x_upper=2.0,
                parameters={"a": 1.0, "b": 2.0},
            ),
        ),
        "increasing",
    )


def _write_bundle(root: Path, *, include_model: bool = True) -> tuple[Path, FittedModel]:
    root.mkdir()
    model = _linear_model()
    structure = "P1" if include_model else None
    recommendation = {
        "structure": structure,
        "decision_state": "P1_RETAINED" if include_model else "DESCRIPTIVE_ONLY",
        "formula": model.formula if include_model else None,
        "model_instance_hash": model.model_instance_hash if include_model else None,
    }
    report = {
        "schema_version": "monotone-report-v1",
        "report_id": "a" * 64,
        "generated_at": "2026-01-01T00:00:00Z",
        "input": {
            "sha256": "b" * 64,
            "n_input": 3,
            "n_used": 3,
            "n_skipped": 0,
            "n_unique_x": 3,
            "extra_columns_ignored": [],
        },
        "candidates": {
            "P1": {
                "status": "VALID",
                "available": True,
                "metrics": {},
                "segment_metrics": [],
                "model": model.to_dict(),
            },
            "P2": {"status": "NO_BALANCED_SPLIT", "available": False},
        },
        "validation": {},
        "recommendation": recommendation,
        "warnings": [],
        "diagnostics": {},
        "llm_advisor": {"status": "NOT_CONFIGURED", "used_for_formula": False},
    }
    artifacts: dict[str, bytes] = {
        "report.json": _json_bytes(report),
        "report.html": b"<!doctype html><title>report</title>\n",
        "model-comparison.json": b"{}\n",
        "input-audit.json": b"{}\n",
        "observations.csv": b"row_id,x,y\nid:one,0,1\n",
        "plot-one.svg": b"<svg xmlns=\"http://www.w3.org/2000/svg\"/>\n",
        "plot-two.svg": b"<svg xmlns=\"http://www.w3.org/2000/svg\"/>\n",
    }
    if include_model:
        artifacts["recommended-model.json"] = _json_bytes(model.to_dict())
    for name, payload in artifacts.items():
        (root / name).write_bytes(payload)
    manifest = {
        "schema_version": "bundle-manifest-v1",
        "report_id": report["report_id"],
        "artifacts": [
            {"path": name, "bytes": len(payload), "sha256": sha256(payload).hexdigest()}
            for name, payload in sorted(artifacts.items())
        ],
    }
    (root / "manifest.json").write_bytes(_json_bytes(manifest))
    return root, model


def _refresh_manifest_artifact(root: Path, name: str) -> None:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = (root / name).read_bytes()
    record = next(item for item in manifest["artifacts"] if item["path"] == name)
    record["bytes"] = len(payload)
    record["sha256"] = sha256(payload).hexdigest()
    manifest_path.write_bytes(_json_bytes(manifest))


def test_completed_bundle_verifies_all_declared_artifacts_and_recommended_model(
    tmp_path: Path,
) -> None:
    bundle, _ = _write_bundle(tmp_path / "bundle")

    result = verify_bundle(bundle)

    assert result.status == "VERIFIED"
    assert result.report_id == "a" * 64
    assert result.artifact_count == 8
    assert result.recommended_model_present is True


def test_descriptive_bundle_verifies_only_when_recommended_model_is_absent(tmp_path: Path) -> None:
    bundle, _ = _write_bundle(tmp_path / "bundle", include_model=False)

    result = verify_bundle(bundle)

    assert result.status == "VERIFIED"
    assert result.artifact_count == 7
    assert result.recommended_model_present is False


def test_bundle_verification_rejects_artifact_byte_tampering(tmp_path: Path) -> None:
    bundle, _ = _write_bundle(tmp_path / "bundle")
    plot = bundle / "plot-one.svg"
    payload = plot.read_bytes()
    plot.write_bytes(payload.replace(b"svg", b"SVG", 1))

    with pytest.raises(BundleRuntimeError) as captured:
        verify_bundle(bundle)

    assert captured.value.code == "ARTIFACT_HASH_MISMATCH"


def test_bundle_verification_rejects_rehashed_but_semantically_different_model(
    tmp_path: Path,
) -> None:
    bundle, _ = _write_bundle(tmp_path / "bundle")
    other = FittedModel(
        (
            SegmentModel(
                family_id="poly1_v1",
                x_lower=0.0,
                x_upper=2.0,
                parameters={"a": 5.0, "b": 2.0},
            ),
        ),
        "increasing",
    )
    (bundle / "recommended-model.json").write_bytes(_json_bytes(other.to_dict()))
    _refresh_manifest_artifact(bundle, "recommended-model.json")

    with pytest.raises(BundleRuntimeError) as captured:
        verify_bundle(bundle)

    assert captured.value.code == "MODEL_IDENTITY_MISMATCH"


def test_prediction_retains_rows_and_types_invalid_and_out_of_domain_x(tmp_path: Path) -> None:
    bundle, _ = _write_bundle(tmp_path / "bundle")
    source = tmp_path / "values.csv"
    source.write_text(
        "row_id,x,note\n"
        "=unsafe,0,left edge\n"
        "same-a,1,duplicate support A\n"
        "same-b,1,duplicate support B\n"
        "far,3,outside\n"
        "bad,not-a-number,invalid\n"
        "infinite,inf,nonfinite\n"
        "\n",
        encoding="utf-8",
    )
    output = tmp_path / "predictions.csv"

    result = write_predictions(bundle / "recommended-model.json", source, output)

    assert result.status == "WRITTEN"
    assert result.n_input == 6
    assert result.n_ok == 3
    assert result.n_out_of_domain == 1
    assert result.n_invalid_x == 2
    with output.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows == [
        {"row_id": "id:%3Dunsafe", "x": "0.0", "prediction": "1.0", "status": "OK"},
        {"row_id": "id:same-a", "x": "1.0", "prediction": "2.0", "status": "OK"},
        {"row_id": "id:same-b", "x": "1.0", "prediction": "2.0", "status": "OK"},
        {"row_id": "id:far", "x": "3.0", "prediction": "", "status": "OUT_OF_DOMAIN"},
        {"row_id": "id:bad", "x": "", "prediction": "", "status": "INVALID_X"},
        {"row_id": "id:infinite", "x": "", "prediction": "", "status": "INVALID_X"},
    ]


def test_prediction_generated_row_ids_cannot_collide_with_external_ids(tmp_path: Path) -> None:
    bundle, _ = _write_bundle(tmp_path / "bundle")
    source = tmp_path / "ids.csv"
    source.write_text(
        "row_id,x\n"
        "duplicate,0\n"
        "duplicate,1\n"
        "row-000001,2\n",
        encoding="utf-8",
    )
    output = tmp_path / "predictions.csv"

    write_predictions(bundle / "recommended-model.json", source, output)

    with output.open(encoding="utf-8", newline="") as stream:
        ids = [row["row_id"] for row in csv.DictReader(stream)]
    assert ids == ["id:row-000001~1", "id:row-000002", "id:row-000001"]
    assert len(ids) == len(set(ids))


def test_prediction_schema_error_does_not_leave_partial_output(tmp_path: Path) -> None:
    bundle, _ = _write_bundle(tmp_path / "bundle")
    source = tmp_path / "bad.csv"
    source.write_text("x,x\n1,2\n", encoding="utf-8")
    output = tmp_path / "predictions.csv"

    with pytest.raises(BundleRuntimeError) as captured:
        write_predictions(bundle / "recommended-model.json", source, output)

    assert captured.value.code == "PREDICTION_SCHEMA_ERROR"
    assert not output.exists()
    assert not list(tmp_path.glob(".predictions.csv.*.tmp"))
