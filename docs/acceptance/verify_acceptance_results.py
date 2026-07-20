#!/usr/bin/env python3
"""Fail-closed verifier for authenticated implementation acceptance results."""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
from importlib import metadata
import json
import os
import re
import stat
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterator


MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_HASHED_FILE_BYTES = 1024 * 1024 * 1024
MAX_HASHED_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
MAX_HASHED_FILES = 8192
EXPECTED_DESIGN_MANIFEST_PATH = "docs/acceptance/acceptance-manifest-v1.json"
EXPECTED_DESIGN_MANIFEST_SHA256 = "4202b73297b0c92dde3a7f5a2490343d7b9db7e824485df09ca65902ecb1feb9"
REVIEW_AXES = ("statistical_model", "security_operations")
RELEASE_ADDITIONAL_PATHS = (
    ".scratch/monotone-curve-approximation/spec.md",
    "docs/specification/01-data-contract.md",
    "docs/specification/02-model-contract.md",
    "docs/specification/03-validation-uplift-contract.md",
    "docs/specification/04-residual-diagnostics-contract.md",
    "docs/specification/05-fitting-strategy-verdict.md",
    "docs/specification/06-delivery-surface-contract.md",
    "docs/specification/07-report-contract.md",
    "docs/specification/08-acceptance-handoff.md",
    "docs/specification/model.schema.json",
    "docs/specification/production-report.schema.json",
    "docs/specification/manifest.schema.json",
    "docs/specification/llm-start-advice.schema.json",
    "docs/specification/resolved-policy.schema.json",
    "docs/specification/registry.schema.json",
)
VERIFIER_RUNTIME_LOCK_PATH = "docs/acceptance/verifier-runtime/uv.lock"
EXPECTED_VERIFIER_PACKAGES = {
    "cryptography": "49.0.0",
    "jsonschema": "4.26.0",
}
PLATFORM_SLUGS = {
    "macOS 14 or newer, arm64, CPython 3.12": "macos-arm64-cpython312",
    "Ubuntu 24.04 LTS, x86_64, CPython 3.12": "ubuntu2404-x86_64-cpython312",
}
REGISTRY_FAMILIES = (
    "constant_v1",
    "poly1_v1",
    "poly2_v1",
    "poly3_v1",
    "exp_affine_v1",
    "log_shift_v1",
    "reciprocal_shift_pos_v1",
    "logistic_v1",
)


@dataclass
class ReadBudget:
    sizes: dict[str, int] = field(default_factory=dict)
    digests: dict[str, str] = field(default_factory=dict)
    stats: dict[str, tuple[int, int, int, int, int]] = field(default_factory=dict)
    total_bytes: int = 0

    def reserve(self, relative: str, size: int) -> None:
        if size < 0 or size > MAX_HASHED_FILE_BYTES:
            raise ValueError(f"{relative}: file exceeds the bounded verifier limit")
        prior = self.sizes.get(relative)
        if prior is not None:
            if prior != size:
                raise ValueError(f"{relative}: file size changed during verification")
            return
        if len(self.sizes) + 1 > MAX_HASHED_FILES:
            raise ValueError("referenced file count exceeds verifier limit")
        if self.total_bytes + size > MAX_HASHED_TOTAL_BYTES:
            raise ValueError("referenced byte total exceeds verifier limit")
        self.sizes[relative] = size
        self.total_bytes += size

    def record_digest(self, relative: str, digest: str) -> None:
        prior = self.digests.get(relative)
        if prior is not None and prior != digest:
            raise ValueError(f"{relative}: content changed during verification")
        self.digests[relative] = digest

    def record_stat(self, relative: str, info: os.stat_result) -> None:
        observed = stable_stat_tuple(info)
        prior = self.stats.get(relative)
        if prior is not None and prior != observed:
            raise ValueError(f"{relative}: file identity or metadata changed during verification")
        self.stats[relative] = observed


def duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def strict_json_bytes(payload: bytes, label: str) -> Any:
    if len(payload) > MAX_JSON_BYTES:
        raise ValueError(f"{label}: JSON exceeds {MAX_JSON_BYTES} bytes")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"{label}: UTF-8 BOM is forbidden")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label}: invalid UTF-8") from error

    def reject_constant(token: str) -> None:
        raise ValueError(f"{label}: non-finite JSON token {token}")

    decoder = json.JSONDecoder(object_pairs_hook=duplicate_keys, parse_constant=reject_constant)
    try:
        value, end = decoder.raw_decode(text)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"{label}: invalid or excessively nested JSON") from error
    if text[end:].strip():
        raise ValueError(f"{label}: trailing JSON data")
    return value


def relative_parts(relative: str) -> tuple[str, ...]:
    if not relative or relative.startswith("/") or "\\" in relative:
        raise ValueError(f"unsafe relative path: {relative!r}")
    parts = tuple(relative.split("/"))
    if any(part in {"", ".", ".."} or "\x00" in part for part in parts):
        raise ValueError(f"unsafe relative path: {relative!r}")
    return parts


def open_flags(directory: bool) -> int:
    if not hasattr(os, "O_NOFOLLOW") or (directory and not hasattr(os, "O_DIRECTORY")):
        raise ValueError("platform lacks required no-follow descriptor APIs")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    if directory:
        flags |= os.O_DIRECTORY
    return flags


