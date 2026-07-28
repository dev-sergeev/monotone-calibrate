"""Verification and typed prediction for completed calibration bundles."""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Literal, Mapping
from urllib.parse import quote

from .model_runtime import model_from_dict


_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CORE_ARTIFACTS = frozenset(
    {
        "report.html",
        "report.json",
        "model-comparison.json",
        "input-audit.json",
        "observations.csv",
        "plot-one.svg",
        "plot-two.svg",
    }
)
_OPTIONAL_ARTIFACTS = frozenset({"recommended-model.json"})
_REPORT_FIELDS_V1 = frozenset(
    {
        "schema_version",
        "report_id",
        "generated_at",
        "input",
        "candidates",
        "validation",
        "recommendation",
        "warnings",
        "diagnostics",
        "llm_advisor",
    }
)
_REPORT_FIELDS_V2 = _REPORT_FIELDS_V1 | {"search"}
_SEARCH_FIELDS = frozenset(
    {
        "policy_id",
        "profile",
        "approximate",
        "variant_count",
        "min_segment_share",
        "max_elementary_starts",
        "eligible_cells",
        "coarse_cells",
        "evaluated_cells",
        "evaluated_candidates",
        "refinement_pairs",
        "termination",
    }
)


class BundleRuntimeError(ValueError):
    """A stable, typed failure at the bundle/prediction boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: Literal["VERIFIED"]
    bundle_path: Path
    report_id: str
    artifact_count: int
    recommended_model_present: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "bundle_path": str(self.bundle_path),
            "report_id": self.report_id,
            "artifact_count": self.artifact_count,
            "recommended_model_present": self.recommended_model_present,
        }


@dataclass(frozen=True, slots=True)
class PredictionResult:
    status: Literal["WRITTEN"]
    output_path: Path
    n_input: int
    n_ok: int
    n_out_of_domain: int
    n_invalid_x: int

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "output_path": str(self.output_path),
            "n_input": self.n_input,
            "n_ok": self.n_ok,
            "n_out_of_domain": self.n_out_of_domain,
            "n_invalid_x": self.n_invalid_x,
        }


def _fail(code: str, message: str) -> None:
    raise BundleRuntimeError(code, message)


def _object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("DUPLICATE_JSON_KEY", f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _load_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        payload = path.read_bytes()
        text = payload.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_object_from_pairs,
            parse_constant=lambda token: _fail(
                "NONFINITE_JSON_NUMBER", f"{label} contains {token}"
            ),
        )
    except BundleRuntimeError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        _fail("INVALID_JSON", f"cannot read strict UTF-8 JSON object {label}: {error}")
    if not isinstance(value, dict):
        _fail("INVALID_JSON_OBJECT", f"{label} must be a JSON object")
    return value


def _sha256_string(value: object, label: str) -> str:
    if not isinstance(value, str) or _HEX_SHA256.fullmatch(value) is None:
        _fail("INVALID_SHA256", f"{label} must be a lowercase SHA-256 hex string")
    return value


def _artifact_records(manifest: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    if set(manifest) != {"schema_version", "report_id", "artifacts"}:
        _fail("INVALID_MANIFEST", "manifest has unknown or missing fields")
    if manifest.get("schema_version") != "bundle-manifest-v1":
        _fail("INVALID_MANIFEST", "unsupported manifest schema version")
    _sha256_string(manifest.get("report_id"), "manifest report_id")
    raw_records = manifest.get("artifacts")
    if not isinstance(raw_records, list):
        _fail("INVALID_MANIFEST", "manifest artifacts must be a list")
    records: list[dict[str, object]] = []
    names: set[str] = set()
    for raw in raw_records:
        if not isinstance(raw, dict) or set(raw) != {"path", "bytes", "sha256"}:
            _fail("INVALID_MANIFEST", "each artifact record must have path, bytes and sha256")
        name = raw.get("path")
        if (
            not isinstance(name, str)
            or not name
            or name in {".", "..", "manifest.json"}
            or "/" in name
            or "\\" in name
            or "\x00" in name
            or Path(name).name != name
        ):
            _fail("UNSAFE_ARTIFACT_PATH", f"unsafe artifact basename: {name!r}")
        if name in names:
            _fail("DUPLICATE_ARTIFACT", f"duplicate artifact path: {name}")
        names.add(name)
        size = raw.get("bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            _fail("INVALID_MANIFEST", f"invalid byte count for {name}")
        _sha256_string(raw.get("sha256"), f"artifact {name} sha256")
        records.append(raw)
    allowed_sets = {_CORE_ARTIFACTS, _CORE_ARTIFACTS | _OPTIONAL_ARTIFACTS}
    if frozenset(names) not in allowed_sets:
        _fail("INVALID_ARTIFACT_SET", "manifest does not declare the exact v1 artifact set")
    return tuple(records)


def _verify_report_and_model(root: Path, report_id: str, model_present: bool) -> None:
    report = _load_json_object(root / "report.json", "report.json")
    schema_version = report.get("schema_version")
    expected_fields = (
        _REPORT_FIELDS_V1
        if schema_version == "monotone-report-v1"
        else _REPORT_FIELDS_V2 if schema_version == "monotone-report-v2" else None
    )
    if expected_fields is None or set(report) != expected_fields:
        _fail("INVALID_REPORT", "report.json is not a supported closed monotone-report object")
    if report.get("report_id") != report_id:
        _fail("REPORT_ID_MISMATCH", "manifest and report report_id values differ")
    if schema_version == "monotone-report-v2":
        search = report.get("search")
        if (
            not isinstance(search, dict)
            or set(search) != _SEARCH_FIELDS
            or search.get("policy_id") not in {"candidate-search-v1", "candidate-search-v2"}
            or search.get("profile") not in {"fast", "balanced", "quality", "exhaustive"}
            or not isinstance(search.get("approximate"), bool)
            or not isinstance(search.get("variant_count"), int)
            or isinstance(search.get("variant_count"), bool)
            or search["variant_count"] < 1
            or not isinstance(search.get("min_segment_share"), (int, float))
            or isinstance(search.get("min_segment_share"), bool)
            or not 0.0 < search["min_segment_share"] <= 0.5
            or (
                search.get("max_elementary_starts") is not None
                and (
                    not isinstance(search.get("max_elementary_starts"), int)
                    or isinstance(search.get("max_elementary_starts"), bool)
                    or search["max_elementary_starts"] < 0
                )
            )
            or not all(
                isinstance(search.get(name), int)
                and not isinstance(search.get(name), bool)
                and search[name] >= 0
                for name in (
                    "eligible_cells",
                    "coarse_cells",
                    "evaluated_cells",
                    "evaluated_candidates",
                    "refinement_pairs",
                )
            )
            or not isinstance(search.get("termination"), str)
        ):
            _fail("INVALID_REPORT", "report search provenance is malformed")
    recommendation = report.get("recommendation")
    if not isinstance(recommendation, dict) or set(recommendation) != {
        "structure",
        "decision_state",
        "formula",
        "model_instance_hash",
    }:
        _fail("INVALID_RECOMMENDATION", "report recommendation object is malformed")
    structure = recommendation.get("structure")
    if structure not in {None, "P1", "P2"}:
        _fail("INVALID_RECOMMENDATION", "recommended structure must be P1, P2 or null")
    expects_model = structure in {"P1", "P2"}
    if model_present != expects_model:
        _fail(
            "MODEL_PRESENCE_MISMATCH",
            "recommended-model.json presence does not match the report recommendation",
        )
    if not expects_model:
        if recommendation.get("formula") is not None or recommendation.get("model_instance_hash") is not None:
            _fail("MODEL_IDENTITY_MISMATCH", "a report without recommendation must not identify a model")
        return

    model_payload = _load_json_object(root / "recommended-model.json", "recommended-model.json")
    try:
        model = model_from_dict(model_payload)
    except (KeyError, TypeError, ValueError) as error:
        _fail("INVALID_MODEL", f"recommended model is not a valid typed registry model: {error}")
    expected_segments = 1 if structure == "P1" else 2
    if model.segment_count != expected_segments:
        _fail("MODEL_STRUCTURE_MISMATCH", "recommended structure and model segment count differ")
    serialized_formula = model_payload.get("formula")
    if (
        recommendation.get("formula") != serialized_formula
        or recommendation.get("model_instance_hash") != model.model_instance_hash
    ):
        _fail("MODEL_IDENTITY_MISMATCH", "report formula/hash do not match the typed model")

    candidates = report.get("candidates")
    if not isinstance(candidates, dict) or set(candidates) != {"P1", "P2"}:
        _fail("INVALID_REPORT", "report candidates must contain exactly P1 and P2")
    selected = candidates.get(structure)
    if not isinstance(selected, dict) or selected.get("available") is not True:
        _fail("MODEL_STRUCTURE_MISMATCH", "recommended candidate is not available in report")
    candidate_payload = selected.get("model")
    if not isinstance(candidate_payload, dict):
        _fail("MODEL_STRUCTURE_MISMATCH", "recommended candidate has no typed model")
    try:
        candidate_model = model_from_dict(candidate_payload)
    except (KeyError, TypeError, ValueError) as error:
        _fail("INVALID_MODEL", f"report candidate model is invalid: {error}")
    if (
        candidate_model.segment_count != expected_segments
        or candidate_model.model_structure_hash != model.model_structure_hash
        or candidate_model.model_instance_hash != model.model_instance_hash
        or candidate_payload.get("formula") != serialized_formula
    ):
        _fail("MODEL_IDENTITY_MISMATCH", "report candidate and recommended model differ")


def verify_bundle(path: str | Path) -> VerificationResult:
    """Verify one immutable report bundle and its typed recommendation."""

    root = Path(path)
    if root.is_symlink() or not root.is_dir():
        _fail("INVALID_BUNDLE_DIRECTORY", f"bundle must be a real directory: {root}")
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        _fail("INVALID_MANIFEST", "manifest.json must be a regular non-symlink file")
    manifest = _load_json_object(manifest_path, "manifest.json")
    records = _artifact_records(manifest)
    declared_names = {str(record["path"]) for record in records}
    physical_names = {entry.name for entry in root.iterdir()}
    if physical_names != declared_names | {"manifest.json"}:
        _fail("ARTIFACT_SET_MISMATCH", "physical bundle files do not exactly match manifest")

    for record in records:
        name = str(record["path"])
        artifact = root / name
        if artifact.is_symlink() or not artifact.is_file():
            _fail("UNSAFE_ARTIFACT", f"artifact must be a regular non-symlink file: {name}")
        try:
            payload = artifact.read_bytes()
        except OSError as error:
            _fail("ARTIFACT_NOT_READABLE", f"cannot read artifact {name}: {error}")
        if len(payload) != record["bytes"]:
            _fail("ARTIFACT_SIZE_MISMATCH", f"artifact byte count differs: {name}")
        if sha256(payload).hexdigest() != record["sha256"]:
            _fail("ARTIFACT_HASH_MISMATCH", f"artifact SHA-256 differs: {name}")

    report_id = _sha256_string(manifest.get("report_id"), "manifest report_id")
    model_present = "recommended-model.json" in declared_names
    _verify_report_and_model(root, report_id, model_present)
    return VerificationResult(
        status="VERIFIED",
        bundle_path=root.resolve(),
        report_id=report_id,
        artifact_count=len(records),
        recommended_model_present=model_present,
    )


def _prediction_rows(path: Path) -> tuple[list[str], list[tuple[int, list[str]]]]:
    if path.is_symlink() or not path.is_file():
        _fail("INVALID_PREDICTION_INPUT", f"prediction input must be a regular file: {path}")
    try:
        text = path.read_bytes().decode("utf-8-sig")
        parsed = list(csv.reader(text.splitlines(keepends=True), dialect="excel", strict=True))
    except UnicodeDecodeError as error:
        _fail("INVALID_UTF8", f"prediction input must be UTF-8: {error}")
    except (OSError, csv.Error) as error:
        _fail("MALFORMED_PREDICTION_CSV", f"cannot read prediction CSV: {error}")
    if not parsed:
        _fail("PREDICTION_SCHEMA_ERROR", "prediction CSV header is missing")
    header = parsed[0]
    if len(header) != len(set(header)) or header.count("x") != 1 or header.count("row_id") > 1:
        _fail(
            "PREDICTION_SCHEMA_ERROR",
            "prediction CSV requires exactly one x header and at most one row_id header",
        )
    rows = [
        (source_row, values)
        for source_row, values in enumerate(parsed[1:], start=1)
        if values and not all(value == "" for value in values)
    ]
    return header, rows


def _encoded_row_id(logical_id: str) -> str:
    return "id:" + quote(
        logical_id,
        safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-",
        encoding="utf-8",
        errors="strict",
    )


def _load_typed_model(path: Path):
    if path.is_symlink() or not path.is_file():
        _fail("INVALID_MODEL", f"model must be a regular non-symlink file: {path}")
    payload = _load_json_object(path, "model")
    try:
        return model_from_dict(payload)
    except (KeyError, TypeError, ValueError) as error:
        _fail("INVALID_MODEL", f"model is not a typed registry artifact: {error}")


def write_predictions(
    model_path: str | Path,
    input_csv: str | Path,
    output_csv: str | Path,
) -> PredictionResult:
    """Apply a typed registry model and atomically write one result per nonblank row."""

    model = _load_typed_model(Path(model_path))
    header, raw_rows = _prediction_rows(Path(input_csv))
    x_index = header.index("x")
    row_id_index = header.index("row_id") if "row_id" in header else None
    external_ids = [
        values[row_id_index]
        for _, values in raw_rows
        if row_id_index is not None
        and row_id_index < len(values)
        and values[row_id_index] != ""
    ]
    id_counts = Counter(external_ids)
    reserved_external_ids = {value for value, count in id_counts.items() if count == 1}
    assigned_ids: set[str] = set()

    rendered: list[tuple[str, str, str, str]] = []
    n_ok = 0
    n_out_of_domain = 0
    n_invalid_x = 0
    for source_row, values in raw_rows:
        external_id = (
            values[row_id_index]
            if row_id_index is not None and row_id_index < len(values) and values[row_id_index] != ""
            else None
        )
        if external_id is not None and id_counts.get(external_id) == 1:
            logical_id = external_id
        else:
            base_id = f"row-{source_row:06d}"
            logical_id = base_id
            suffix = 1
            while logical_id in reserved_external_ids or logical_id in assigned_ids:
                logical_id = f"{base_id}~{suffix}"
                suffix += 1
        assigned_ids.add(logical_id)
        raw_x = values[x_index] if x_index < len(values) else ""
        try:
            x_value = float(raw_x.strip())
        except ValueError:
            x_value = math.nan
        if not math.isfinite(x_value):
            rendered.append((_encoded_row_id(logical_id), "", "", "INVALID_X"))
            n_invalid_x += 1
            continue
        if x_value == 0.0:
            x_value = 0.0
        x_text = repr(x_value)
        if x_value < model.x_lower or x_value > model.x_upper:
            rendered.append((_encoded_row_id(logical_id), x_text, "", "OUT_OF_DOMAIN"))
            n_out_of_domain += 1
            continue
        prediction = float(model.predict(x_value))
        if not math.isfinite(prediction):
            _fail("NONFINITE_MODEL_PREDICTION", f"typed model returned no finite value for x={x_text}")
        rendered.append((_encoded_row_id(logical_id), x_text, repr(prediction), "OK"))
        n_ok += 1

    destination = Path(output_csv)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(["row_id", "x", "prediction", "status"])
            writer.writerows(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    except OSError as error:
        _fail("PREDICTION_OUTPUT_ERROR", f"cannot atomically write predictions: {error}")
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    return PredictionResult(
        status="WRITTEN",
        output_path=destination.resolve(),
        n_input=len(raw_rows),
        n_ok=n_ok,
        n_out_of_domain=n_out_of_domain,
        n_invalid_x=n_invalid_x,
    )
