"""Strict CSV adapter and immutable logical observation set."""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from typing import Final


MAX_INPUT_BYTES: Final = 32 * 1024 * 1024
MODEL_COLUMNS: Final = frozenset({"weight", "weights", "uncertainty", "sigma"})


class DataContractError(ValueError):
    """A file-level error that prevents row interpretation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Observation:
    source_row: int
    row_id: str
    external_row_id: str | None
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class ExcludedRow:
    source_row: int
    row_id: str
    external_row_id: str | None
    raw_x: str
    raw_y: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObservationSet:
    source_path: Path
    input_sha256: str
    observations: tuple[Observation, ...]
    exclusions: tuple[ExcludedRow, ...]
    warning_codes: tuple[str, ...]
    extra_columns: tuple[str, ...]

    @property
    def n_input(self) -> int:
        return len(self.observations) + len(self.exclusions)

    @property
    def n_used(self) -> int:
        return len(self.observations)

    @property
    def n_skipped(self) -> int:
        return len(self.exclusions)

    @property
    def n_unique_x(self) -> int:
        return len({row.x for row in self.observations})

    @property
    def fit_readiness(self) -> str:
        if self.n_used == 0:
            return "NO_VALID_ROWS"
        if self.n_unique_x == 1:
            return "CONSTANT_X"
        if self.n_used < 4 or self.n_unique_x < 3:
            return "INSUFFICIENT_DATA_FOR_P1"
        return "READY"


@dataclass(frozen=True, slots=True)
class _RawRow:
    source_row: int
    values: tuple[str, ...]


def _finite_number(raw: str, field: str) -> tuple[float | None, str | None]:
    stripped = raw.strip()
    if not stripped:
        return None, f"MISSING_{field}"
    try:
        value = float(stripped)
    except ValueError:
        return None, f"INVALID_{field}"
    if not math.isfinite(value):
        return None, f"NONFINITE_{field}"
    if value == 0.0:
        value = 0.0  # canonicalize negative zero
    return value, None


def read_xy_csv(path: str | Path) -> ObservationSet:
    """Read one UTF-8 comma-separated x,y file without aggregating observations."""

    source = Path(path)
    try:
        info = source.stat()
    except OSError as error:
        raise DataContractError("INPUT_NOT_READABLE", f"cannot read input: {source}") from error
    if not source.is_file():
        raise DataContractError("INPUT_NOT_REGULAR", f"input is not a regular file: {source}")
    if info.st_size > MAX_INPUT_BYTES:
        raise DataContractError("INPUT_TOO_LARGE", f"input exceeds {MAX_INPUT_BYTES} bytes")
    try:
        payload = source.read_bytes()
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DataContractError("INVALID_UTF8", "input must be UTF-8") from error

    try:
        rows = list(csv.reader(text.splitlines(keepends=True), dialect="excel", strict=True))
    except csv.Error as error:
        raise DataContractError("MALFORMED_CSV", f"malformed CSV: {error}") from error
    if not rows:
        raise DataContractError("SCHEMA_ERROR", "CSV header is missing")

    header = rows[0]
    if header and header[0].startswith("\ufeff"):
        header[0] = header[0].removeprefix("\ufeff")
    if len(header) != len(set(header)) or header.count("x") != 1 or header.count("y") != 1:
        raise DataContractError("SCHEMA_ERROR", "headers x and y must occur exactly once")
    unsupported = sorted(set(header) & MODEL_COLUMNS)
    if unsupported:
        raise DataContractError(
            "UNSUPPORTED_MODEL_COLUMN",
            f"unsupported model columns: {', '.join(unsupported)}",
        )
    row_id_index = header.index("row_id") if "row_id" in header else None
    x_index = header.index("x")
    y_index = header.index("y")
    raw_rows: list[_RawRow] = []
    for source_row, values in enumerate(rows[1:], start=1):
        if not values or all(value == "" for value in values):
            continue
        if len(values) != len(header):
            raise DataContractError(
                "CSV_ROW_WIDTH_ERROR",
                f"row {source_row} has {len(values)} fields; expected {len(header)}",
            )
        raw_rows.append(_RawRow(source_row, tuple(values)))

    external_ids = [
        row.values[row_id_index]
        for row in raw_rows
        if row_id_index is not None and row.values[row_id_index] != ""
    ]
    id_counts = Counter(external_ids)
    observations: list[Observation] = []
    exclusions: list[ExcludedRow] = []
    warnings: set[str] = set()

    for row in raw_rows:
        external = row.values[row_id_index] if row_id_index is not None else None
        if external == "":
            external = None
        if external is not None and id_counts[external] == 1:
            canonical_id = external
        else:
            canonical_id = f"row-{row.source_row:06d}"
            if row_id_index is not None:
                warnings.add("ROW_ID_REPLACED")
        raw_x = row.values[x_index]
        raw_y = row.values[y_index]
        x, x_reason = _finite_number(raw_x, "X")
        y, y_reason = _finite_number(raw_y, "Y")
        reasons = tuple(reason for reason in (x_reason, y_reason) if reason is not None)
        if reasons:
            exclusions.append(
                ExcludedRow(
                    source_row=row.source_row,
                    row_id=canonical_id,
                    external_row_id=external,
                    raw_x=raw_x,
                    raw_y=raw_y,
                    reason_codes=reasons,
                )
            )
            warnings.add("INVALID_ROWS_SKIPPED")
        else:
            assert x is not None and y is not None
            observations.append(
                Observation(
                    source_row=row.source_row,
                    row_id=canonical_id,
                    external_row_id=external,
                    x=x,
                    y=y,
                )
            )

    pairs = Counter((row.x, row.y) for row in observations)
    if any(count > 1 for count in pairs.values()):
        warnings.add("EXACT_DUPLICATES_PRESENT")
    extra_columns = tuple(name for name in header if name not in {"x", "y", "row_id"})
    return ObservationSet(
        source_path=source.resolve(),
        input_sha256=hashlib.sha256(payload).hexdigest(),
        observations=tuple(observations),
        exclusions=tuple(exclusions),
        warning_codes=tuple(sorted(warnings)),
        extra_columns=extra_columns,
    )