@contextmanager
def open_relative_fd(root: Path, relative: str, *, directory: bool = False) -> Iterator[int]:
    """Open beneath root while refusing symlinks at every path component."""

    parts = relative_parts(relative)
    descriptors: list[int] = []
    try:
        current = os.open(root, open_flags(True))
        descriptors.append(current)
        for part in parts[:-1]:
            current = os.open(part, open_flags(True), dir_fd=current)
            descriptors.append(current)
        leaf = os.open(parts[-1], open_flags(directory), dir_fd=current)
        descriptors.append(leaf)
        info = os.fstat(leaf)
        expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not expected:
            kind = "directory" if directory else "regular file"
            raise ValueError(f"{relative}: not a non-symlink {kind}")
        yield leaf
    except OSError as error:
        raise ValueError(f"cannot safely open {relative!r}: {error.strerror}") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def stable_stat_tuple(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_relative_bytes(
    root: Path,
    relative: str,
    *,
    max_bytes: int,
    budget: ReadBudget,
) -> bytes:
    with open_relative_fd(root, relative) as descriptor:
        initial = os.fstat(descriptor)
        if initial.st_size > max_bytes:
            raise ValueError(f"{relative}: file exceeds {max_bytes} bytes")
        budget.reserve(relative, initial.st_size)
        payload = bytearray()
        while True:
            block = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - len(payload)))
            if not block:
                break
            payload.extend(block)
            if len(payload) > max_bytes:
                raise ValueError(f"{relative}: file exceeds {max_bytes} bytes")
        final = os.fstat(descriptor)
        if stable_stat_tuple(initial) != stable_stat_tuple(final) or len(payload) != final.st_size:
            raise ValueError(f"{relative}: file changed while being read")
        result = bytes(payload)
        budget.record_stat(relative, final)
        budget.record_digest(relative, hashlib.sha256(result).hexdigest())
        return result


def hash_relative(
    root: Path,
    relative: str,
    budget: ReadBudget,
    *,
    max_bytes: int = MAX_HASHED_FILE_BYTES,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    with open_relative_fd(root, relative) as descriptor:
        initial = os.fstat(descriptor)
        if initial.st_size > max_bytes:
            raise ValueError(f"{relative}: file exceeds {max_bytes} bytes")
        budget.reserve(relative, initial.st_size)
        observed = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            observed += len(block)
            if observed > max_bytes:
                raise ValueError(f"{relative}: file grew beyond {max_bytes} bytes")
            digest.update(block)
        final = os.fstat(descriptor)
        if stable_stat_tuple(initial) != stable_stat_tuple(final) or observed != final.st_size:
            raise ValueError(f"{relative}: file changed while being hashed")
    value = digest.hexdigest()
    budget.record_stat(relative, final)
    budget.record_digest(relative, value)
    return value, observed


def release_snapshot_digest(root: Path, budget: ReadBudget) -> tuple[str, int]:
    """Hash the exact frozen normative ledger before loading project schemas."""

    paths: set[str] = set(RELEASE_ADDITIONAL_PATHS)

    def recurse(descriptor: int, prefix: str) -> None:
        with os.scandir(descriptor) as entries:
            names = sorted((entry.name for entry in entries), key=lambda value: value.encode("utf-8"))
        for name in names:
            relative = f"{prefix}/{name}"
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise ValueError(f"normative release snapshot contains symlink: {relative}")
            if stat.S_ISDIR(info.st_mode):
                if name in {"evidence", "__pycache__"}:
                    continue
                child = os.open(name, open_flags(True), dir_fd=descriptor)
                try:
                    recurse(child, relative)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                if not name.endswith(".pyc"):
                    paths.add(relative)
            else:
                raise ValueError(f"normative release snapshot contains special file: {relative}")

    with open_relative_fd(root, "docs/acceptance", directory=True) as descriptor:
        recurse(descriptor, "docs/acceptance")
    ledger = bytearray()
    for relative in sorted(paths, key=lambda value: value.encode("utf-8")):
        digest, size = hash_relative(root, relative, budget)
        ledger.extend(f"{relative}\t{size}\t{digest}\n".encode("utf-8"))
    return hashlib.sha256(ledger).hexdigest(), len(paths)


def verifier_runtime_lock_digest(root: Path, budget: ReadBudget) -> str:
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12):
        raise ValueError("acceptance verifier requires CPython 3.12")
    if not (
        sys.flags.isolated
        and sys.flags.ignore_environment
        and sys.flags.no_user_site
        and sys.flags.safe_path
    ):
        raise ValueError("acceptance verifier must run with Python isolated mode (-I)")
    actual_prefix = Path(sys.prefix).resolve()
    try:
        actual_prefix.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("caller-owned verifier runtime must be outside the implementation project")
    if Path(sys.base_prefix).resolve() == actual_prefix:
        raise ValueError("acceptance verifier must run from a dedicated virtual environment")
    for package, expected in EXPECTED_VERIFIER_PACKAGES.items():
        try:
            actual = metadata.version(package)
        except metadata.PackageNotFoundError as error:
            raise ValueError(f"missing verifier runtime package: {package}") from error
        if actual != expected:
            raise ValueError(f"verifier runtime package mismatch: {package}=={actual}, expected {expected}")
    digest, _ = hash_relative(root, VERIFIER_RUNTIME_LOCK_PATH, budget, max_bytes=MAX_JSON_BYTES)
    return digest


def strict_load(root: Path, relative: str, budget: ReadBudget) -> Any:
    payload = read_relative_bytes(root, relative, max_bytes=MAX_JSON_BYTES, budget=budget)
    return strict_json_bytes(payload, relative)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def check_file_ref(
    root: Path,
    reference: dict[str, str],
    budget: ReadBudget,
    *,
    max_bytes: int = MAX_HASHED_FILE_BYTES,
) -> int:
    actual, size = hash_relative(root, reference["path"], budget, max_bytes=max_bytes)
    if actual != reference["sha256"]:
        raise ValueError(f"hash mismatch: {reference['path']}")
    return size


def load_checked_json(root: Path, reference: dict[str, str], budget: ReadBudget) -> Any:
    payload = read_relative_bytes(root, reference["path"], max_bytes=MAX_JSON_BYTES, budget=budget)
    actual = hashlib.sha256(payload).hexdigest()
    if actual != reference["sha256"]:
        raise ValueError(f"hash mismatch: {reference['path']}")
    return strict_json_bytes(payload, reference["path"])


def schema_validate(instance: Any, schema: Any, label: str) -> None:
    try:
        import jsonschema
    except ImportError as error:  # pragma: no cover
        raise ValueError("jsonschema dependency is required") from error

    def reject_remote_refs(value: Any) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and not reference.startswith("#"):
                raise ValueError(f"{label} schema contains a non-local $ref")
            for item in value.values():
                reject_remote_refs(item)
        elif isinstance(value, list):
            for item in value:
                reject_remote_refs(item)

    reject_remote_refs(schema)
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as error:
        raise ValueError(f"{label} schema itself is invalid: {error.message}") from error
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path)
        raise ValueError(f"{label} schema error at {location or '<root>'}: {first.message}")


