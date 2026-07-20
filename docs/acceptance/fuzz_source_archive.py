#!/usr/bin/env python3
"""Read-only contract fuzzing for the canonical source ZIP verifier."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import importlib.util
import json
import stat
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path


VERIFIER_PATH = Path(__file__).with_name("verify_acceptance_results.py")
VERIFIER_SPEC = importlib.util.spec_from_file_location(
    "_monotone_acceptance_verifier",
    VERIFIER_PATH,
)
if VERIFIER_SPEC is None or VERIFIER_SPEC.loader is None:  # pragma: no cover - import contract
    raise RuntimeError(f"cannot load verifier module: {VERIFIER_PATH}")
VERIFIER_MODULE = importlib.util.module_from_spec(VERIFIER_SPEC)
sys.modules[VERIFIER_SPEC.name] = VERIFIER_MODULE
VERIFIER_SPEC.loader.exec_module(VERIFIER_MODULE)
ReadBudget = VERIFIER_MODULE.ReadBudget
validate_source_archive = VERIFIER_MODULE.validate_source_archive


CANONICAL_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
CANONICAL_MODE = stat.S_IFREG | 0o644
SOURCE_FILES = (
    ("alpha.txt", b"alpha source payload\n"),
    ("nested/beta.bin", b"\x00\x01beta source payload\xff"),
)
POLICY = {"limits": {"source_total_bytes_max": 1024 * 1024}}


@dataclass(frozen=True)
class Member:
    name: str
    payload: bytes
    compression: int = zipfile.ZIP_STORED
    mode: int = CANONICAL_MODE
    timestamp: tuple[int, int, int, int, int, int] = CANONICAL_TIMESTAMP
    extra: bytes = b""
    comment: bytes = b""


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def source_index(files: tuple[tuple[str, bytes], ...] = SOURCE_FILES) -> dict[str, dict[str, object]]:
    return {
        name: {"path": name, "bytes": len(payload), "sha256": sha256(payload)}
        for name, payload in files
    }


def canonical_members(files: tuple[tuple[str, bytes], ...] = SOURCE_FILES) -> list[Member]:
    return [Member(name, payload) for name, payload in files]


def build_archive(
    path: Path,
    members: list[Member],
    *,
    archive_comment: bytes = b"",
) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Duplicate name:.*", category=UserWarning)
        with zipfile.ZipFile(path, mode="w", allowZip64=True) as archive:
            archive.comment = archive_comment
            for member in members:
                info = zipfile.ZipInfo(member.name, date_time=member.timestamp)
                info.create_system = 3
                info.external_attr = member.mode << 16
                info.compress_type = member.compression
                info.extra = member.extra
                info.comment = member.comment
                archive.writestr(info, member.payload)
    return {"path": path.relative_to(path.parents[1]).as_posix(), "sha256": sha256(path.read_bytes())}


def file_identity(path: Path) -> tuple[int, int, int, int, int, str]:
    info = path.stat()
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        sha256(path.read_bytes()),
    )


def validate_read_only(
    root: Path,
    reference: dict[str, str],
    index: dict[str, dict[str, object]],
) -> None:
    path = root / reference["path"]
    before = file_identity(path)
    validate_source_archive(root, reference, index, POLICY, ReadBudget())
    assert file_identity(path) == before, "successful validation mutated the source archive"


def expect_rejected(
    root: Path,
    reference: dict[str, str],
    index: dict[str, dict[str, object]],
) -> None:
    path = root / reference["path"]
    before = file_identity(path)
    try:
        validate_source_archive(root, reference, index, POLICY, ReadBudget())
    except ValueError:
        pass
    else:
        raise AssertionError(f"mutated archive was accepted: {reference['path']}")
    assert file_identity(path) == before, "rejected validation mutated the source archive"


def main() -> None:
    accepted = 0
    rejected = 0
    with tempfile.TemporaryDirectory(prefix="fuzz-source-archive-") as temporary:
        root = Path(temporary)
        archive_dir = root / "archives"
        canonical = canonical_members()
        index = source_index()

        baseline_path = archive_dir / "canonical.zip"
        baseline_ref = build_archive(baseline_path, canonical)
        validate_read_only(root, baseline_ref, index)
        accepted += 1
        baseline_bytes = baseline_path.read_bytes()
        baseline_hash = sha256(baseline_bytes)

        duplicate = [canonical[0], canonical[0], canonical[1]]
        expect_rejected(root, build_archive(archive_dir / "duplicate.zip", duplicate), index)
        rejected += 1

        expect_rejected(root, build_archive(archive_dir / "missing.zip", canonical[:1]), index)
        rejected += 1

        extra = [canonical[0], Member("extra.txt", b"extra"), canonical[1]]
        expect_rejected(root, build_archive(archive_dir / "extra.zip", extra), index)
        rejected += 1

        deflated = [replace(canonical[0], compression=zipfile.ZIP_DEFLATED), canonical[1]]
        expect_rejected(root, build_archive(archive_dir / "deflated.zip", deflated), index)
        rejected += 1

        bad_mode = [replace(canonical[0], mode=stat.S_IFREG | 0o600), canonical[1]]
        expect_rejected(root, build_archive(archive_dir / "bad-mode.zip", bad_mode), index)
        rejected += 1

        bad_timestamp = [replace(canonical[0], timestamp=(1980, 1, 2, 0, 0, 0)), canonical[1]]
        expect_rejected(root, build_archive(archive_dir / "timestamp.zip", bad_timestamp), index)
        rejected += 1

        expect_rejected(
            root,
            build_archive(archive_dir / "archive-comment.zip", canonical, archive_comment=b"comment"),
            index,
        )
        rejected += 1

        member_comment = [replace(canonical[0], comment=b"comment"), canonical[1]]
        expect_rejected(root, build_archive(archive_dir / "member-comment.zip", member_comment), index)
        rejected += 1

        member_extra = [replace(canonical[0], extra=b"\xfe\xca\x00\x00"), canonical[1]]
        expect_rejected(root, build_archive(archive_dir / "member-extra.zip", member_extra), index)
        rejected += 1

        wrong_size = source_index()
        wrong_size["alpha.txt"] = {**wrong_size["alpha.txt"], "bytes": wrong_size["alpha.txt"]["bytes"] + 1}
        expect_rejected(root, build_archive(archive_dir / "size-mismatch.zip", canonical), wrong_size)
        rejected += 1

        wrong_hash = source_index()
        wrong_hash["alpha.txt"] = {**wrong_hash["alpha.txt"], "sha256": "0" * 64}
        expect_rejected(root, build_archive(archive_dir / "hash-mismatch.zip", canonical), wrong_hash)
        rejected += 1

        twin_path = archive_dir / "canonical-twin.zip"
        twin_ref = build_archive(twin_path, canonical)
        assert twin_path.read_bytes() == baseline_bytes, "canonical ZIP construction is not reproducible"
        assert twin_ref["sha256"] == baseline_hash
        validate_read_only(root, twin_ref, index)
        accepted += 1

        changed_files = (
            ("alpha.txt", b"ALPHA source payload\n"),
            SOURCE_FILES[1],
        )
        changed_path = archive_dir / "content-change.zip"
        changed_ref = build_archive(changed_path, canonical_members(changed_files))
        assert changed_ref["sha256"] != baseline_hash, "content change did not change archive identity"
        validate_read_only(root, changed_ref, source_index(changed_files))
        accepted += 1

        stale_ref = {"path": changed_ref["path"], "sha256": baseline_hash}
        expect_rejected(root, stale_ref, source_index(changed_files))
        rejected += 1

    assert accepted == 3
    assert rejected == 12
    print(
        json.dumps(
            {
                "accepted_canonical_archives": accepted,
                "canonical_sha256": baseline_hash,
                "rejected_mutations": rejected,
                "status": "PASS",
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