def byte_sorted_unique(values: list[str], label: str) -> None:
    if values != sorted(values, key=lambda value: value.encode("utf-8")):
        raise ValueError(f"{label} is not bytewise sorted")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} contains duplicates")


def is_excluded(relative: str, prefixes: tuple[str, ...]) -> bool:
    return any(relative == prefix or relative.startswith(prefix + "/") for prefix in prefixes)


def scan_source_root(root: Path, source_root: str, policy: dict[str, Any]) -> set[str]:
    ignored_dirs = set(policy["ignored_directory_names"])
    ignored_files = set(policy["ignored_file_names"])
    ignored_suffixes = tuple(policy["ignored_suffixes"])
    excluded = tuple(policy["excluded_prefixes"])
    found: set[str] = set()

    def recurse(descriptor: int, prefix: str) -> None:
        with os.scandir(descriptor) as entries:
            ordered = sorted(list(entries), key=lambda entry: entry.name.encode("utf-8"))
        for entry in ordered:
            relative = f"{prefix}/{entry.name}"
            try:
                info = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
            except OSError as error:
                raise ValueError(f"cannot stat source path {relative}: {error.strerror}") from error
            if stat.S_ISLNK(info.st_mode):
                raise ValueError(f"source inventory contains symlink: {relative}")
            if is_excluded(relative, excluded):
                continue
            if stat.S_ISDIR(info.st_mode):
                if entry.name in ignored_dirs:
                    continue
                try:
                    child = os.open(entry.name, open_flags(True), dir_fd=descriptor)
                except OSError as error:
                    raise ValueError(f"cannot safely traverse source directory {relative}") from error
                try:
                    recurse(child, relative)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                if entry.name in ignored_files or entry.name.endswith(ignored_suffixes):
                    continue
                found.add(relative)
            else:
                raise ValueError(f"source inventory contains special file: {relative}")

    with open_relative_fd(root, source_root, directory=True) as descriptor:
        recurse(descriptor, source_root)
    return found


def enumerate_source_inventory(root: Path, policy: dict[str, Any]) -> list[str]:
    for key in (
        "root_files",
        "source_roots",
        "excluded_top_level_entries",
        "excluded_prefixes",
        "required_files",
        "ignored_directory_names",
        "ignored_file_names",
        "ignored_suffixes",
    ):
        byte_sorted_unique(policy[key], f"source policy {key}")
    top_files = {parts[0] for value in policy["root_files"] if len(parts := relative_parts(value)) == 1}
    top_roots = {relative_parts(value)[0] for value in policy["source_roots"]}
    excluded_top = set(policy["excluded_top_level_entries"])
    ignored_files = set(policy["ignored_file_names"])
    root_descriptor = os.open(root, open_flags(True))
    try:
        with os.scandir(root_descriptor) as entries:
            top_entries = list(entries)
        for entry in top_entries:
            info = os.stat(entry.name, dir_fd=root_descriptor, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError(f"unsafe top-level source entry: {entry.name}")
            if entry.name in ignored_files:
                continue
            if entry.name in top_files:
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError(f"top-level source file has wrong type: {entry.name}")
                continue
            if entry.name in top_roots:
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError(f"top-level source root has wrong type: {entry.name}")
                continue
            if entry.name in excluded_top:
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError(f"excluded top-level entry must be a directory: {entry.name}")
                continue
            raise ValueError(f"unlisted top-level entry is forbidden: {entry.name}")
    finally:
        os.close(root_descriptor)
    paths = set(policy["root_files"])
    for relative in policy["root_files"]:
        with open_relative_fd(root, relative):
            pass
    for source_root in policy["source_roots"]:
        paths.update(scan_source_root(root, source_root, policy))
    required = set(policy["required_files"])
    if not required <= paths:
        missing = sorted(required - paths, key=lambda value: value.encode("utf-8"))[0]
        raise ValueError(f"source inventory is missing required path: {missing}")
    return sorted(paths, key=lambda value: value.encode("utf-8"))


def validate_source_manifest(
    root: Path,
    reference: dict[str, str],
    design: dict[str, Any],
    budget: ReadBudget,
) -> dict[str, dict[str, Any]]:
    policy_ref = {
        "path": design["source_inventory_policy"]["path"],
        "sha256": design["source_inventory_policy"]["sha256"],
    }
    policy_schema_ref = {
        "path": design["source_inventory_policy"]["schema_path"],
        "sha256": design["source_inventory_policy"]["schema_sha256"],
    }
    policy = load_checked_json(root, policy_ref, budget)
    policy_schema = load_checked_json(root, policy_schema_ref, budget)
    schema_validate(policy, policy_schema, "source inventory policy")
    manifest = load_checked_json(root, reference, budget)
    manifest_schema = strict_load(root, "docs/acceptance/source-manifest.schema.json", budget)
    schema_validate(manifest, manifest_schema, "source manifest")
    if manifest["inventory_policy_sha256"] != policy_ref["sha256"]:
        raise ValueError("source manifest inventory policy hash mismatch")

    expected_paths = enumerate_source_inventory(root, policy)
    entries = manifest["files"]
    declared_paths = [entry["path"] for entry in entries]
    byte_sorted_unique(declared_paths, "source manifest paths")
    if declared_paths != expected_paths:
        missing = sorted(set(expected_paths) - set(declared_paths), key=lambda value: value.encode("utf-8"))
        extra = sorted(set(declared_paths) - set(expected_paths), key=lambda value: value.encode("utf-8"))
        raise ValueError(f"source manifest is not exact; missing={missing[:1]}, extra={extra[:1]}")
    if canonical_hash(entries) != manifest["source_tree_sha256"]:
        raise ValueError("source tree digest mismatch")

    limits = policy["limits"]
    if len(entries) > limits["source_files_max"]:
        raise ValueError("source file count exceeds source policy")
    source_total = 0
    index: dict[str, dict[str, Any]] = {}
    for entry in entries:
        actual_size = check_file_ref(
            root,
            {"path": entry["path"], "sha256": entry["sha256"]},
            budget,
            max_bytes=limits["source_file_bytes_max"],
        )
        if actual_size != entry["bytes"]:
            raise ValueError(f"source byte count mismatch: {entry['path']}")
        source_total += actual_size
        index[entry["path"]] = entry
    if source_total > limits["source_total_bytes_max"]:
        raise ValueError("source byte total exceeds source policy")
    if enumerate_source_inventory(root, policy) != expected_paths:
        raise ValueError("source inventory changed while being verified")
    for entry in entries:
        final_digest, final_size = hash_relative(
            root,
            entry["path"],
            budget,
            max_bytes=limits["source_file_bytes_max"],
        )
        if final_digest != entry["sha256"] or final_size != entry["bytes"]:
            raise ValueError(f"source file changed after initial verification: {entry['path']}")
    return index


def validate_source_archive(
    root: Path,
    reference: dict[str, str],
    source_index: dict[str, dict[str, Any]],
    policy: dict[str, Any],
    budget: ReadBudget,
) -> None:
    """Validate the single authoritative, content-addressed source snapshot."""

    check_file_ref(root, reference, budget, max_bytes=policy["limits"]["source_total_bytes_max"])
    expected_paths = list(source_index)
    with open_relative_fd(root, reference["path"]) as descriptor:
        initial = os.fstat(descriptor)
        with os.fdopen(os.dup(descriptor), "rb", closefd=True) as stream:
            try:
                with zipfile.ZipFile(stream, mode="r", allowZip64=True) as archive:
                    if archive.comment:
                        raise ValueError("source snapshot ZIP comment is forbidden")
                    members = archive.infolist()
                    names = [member.filename for member in members]
                    byte_sorted_unique(names, "source snapshot ZIP members")
                    if names != expected_paths:
                        missing = sorted(set(expected_paths) - set(names), key=lambda value: value.encode("utf-8"))
                        extra = sorted(set(names) - set(expected_paths), key=lambda value: value.encode("utf-8"))
                        raise ValueError(
                            f"source snapshot ZIP is not exact; missing={missing[:1]}, extra={extra[:1]}"
                        )
                    total = 0
                    for member in members:
                        entry = source_index[member.filename]
                        mode = member.external_attr >> 16
                        if (
                            member.create_system != 3
                            or not stat.S_ISREG(mode)
                            or stat.S_IMODE(mode) != 0o644
                            or member.is_dir()
                        ):
                            raise ValueError(f"non-canonical source ZIP member type/mode: {member.filename}")
                        if member.compress_type != zipfile.ZIP_STORED or member.compress_size != member.file_size:
                            raise ValueError(f"source ZIP member must be uncompressed: {member.filename}")
                        if member.flag_bits & ~0x800 or member.extra or member.comment:
                            raise ValueError(f"source ZIP member has forbidden metadata: {member.filename}")
                        if member.date_time != (1980, 1, 1, 0, 0, 0):
                            raise ValueError(f"source ZIP member timestamp is not canonical: {member.filename}")
                        if member.file_size != entry["bytes"]:
                            raise ValueError(f"source ZIP member size mismatch: {member.filename}")
                        total += member.file_size
                        if total > policy["limits"]["source_total_bytes_max"]:
                            raise ValueError("source ZIP uncompressed total exceeds source policy")
                        digest = hashlib.sha256()
                        observed = 0
                        with archive.open(member, mode="r") as member_stream:
                            while True:
                                block = member_stream.read(1024 * 1024)
                                if not block:
                                    break
                                observed += len(block)
                                if observed > entry["bytes"]:
                                    raise ValueError(f"source ZIP member grew beyond manifest: {member.filename}")
                                digest.update(block)
                        if observed != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
                            raise ValueError(f"source ZIP member hash mismatch: {member.filename}")
            except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as error:
                raise ValueError(f"invalid source snapshot ZIP: {error}") from error
        final = os.fstat(descriptor)
        if stable_stat_tuple(initial) != stable_stat_tuple(final):
            raise ValueError("source snapshot ZIP changed while being validated")
        budget.record_stat(reference["path"], final)


def tree_digest_relative(root: Path, relative: str, budget: ReadBudget) -> str:
    files: list[str] = []

    def recurse(descriptor: int, prefix: str) -> None:
        with os.scandir(descriptor) as entries:
            names = sorted((entry.name for entry in entries), key=lambda value: value.encode("utf-8"))
        for name in names:
            full = f"{prefix}/{name}"
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise ValueError(f"fixture tree contains symlink: {full}")
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, open_flags(True), dir_fd=descriptor)
                try:
                    recurse(child, full)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                files.append(full)
            else:
                raise ValueError(f"fixture tree contains special file: {full}")

    with open_relative_fd(root, relative, directory=True) as descriptor:
        recurse(descriptor, relative)
    ledger = bytearray()
    for full in sorted(files, key=lambda value: value.encode("utf-8")):
        digest, size = hash_relative(root, full, budget)
        inner = full[len(relative) + 1 :]
        ledger.extend(f"{inner}\t{size}\t{digest}\n".encode("utf-8"))
    return hashlib.sha256(ledger).hexdigest()


def verify_fixture_files(root: Path, fixture: dict[str, Any], budget: ReadBudget) -> None:
    files = fixture.get("files", [])
    hashes = fixture.get("sha256", [])
    if len(files) != len(hashes):
        raise ValueError(f"fixture file/hash cardinality mismatch: {fixture['id']}")
    for relative, expected in zip(files, hashes, strict=True):
        try:
            with open_relative_fd(root, relative, directory=True):
                pass
        except ValueError:
            actual, _ = hash_relative(root, relative, budget)
        else:
            actual = tree_digest_relative(root, relative, budget)
        if actual != expected:
            raise ValueError(f"fixture hash mismatch: {fixture['id']}:{relative}")


def validate_semantic_file(
    root: Path,
    reference: dict[str, str],
    semantic: dict[str, str],
    budget: ReadBudget,
    label: str,
) -> Any:
    if reference["path"] != semantic["path"]:
        raise ValueError(f"{label} path does not match source policy")
    schema_ref = {"path": semantic["schema_path"], "sha256": semantic["schema_sha256"]}
    schema = load_checked_json(root, schema_ref, budget)
    instance = load_checked_json(root, reference, budget)
    schema_validate(instance, schema, label)
    if instance.get(semantic["identity_field"]) != semantic["identity_value"]:
        raise ValueError(f"{label} semantic identity mismatch")
    return instance


def read_external_key(path: Path, project_root: Path) -> bytes:
    if not path.is_absolute():
        raise ValueError("reviewer public-key paths must be absolute")
    normalized = Path(os.path.abspath(path))
    try:
        normalized.relative_to(project_root)
    except ValueError:
        pass
    else:
        raise ValueError("reviewer trust roots must be outside the project tree")
    relative = normalized.as_posix().lstrip("/")
    external_budget = ReadBudget()
    return read_relative_bytes(Path("/"), relative, max_bytes=65536, budget=external_budget)


def load_ed25519_key(path: Path, project_root: Path):
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
    except ImportError as error:  # pragma: no cover
        raise ValueError("cryptography dependency is required for reviewer authentication") from error
    payload = read_external_key(path, project_root)
    try:
        key = Ed25519PublicKey.from_public_bytes(payload) if len(payload) == 32 else load_pem_public_key(payload)
    except Exception as error:
        raise ValueError(f"invalid Ed25519 public key: {path}") from error
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError(f"reviewer key is not Ed25519: {path}")
    raw = key.public_bytes_raw()
    return key, hashlib.sha256(raw).hexdigest()


def verify_review_signatures(
    root: Path,
    results: dict[str, Any],
    evidence_digest: str,
    release_digest: str,
    runtime_lock_digest: str,
    reviewer_public_keys: dict[str, Path],
    review_schema: dict[str, Any],
    budget: ReadBudget,
) -> list[str]:
    if set(reviewer_public_keys) != set(REVIEW_AXES):
        raise ValueError("exactly two ordered out-of-band reviewer keys are required")
    reviewer_ids: list[str] = []
    key_hashes: list[str] = []
    review_paths: list[str] = []
    for axis in REVIEW_AXES:
        reviewer = results["reviewers"][axis]
        key, key_hash = load_ed25519_key(reviewer_public_keys[axis], root)
        if reviewer["public_key_sha256"] != key_hash:
            raise ValueError(f"reviewer key fingerprint mismatch: {axis}")
        if reviewer["evidence_digest"] != evidence_digest:
            raise ValueError(f"reviewer digest mismatch: {axis}")
        if reviewer["release_snapshot_digest"] != release_digest:
            raise ValueError(f"reviewer release snapshot mismatch: {axis}")
        if reviewer["verifier_runtime_lock_sha256"] != runtime_lock_digest:
            raise ValueError(f"reviewer verifier-runtime lock mismatch: {axis}")
        review = load_checked_json(root, reviewer["review_evidence"], budget)
        schema_validate(review, review_schema, f"review evidence {axis}")
        payload = review["signed_payload"]
        expected = {
            "reviewer_id": reviewer["reviewer_id"],
            "axis": axis,
            "verdict": "GO",
            "evidence_digest": evidence_digest,
            "release_snapshot_digest": release_digest,
            "verifier_runtime_lock_sha256": runtime_lock_digest,
            "public_key_sha256": key_hash,
        }
        if payload != expected:
            raise ValueError(f"signed reviewer payload mismatch: {axis}")
        try:
            signature = base64.b64decode(review["signature"]["value_base64"], validate=True)
            key.verify(signature, canonical_bytes(payload))
        except Exception as error:
            raise ValueError(f"invalid reviewer signature: {axis}") from error
        reviewer_ids.append(reviewer["reviewer_id"])
        key_hashes.append(key_hash)
        review_paths.append(reviewer["review_evidence"]["path"])
    if len(set(reviewer_ids)) != 2:
        raise ValueError("reviewer IDs are not distinct")
    if len(set(key_hashes)) != 2:
        raise ValueError("reviewer public keys are not distinct")
    if len(set(review_paths)) != 2:
        raise ValueError("review evidence paths are not distinct")
    return key_hashes


def verify(
    results_path: Path,
    project_root: Path,
    reviewer_public_keys: dict[str, Path],
    trusted_release_digest: str,
) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    budget = ReadBudget()
    observed_release_digest, release_file_count = release_snapshot_digest(root, budget)
    if observed_release_digest != trusted_release_digest:
        raise ValueError("normative release snapshot differs from the caller-supplied trust anchor")
    runtime_lock_digest = verifier_runtime_lock_digest(root, budget)
    try:
        candidate = results_path if results_path.is_absolute() else root / results_path
        relative_results = Path(os.path.abspath(candidate)).relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError("results file is outside project root") from error

    results = strict_load(root, relative_results, budget)
    results_schema = strict_load(root, "docs/acceptance/acceptance-results.schema.json", budget)
    evidence_schema = strict_load(root, "docs/acceptance/acceptance-evidence.schema.json", budget)
    design_schema = strict_load(root, "docs/acceptance/acceptance-manifest.schema.json", budget)
    platform_run_schema = strict_load(root, "docs/acceptance/acceptance-platform-run.schema.json", budget)
    oracle_result_schema = strict_load(root, "docs/acceptance/acceptance-oracle-result.schema.json", budget)
    review_schema = strict_load(root, "docs/acceptance/acceptance-review.schema.json", budget)
    environment_schema = strict_load(root, "docs/acceptance/acceptance-environment.schema.json", budget)
    environment_probe_schema = strict_load(
        root,
        "docs/acceptance/acceptance-environment-probe.schema.json",
        budget,
    )
    schema_validate(results, results_schema, "acceptance results")
    if results["release_snapshot_digest"] != trusted_release_digest:
        raise ValueError("results do not bind the caller-supplied release snapshot digest")
    if results["verifier_runtime_lock_sha256"] != runtime_lock_digest:
        raise ValueError("results do not bind the frozen verifier-runtime lock")

    if results["design_manifest"] != {
        "path": EXPECTED_DESIGN_MANIFEST_PATH,
        "sha256": EXPECTED_DESIGN_MANIFEST_SHA256,
    }:
        raise ValueError("results do not reference the compiled frozen design manifest")

    design = load_checked_json(root, results["design_manifest"], budget)
    schema_validate(design, design_schema, "design manifest")
    if design.get("acceptance_id") != results["acceptance_id"]:
        raise ValueError("acceptance_id does not match design manifest")
    design_gates = design.get("gates", [])
    if len(design_gates) != 20 or any(gate.get("status") != "NOT_RUN" for gate in design_gates):
        raise ValueError("frozen design manifest must contain 20 NOT_RUN gates")
    if design["platform_policy"]["required"] != results["required_platforms"]:
        raise ValueError("required platforms do not match design manifest")

    gate_policy = load_checked_json(
        root,
        {"path": design["gate_execution_policy"]["path"], "sha256": design["gate_execution_policy"]["sha256"]},
        budget,
    )
    gate_policy_schema = load_checked_json(
        root,
        {"path": design["gate_execution_policy"]["schema_path"], "sha256": design["gate_execution_policy"]["schema_sha256"]},
        budget,
    )
    schema_validate(gate_policy, gate_policy_schema, "gate execution policy")
    if gate_policy["required_platforms"] != results["required_platforms"]:
        raise ValueError("gate execution platforms do not match results")
    if [item["gate_id"] for item in gate_policy["gates"]] != [item["id"] for item in design_gates]:
        raise ValueError("gate execution policy order/IDs do not match design gates")
    for design_gate, execution in zip(design_gates, gate_policy["gates"], strict=True):
        if execution["tolerance_policy"] != design_gate.get("tolerance_policy"):
            raise ValueError(f"gate tolerance policy mismatch: {design_gate['id']}")

    source_index = validate_source_manifest(root, results["source_manifest"], design, budget)
    source_manifest = load_checked_json(root, results["source_manifest"], budget)
    if source_manifest["source_archive_sha256"] != results["source_archive"]["sha256"]:
        raise ValueError("source manifest does not bind the authoritative source archive")
    for execution in gate_policy["gates"]:
        for target in execution["source_targets"]:
            if target not in source_index:
                raise ValueError(f"gate command target is absent from exact source manifest: {target}")
    for key in ("design_manifest", "dependency_lock", "policy", "registry"):
        reference = results[key]
        source_entry = source_index.get(reference["path"])
        if source_entry is None or source_entry["sha256"] != reference["sha256"]:
            raise ValueError(f"{key} is not bound to the exact source manifest")
    check_file_ref(root, results["dependency_lock"], budget)

    source_policy = load_checked_json(
        root,
        {
            "path": design["source_inventory_policy"]["path"],
            "sha256": design["source_inventory_policy"]["sha256"],
        },
        budget,
    )
    validate_source_archive(root, results["source_archive"], source_index, source_policy, budget)
    resolved_policy = validate_semantic_file(
        root,
        results["policy"],
        source_policy["semantic_files"]["resolved_policy"],
        budget,
        "resolved policy",
    )
    registry = validate_semantic_file(
        root,
        results["registry"],
        source_policy["semantic_files"]["registry"],
        budget,
        "registry",
    )
    if resolved_policy["registry_version"] != registry["registry_version"]:
        raise ValueError("policy and registry versions differ")
    family_ids = tuple(family["family_id"] for family in registry["families"])
    if family_ids != REGISTRY_FAMILIES:
        raise ValueError("registry family order/set differs from registry-v1")

    shared = {
        "release_snapshot_digest": trusted_release_digest,
        "verifier_runtime_lock_sha256": runtime_lock_digest,
        "design_manifest_sha256": results["design_manifest"]["sha256"],
        "source_manifest_sha256": results["source_manifest"]["sha256"],
        "source_archive_sha256": results["source_archive"]["sha256"],
        "dependency_lock_sha256": results["dependency_lock"]["sha256"],
        "policy_sha256": results["policy"]["sha256"],
        "registry_sha256": results["registry"]["sha256"],
    }
    fixture_by_id = {fixture["id"]: fixture for fixture in design["fixture_sets"]}
    if len(fixture_by_id) != len(design["fixture_sets"]):
        raise ValueError("design manifest contains duplicate fixture IDs")
    for fixture in design["fixture_sets"]:
        verify_fixture_files(root, fixture, budget)
    evidence_ledger: list[dict[str, str]] = []
    platform_environment_ids: dict[str, str] = {}
    platform_environment_refs: dict[str, dict[str, str]] = {}
    for design_gate, execution, gate in zip(design_gates, gate_policy["gates"], results["gates"], strict=True):
        expected_design_hash = canonical_hash(design_gate)
        expected = {
            "gate_id": design_gate["id"],
            "priority": design_gate["priority"],
            "design_gate_sha256": expected_design_hash,
            "evidence_path": design_gate["evidence_path"],
            "declared_executor": design_gate["command_or_executor"],
        }
        for key, value in expected.items():
            if gate[key] != value:
                raise ValueError(f"gate {gate['gate_id']} does not match design field {key}")

        evidence = load_checked_json(
            root,
            {"path": gate["evidence_path"], "sha256": gate["evidence_sha256"]},
            budget,
        )
        schema_validate(evidence, evidence_schema, gate["gate_id"])
        for key, value in shared.items():
            if evidence[key] != value:
                raise ValueError(f"gate {gate['gate_id']} mismatches {key}")
        if evidence["gate_id"] != gate["gate_id"] or evidence["design_gate_sha256"] != expected_design_hash:
            raise ValueError(f"gate evidence identity mismatch: {gate['gate_id']}")
        if evidence["declared_executor"] != gate["declared_executor"]:
            raise ValueError(f"gate executor mismatch: {gate['gate_id']}")

        fixture_records = [fixture_by_id[fixture_id] for fixture_id in design_gate["fixture_ids"]]
        fixture_ledger_sha256 = canonical_hash(fixture_records)
        if evidence["fixture_ids"] != design_gate["fixture_ids"]:
            raise ValueError(f"gate fixture IDs mismatch: {gate['gate_id']}")
        if evidence["fixture_ledger_sha256"] != fixture_ledger_sha256:
            raise ValueError(f"gate fixture ledger mismatch: {gate['gate_id']}")

        artifact_refs = evidence["artifact_hashes"]
        artifact_paths = [reference["path"] for reference in artifact_refs]
        if len(artifact_paths) != len(set(artifact_paths)):
            raise ValueError(f"duplicate artifact path in gate evidence: {gate['gate_id']}")
        artifact_map = {reference["path"]: reference["sha256"] for reference in artifact_refs}
        for artifact in artifact_refs:
            check_file_ref(root, artifact, budget)

        environment_ids: list[str] = []
        expected_test_ids = execution["test_ids"]
        for platform, platform_run in zip(results["required_platforms"], evidence["platform_runs"], strict=True):
            platform_slug = PLATFORM_SLUGS[platform]
            if platform_run["platform"] != platform:
                raise ValueError(f"gate platform mismatch: {gate['gate_id']}")
            if platform_run["declared_executor"] != gate["declared_executor"]:
                raise ValueError(f"platform executor mismatch: {gate['gate_id']}")
            if platform_run["execution_context"] != gate_policy["execution_context"]:
                raise ValueError(f"platform execution context mismatch: {gate['gate_id']}")
            if platform_run["argv"] != execution["argv"]:
                raise ValueError(f"platform argv mismatch: {gate['gate_id']}")
            if platform_run["source_targets"] != execution["source_targets"]:
                raise ValueError(f"platform source targets mismatch: {gate['gate_id']}")
            if platform_run["test_ids"] != expected_test_ids:
                raise ValueError(f"platform test IDs mismatch: {gate['gate_id']}")
            environment_ids.append(platform_run["environment_id"])
            prior_environment = platform_environment_ids.setdefault(platform, platform_run["environment_id"])
            if prior_environment != platform_run["environment_id"]:
                raise ValueError(f"platform environment changed between gates: {platform}")
            expected_prefix = f"evidence/acceptance/runs/{gate['gate_id']}/{platform_slug}"
            if platform_run["raw_result"]["path"] != f"{expected_prefix}/result.json":
                raise ValueError(f"platform result path mismatch: {gate['gate_id']}")
            if platform_run["log"]["path"] != f"{expected_prefix}/execution.log":
                raise ValueError(f"platform log path mismatch: {gate['gate_id']}")
            expected_environment_path = f"evidence/acceptance/platforms/{platform_slug}/environment.json"
            if platform_run["environment"]["path"] != expected_environment_path:
                raise ValueError(f"platform environment path mismatch: {gate['gate_id']}")
            for reference_key in ("environment", "raw_result", "log"):
                reference = platform_run[reference_key]
                if artifact_map.get(reference["path"]) != reference["sha256"]:
                    raise ValueError(f"platform artifact is not in gate ledger: {gate['gate_id']}")
            prior_environment_ref = platform_environment_refs.setdefault(platform, platform_run["environment"])
            if prior_environment_ref != platform_run["environment"]:
                raise ValueError(f"platform environment artifact changed between gates: {platform}")
            environment = load_checked_json(root, platform_run["environment"], budget)
            schema_validate(environment, environment_schema, f"environment {platform}")
            if environment["environment_id"] != platform_run["environment_id"]:
                raise ValueError(f"platform environment ID mismatch: {gate['gate_id']}")
            if canonical_hash(environment["identity_payload"]) != environment["environment_id"]:
                raise ValueError(f"platform environment ID is not canonical: {gate['gate_id']}")
            if environment["raw_probe"]["path"] != f"evidence/acceptance/platforms/{platform_slug}/probe.json":
                raise ValueError(f"platform probe path mismatch: {gate['gate_id']}")
            if artifact_map.get(environment["raw_probe"]["path"]) != environment["raw_probe"]["sha256"]:
                raise ValueError(f"platform probe is not in gate ledger: {gate['gate_id']}")
            probe = load_checked_json(root, environment["raw_probe"], budget)
            schema_validate(probe, environment_probe_schema, f"environment probe {platform}")
            package_names = [item["name"] for item in probe["packages"]]
            byte_sorted_unique(package_names, f"environment packages {platform}")
            byte_sorted_unique(probe["cpu_numerical_features"], f"CPU numerical features {platform}")
            packages = {item["name"]: item["version"] for item in probe["packages"]}
            if packages.get("scipy") != probe["solver_backend"]["version"]:
                raise ValueError(f"solver backend version is not bound to installed packages: {gate['gate_id']}")
            expected_identity = {
                key: probe[key]
                for key in (
                    "platform",
                    "os_name",
                    "distribution",
                    "distribution_version",
                    "architecture",
                    "python_implementation",
                    "python_version",
                    "python_build",
                    "python_compiler",
                    "python_executable_sha256",
                    "solver_backend",
                    "cpu_numerical_features",
                    "blas_lapack",
                    "thread_policy",
                    "floating_point_mode",
                )
            }
            expected_identity.update({
                "source_manifest_sha256": results["source_manifest"]["sha256"],
                "source_archive_sha256": results["source_archive"]["sha256"],
                "dependency_lock_sha256": results["dependency_lock"]["sha256"],
                "policy_sha256": results["policy"]["sha256"],
                "registry_sha256": results["registry"]["sha256"],
                "packages_sha256": canonical_hash(probe["packages"]),
            })
            if environment["identity_payload"] != expected_identity:
                raise ValueError(f"platform environment is not the canonical probe projection: {gate['gate_id']}")
            raw = load_checked_json(root, platform_run["raw_result"], budget)
            schema_validate(raw, platform_run_schema, f"platform result {gate['gate_id']}")
            expected_raw = {
                "schema_version": "acceptance-platform-run-v1",
                "gate_id": gate["gate_id"],
                "platform": platform,
                "environment_id": platform_run["environment_id"],
                "declared_executor": gate["declared_executor"],
                "execution_context": gate_policy["execution_context"],
                "argv": execution["argv"],
                "source_targets": execution["source_targets"],
                "test_results": [{"test_id": test_id, "status": "PASS"} for test_id in expected_test_ids],
                "exit_status": 0,
            }
            if raw != expected_raw:
                raise ValueError(f"platform raw result mismatch: {gate['gate_id']}")
        if len(set(environment_ids)) != 2:
            raise ValueError(f"platform environment IDs are not distinct: {gate['gate_id']}")

        oracle = evidence["oracle_results"][0]
        oracle_id = f"{gate['gate_id']}::oracle-v1"
        oracle_text_sha256 = hashlib.sha256(design_gate["oracle"].encode("utf-8")).hexdigest()
        if oracle != {
            "oracle_id": oracle_id,
            "oracle_text_sha256": oracle_text_sha256,
            "fixture_ledger_sha256": fixture_ledger_sha256,
            "tolerance_policy": design_gate.get("tolerance_policy"),
            "status": "PASS",
            "raw_result": oracle["raw_result"],
        }:
            raise ValueError(f"oracle binding mismatch: {gate['gate_id']}")
        if oracle["raw_result"]["path"] != f"evidence/acceptance/oracles/{gate['gate_id']}.json":
            raise ValueError(f"oracle result path mismatch: {gate['gate_id']}")
        if artifact_map.get(oracle["raw_result"]["path"]) != oracle["raw_result"]["sha256"]:
            raise ValueError(f"oracle result is not in gate artifact ledger: {gate['gate_id']}")
        raw_oracle = load_checked_json(root, oracle["raw_result"], budget)
        schema_validate(raw_oracle, oracle_result_schema, f"oracle result {gate['gate_id']}")
        for key, value in {
            "gate_id": gate["gate_id"],
            "oracle_id": oracle_id,
            "oracle_text_sha256": oracle_text_sha256,
            "fixture_ledger_sha256": fixture_ledger_sha256,
            "tolerance_policy": design_gate.get("tolerance_policy"),
            "status": "PASS",
        }.items():
            if raw_oracle[key] != value:
                raise ValueError(f"raw oracle mismatch for {key}: {gate['gate_id']}")
        for observation in raw_oracle["observations"]:
            if observation["claim_id"] != f"{gate['gate_id']}::oracle-claim-v1" or observation["status"] != "PASS":
                raise ValueError(f"oracle claim identity mismatch: {gate['gate_id']}")
            reference = observation["source_artifact"]
            if artifact_map.get(reference["path"]) != reference["sha256"]:
                raise ValueError(f"oracle observation source is not retained: {gate['gate_id']}")

        evidence_ledger.append({"gate_id": gate["gate_id"], "evidence_sha256": gate["evidence_sha256"]})

    final_source_paths = enumerate_source_inventory(root, source_policy)
    if final_source_paths != list(source_index):
        raise ValueError("source inventory changed before signature acceptance")
    for relative in final_source_paths:
        entry = source_index[relative]
        final_digest, final_size = hash_relative(
            root,
            relative,
            budget,
            max_bytes=source_policy["limits"]["source_file_bytes_max"],
        )
        if final_digest != entry["sha256"] or final_size != entry["bytes"]:
            raise ValueError(f"source file changed before signature acceptance: {relative}")

    evidence_preimage = {
        "acceptance_id": results["acceptance_id"],
        "release_snapshot_digest": trusted_release_digest,
        "verifier_runtime_lock_sha256": runtime_lock_digest,
        "dependency_lock_sha256": results["dependency_lock"]["sha256"],
        "design_manifest_sha256": results["design_manifest"]["sha256"],
        "gate_evidence": evidence_ledger,
        "policy_sha256": results["policy"]["sha256"],
        "registry_sha256": results["registry"]["sha256"],
        "source_inventory_policy_sha256": design["source_inventory_policy"]["sha256"],
        "source_manifest_sha256": results["source_manifest"]["sha256"],
        "source_archive_sha256": results["source_archive"]["sha256"],
    }
    evidence_digest = canonical_hash(evidence_preimage)
    if evidence_digest != results["reviewed_evidence_digest"]:
        raise ValueError("reviewed evidence digest mismatch")
    key_hashes = verify_review_signatures(
        root,
        results,
        evidence_digest,
        trusted_release_digest,
        runtime_lock_digest,
        reviewer_public_keys,
        review_schema,
        budget,
    )
    if enumerate_source_inventory(root, source_policy) != final_source_paths:
        raise ValueError("source inventory changed while signatures were verified")
    for relative in final_source_paths:
        entry = source_index[relative]
        digest_after_signatures, size_after_signatures = hash_relative(
            root,
            relative,
            budget,
            max_bytes=source_policy["limits"]["source_file_bytes_max"],
        )
        if digest_after_signatures != entry["sha256"] or size_after_signatures != entry["bytes"]:
            raise ValueError(f"source file changed while signatures were verified: {relative}")

    return {
        "status": "PASS",
        "acceptance_id": results["acceptance_id"],
        "gates": len(results["gates"]),
        "reviewed_evidence_digest": evidence_digest,
        "release_snapshot_digest": trusted_release_digest,
        "verifier_runtime_lock_sha256": runtime_lock_digest,
        "release_snapshot_files": release_file_count,
        "authenticated_reviewer_key_sha256": key_hashes,
        "bounded_files": len(budget.sizes),
        "bounded_bytes": budget.total_bytes,
    }


def parse_reviewer_keys(values: list[str]) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for value in values:
        axis, separator, path = value.partition("=")
        if not separator or axis not in REVIEW_AXES or not path or axis in parsed:
            raise ValueError("reviewer keys must be unique AXIS=/absolute/path pairs for both required axes")
        parsed[axis] = Path(path)
    if set(parsed) != set(REVIEW_AXES):
        raise ValueError("both statistical_model and security_operations reviewer keys are required")
    return {axis: parsed[axis] for axis in REVIEW_AXES}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--trusted-release-digest", required=True)
    parser.add_argument("--reviewer-public-key", action="append", default=[], metavar="AXIS=/ABSOLUTE/PATH")
    arguments = parser.parse_args(argv)
    try:
        if not re.fullmatch(r"[0-9a-f]{64}", arguments.trusted_release_digest):
            raise ValueError("--trusted-release-digest must be 64 lowercase hexadecimal characters")
        reviewer_keys = parse_reviewer_keys(arguments.reviewer_public_key)
        result = verify(
            arguments.results,
            arguments.project_root,
            reviewer_keys,
            arguments.trusted_release_digest,
        )
    except Exception as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
