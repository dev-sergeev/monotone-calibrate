#!/usr/bin/env python3
"""Fail-closed audit for the throwaway offline calibration-report prototype.

Run ``build.py`` first, then run this file.  The audit deliberately
uses only the standard library and writes a machine-readable verdict next to
the prototype.  It is not a production report verifier.
"""

from __future__ import annotations

import csv
import copy
import gzip
import hashlib
import json
import math
import re
import statistics
import sys
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import quote, unquote_to_bytes, urlsplit
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
GENERATED = HERE / "generated"
RESULTS = HERE / "audit-results.json"
MATRIX = HERE / "fixtures" / "matrix.json"
CANONICAL_SCHEMA = HERE.parents[1] / "specification" / "report.schema.json"
IDENTIFIER_SAFE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-"

REQUIRED_GATES = (
    "RAW",
    "CMP",
    "GEOM",
    "METRIC",
    "WARN",
    "RESID",
    "A11Y-STATIC",
    "JSON",
    "FAIL",
    "UNC",
    "SECURITY-STATIC",
    "PROTOTYPE",
)

DECISION_GATE_SPECS = (
    ("PRACTICAL_REL_MSE_UPLIFT", 0.10, ">="),
    ("POSITIVE_REPETITION_SHARE", 0.90, ">="),
    ("SPLIT_SENSITIVITY_P10", 0.0, ">="),
    ("BOOTSTRAP_STABILITY_LOWER", 0.05, ">="),
    ("DELTA_RMSE_POSITIVE", 0.0, ">"),
    ("MAE_NO_HARM_RATIO", 1.02, "<="),
    ("P2_VALID_FIT_RATE", 0.90, ">="),
    ("P2_DIRECTION_FREQUENCY", 0.80, ">="),
    ("P2_DOMINANT_PAIR_FREQUENCY", 0.60, ">="),
    ("P2_BOUNDARY_WIDTH_FRACTION", 0.25, "<="),
    ("P2_EDGE_HIT_RATE", 0.20, "<="),
    ("P2_DEGENERACY_RATE", 0.10, "<="),
    ("P2_HARD_FAILURE_RATE", 0.10, "<="),
    ("P2_FULL_REFIT_CERTIFIED", "certified", "=="),
)

VOID_HTML_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}

URL_ATTRIBUTES = {"action", "formaction", "href", "poster", "src", "xlink:href"}
FORBIDDEN_HTML_TAGS = {"base", "embed", "form", "iframe", "object", "script"}
FORBIDDEN_SVG_TAGS = {"foreignobject", "iframe", "script"}


@dataclass
class Check:
    gate: str
    check_id: str
    passed: bool
    bundle: str | None
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


class Audit:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(
        self,
        gate: str,
        check_id: str,
        passed: bool,
        message: str,
        *,
        bundle: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> bool:
        if gate not in REQUIRED_GATES:
            raise ValueError(f"unknown gate: {gate}")
        self.checks.append(
            Check(
                gate=gate,
                check_id=check_id,
                passed=bool(passed),
                bundle=bundle,
                message=message,
                evidence=evidence or {},
            )
        )
        return bool(passed)

    def guarded(
        self,
        gate: str,
        check_id: str,
        description: str,
        function: Any,
        *,
        bundle: str | None = None,
    ) -> Any:
        try:
            value = function()
        except Exception as exc:  # fail closed and preserve the cause
            self.add(
                gate,
                check_id,
                False,
                f"{description}: {type(exc).__name__}: {exc}",
                bundle=bundle,
            )
            return None
        self.add(gate, check_id, True, description, bundle=bundle)
        return value

    def result(self, bundles: Iterable[str]) -> dict[str, Any]:
        bundle_names = sorted(set(bundles))
        gates: dict[str, Any] = {}
        for gate in REQUIRED_GATES:
            relevant = [check for check in self.checks if check.gate == gate]
            gates[gate] = {
                "status": "PASS" if relevant and all(item.passed for item in relevant) else "FAIL",
                "passed": sum(item.passed for item in relevant),
                "failed": sum(not item.passed for item in relevant),
                "check_count": len(relevant),
            }
        passed = bool(bundle_names) and all(item["status"] == "PASS" for item in gates.values())
        return {
            "audit_schema_version": "1.0.0",
            "prototype_only": True,
            "generated_root": "generated",
            "status": "PASS" if passed else "FAIL",
            "bundle_count": len(bundle_names),
            "bundles": bundle_names,
            "gates": gates,
            "checks": [asdict(check) for check in self.checks],
        }


@dataclass
class HtmlNode:
    tag: str
    attrs: dict[str, str]
    parent: "HtmlNode | None" = None
    children: list["HtmlNode"] = field(default_factory=list)
    chunks: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        pieces = list(self.chunks)
        for child in self.children:
            pieces.append(child.text)
        return " ".join(" ".join(pieces).split())

    def walk(self) -> Iterator["HtmlNode"]:
        yield self
        for child in self.children:
            yield from child.walk()


class HtmlDocument(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("#document", {})
        self.stack = [self.root]
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): value or "" for name, value in attrs}
        node = HtmlNode(tag.lower(), attributes, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.lower() not in VOID_HTML_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_HTML_TAGS:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == lowered:
                del self.stack[index:]
                return
        self.errors.append(f"unmatched closing tag </{tag}>")

    def handle_data(self, data: str) -> None:
        self.stack[-1].chunks.append(data)

    @property
    def nodes(self) -> list[HtmlNode]:
        return list(self.root.walk())[1:]

    def by_tag(self, tag: str) -> list[HtmlNode]:
        lowered = tag.lower()
        return [node for node in self.nodes if node.tag == lowered]


@dataclass
class Bundle:
    name: str
    path: Path
    report: dict[str, Any]
    html_text: str
    html: HtmlDocument


def load_matrix(audit: Audit) -> dict[str, dict[str, Any]]:
    raw = audit.guarded(
        "FAIL",
        "FAIL-MATRIX-001",
        "fixture matrix is strict JSON",
        lambda: strict_json(MATRIX),
    )
    if not isinstance(raw, dict) or not isinstance(raw.get("fixtures"), list):
        audit.add("FAIL", "FAIL-MATRIX-002", False, "fixture matrix must contain a fixtures array")
        return {}
    fixtures: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    malformed: list[int] = []
    for index, fixture in enumerate(raw["fixtures"]):
        if (
            not isinstance(fixture, dict)
            or not isinstance(fixture.get("id"), str)
            or not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", fixture["id"])
        ):
            malformed.append(index)
            continue
        fixture_id = fixture["id"]
        if fixture_id in fixtures:
            duplicates.append(fixture_id)
        fixtures[fixture_id] = fixture
    audit.add(
        "FAIL",
        "FAIL-MATRIX-002",
        bool(fixtures) and not duplicates and not malformed,
        "fixture IDs are unique and well formed",
        evidence={"fixture_ids": sorted(fixtures), "duplicates": duplicates, "malformed": malformed},
    )
    resolved_paths = [(GENERATED / fixture_id).resolve() for fixture_id in fixtures]
    path_errors = [
        fixture_id
        for fixture_id, resolved in zip(fixtures, resolved_paths)
        if resolved.parent != GENERATED.resolve()
    ]
    negative_examples = ["../escape", "/absolute", "a/b", "a..", "a__b"]
    negative_rejected = all(not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", value) for value in negative_examples)
    audit.add(
        "SECURITY-STATIC",
        "SECURITY-MATRIX-PATH-001",
        not malformed and not path_errors and negative_rejected,
        "fixture IDs are allowlisted path components whose resolved bundle path stays under generated/",
        evidence={"path_errors": path_errors, "negative_examples": negative_examples, "negative_rejected": negative_rejected},
    )
    required_coverages = raw.get("required_coverages", [])
    declared_coverages = [
        coverage
        for fixture in fixtures.values()
        for coverage in fixture.get("covers", [])
        if isinstance(coverage, str)
    ]
    invalid_cover_lists = sorted(
        fixture_id
        for fixture_id, fixture in fixtures.items()
        if not isinstance(fixture.get("covers"), list)
        or any(not isinstance(value, str) for value in fixture.get("covers", []))
    )
    required_strings = (
        [value for value in required_coverages if isinstance(value, str)]
        if isinstance(required_coverages, list)
        else []
    )
    required_set = set(required_strings)
    declared_set = set(declared_coverages)
    duplicate_required = (
        sorted({value for value in required_strings if required_strings.count(value) > 1})
        if isinstance(required_coverages, list)
        else []
    )
    audit.add(
        "FAIL",
        "FAIL-MATRIX-004",
        bool(required_set)
        and isinstance(required_coverages, list)
        and len(required_strings) == len(required_coverages)
        and not duplicate_required
        and not invalid_cover_lists
        and required_set == declared_set,
        "fixture matrix explicitly and exactly covers every report-contract state",
        evidence={
            "required": sorted(required_set),
            "declared": sorted(declared_set),
            "missing": sorted(required_set - declared_set),
            "undeclared": sorted(declared_set - required_set),
            "duplicate_required": duplicate_required,
            "invalid_cover_lists": invalid_cover_lists,
        },
    )
    return fixtures


def strict_json(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON token {value}")

    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle, parse_constant=reject_constant)
    for json_path, item in walk_json(value):
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"non-finite number at {json_path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_media_type(path: Path | str) -> str:
    name = str(path)
    if name.endswith(".html"):
        return "text/html"
    if name.endswith(".svg"):
        return "image/svg+xml"
    if name.endswith(".csv"):
        return "text/csv"
    if name.endswith(".jsonl.gz"):
        return "application/gzip"
    if name.endswith(".jsonl"):
        return "application/x-ndjson"
    if name.endswith(".json"):
        return "application/json"
    return "application/octet-stream"


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def walk_json(value: Any, path: str = "$") -> Iterator[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk_json(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_json(child, f"{path}[{index}]")


def json_get(value: Any, dotted_path: str, default: Any = None) -> Any:
    current = value
    for part in dotted_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def first_json(value: Any, paths: Iterable[str], default: Any = None) -> Any:
    sentinel = object()
    for path in paths:
        found = json_get(value, path, sentinel)
        if found is not sentinel:
            return found
    return default


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    opener = gzip.open if path.suffix == ".gz" else Path.open
    if path.suffix == ".gz":
        handle = opener(path, "rt", encoding="utf-8", newline="")
    else:
        handle = opener(path, "r", encoding="utf-8", newline="")
    with handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV header is missing")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("CSV header contains duplicate fields")
        return list(reader)


def parse_html(text: str) -> HtmlDocument:
    parser = HtmlDocument()
    parser.feed(text)
    parser.close()
    return parser


def local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1].lower()


def is_external_reference(value: str) -> bool:
    cleaned = value.strip()
    if not cleaned or cleaned.startswith("#"):
        return False
    split = urlsplit(cleaned)
    return bool(split.scheme or split.netloc or cleaned.startswith("//"))


def discover_bundles(audit: Audit) -> list[Path]:
    if not GENERATED.is_dir():
        audit.add("PROTOTYPE", "PROTOTYPE-DISCOVERY-001", False, "generated/ directory is missing")
        return []
    report_paths = sorted(GENERATED.glob("*/report.json"))
    audit.add(
        "PROTOTYPE",
        "PROTOTYPE-DISCOVERY-001",
        bool(report_paths),
        "at least one generated fixture bundle exists",
        evidence={"report_paths": [str(path.relative_to(HERE)) for path in report_paths]},
    )
    return [path.parent for path in report_paths]


def load_bundle(path: Path, audit: Audit) -> Bundle | None:
    name = path.name
    report = audit.guarded(
        "JSON",
        "JSON-PARSE-001",
        "report.json is strict RFC-8259 JSON with finite numbers",
        lambda: strict_json(path / "report.json"),
        bundle=name,
    )
    html_text = audit.guarded(
        "A11Y-STATIC",
        "A11Y-HTML-001",
        "report.html exists and is UTF-8",
        lambda: (path / "report.html").read_text(encoding="utf-8"),
        bundle=name,
    )
    if not isinstance(report, dict) or not isinstance(html_text, str):
        return None
    html = audit.guarded(
        "A11Y-STATIC",
        "A11Y-HTML-002",
        "report.html can be parsed",
        lambda: parse_html(html_text),
        bundle=name,
    )
    if not isinstance(html, HtmlDocument):
        return None
    return Bundle(name=name, path=path, report=report, html_text=html_text, html=html)


def audit_prototype_boundary(bundle: Bundle, audit: Audit) -> None:
    files = [path for path in bundle.path.rglob("*") if path.is_file()]
    forbidden = [
        str(path.relative_to(bundle.path))
        for path in files
        if path.name == "COMPLETE" or path.name == "recommended-model.json"
    ]
    audit.add(
        "PROTOTYPE",
        "PROTOTYPE-BOUNDARY-001",
        not forbidden,
        "prototype bundle contains neither COMPLETE nor recommended-model.json",
        bundle=bundle.name,
        evidence={"forbidden_files": forbidden},
    )

    report_marker = first_json(
        bundle.report,
        (
            "prototype_only",
            "prototype.prototype_only",
            "prototype.enabled",
            "meta.prototype_only",
        ),
    )
    visible = bundle.html.root.text.casefold()
    visible_marker = ("prototype" in visible or "прототип" in visible) and (
        "synthetic" in visible or "синтет" in visible
    )
    audit.add(
        "PROTOTYPE",
        "PROTOTYPE-BOUNDARY-002",
        report_marker is True and visible_marker,
        "JSON and visible HTML explicitly identify a synthetic prototype",
        bundle=bundle.name,
        evidence={"json_marker": report_marker, "visible_marker": visible_marker},
    )


def parse_csp(value: str) -> dict[str, set[str]]:
    directives: dict[str, set[str]] = {}
    for raw_directive in value.split(";"):
        tokens = raw_directive.strip().split()
        if tokens:
            directives[tokens[0].casefold()] = set(tokens[1:])
    return directives


def audit_html_security(bundle: Bundle, audit: Audit) -> None:
    nodes = bundle.html.nodes
    forbidden_tags = sorted({node.tag for node in nodes if node.tag in FORBIDDEN_HTML_TAGS})
    event_attributes = sorted(
        {
            f"{node.tag}[{name}]"
            for node in nodes
            for name in node.attrs
            if name.casefold().startswith("on")
        }
    )
    audit.add(
        "SECURITY-STATIC",
        "SECURITY-HTML-001",
        not forbidden_tags and not event_attributes,
        "HTML has no executable/form/embed tags or event handlers",
        bundle=bundle.name,
        evidence={"forbidden_tags": forbidden_tags, "event_attributes": event_attributes},
    )

    external: list[str] = []
    dangerous: list[str] = []
    for node in nodes:
        for name, value in node.attrs.items():
            lowered_name = name.casefold()
            lowered_value = value.strip().casefold()
            if lowered_name in URL_ATTRIBUTES:
                if lowered_value.startswith(("javascript:", "vbscript:")):
                    dangerous.append(f"{node.tag}[{name}]={value}")
                elif is_external_reference(value):
                    external.append(f"{node.tag}[{name}]={value}")
            if lowered_name == "style" and (
                "url(" in lowered_value or "@import" in lowered_value or "expression(" in lowered_value
            ):
                dangerous.append(f"{node.tag}[style]")
    css_text = "\n".join(node.text for node in nodes if node.tag == "style").casefold()
    if "@import" in css_text or "url(" in css_text or "expression(" in css_text:
        dangerous.append("style element")
    audit.add(
        "SECURITY-STATIC",
        "SECURITY-OFFLINE-002",
        not external and not dangerous,
        "HTML has no network, script URL, CSS import or external resource dependency",
        bundle=bundle.name,
        evidence={"external_references": external, "dangerous_references": dangerous},
    )

    csp_values = [
        node.attrs.get("content", "")
        for node in nodes
        if node.tag == "meta"
        and node.attrs.get("http-equiv", "").casefold() == "content-security-policy"
    ]
    directives = parse_csp(csp_values[0]) if len(csp_values) == 1 else {}
    required_none = {
        "default-src",
        "script-src",
        "connect-src",
        "object-src",
        "frame-src",
        "base-uri",
        "form-action",
        "font-src",
        "media-src",
    }
    bad_directives = sorted(
        directive
        for directive in required_none
        if directives.get(directive) != {"'none'"}
    )
    img_ok = directives.get("img-src", set()).issubset({"'self'", "data:"}) and bool(
        directives.get("img-src")
    )
    style_ok = directives.get("style-src", set()).issubset({"'self'", "'unsafe-inline'"}) and bool(
        directives.get("style-src")
    )
    audit.add(
        "SECURITY-STATIC",
        "SECURITY-CSP-003",
        len(csp_values) == 1 and not bad_directives and img_ok and style_ok,
        "one fail-closed CSP permits only local/data images and local/inline styles",
        bundle=bundle.name,
        evidence={
            "csp_count": len(csp_values),
            "bad_none_directives": bad_directives,
            "img_src": sorted(directives.get("img-src", set())),
            "style_src": sorted(directives.get("style-src", set())),
        },
    )


def audit_svg_security_and_accessibility(bundle: Bundle, audit: Audit) -> None:
    svg_paths = sorted(bundle.path.rglob("*.svg"))
    audit.add(
        "A11Y-STATIC",
        "A11Y-SVG-003",
        bool(svg_paths),
        "bundle contains SVG figures",
        bundle=bundle.name,
        evidence={"svg_files": [str(path.relative_to(bundle.path)) for path in svg_paths]},
    )
    for index, path in enumerate(svg_paths, start=1):
        relative = str(path.relative_to(bundle.path))
        try:
            raw = path.read_text(encoding="utf-8")
            if re.search(r"<!DOCTYPE|<!ENTITY", raw, flags=re.IGNORECASE):
                raise ValueError("DTD/entity declaration is forbidden")
            root = ET.fromstring(raw)
        except Exception as exc:
            audit.add(
                "SECURITY-STATIC",
                f"SECURITY-SVG-{index:03d}",
                False,
                f"{relative} is not safe parseable SVG: {type(exc).__name__}: {exc}",
                bundle=bundle.name,
            )
            audit.add(
                "A11Y-STATIC",
                f"A11Y-SVG-{index + 100:03d}",
                False,
                f"{relative} accessibility cannot be inspected",
                bundle=bundle.name,
            )
            continue

        forbidden: list[str] = []
        references: list[str] = []
        title_texts: list[str] = []
        desc_texts: list[str] = []
        for element in root.iter():
            tag = local_name(element.tag)
            if tag in FORBIDDEN_SVG_TAGS:
                forbidden.append(tag)
            if tag == "title" and "".join(element.itertext()).strip():
                title_texts.append("".join(element.itertext()).strip())
            if tag == "desc" and "".join(element.itertext()).strip():
                desc_texts.append("".join(element.itertext()).strip())
            for attribute, value in element.attrib.items():
                attr_name = local_name(attribute)
                if attr_name.startswith("on"):
                    forbidden.append(f"{tag}[{attr_name}]")
                if attr_name == "style" and re.search(
                    r"url\s*\(|@import|expression\s*\(", value, flags=re.IGNORECASE
                ):
                    forbidden.append(f"{tag}[style]")
                if attr_name == "href" and value and not value.startswith("#"):
                    references.append(value)
        audit.add(
            "SECURITY-STATIC",
            f"SECURITY-SVG-{index:03d}",
            not forbidden and not references,
            f"{relative} is inert and has no external references",
            bundle=bundle.name,
            evidence={"forbidden": forbidden, "references": references},
        )
        audit.add(
            "A11Y-STATIC",
            f"A11Y-SVG-{index + 100:03d}",
            bool(title_texts) and bool(desc_texts) and bool(root.attrib.get("viewBox")),
            f"{relative} has title, description and scalable viewBox",
            bundle=bundle.name,
            evidence={"titles": title_texts, "descriptions": desc_texts},
        )


def audit_html_accessibility(bundle: Bundle, audit: Audit) -> None:
    html_nodes = bundle.html.by_tag("html")
    language = html_nodes[0].attrs.get("lang", "") if len(html_nodes) == 1 else ""
    titles = [node.text for node in bundle.html.by_tag("title") if node.text]
    h1s = [node.text for node in bundle.html.by_tag("h1") if node.text]
    mains = bundle.html.by_tag("main")
    audit.add(
        "A11Y-STATIC",
        "A11Y-STRUCTURE-010",
        len(html_nodes) == 1 and language.casefold().startswith("ru") and len(titles) == 1 and len(h1s) == 1 and len(mains) == 1,
        "HTML declares Russian language and has one title, h1 and main landmark",
        bundle=bundle.name,
        evidence={"lang": language, "titles": titles, "h1s": h1s, "main_count": len(mains)},
    )

    bad_images = [
        node.attrs.get("src", "<missing src>")
        for node in bundle.html.by_tag("img")
        if not node.attrs.get("alt", "").strip()
    ]
    bad_figures = [index for index, node in enumerate(bundle.html.by_tag("figure")) if not any(
        child.tag == "figcaption" and child.text for child in node.walk()
    )]
    audit.add(
        "A11Y-STATIC",
        "A11Y-FIGURES-011",
        not bad_images and not bad_figures,
        "every image has alt text and every figure has a visible caption",
        bundle=bundle.name,
        evidence={"images_without_alt": bad_images, "figures_without_caption": bad_figures},
    )

    truth_errors: list[str] = []
    for image in bundle.html.by_tag("img"):
        src = image.attrs.get("src", "")
        alt = image.attrs.get("alt", "").casefold()
        figure_text = image.parent.text.casefold() if image.parent else ""
        if src.endswith("one-function.svg"):
            certified = json_get(bundle.report, "refit.p1.status") == "certified"
            if certified and "линии нет" in alt + figure_text:
                truth_errors.append("p1_certified_described_as_absent")
            if not certified and ("линии нет" not in alt or "линия отсутствует" not in figure_text):
                truth_errors.append("p1_failure_alt_or_caption")
        if src.endswith("two-segment.svg"):
            certified = json_get(bundle.report, "refit.p2.status") == "certified"
            if certified and "ветвей и границы нет" in alt + figure_text:
                truth_errors.append("p2_certified_described_as_absent")
            if not certified and ("ветвей и границы нет" not in alt or "ветви и c отсутствуют" not in figure_text):
                truth_errors.append("p2_failure_alt_or_caption")
    audit.add(
        "A11Y-STATIC",
        "A11Y-FIGURE-TRUTH-013",
        not truth_errors,
        "plot alt text and visible captions truthfully match certified versus line-free failure state",
        bundle=bundle.name,
        evidence={"errors": truth_errors},
    )

    bad_tables = []
    for index, table in enumerate(bundle.html.by_tag("table")):
        descendants = list(table.walk())
        if not any(node.tag == "caption" and node.text for node in descendants) or not any(
            node.tag == "th" and node.text for node in descendants
        ):
            bad_tables.append(index)
    positive_tabindex = [
        f"{node.tag}#{node.attrs.get('id', '')}"
        for node in bundle.html.nodes
        if node.attrs.get("tabindex", "").lstrip("+").isdigit()
        and int(node.attrs["tabindex"]) > 0
    ]
    audit.add(
        "A11Y-STATIC",
        "A11Y-TABLES-012",
        bool(bundle.html.by_tag("table")) and not bad_tables and not positive_tabindex,
        "tables have captions/headers and focus order is not overridden",
        bundle=bundle.name,
        evidence={"bad_tables": bad_tables, "positive_tabindex": positive_tabindex},
    )


def audit_json_null_metrics(bundle: Bundle, audit: Audit) -> None:
    null_metrics: list[str] = []
    missing_reasons: list[str] = []
    metric_tokens = ("r2", "r²", "rmse", "mae", "mse", "uplift", "metric")
    for path, value in walk_json(bundle.report):
        if path.startswith("$.provenance.hash_payloads"):
            continue
        if not isinstance(value, dict):
            continue
        for key, item in value.items():
            lowered = key.casefold().replace("_", "")
            if item is None and any(token.replace("_", "") in lowered for token in metric_tokens):
                metric_path = f"{path}.{key}"
                null_metrics.append(metric_path)
                reason = value.get(f"{key}_reason") or value.get("reason")
                status = value.get(f"{key}_status") or value.get("status")
                if not (isinstance(reason, str) and reason.strip() and status):
                    missing_reasons.append(metric_path)
            if key == "value" and item is None and any(
                token in path.casefold().replace("_", "") for token in metric_tokens
            ):
                null_metrics.append(f"{path}.value")
                if not (
                    isinstance(value.get("reason"), str)
                    and value["reason"].strip()
                    and value.get("status")
                ):
                    missing_reasons.append(f"{path}.value")
    audit.add(
        "JSON",
        "JSON-NULL-REASON-010",
        not missing_reasons,
        "every null metric has a nonempty reason and typed status",
        bundle=bundle.name,
        evidence={"null_metrics": sorted(set(null_metrics)), "missing_reasons": sorted(set(missing_reasons))},
    )


def warning_codes(report: dict[str, Any]) -> set[str]:
    warnings = report.get("warnings", [])
    return {
        item.get("code")
        for item in warnings
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }


def failure_codes(report: dict[str, Any]) -> set[str]:
    failures = report.get("failures", [])
    return {
        item.get("code")
        for item in failures
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }


def metric_value(report: dict[str, Any], procedure: str) -> Any:
    return json_get(report, f"validation.procedures.{procedure}.r2_oos.value")


def close_or_equal(actual: Any, expected: Any, tolerance: float = 1e-12) -> bool:
    if actual is None or expected is None:
        return actual is expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(float(actual), float(expected), rel_tol=0, abs_tol=tolerance)
    return actual == expected


def deep_close(actual: Any, expected: Any, tolerance: float = 1e-12) -> bool:
    if isinstance(actual, dict) or isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and isinstance(expected, dict)
            and set(actual) == set(expected)
            and all(deep_close(actual[key], expected[key], tolerance) for key in actual)
        )
    if isinstance(actual, list) or isinstance(expected, list):
        return (
            isinstance(actual, list)
            and isinstance(expected, list)
            and len(actual) == len(expected)
            and all(deep_close(left, right, tolerance) for left, right in zip(actual, expected))
        )
    return close_or_equal(actual, expected, tolerance)


def decode_export_id(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("id:"):
        raise ValueError(f"export identifier lacks id: prefix: {value!r}")
    return unquote_to_bytes(value[3:]).decode("utf-8", errors="strict")


def encode_export_id(value: str) -> str:
    return "id:" + quote(value, safe=IDENTIFIER_SAFE)


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def median_or_none(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def finite_sample_qn(values: list[float]) -> float | None:
    """Independent O(n²) Qn oracle for the small prototype fixtures.

    The order statistic and finite-sample factors follow the original Qn
    definition.  Production code may use a selection algorithm; the audit
    intentionally uses the simple pairwise construction.
    """

    n = len(values)
    if n < 2 or any(not math.isfinite(value) for value in values):
        return None
    distances = sorted(
        abs(values[left] - values[right])
        for left in range(n - 1)
        for right in range(left + 1, n)
    )
    h = n // 2 + 1
    k = h * (h - 1) // 2
    finite_factors = {
        2: 0.399,
        3: 0.994,
        4: 0.512,
        5: 0.844,
        6: 0.611,
        7: 0.857,
        8: 0.669,
        9: 0.872,
    }
    correction = finite_factors.get(
        n,
        n / (n + 1.4) if n % 2 else n / (n + 3.8),
    )
    return 2.2219 * correction * distances[k - 1]


def robust_scale_candidate(values: list[float]) -> tuple[float | None, str]:
    """Apply the normative Qn-first, MAD-only-if-Qn-impossible protocol."""

    qn = finite_sample_qn(values)
    if qn is not None:
        return qn, "qn_finite_sample"
    if not values:
        return None, "qn_finite_sample"
    center = statistics.median(values)
    return (
        1.4826 * statistics.median(abs(value - center) for value in values),
        "finite_sample_s_mad",
    )


def mean_or_none(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def spread_or_none(values: list[float]) -> float | None:
    return max(values) - min(values) if values else None


def gate_observed_values(report: dict[str, Any]) -> dict[str, Any]:
    p1_mae = reported_metric(report, "validation.procedures.p1.mae_oof")
    p2_mae = reported_metric(report, "validation.procedures.p2.mae_oof")
    bootstrap_interval = json_get(report, "validation.bootstrap_stability.interval")
    return {
        "PRACTICAL_REL_MSE_UPLIFT": reported_metric(report, "validation.uplift.rel_mse_uplift"),
        "POSITIVE_REPETITION_SHARE": reported_metric(report, "validation.uplift.positive_repetition_share"),
        "SPLIT_SENSITIVITY_P10": reported_metric(report, "validation.split_sensitivity.p10"),
        "BOOTSTRAP_STABILITY_LOWER": (
            bootstrap_interval[0]
            if isinstance(bootstrap_interval, list) and len(bootstrap_interval) == 2
            else None
        ),
        "DELTA_RMSE_POSITIVE": reported_metric(report, "validation.uplift.delta_rmse"),
        "MAE_NO_HARM_RATIO": (
            float(p2_mae) / float(p1_mae)
            if finite_number(p1_mae) and float(p1_mae) > 0 and finite_number(p2_mae)
            else None
        ),
        "P2_VALID_FIT_RATE": reported_metric(report, "validation.p2_stability.valid_fit_rate"),
        "P2_DIRECTION_FREQUENCY": reported_metric(report, "validation.p2_stability.direction_frequency"),
        "P2_DOMINANT_PAIR_FREQUENCY": reported_metric(
            report, "validation.p2_stability.dominant_family_pair_frequency"
        ),
        "P2_BOUNDARY_WIDTH_FRACTION": reported_metric(
            report, "validation.p2_stability.boundary_width_fraction"
        ),
        "P2_EDGE_HIT_RATE": reported_metric(report, "validation.p2_stability.edge_hit_rate"),
        "P2_DEGENERACY_RATE": reported_metric(report, "validation.p2_stability.degeneracy_rate"),
        "P2_HARD_FAILURE_RATE": reported_metric(report, "validation.p2_stability.hard_failure_rate"),
        "P2_FULL_REFIT_CERTIFIED": (
            json_get(report, "refit.p2.status")
            if json_get(report, "validation.repetitions", 0) > 0
            else None
        ),
    }


def gate_passes(observed: Any, threshold: Any, operator: str) -> bool:
    if observed is None:
        return False
    if operator == ">=":
        return finite_number(observed) and finite_number(threshold) and float(observed) >= float(threshold)
    if operator == ">":
        return finite_number(observed) and finite_number(threshold) and float(observed) > float(threshold)
    if operator == "<=":
        return finite_number(observed) and finite_number(threshold) and float(observed) <= float(threshold)
    if operator == "==":
        return observed == threshold
    raise ValueError(f"unsupported gate operator: {operator}")


def contract_state_errors(report: dict[str, Any]) -> list[str]:
    """Check recommendation/failure transitions encoded by the normative schema.

    This deliberately duplicates the decision-bearing conditional subset instead
    of trusting the producer or fixture matrix.  It also gives the mutation tests
    below a standard-library fail-closed oracle.
    """

    errors: list[str] = []
    role = json_get(report, "recommendation.role")
    decision_code = json_get(report, "decision.code")
    recommended = json_get(report, "decision.recommended_procedure")
    analysis_status = json_get(report, "status.analysis_status")
    recommendation_status = json_get(report, "status.recommendation_status")
    repetitions = json_get(report, "validation.repetitions")
    gates = json_get(report, "validation.decision_gates", [])
    statuses = [gate.get("status") for gate in gates if isinstance(gate, dict)]
    all_pass = len(statuses) == len(DECISION_GATE_SPECS) and all(status == "pass" for status in statuses)
    status_by_id = {
        gate.get("id"): gate.get("status")
        for gate in gates
        if isinstance(gate, dict)
    }
    if (
        json_get(report, "status.decision_reason") != decision_code
        or json_get(report, "validation.uplift.decision") != decision_code
    ):
        errors.append("decision_code_sync")

    expected_recommendation_status = {
        None: "NO_VALIDATED_RECOMMENDATION",
        "one": "ONE_RECOMMENDED",
        "two": "TWO_RECOMMENDED",
    }.get(role)
    if role not in {None, "one", "two"}:
        errors.append("recommendation.role")
    if recommended != role:
        errors.append("decision.recommended_procedure")
    if recommendation_status != expected_recommendation_status:
        errors.append("status.recommendation_status")
    if all_pass != (role == "two"):
        errors.append("decision_gates.all_pass_iff_p2")

    if role is None:
        if any(
            json_get(report, path) is not None
            for path in (
                "recommendation.model_preview_path",
                "recommendation.formula_display",
                "recommendation.domain",
                "recommendation.extrapolation",
                "recommendation.global_primary_r2_oos.value",
                "recommendation.global_primary_rmse_oof.value",
                "recommendation.global_primary_mae_oof.value",
            )
        ):
            errors.append("recommendation.null_payload")
    elif role == "one":
        if not isinstance(repetitions, int) or repetitions < 1:
            errors.append("one.repetitions")
        if json_get(report, "validation.procedures.p1.status") != "valid":
            errors.append("one.p1_validation")
        if json_get(report, "refit.p1.status") != "certified":
            errors.append("one.p1_refit")
        if all_pass:
            errors.append("one.requires_nonpass_gate")
        if json_get(report, "recommendation.model_preview_path") != "models/one-model-preview.json":
            errors.append("one.model_preview")
    elif role == "two":
        if not isinstance(repetitions, int) or repetitions < 1:
            errors.append("two.repetitions")
        if json_get(report, "validation.procedures.p1.status") != "valid":
            errors.append("two.p1_validation")
        if json_get(report, "validation.procedures.p2.status") not in {"valid", "fallback"}:
            errors.append("two.p2_validation")
        if json_get(report, "validation.procedures.p2.full_data_refit_status") != "certified":
            errors.append("two.p2_full_data_refit_status")
        fallback_rate = reported_metric(report, "validation.procedures.p2.fallback_rate")
        if not finite_number(fallback_rate) or not 0 <= float(fallback_rate) <= 0.10:
            errors.append("two.fallback_rate")
        if json_get(report, "refit.p1.status") != "certified" or json_get(report, "refit.p2.status") != "certified":
            errors.append("two.refits")
        if not all_pass:
            errors.append("two.requires_all_pass")
        if json_get(report, "recommendation.model_preview_path") != "models/two-model-preview.json":
            errors.append("two.model_preview")

    if analysis_status == "FAILED":
        failed_state = (
            role is None
            and repetitions == 0
            and json_get(report, "validation.procedures.p1.status") == "failed"
            and json_get(report, "validation.procedures.p2.status") == "failed"
            and json_get(report, "refit.p1.status") == "failed"
            and json_get(report, "refit.p2.status") == "unavailable"
        )
        if not failed_state:
            errors.append("failed_state")
    elif role is not None and analysis_status != "SUCCEEDED":
        errors.append("recommended_analysis_status")
    elif role is None and analysis_status not in {"FAILED", "NO_RECOMMENDATION"}:
        errors.append("no_recommendation_analysis_status")

    p2_refit_status = json_get(report, "refit.p2.status")
    p2_full_refit_status = json_get(report, "validation.procedures.p2.full_data_refit_status")
    expected_terminal_state = {
        "CLEAR_PRACTICAL_UPLIFT": ("two", "SUCCEEDED", "TWO_RECOMMENDED"),
        "NO_UPLIFT_OR_HARM": ("one", "SUCCEEDED", "ONE_RECOMMENDED"),
        "STATISTICAL_ONLY_SMALL": ("one", "SUCCEEDED", "ONE_RECOMMENDED"),
        "PRACTICALLY_PROMISING_UNCERTAIN": ("one", "SUCCEEDED", "ONE_RECOMMENDED"),
        "UNSTABLE_SELECTION": ("one", "SUCCEEDED", "ONE_RECOMMENDED"),
        "NO_VALID_TWO_SEGMENT": ("one", "SUCCEEDED", "ONE_RECOMMENDED"),
        "DESCRIPTIVE_ONLY": (None, "NO_RECOMMENDATION", "NO_VALIDATED_RECOMMENDATION"),
        "PIPELINE_FAILURE": (None, "FAILED", "NO_VALIDATED_RECOMMENDATION"),
    }.get(decision_code)
    if expected_terminal_state is None:
        errors.append("decision_code.unknown")
    elif (role, analysis_status, recommendation_status) != expected_terminal_state:
        errors.append("decision_code.terminal_state")

    if decision_code == "NO_UPLIFT_OR_HARM":
        if status_by_id.get("DELTA_RMSE_POSITIVE") != "fail" or p2_refit_status != "certified":
            errors.append("NO_UPLIFT_OR_HARM.conditions")
    elif decision_code == "STATISTICAL_ONLY_SMALL":
        if (
            status_by_id.get("PRACTICAL_REL_MSE_UPLIFT") != "fail"
            or status_by_id.get("DELTA_RMSE_POSITIVE") != "pass"
            or p2_refit_status != "certified"
        ):
            errors.append("STATISTICAL_ONLY_SMALL.conditions")
    elif decision_code == "PRACTICALLY_PROMISING_UNCERTAIN":
        required_pass = {
            "PRACTICAL_REL_MSE_UPLIFT", "POSITIVE_REPETITION_SHARE",
            "SPLIT_SENSITIVITY_P10", "DELTA_RMSE_POSITIVE", "MAE_NO_HARM_RATIO",
        }
        if (
            any(status_by_id.get(gate_id) != "pass" for gate_id in required_pass)
            or status_by_id.get("BOOTSTRAP_STABILITY_LOWER") != "unavailable"
            or json_get(report, "validation.bootstrap_stability.status") != "unavailable"
            or p2_refit_status != "certified"
        ):
            errors.append("PRACTICALLY_PROMISING_UNCERTAIN.conditions")
    elif decision_code == "UNSTABLE_SELECTION":
        practical_ids = [item[0] for item in DECISION_GATE_SPECS[:6]]
        stability_ids = [item[0] for item in DECISION_GATE_SPECS[6:13]]
        if (
            any(status_by_id.get(gate_id) != "pass" for gate_id in practical_ids)
            or not any(status_by_id.get(gate_id) in {"fail", "unavailable"} for gate_id in stability_ids)
            or p2_refit_status != "certified"
        ):
            errors.append("UNSTABLE_SELECTION.conditions")
    elif decision_code == "NO_VALID_TWO_SEGMENT":
        if (
            json_get(report, "validation.procedures.p2.status") not in {"fallback", "unavailable", "failed"}
            or p2_full_refit_status != "unavailable"
            or p2_refit_status != "unavailable"
            or status_by_id.get("P2_FULL_REFIT_CERTIFIED") != "fail"
        ):
            errors.append("NO_VALID_TWO_SEGMENT.conditions")
    elif decision_code == "CLEAR_PRACTICAL_UPLIFT":
        if (
            not all_pass
            or p2_refit_status != "certified"
            or p2_full_refit_status != "certified"
        ):
            errors.append("CLEAR_PRACTICAL_UPLIFT.conditions")
    elif decision_code == "DESCRIPTIVE_ONLY":
        if (
            repetitions != 0
            or json_get(report, "validation.procedures.p1.status") != "unavailable"
            or json_get(report, "validation.procedures.p2.status") != "unavailable"
        ):
            errors.append("DESCRIPTIVE_ONLY.conditions")
    elif decision_code == "PIPELINE_FAILURE":
        pipeline_failures = [
            item for item in json_get(report, "failures", [])
            if isinstance(item, dict)
            and item.get("severity") == "error"
            and item.get("recommendation_effect") == "no_recommendation"
        ]
        if not pipeline_failures:
            errors.append("PIPELINE_FAILURE.conditions")
    return errors


def audit_fixture_state(bundle: Bundle, expected: dict[str, Any], audit: Audit) -> None:
    expected_recommendation = expected.get("recommendation")
    actual_recommendation = json_get(bundle.report, "decision.recommended_procedure")
    actual_reason = json_get(bundle.report, "status.decision_reason")
    audit.add(
        "FAIL",
        "FAIL-STATE-010",
        bundle.report.get("report_id") == f"prototype-{bundle.name}"
        and actual_recommendation == expected_recommendation
        and actual_reason == expected.get("decision_reason"),
        "fixture identity, recommendation and decision reason match matrix",
        bundle=bundle.name,
        evidence={
            "expected_recommendation": expected_recommendation,
            "actual_recommendation": actual_recommendation,
            "expected_reason": expected.get("decision_reason"),
            "actual_reason": actual_reason,
            "report_id": bundle.report.get("report_id"),
        },
    )

    expected_one = expected.get("r2_one")
    expected_two = expected.get("r2_two")
    actual_one = metric_value(bundle.report, "p1")
    actual_two = metric_value(bundle.report, "p2")
    validation_unavailable = bool(expected.get("pipeline_failure")) or expected.get("profile") == "small"
    expected_two_value = (
        None if validation_unavailable else expected_one if expected.get("p2_failure") else expected_two
    )
    audit.add(
        "METRIC",
        "METRIC-FIXTURE-010",
        close_or_equal(actual_one, expected_one) and close_or_equal(actual_two, expected_two_value),
        "canonical pooled OOF R² values match fixture matrix",
        bundle=bundle.name,
        evidence={
            "expected_p1": expected_one,
            "actual_p1": actual_one,
            "expected_p2": expected_two_value,
            "actual_p2": actual_two,
        },
    )

    expected_failure_codes = {
        code for code in (expected.get("p2_failure"), expected.get("pipeline_failure")) if code
    }
    actual_failure_codes = failure_codes(bundle.report)
    audit.add(
        "FAIL",
        "FAIL-CODES-011",
        expected_failure_codes.issubset(actual_failure_codes)
        and (not expected_failure_codes or actual_failure_codes == expected_failure_codes),
        "typed failure codes match fixture expectations",
        bundle=bundle.name,
        evidence={"expected": sorted(expected_failure_codes), "actual": sorted(actual_failure_codes)},
    )

    recommendation_status = json_get(bundle.report, "status.recommendation_status")
    expected_status = (
        "TWO_RECOMMENDED"
        if expected_recommendation == "two"
        else "ONE_RECOMMENDED"
        if expected_recommendation == "one"
        else "NO_VALIDATED_RECOMMENDATION"
    )
    model_preview = json_get(bundle.report, "recommendation.model_preview_path")
    audit.add(
        "FAIL",
        "FAIL-RECOMMENDATION-012",
        recommendation_status == expected_status
        and ((model_preview is None) == (expected_recommendation is None)),
        "recommendation status and preview presence cannot imply a false recommendation",
        bundle=bundle.name,
        evidence={
            "expected_status": expected_status,
            "actual_status": recommendation_status,
            "model_preview_path": model_preview,
        },
    )


def audit_decision_contract(bundle: Bundle, audit: Audit) -> None:
    gates = json_get(bundle.report, "validation.decision_gates", [])
    observed_sources = gate_observed_values(bundle.report)
    errors: list[str] = []
    gate_evidence: list[dict[str, Any]] = []
    if not isinstance(gates, list) or len(gates) != len(DECISION_GATE_SPECS):
        errors.append(f"gate_count:{len(gates) if isinstance(gates, list) else 'not_array'}")
        gates = gates if isinstance(gates, list) else []
    for index, (gate_id, threshold, operator) in enumerate(DECISION_GATE_SPECS):
        if index >= len(gates) or not isinstance(gates[index], dict):
            errors.append(f"missing:{gate_id}")
            continue
        gate = gates[index]
        source_observed = observed_sources[gate_id]
        available = source_observed is not None
        passed = gate_passes(source_observed, threshold, operator) if available else False
        expected_status = "pass" if passed else "fail" if available else "unavailable"
        expected_reason = "THRESHOLD_MET" if passed else "THRESHOLD_NOT_MET" if available else None
        if gate.get("id") != gate_id:
            errors.append(f"{gate_id}.id")
        if gate.get("policy_version") != "prototype-policy-v1":
            errors.append(f"{gate_id}.policy_version")
        if not close_or_equal(gate.get("threshold"), threshold):
            errors.append(f"{gate_id}.threshold")
        if not close_or_equal(gate.get("observed"), source_observed):
            errors.append(f"{gate_id}.observed")
        if gate.get("status") != expected_status:
            errors.append(f"{gate_id}.status")
        reason = gate.get("reason")
        if expected_reason is not None and reason != expected_reason:
            errors.append(f"{gate_id}.reason")
        if expected_reason is None and not (isinstance(reason, str) and reason.strip()):
            errors.append(f"{gate_id}.unavailable_reason")
        gate_evidence.append(
            {
                "id": gate_id,
                "operator": operator,
                "threshold": threshold,
                "source_observed": source_observed,
                "expected_status": expected_status,
                "reported_status": gate.get("status"),
            }
        )
    audit.add(
        "CMP",
        "CMP-DECISION-GATES-020",
        not errors,
        "exactly 14 ordered gates use versioned thresholds, declared operators and independently sourced observations",
        bundle=bundle.name,
        evidence={"errors": errors, "gates": gate_evidence},
    )

    state_errors = contract_state_errors(bundle.report)
    audit.add(
        "FAIL",
        "FAIL-STATE-MACHINE-022",
        not state_errors,
        "recommendation, decision gates, validation/refit statuses and terminal failure states are coupled fail closed",
        bundle=bundle.name,
        evidence={"errors": state_errors},
    )

    repetitions = json_get(bundle.report, "validation.repetitions")
    n_used = json_get(bundle.report, "input.n_used")
    expected_appearances = repetitions * n_used if isinstance(repetitions, int) and isinstance(n_used, int) else None
    loss_errors: list[str] = []
    for procedure in ("p1", "p2"):
        block = json_get(bundle.report, f"validation.procedures.{procedure}", {})
        status = block.get("status") if isinstance(block, dict) else None
        appearances = block.get("oof_appearances") if isinstance(block, dict) else None
        successes = block.get("successful_appearances") if isinstance(block, dict) else None
        fallback_rate = reported_metric(bundle.report, f"validation.procedures.{procedure}.fallback_rate")
        losses = {
            name: reported_metric(bundle.report, f"validation.procedures.{procedure}.{name}")
            for name in ("mse_oof", "rmse_oof", "mae_oof")
        }
        if isinstance(repetitions, int) and repetitions > 0:
            if status not in {"valid", "fallback"}:
                loss_errors.append(f"{procedure}.status_with_appearances")
            if appearances != expected_appearances or successes != expected_appearances:
                loss_errors.append(f"{procedure}.appearance_denominator")
            if not all(finite_number(value) and float(value) >= 0 for value in losses.values()):
                loss_errors.append(f"{procedure}.finite_nonnegative_losses")
            elif not close_or_equal(float(losses["rmse_oof"]) ** 2, losses["mse_oof"]):
                loss_errors.append(f"{procedure}.rmse_squared")
            if not finite_number(fallback_rate) or not 0 <= float(fallback_rate) <= 1:
                loss_errors.append(f"{procedure}.fallback_rate")
        else:
            if appearances != 0 or successes != 0:
                loss_errors.append(f"{procedure}.zero_appearance_state")
            if any(value is not None for value in losses.values()) or fallback_rate is not None:
                loss_errors.append(f"{procedure}.losses_without_appearances")
    audit.add(
        "METRIC",
        "METRIC-LOSS-APPEARANCE-023",
        not loss_errors,
        "OOF loss domains, RMSE/MSE identity and procedure appearance denominators are valid in every state",
        bundle=bundle.name,
        evidence={"expected_appearances": expected_appearances, "errors": loss_errors},
    )


def audit_warning_semantics(bundle: Bundle, expected: dict[str, Any], audit: Audit) -> None:
    recommendation = expected.get("recommendation")
    expected_value = (
        expected.get("r2_two") if recommendation == "two" else expected.get("r2_one") if recommendation == "one" else None
    )
    should_warn = recommendation is not None and expected_value is not None and expected_value < 0.60
    codes = warning_codes(bundle.report)
    has_warning = "BELOW_PRODUCT_R2" in codes
    warning_records = [
        item
        for item in bundle.report.get("warnings", [])
        if isinstance(item, dict) and item.get("code") == "BELOW_PRODUCT_R2"
    ]
    scope_ok = all(
        item.get("scope") == "recommended.global_primary_r2_oos"
        and item.get("recommendation_effect") == "none"
        for item in warning_records
    )
    audit.add(
        "WARN",
        "WARN-THRESHOLD-010",
        has_warning == should_warn and scope_ok,
        "BELOW_PRODUCT_R2 uses only the unrounded recommended pooled OOF R² and never changes recommendation",
        bundle=bundle.name,
        evidence={
            "recommended_value": expected_value,
            "should_warn": should_warn,
            "warning_codes": sorted(codes),
            "records": warning_records,
        },
    )

    visible = bundle.html.root.text
    visible_warning = "BELOW_PRODUCT_R2" in visible and "0.60" in visible
    audit.add(
        "WARN",
        "WARN-VISIBLE-011",
        visible_warning == should_warn,
        "low-R² warning is visible as text exactly when required",
        bundle=bundle.name,
        evidence={"should_warn": should_warn, "visible_warning": visible_warning},
    )

    if bundle.name == "two_recommended_low_r2_05996":
        audit.add(
            "WARN",
            "WARN-05996-012",
            should_warn and has_warning and "0.5996" in visible,
            "0.5996 remains visibly below 0.60 after presentation formatting",
            bundle=bundle.name,
        )
    if bundle.name == "one_recommended_exact_06000":
        audit.add(
            "WARN",
            "WARN-06000-013",
            expected_value == 0.6 and not has_warning and not visible_warning,
            "exactly 0.6000 does not trigger BELOW_PRODUCT_R2",
            bundle=bundle.name,
        )


def audit_metric_scopes(bundle: Bundle, audit: Audit) -> None:
    errors: list[str] = []
    for procedure in ("p1", "p2"):
        metric_object = json_get(bundle.report, f"validation.procedures.{procedure}.r2_oos")
        if not isinstance(metric_object, dict) or metric_object.get("scope") != "oof" or not metric_object.get(
            "definition"
        ):
            errors.append(f"validation.{procedure}.r2_oos")
    for procedure in ("p1", "p2"):
        metric_object = json_get(bundle.report, f"refit.{procedure}.r2_fit_all")
        if not isinstance(metric_object, dict) or metric_object.get("scope") != "in_sample":
            errors.append(f"refit.{procedure}.r2_fit_all")
    audit.add(
        "METRIC",
        "METRIC-SCOPE-011",
        not errors,
        "pooled OOF metrics and descriptive refit metrics have distinct typed scopes",
        bundle=bundle.name,
        evidence={"scope_errors": errors},
    )

    validation = bundle.report.get("validation", {})
    procedures = validation.get("procedures", {}) if isinstance(validation, dict) else {}
    comparison_ok = (
        isinstance(procedures, dict)
        and set(procedures) == {"p1", "p2"}
        and validation.get("estimand")
        and validation.get("null_baseline")
        and isinstance(validation.get("uplift"), dict)
    )
    audit.add(
        "CMP",
        "CMP-CANONICAL-010",
        bool(comparison_ok),
        "canonical comparison contains P1/P2, shared estimand/null baseline and paired uplift",
        bundle=bundle.name,
    )


def numeric_csv(row: dict[str, str], field: str) -> float | None:
    value = row.get(field, "")
    return None if value == "" else float(value)


def reported_metric(report: dict[str, Any], path: str) -> Any:
    block = json_get(report, path)
    return block.get("value") if isinstance(block, dict) else None


def compute_r2(y: list[float], predictions: list[float], null_predictions: list[float]) -> float | None:
    denominator = sum((actual - null) ** 2 for actual, null in zip(y, null_predictions))
    if denominator == 0.0:
        return None
    numerator = sum((actual - predicted) ** 2 for actual, predicted in zip(y, predictions))
    return 1.0 - numerator / denominator


def compute_fit_r2(rows: list[dict[str, Any]], field: str, segment: str | None = None) -> float | None:
    selected = [row for row in rows if segment is None or row.get("segment_refit") == segment]
    if not selected or any(row.get(field) is None for row in selected):
        return None
    mean_y = sum(float(row["y"]) for row in selected) / len(selected)
    denominator = sum((float(row["y"]) - mean_y) ** 2 for row in selected)
    if denominator == 0.0:
        return None
    numerator = sum((float(row["y"]) - float(row[field])) ** 2 for row in selected)
    return 1.0 - numerator / denominator


def audit_statistical_reconciliation(bundle: Bundle, audit: Audit) -> None:
    appearances = read_csv_rows(bundle.path / "oof-appearances.csv")
    repetitions = json_get(bundle.report, "validation.repetitions")
    n_used = json_get(bundle.report, "input.n_used")
    expected_count = repetitions * n_used if isinstance(repetitions, int) and isinstance(n_used, int) else -1
    count_ok = len(appearances) == expected_count
    procedure_errors: list[str] = []
    computed: dict[str, dict[str, float | None]] = {}

    if appearances:
        y = [float(row["y"]) for row in appearances]
        null_predictions = [float(row["pred_null_oof"]) for row in appearances]
        null_sse = sum((actual - predicted) ** 2 for actual, predicted in zip(y, null_predictions))
        if not close_or_equal(reported_metric(bundle.report, "validation.pooled_null_sse"), null_sse):
            procedure_errors.append("pooled_null_sse")
        for procedure, prefix in (("p1", "one"), ("p2", "two")):
            predictions = [float(row[f"pred_{prefix}_oof"]) for row in appearances]
            residuals = [float(row[f"resid_{prefix}_oof"]) for row in appearances]
            if any(not math.isclose(residual, actual - prediction, rel_tol=0, abs_tol=1e-12) for residual, actual, prediction in zip(residuals, y, predictions)):
                procedure_errors.append(f"{procedure}.residual_identity")
            mse = sum(value * value for value in residuals) / len(residuals)
            rmse = math.sqrt(mse)
            mae = sum(abs(value) for value in residuals) / len(residuals)
            r2 = compute_r2(y, predictions, null_predictions)
            computed[procedure] = {"mse": mse, "rmse": rmse, "mae": mae, "r2": r2}
            for metric_name, expected in (("mse_oof", mse), ("rmse_oof", rmse), ("mae_oof", mae), ("r2_oos", r2)):
                actual = reported_metric(bundle.report, f"validation.procedures.{procedure}.{metric_name}")
                if not close_or_equal(actual, expected):
                    procedure_errors.append(f"{procedure}.{metric_name}")
            if json_get(bundle.report, f"validation.procedures.{procedure}.oof_appearances") != len(appearances):
                procedure_errors.append(f"{procedure}.oof_appearances")
    else:
        if expected_count != 0:
            procedure_errors.append("missing_appearances")
        for procedure in ("p1", "p2"):
            for metric_name in ("mse_oof", "rmse_oof", "mae_oof", "r2_oos", "fallback_rate"):
                if reported_metric(bundle.report, f"validation.procedures.{procedure}.{metric_name}") is not None:
                    procedure_errors.append(f"{procedure}.{metric_name}.must_be_null")

    audit.add(
        "METRIC",
        "METRIC-RECOMPUTE-020",
        count_ok and not procedure_errors,
        "pooled OOF R²/RMSE/MAE/MSE and null SSE independently recompute from long appearance export",
        bundle=bundle.name,
        evidence={"appearance_rows": len(appearances), "expected_rows": expected_count, "errors": procedure_errors, "computed": computed},
    )

    uplift_errors: list[str] = []
    if appearances:
        p1, p2 = computed["p1"], computed["p2"]
        expected_uplift = {
            "delta_mse": p1["mse"] - p2["mse"],
            "delta_rmse": p1["rmse"] - p2["rmse"],
            "delta_mae": p1["mae"] - p2["mae"],
            "delta_r2": None if p1["r2"] is None or p2["r2"] is None else p2["r2"] - p1["r2"],
            "rel_mse_uplift": None if not p1["mse"] else (p1["mse"] - p2["mse"]) / p1["mse"],
        }
    else:
        expected_uplift = {name: None for name in ("delta_mse", "delta_rmse", "delta_mae", "delta_r2", "rel_mse_uplift")}
    for name, expected in expected_uplift.items():
        if not close_or_equal(reported_metric(bundle.report, f"validation.uplift.{name}"), expected):
            uplift_errors.append(name)
    audit.add(
        "METRIC",
        "METRIC-UPLIFT-021",
        not uplift_errors,
        "all paired uplift measures independently reconcile with P1/P2 pooled errors",
        bundle=bundle.name,
        evidence={"expected": expected_uplift, "errors": uplift_errors},
    )

    fallback_errors: list[str] = []
    p2_status = json_get(bundle.report, "validation.procedures.p2.status")
    p2_attempt_status = json_get(bundle.report, "validation.procedures.p2.attempt_status")
    fallback_rate = reported_metric(bundle.report, "validation.procedures.p2.fallback_rate")
    fallback_rows = [row for row in appearances if row.get("fallback_code")]
    actual_fallback_rate = len(fallback_rows) / len(appearances) if appearances else None
    fallback_fit_ids = sorted({decode_export_id(row["outer_fold_id"]) for row in fallback_rows})
    all_fit_ids = sorted({decode_export_id(row["outer_fold_id"]) for row in appearances})
    fallback_reason_counts: dict[str, int] = {}
    for row in fallback_rows:
        fallback_reason_counts[row["fallback_code"]] = fallback_reason_counts.get(row["fallback_code"], 0) + 1
    if not close_or_equal(fallback_rate, actual_fallback_rate):
        fallback_errors.append("fallback_rate")
    if json_get(bundle.report, "validation.procedures.p2.fallback_appearances") != len(fallback_rows):
        fallback_errors.append("fallback_appearances")
    if json_get(bundle.report, "validation.procedures.p2.fallback_outer_fit_ids") != fallback_fit_ids:
        fallback_errors.append("fallback_outer_fit_ids")
    if not deep_close(json_get(bundle.report, "validation.procedures.p2.fallback_reason_counts"), fallback_reason_counts):
        fallback_errors.append("fallback_reason_counts")
    if any(
        row.get("p2_attempt_status") != ("failed_with_p1_fallback" if row.get("fallback_code") else "succeeded")
        for row in appearances
    ):
        fallback_errors.append("appearance_attempt_status")
    if any(row["pred_one_oof"] != row["pred_two_oof"] for row in fallback_rows):
        fallback_errors.append("fallback_predictions")
    if p2_status == "fallback":
        if not appearances or not fallback_rows:
            fallback_errors.append("fallback_denominator")
        expected_attempt = "failed_with_p1_fallback" if len(fallback_rows) == len(appearances) else "partial_p1_fallback"
        if p2_attempt_status != expected_attempt:
            fallback_errors.append("aggregate_attempt_status")
    elif p2_status == "valid":
        if (
            fallback_rows
            or fallback_rate != 0.0
            or p2_attempt_status != "succeeded"
            or json_get(bundle.report, "refit.p2.status") != "certified"
        ):
            fallback_errors.append("valid_transition")
    elif appearances:
        fallback_errors.append("unexpected_appearances_for_unavailable_or_failed")
    if json_get(bundle.report, "refit.p2.status") == "certified" and all_fit_ids:
        expected_valid_fit_rate = (len(all_fit_ids) - len(fallback_fit_ids)) / len(all_fit_ids)
        expected_hard_failure_rate = len(fallback_fit_ids) / len(all_fit_ids)
        if not close_or_equal(reported_metric(bundle.report, "validation.p2_stability.valid_fit_rate"), expected_valid_fit_rate):
            fallback_errors.append("valid_fit_rate")
        if not close_or_equal(reported_metric(bundle.report, "validation.p2_stability.hard_failure_rate"), expected_hard_failure_rate):
            fallback_errors.append("hard_failure_rate")
    audit.add(
        "FAIL",
        "FAIL-FALLBACK-020",
        not fallback_errors,
        "P2 deployable-procedure fallback is distinct from full-data P2 refit status",
        bundle=bundle.name,
        evidence={
            "p2_status": p2_status,
            "aggregate_attempt_status": p2_attempt_status,
            "fallback_rows": len(fallback_rows),
            "fallback_fit_ids": fallback_fit_ids,
            "appearance_rows": len(appearances),
            "errors": fallback_errors,
        },
    )

    observations = bundle.report.get("observations", [])
    refit_errors: list[str] = []
    expected_refit = {
        "p1_all": compute_fit_r2(observations, "pred_one_refit"),
        "p2_all": compute_fit_r2(observations, "pred_two_refit"),
        "p2_left": compute_fit_r2(observations, "pred_two_refit", "left"),
        "p2_right": compute_fit_r2(observations, "pred_two_refit", "right"),
    }
    actual_refit = {
        "p1_all": reported_metric(bundle.report, "refit.p1.r2_fit_all"),
        "p2_all": reported_metric(bundle.report, "refit.p2.r2_fit_all"),
        "p2_left": reported_metric(bundle.report, "refit.p2.left.r2_fit"),
        "p2_right": reported_metric(bundle.report, "refit.p2.right.r2_fit"),
    }
    for name in expected_refit:
        if not close_or_equal(actual_refit[name], expected_refit[name]):
            refit_errors.append(name)
    if json_get(bundle.report, "refit.p1.status") != "certified" and any(row.get("pred_one_refit") is not None for row in observations):
        refit_errors.append("failed_p1_predictions")
    if json_get(bundle.report, "refit.p2.status") != "certified":
        forbidden = ("formula_left", "formula_right", "boundary", "domain", "model_structure_hash", "model_instance_hash")
        if any(json_get(bundle.report, f"refit.p2.{name}") is not None for name in forbidden):
            refit_errors.append("unavailable_p2_fields")
        if any(row.get("pred_two_refit") is not None or row.get("segment_refit") is not None for row in observations):
            refit_errors.append("unavailable_p2_predictions")
    audit.add(
        "METRIC",
        "METRIC-REFIT-022",
        not refit_errors,
        "descriptive full-data and local R² independently recompute from refit predictions; failed refits expose no model state",
        bundle=bundle.name,
        evidence={"expected": expected_refit, "actual": actual_refit, "errors": refit_errors},
    )

    role = json_get(bundle.report, "recommendation.role")
    formula = json_get(bundle.report, "recommendation.formula_display")
    formula_ok = (
        formula is None and json_get(bundle.report, "recommendation.model_preview_path") is None
        if role is None
        else formula == json_get(bundle.report, "refit.p1.formula_display")
        if role == "one"
        else all(part and part in formula for part in (json_get(bundle.report, "refit.p2.formula_left"), json_get(bundle.report, "refit.p2.formula_right")))
    )
    audit.add(
        "FAIL",
        "FAIL-FORMULA-021",
        bool(formula_ok),
        "recommendation formula is the certified selected refit formula, or absent with no recommendation",
        bundle=bundle.name,
        evidence={"role": role, "formula": formula},
    )


def audit_uncertainty(bundle: Bundle, audit: Audit) -> None:
    block = json_get(bundle.report, "validation.bootstrap_stability")
    required = {
        "status", "label", "target", "level", "unit", "resampling_unit", "selection_scope", "percentile_method",
        "resample_export", "successful_resamples", "requested_resamples",
        "failed_resamples", "failure_reasons", "reason",
    }
    present = isinstance(block, dict) and required.issubset(block)
    rows = read_csv_rows(bundle.path / "bootstrap-resamples.csv") if (bundle.path / "bootstrap-resamples.csv").is_file() else []
    successes = [row for row in rows if row.get("status") == "success"]
    failures = [row for row in rows if row.get("status") == "failed"]
    appearances = read_csv_rows(bundle.path / "oof-appearances.csv")
    failure_reason_counts: dict[str, int] = {}
    for row in failures:
        code = row.get("failure_code", "")
        if code:
            failure_reason_counts[code] = failure_reason_counts.get(code, 0) + 1

    def percentile(values: list[float], probability: float) -> float:
        ordered = sorted(values)
        position = (len(ordered) - 1) * probability
        lower, upper = math.floor(position), math.ceil(position)
        if lower == upper:
            return ordered[lower]
        fraction = position - lower
        return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction

    counts_consistent = bool(present) and (
        block.get("target") == "full_pipeline_rel_mse_uplift"
        and block.get("level") == 0.90
        and block.get("unit") == "fraction"
        and block.get("selection_scope") == "full_pipeline"
        and len(rows) == block.get("requested_resamples")
        and len(successes) == block.get("successful_resamples")
        and len(failures) == block.get("failed_resamples")
        and len(successes) + len(failures) == len(rows)
        and failure_reason_counts == block.get("failure_reasons")
    )
    consistent = False
    if present and block["status"] == "available":
        interval = block.get("interval")
        values = [float(row["rel_mse_uplift"]) for row in successes if row.get("rel_mse_uplift")]
        expected_interval = [percentile(values, 0.05), percentile(values, 0.95)] if values else None
        consistent = (
            isinstance(interval, list)
            and len(interval) == 2
            and all(isinstance(value, (int, float)) and math.isfinite(value) for value in interval)
            and interval[0] <= interval[1]
            and block.get("successful_resamples", 0) > 0
            and block.get("reason") is None
            and "stability" in str(block.get("label", "")).casefold()
            and expected_interval is not None
            and all(close_or_equal(actual, expected) for actual, expected in zip(interval, expected_interval))
            and len(values) == len(successes)
            and all(not row.get("rel_mse_uplift") and row.get("failure_code") for row in failures)
        )
    elif present and block["status"] == "unavailable":
        consistent = (
            block.get("interval") is None
            and bool(block.get("reason"))
            and not successes
            and all(not row.get("rel_mse_uplift") and row.get("failure_code") for row in failures)
        )
    full_fallback = bool(appearances) and all(row.get("fallback_code") for row in appearances)
    fallback_expected: float | None = None
    fallback_semantics = True
    if full_fallback:
        p1_errors = [(float(row["y"]) - float(row["pred_one_oof"])) ** 2 for row in appearances]
        p2_errors = [(float(row["y"]) - float(row["pred_two_oof"])) ** 2 for row in appearances]
        mse1 = statistics.fmean(p1_errors)
        mse2 = statistics.fmean(p2_errors)
        if mse1 > 0:
            fallback_expected = (mse1 - mse2) / mse1
            exported_values = [float(row["rel_mse_uplift"]) for row in successes if row.get("rel_mse_uplift")]
            fallback_semantics = (
                block.get("status") == "available"
                and len(successes) == block.get("requested_resamples")
                and not failures
                and len(exported_values) == len(successes)
                and all(close_or_equal(value, fallback_expected) for value in exported_values)
                and isinstance(block.get("interval"), list)
                and len(block["interval"]) == 2
                and all(close_or_equal(value, fallback_expected) for value in block["interval"])
            )
    audit.add(
        "UNC",
        "UNC-SEMANTICS-010",
        present and counts_consistent and consistent and fallback_semantics,
        "uncertainty/stability state declares method scope, unit, success count and absence reason",
        bundle=bundle.name,
        evidence={
            "block": block if isinstance(block, dict) else None,
            "export_rows": len(rows),
            "success_rows": len(successes),
            "failure_rows": len(failures),
            "failure_reason_counts": failure_reason_counts,
            "counts_consistent": counts_consistent,
            "full_p2_fallback": full_fallback,
            "full_fallback_expected_rel_mse_uplift": fallback_expected,
            "full_fallback_semantics": fallback_semantics,
        },
    )


def audit_p2_stability_ledger(bundle: Bundle, audit: Audit) -> None:
    block = json_get(bundle.report, "validation.p2_stability", {})
    ledger = block.get("outer_fit_ledger", []) if isinstance(block, dict) else []
    rows = read_csv_rows(bundle.path / "p2-stability-outer-fits.csv")
    errors: list[str] = []
    try:
        csv_ids = [decode_export_id(row["outer_fit_id"]) for row in rows]
    except (KeyError, TypeError, ValueError, UnicodeDecodeError):
        csv_ids = []
        errors.append("csv_identifier_encoding")
    ledger_ids = [item.get("outer_fit_id") for item in ledger]
    if csv_ids != ledger_ids:
        errors.append("csv_ledger_ids")
    if len(rows) != len(ledger):
        errors.append("csv_ledger_count")

    valid = [item for item in ledger if item.get("status") == "valid"]
    fallback = [item for item in ledger if item.get("status") == "fallback"]
    total = len(ledger)

    def rate(count: int, denominator: int) -> float | None:
        return count / denominator if denominator else None

    family_counts: dict[str, int] = {}
    direction_counts: dict[str, int] = {}
    for item in valid:
        family_counts[str(item.get("family_pair"))] = family_counts.get(str(item.get("family_pair")), 0) + 1
        direction_counts[str(item.get("direction"))] = direction_counts.get(str(item.get("direction")), 0) + 1
    expected_family = max(family_counts, key=lambda key: (family_counts[key], key)) if family_counts else None
    breakpoints = [float(item["boundary"]) for item in valid]

    def percentile(values: list[float], probability: float) -> float:
        ordered = sorted(values)
        position = (len(ordered) - 1) * probability
        lower, upper = math.floor(position), math.ceil(position)
        if lower == upper:
            return ordered[lower]
        fraction = position - lower
        return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction

    central80 = [percentile(breakpoints, 0.10), percentile(breakpoints, 0.90)] if breakpoints else None
    domain = json_get(bundle.report, "input.domain", [0.0, 1.0])
    domain_span = float(domain[1]) - float(domain[0])
    collapse_count = sum(item.get("collapsed") is True for item in ledger)
    expected = {
        "valid_fit_rate": rate(len(valid), total),
        "dominant_family_pair_frequency": rate(max(family_counts.values()), len(valid)) if family_counts else None,
        "direction_frequency": rate(max(direction_counts.values()), len(valid)) if direction_counts else None,
        "boundary_width_fraction": (central80[1] - central80[0]) / domain_span if central80 else None,
        "edge_hit_rate": rate(sum(item.get("edge_hit") is True for item in valid), len(valid)),
        "fallback_rate": rate(len(fallback), total),
        "flat_profile_rate": rate(sum(item.get("flat_profile") is True for item in valid), total) if valid else None,
        "multiple_near_optima_rate": rate(sum(item.get("multiple_near_optima") is True for item in valid), total) if valid else None,
        "collapse_rate": rate(collapse_count, total) if total and (valid or collapse_count) else None,
        "degeneracy_rate": rate(sum(
            item.get("flat_profile") is True
            or item.get("multiple_near_optima") is True
            or item.get("collapsed") is True
            for item in ledger
        ), total) if total and (valid or collapse_count) else None,
        "hard_failure_rate": rate(sum(bool(item.get("hard_failure")) for item in ledger), total),
    }
    if block.get("outer_fit_denominator") != total:
        errors.append("outer_fit_denominator")
    if block.get("valid_fit_count") != len(valid) or block.get("fallback_fit_count") != len(fallback):
        errors.append("outer_fit_counts")
    if block.get("dominant_family_pair") != expected_family:
        errors.append("dominant_family_pair")
    if not deep_close(block.get("breakpoint_distribution"), breakpoints):
        errors.append("breakpoint_distribution")
    if not deep_close(block.get("breakpoint_central80_interval"), central80):
        errors.append("breakpoint_central80_interval")
    for name, value in expected.items():
        if not close_or_equal(reported_metric(bundle.report, f"validation.p2_stability.{name}"), value):
            errors.append(name)
    audit.add(
        "UNC",
        "UNC-P2-STABILITY-011",
        not errors,
        "P2 direction/family/boundary/fallback/degeneracy frequencies independently recompute from the outer-fit ledger",
        bundle=bundle.name,
        evidence={"outer_fits": total, "valid": len(valid), "fallback": len(fallback), "expected": expected, "errors": errors},
    )


def audit_split_sensitivity(bundle: Bundle, audit: Audit) -> None:
    block = json_get(bundle.report, "validation.split_sensitivity", {})
    appearances = read_csv_rows(bundle.path / "oof-appearances.csv")
    by_repetition: dict[int, list[dict[str, str]]] = {}
    errors: list[str] = []
    for row in appearances:
        try:
            by_repetition.setdefault(int(row["repetition"]), []).append(row)
        except (KeyError, TypeError, ValueError):
            errors.append("appearance_repetition")
    expected_by_repetition: list[tuple[int, float | None]] = []
    for repetition in sorted(by_repetition):
        rows = by_repetition[repetition]
        p1_errors = [(float(row["y"]) - float(row["pred_one_oof"])) ** 2 for row in rows]
        p2_errors = [(float(row["y"]) - float(row["pred_two_oof"])) ** 2 for row in rows]
        mse1 = statistics.fmean(p1_errors)
        mse2 = statistics.fmean(p2_errors)
        expected_by_repetition.append((
            repetition,
            (mse1 - mse2) / mse1 if mse1 > 0 else None,
        ))
    expected_distribution = [value for _, value in expected_by_repetition if value is not None]
    undefined_effect_reason = (
        "ZERO_P1_MSE" if expected_by_repetition and not expected_distribution else None
    )
    reported_distribution = block.get("repetition_distribution", []) if isinstance(block, dict) else []
    expected_status = "available" if expected_distribution else "unavailable"
    if block.get("status") != expected_status:
        errors.append("split_status")
    if len(reported_distribution) != len(expected_by_repetition):
        errors.append("repetition_distribution_count")
    else:
        for reported, (repetition, expected) in zip(reported_distribution, expected_by_repetition):
            if reported.get("repetition") != repetition:
                errors.append("repetition_id")
            if expected is None:
                if (
                    reported.get("rel_mse_uplift") is not None
                    or reported.get("status") != "unavailable"
                    or (
                        reported.get("reason") != undefined_effect_reason
                        if undefined_effect_reason is not None
                        else not reported.get("reason")
                    )
                ):
                    errors.append("undefined_repetition_effect")
            elif (
                not close_or_equal(reported.get("rel_mse_uplift"), expected)
                or reported.get("status") != "defined"
                or reported.get("reason") is not None
            ):
                errors.append("defined_repetition_effect")

    expected_p10: float | None = None
    if expected_distribution:
        ordered = sorted(expected_distribution)
        position = (len(ordered) - 1) * 0.10
        lower, upper = math.floor(position), math.ceil(position)
        expected_p10 = (
            ordered[lower]
            if lower == upper
            else ordered[lower] * (1 - (position - lower)) + ordered[upper] * (position - lower)
        )
    if not close_or_equal(reported_metric(bundle.report, "validation.split_sensitivity.p10"), expected_p10):
        errors.append("p10")
    p10_metric = json_get(bundle.report, "validation.split_sensitivity.p10", {})
    if expected_p10 is None and not (
        p10_metric.get("value") is None
        and p10_metric.get("status") == "undefined"
        and p10_metric.get("reason")
        and (
            undefined_effect_reason is None
            or p10_metric.get("reason") == undefined_effect_reason
        )
    ):
        errors.append("p10_undefined_semantics")

    expected_positive_share = (
        sum(value > 0 for value in expected_distribution) / len(expected_distribution)
        if expected_distribution else None
    )
    if not close_or_equal(
        reported_metric(bundle.report, "validation.uplift.positive_repetition_share"),
        expected_positive_share,
    ):
        errors.append("positive_repetition_share")
    positive_metric = json_get(bundle.report, "validation.uplift.positive_repetition_share", {})
    if expected_positive_share is None and not (
        positive_metric.get("value") is None
        and positive_metric.get("status") == "undefined"
        and positive_metric.get("reason")
        and (
            undefined_effect_reason is None
            or positive_metric.get("reason") == undefined_effect_reason
        )
    ):
        errors.append("positive_share_undefined_semantics")
    positive_gate = next((
        item for item in json_get(bundle.report, "validation.decision_gates", [])
        if item.get("id") == "POSITIVE_REPETITION_SHARE"
    ), {})
    expected_gate_status = (
        "unavailable" if expected_positive_share is None
        else "pass" if expected_positive_share >= 0.90
        else "fail"
    )
    if (
        positive_gate.get("status") != expected_gate_status
        or not close_or_equal(positive_gate.get("observed"), expected_positive_share)
        or (
            undefined_effect_reason is not None
            and positive_gate.get("reason") != undefined_effect_reason
        )
    ):
        errors.append("positive_repetition_gate")

    expected_hashes = []
    for repetition in sorted(by_repetition):
        fit_ids = sorted({decode_export_id(row["outer_fold_id"]) for row in by_repetition[repetition]})
        expected_hashes.append({"repetition": repetition, "sha256": canonical_sha256(fit_ids)})
    if json_get(bundle.report, "validation.split_id_hashes", []) != expected_hashes:
        errors.append("split_id_hashes")
    audit.add(
        "UNC",
        "UNC-SPLIT-012",
        not errors,
        "repetition-level paired uplift and split-ID hashes independently recompute from long OOF appearances",
        bundle=bundle.name,
        evidence={
            "repetitions": len(by_repetition),
            "expected_distribution": [value for _, value in expected_by_repetition],
            "expected_p10": expected_p10,
            "expected_positive_repetition_share": expected_positive_share,
            "undefined_effect_reason": undefined_effect_reason,
            "errors": errors,
        },
    )


def audit_diagnostics(bundle: Bundle, audit: Audit) -> None:
    observations = bundle.report.get("observations", [])
    observation_by_id = {
        row.get("row_id"): row for row in observations if isinstance(row, dict) and isinstance(row.get("row_id"), str)
    }
    repetitions = json_get(bundle.report, "validation.repetitions", 0)
    role = json_get(bundle.report, "recommendation.role")
    diagnostic_procedure = json_get(bundle.report, "diagnostics.oof.procedure")
    threshold = json_get(bundle.report, "diagnostics.oof.threshold")
    appearance_rows = read_csv_rows(bundle.path / "diagnostic-appearances.csv")
    scale_rows = read_csv_rows(bundle.path / "diagnostic-scale-trace.csv")
    influence_rows = read_csv_rows(bundle.path / "influence-grid.csv")
    p2_fallback_fit_ids = set(json_get(bundle.report, "validation.procedures.p2.fallback_outer_fit_ids", []))
    p2_fallback_reason = json_get(bundle.report, "validation.procedures.p2.reason")

    appearance_errors: list[str] = []
    appearances_by_key: dict[tuple[str, str, int], dict[str, Any]] = {}
    appearance_ids: set[str] = set()
    expected_keys = {
        (row_id, procedure, repetition)
        for row_id in observation_by_id
        for procedure in ("one", "two")
        for repetition in range(1, repetitions + 1)
    }
    for index, row in enumerate(appearance_rows):
        try:
            row_id = decode_export_id(row["row_id"])
            group_id = decode_export_id(row["x_group_id"])
            outer_fold_id = decode_export_id(row["outer_fold_id"])
            appearance_id = decode_export_id(row["appearance_id"])
            procedure = row["procedure"]
            repetition = int(row["repetition"])
            key = (row_id, procedure, repetition)
            source = observation_by_id[row_id]
            if key in appearances_by_key:
                appearance_errors.append(f"duplicate_key:{key}")
            if appearance_id in appearance_ids:
                appearance_errors.append(f"duplicate_appearance_id:{appearance_id}")
            appearance_ids.add(appearance_id)
            if procedure not in {"one", "two"} or key not in expected_keys:
                appearance_errors.append(f"unexpected_key:{key}")
            if group_id != source.get("x_group_id"):
                appearance_errors.append(f"{index}.x_group_id")
            if row.get("action") != "review_only":
                appearance_errors.append(f"{index}.action")
            prefix = procedure
            expected_values = {
                "y": source.get("y"),
                "prediction_oof": source.get(f"pred_{prefix}_oof"),
                "residual_oof": source.get(f"resid_{prefix}_oof"),
                "robust_scale": source.get(f"scale_{prefix}_oof"),
                "z_oof": source.get(f"z_{prefix}_oof"),
            }
            parsed: dict[str, float | None] = {}
            for field_name, expected_value in expected_values.items():
                parsed[field_name] = numeric_csv(row, field_name)
                if not close_or_equal(parsed[field_name], expected_value):
                    appearance_errors.append(f"{index}.{field_name}")
            if parsed["prediction_oof"] is not None and not close_or_equal(
                parsed["residual_oof"], float(parsed["y"]) - float(parsed["prediction_oof"])
            ):
                appearance_errors.append(f"{index}.residual_identity")
            if parsed["robust_scale"] is None:
                if parsed["z_oof"] is not None:
                    appearance_errors.append(f"{index}.z_without_scale")
            elif float(parsed["robust_scale"]) <= 0 or not close_or_equal(
                parsed["z_oof"], float(parsed["residual_oof"]) / float(parsed["robust_scale"])
            ):
                appearance_errors.append(f"{index}.z_identity")
            is_fallback = procedure == "two" and outer_fold_id in p2_fallback_fit_ids
            expected_fallback_status = (
                "failed_with_p1_fallback" if is_fallback
                else "succeeded" if procedure == "two"
                else "not_applicable"
            )
            expected_fallback_code = p2_fallback_reason if is_fallback else None
            if row.get("fallback_status") != expected_fallback_status:
                appearance_errors.append(f"{index}.fallback_status")
            if (row.get("fallback_code") or None) != expected_fallback_code:
                appearance_errors.append(f"{index}.fallback_code")
            appearances_by_key[key] = {
                **parsed,
                "row_id": row_id,
                "x_group_id": group_id,
                "procedure": procedure,
                "repetition": repetition,
                "outer_fold_id": outer_fold_id,
            }
        except Exception as exc:
            appearance_errors.append(f"row:{index}:{type(exc).__name__}:{exc}")
    if set(appearances_by_key) != expected_keys:
        appearance_errors.append(
            f"cartesian_keys:missing={len(expected_keys - set(appearances_by_key))}:"
            f"extra={len(set(appearances_by_key) - expected_keys)}"
        )
    expected_prediction_count = len(observation_by_id) * repetitions
    if json_get(bundle.report, "diagnostics.oof.prediction_count") != expected_prediction_count:
        appearance_errors.append("diagnostics.oof.prediction_count")
    diagnostic_validation_status = (
        json_get(bundle.report, "validation.procedures.p1.status")
        if diagnostic_procedure == "one"
        else json_get(bundle.report, "validation.procedures.p2.status")
        if diagnostic_procedure == "two"
        else None
    )
    expected_oof_status = (
        "available"
        if repetitions > 0
        and diagnostic_procedure in {"one", "two"}
        and diagnostic_validation_status in {"valid", "fallback"}
        else "unavailable"
    )
    if diagnostic_procedure != role:
        appearance_errors.append("diagnostics.oof.procedure_vs_recommendation")
    if json_get(bundle.report, "diagnostics.oof.status") != expected_oof_status:
        appearance_errors.append("diagnostics.oof.status")
    audit.add(
        "RESID",
        "RESID-APPEARANCES-020",
        not appearance_errors,
        "diagnostic appearances are the exact row × procedure × repetition product with residual/z/fallback identities",
        bundle=bundle.name,
        evidence={
            "rows": len(appearance_rows),
            "expected_rows": len(expected_keys),
            "unique_appearance_ids": len(appearance_ids),
            "errors": appearance_errors,
        },
    )

    scale_errors: list[str] = []
    trace_by_key: dict[tuple[str, str, str], dict[str, str]] = {}
    trace_ids: set[str] = set()
    expected_trace_keys: set[tuple[str, str, str]] = set()
    if repetitions > 0:
        for held_id, held in observation_by_id.items():
            for procedure in ("one", "two"):
                for training_id, training in observation_by_id.items():
                    if (
                        training.get("x_group_id") != held.get("x_group_id")
                        and training.get(f"resid_{procedure}_oof") is not None
                    ):
                        expected_trace_keys.add((held_id, procedure, training_id))
    for index, row in enumerate(scale_rows):
        try:
            held_id = decode_export_id(row["row_id"])
            procedure = row["procedure"]
            training_id = decode_export_id(row["training_row_id"])
            training_group = decode_export_id(row["training_x_group_id"])
            trace_id = decode_export_id(row["trace_id"])
            key = (held_id, procedure, training_id)
            if key in trace_by_key:
                scale_errors.append(f"duplicate_key:{key}")
            if trace_id in trace_ids:
                scale_errors.append(f"duplicate_trace_id:{trace_id}")
            trace_ids.add(trace_id)
            if key not in expected_trace_keys:
                scale_errors.append(f"unexpected_key:{key}")
            held = observation_by_id[held_id]
            training = observation_by_id[training_id]
            if training_group != training.get("x_group_id") or training_group == held.get("x_group_id"):
                scale_errors.append(f"{index}.training_group_leakage")
            if not close_or_equal(numeric_csv(row, "sample_residual"), training.get(f"resid_{procedure}_oof")):
                scale_errors.append(f"{index}.sample_residual")
            trace_by_key[key] = row
        except Exception as exc:
            scale_errors.append(f"row:{index}:{type(exc).__name__}:{exc}")
    if set(trace_by_key) != expected_trace_keys:
        scale_errors.append(
            f"trace_keys:missing={len(expected_trace_keys - set(trace_by_key))}:"
            f"extra={len(set(trace_by_key) - expected_trace_keys)}"
        )

    report_scale_traces = json_get(bundle.report, "diagnostics.scale_traces", [])
    report_trace_by_key = {
        (trace.get("row_id"), trace.get("procedure")): trace
        for trace in report_scale_traces
        if isinstance(trace, dict)
    }
    if (
        len(report_scale_traces) != 2 * len(observation_by_id)
        or set(report_trace_by_key) != {(row_id, procedure) for row_id in observation_by_id for procedure in ("one", "two")}
    ):
        scale_errors.append("report_scale_trace_keys")

    derived_scales: dict[tuple[str, str], tuple[float | None, str | None, float, float | None]] = {}
    for held_id, held in observation_by_id.items():
        for procedure in ("one", "two"):
            training = [
                row
                for row in observations
                if row.get("x_group_id") != held.get("x_group_id")
                and row.get(f"resid_{procedure}_oof") is not None
            ] if repetitions > 0 else []
            residuals = [float(row[f"resid_{procedure}_oof"]) for row in training]
            center = median_or_none(residuals)
            tolerance = 64.0 * sys.float_info.epsilon * max([1.0, *(abs(float(row["y"])) for row in training)])

            global_scale_candidate, global_estimator = robust_scale_candidate(residuals)
            global_scale = (
                global_scale_candidate
                if global_scale_candidate is not None
                and math.isfinite(global_scale_candidate)
                and global_scale_candidate > tolerance
                else None
            )
            group_x = {row["x_group_id"]: float(row["x"]) for row in training}
            ordered_groups = sorted(group_x, key=group_x.get)
            bin_count = max(1, min(4, len(ordered_groups) // 5)) if ordered_groups else 0
            bins: list[list[str]] = [[] for _ in range(bin_count)]
            for group_index, group_id in enumerate(ordered_groups):
                bins[min(bin_count - 1, math.floor(group_index * bin_count / len(ordered_groups)))].append(group_id)

            def distance_to_bin(group_ids: list[str]) -> float:
                low = min(group_x[group_id] for group_id in group_ids)
                high = max(group_x[group_id] for group_id in group_ids)
                x_value = float(held["x"])
                return low - x_value if x_value < low else x_value - high if x_value > high else 0.0

            target_index = min(range(bin_count), key=lambda index: (distance_to_bin(bins[index]), index)) if bins else None
            target_groups = bins[target_index] if target_index is not None else []
            local_values = [
                float(row[f"resid_{procedure}_oof"])
                for row in training
                if row["x_group_id"] in target_groups
            ]
            raw_local_candidate, local_estimator = robust_scale_candidate(local_values)
            raw_local_scale = (
                raw_local_candidate
                if raw_local_candidate is not None
                and math.isfinite(raw_local_candidate)
                and raw_local_candidate > tolerance
                else None
            )
            local_floor = 0.5 * global_scale if global_scale is not None else None
            if bin_count == 1 or raw_local_scale is None:
                scale = global_scale
            elif local_floor is None:
                scale = raw_local_scale
            else:
                scale = max(raw_local_scale, local_floor)
            validation_available = repetitions > 0
            reason = None if scale is not None else (
                "ZERO_OR_UNDEFINED_SCALE" if validation_available and training else "OOF_DIAGNOSTICS_UNAVAILABLE"
            )
            report_status = "defined" if scale is not None else "undefined" if validation_available and training else "unavailable"
            expected_report_trace = {
                "row_id": held_id,
                "x_group_id": held.get("x_group_id"),
                "procedure": procedure,
                "status": report_status,
                "reason": reason,
                "estimator": global_estimator,
                "scope": "outer_train_inner_oof",
                "global_scale": global_scale,
                "bin_count": bin_count,
                "target_bin_id": f"bin-{target_index + 1}" if target_index is not None else None,
                "target_bin_group_ids": target_groups,
                "raw_local_scale": raw_local_scale,
                "local_floor": local_floor,
                "final_scale": scale,
                "zero_tolerance": tolerance,
                "training_sample_count": len(training),
                "local_sample_count": len(local_values),
            }
            if not deep_close(report_trace_by_key.get((held_id, procedure)), expected_report_trace):
                scale_errors.append(f"{held_id}.{procedure}.report_trace")
            derived_scales[(held_id, procedure)] = (scale, reason, tolerance, center)
            if not close_or_equal(held.get(f"scale_{procedure}_oof"), scale):
                scale_errors.append(f"{held_id}.{procedure}.observation_scale")
            expected_z = (
                float(held[f"resid_{procedure}_oof"]) / scale
                if scale is not None and held.get(f"resid_{procedure}_oof") is not None
                else None
            )
            if not close_or_equal(held.get(f"z_{procedure}_oof"), expected_z):
                scale_errors.append(f"{held_id}.{procedure}.observation_z")
            for training in training:
                trace = trace_by_key.get((held_id, procedure, training["row_id"]))
                if not trace:
                    continue
                expected_status = "defined" if scale is not None else "undefined"
                try:
                    decoded_target_bin_id = (
                        decode_export_id(trace["target_bin_id"])
                        if trace.get("target_bin_id")
                        else None
                    )
                except (TypeError, ValueError, UnicodeDecodeError):
                    decoded_target_bin_id = "__INVALID_IDENTIFIER_ENCODING__"
                    scale_errors.append(f"{held_id}.{procedure}.target_bin_id_encoding")
                if (
                    trace.get("estimator") != global_estimator
                    or not close_or_equal(numeric_csv(trace, "center_median"), center)
                    or int(trace.get("bin_count", "-1")) != bin_count
                    or decoded_target_bin_id != expected_report_trace["target_bin_id"]
                    or (trace.get("in_target_bin") == "true") != (training["x_group_id"] in target_groups)
                    or not close_or_equal(numeric_csv(trace, "raw_local_scale"), raw_local_scale)
                    or not close_or_equal(numeric_csv(trace, "local_floor"), local_floor)
                    or not close_or_equal(numeric_csv(trace, "computed_scale"), scale)
                    or not close_or_equal(numeric_csv(trace, "zero_tolerance"), tolerance)
                    or trace.get("scale_status") != expected_status
                    or (trace.get("scale_reason") or None) != reason
                ):
                    scale_errors.append(f"{held_id}.{procedure}.trace_state")
    audit.add(
        "RESID",
        "RESID-SCALE-021",
        not scale_errors,
        "outer-training trace excludes the held x-group and independently recomputes Qn-first robust scale, tolerance and z state",
        bundle=bundle.name,
        evidence={
            "trace_rows": len(scale_rows),
            "expected_trace_rows": len(expected_trace_keys),
            "errors": sorted(set(scale_errors)),
        },
    )

    influence_errors: list[str] = []
    influence_block = json_get(bundle.report, "diagnostics.influence", {})
    influence_groups = json_get(bundle.report, "diagnostics.influence_groups", [])
    influence_by_group = {
        group.get("x_group_id"): group
        for group in influence_groups
        if isinstance(group, dict) and isinstance(group.get("x_group_id"), str)
    }
    observation_groups: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        observation_groups.setdefault(observation["x_group_id"], []).append(observation)
    expected_thresholds = {
        "dmax": 0.50,
        "drms": 0.25,
        "delta_c": 0.05,
        "segment_share_delta": 0.05,
        "oof_rmse_relative_delta": 0.10,
        "rel_mse_uplift_delta": 0.05,
    }
    if not deep_close(influence_block.get("thresholds"), expected_thresholds):
        influence_errors.append("thresholds")
    if set(influence_by_group) != set(observation_groups):
        influence_errors.append("group_set")
    grid_by_group: dict[str, list[dict[str, str]]] = {}
    scale_reference_rows = json_get(bundle.report, "diagnostics.rows", [])
    scale_reference_values = [
        json_get(item, "robust_scale.value")
        for item in scale_reference_rows
        if isinstance(item, dict)
    ]
    for index, row in enumerate(influence_rows):
        try:
            group_id = decode_export_id(row["x_group_id"])
            grid_by_group.setdefault(group_id, []).append(row)
            full = float(row["full_prediction"])
            without = float(row["without_group_prediction"])
            s_ref = float(row["s_ref"])
            normalized = float(row["normalized_delta"])
            x_value = float(row["x"])
            reference_row = min(
                scale_reference_rows,
                key=lambda item: (abs(float(item["x"]) - x_value), float(item["x"]), item["row_id"]),
            ) if scale_reference_rows else None
            expected_s_ref = json_get(reference_row, "robust_scale.value") if reference_row else None
            if (
                s_ref <= 0
                or row.get("s_ref_status") != "defined"
                or not close_or_equal(s_ref, expected_s_ref)
            ):
                influence_errors.append(f"{index}.s_ref")
            elif not close_or_equal(normalized, (full - without) / s_ref):
                influence_errors.append(f"{index}.normalized_delta")
            if row.get("status") != "sensitivity_only":
                influence_errors.append(f"{index}.status")
        except Exception as exc:
            influence_errors.append(f"grid:{index}:{type(exc).__name__}:{exc}")

    influence_available = influence_block.get("status") == "available"
    expected_influence_available = (
        repetitions > 0
        and role in {"one", "two"}
        and bool(scale_reference_values)
        and all(finite_number(value) and float(value) > 0 for value in scale_reference_values)
    )
    if influence_available != expected_influence_available:
        influence_errors.append("availability")
    computed_high: dict[str, bool] = {}
    grid_size = influence_block.get("evaluation_grid_size")
    domain = json_get(bundle.report, "input.domain")
    expected_grid_values: list[float] = []
    if isinstance(domain, list) and len(domain) == 2:
        lo, hi = [float(value) for value in domain]
        expected_grid_set = {lo + (hi - lo) * index / 500 for index in range(501)}
        expected_grid_set.update(float(row["x"]) for row in observations)
        boundary = json_get(bundle.report, "refit.p2.boundary")
        if boundary is not None:
            expected_grid_set.add(float(boundary))
        expected_grid_values = sorted(expected_grid_set)
    if grid_size != len(expected_grid_values):
        influence_errors.append("evaluation_grid_size")
    for group_id, observation_group in observation_groups.items():
        group = influence_by_group.get(group_id, {})
        rows = grid_by_group.get(group_id, [])
        expected_row_ids = [row["row_id"] for row in observation_group]
        if group.get("row_ids") != expected_row_ids or not close_or_equal(group.get("x"), observation_group[0]["x"]):
            influence_errors.append(f"{group_id}.inheritance_identity")
        expected_full_state = {
            "recommendation": role,
            "decision_code": json_get(bundle.report, "decision.code"),
            "p2_status": json_get(bundle.report, "refit.p2.status"),
            "direction": (
                json_get(bundle.report, "refit.p2.direction")
                if role == "two"
                else json_get(bundle.report, "refit.p1.direction")
            ),
            "family": (
                f"{json_get(bundle.report, 'refit.p2.family_left')}|{json_get(bundle.report, 'refit.p2.family_right')}"
                if role == "two"
                else json_get(bundle.report, "refit.p1.family_id")
            ),
            "certificate": json_get(bundle.report, "refit.p1.certificate"),
        }
        full_state = group.get("full_state")
        without_state = group.get("without_group_state")
        if not deep_close(full_state, expected_full_state):
            influence_errors.append(f"{group_id}.full_state")
        state_change_fields = {
            "recommendation_changed": "recommendation",
            "decision_changed": "decision_code",
            "p2_status_changed": "p2_status",
            "family_changed": "family",
            "direction_changed": "direction",
            "certificate_changed": "certificate",
        }
        for changed_field, state_field in state_change_fields.items():
            expected_changed = (
                isinstance(full_state, dict)
                and isinstance(without_state, dict)
                and full_state.get(state_field) != without_state.get(state_field)
            )
            if group.get(changed_field) is not expected_changed:
                influence_errors.append(f"{group_id}.{changed_field}")
        if influence_available:
            indices = sorted(int(row["grid_index"]) for row in rows)
            if not isinstance(grid_size, int) or indices != list(range(grid_size)):
                influence_errors.append(f"{group_id}.grid_indices")
            if isinstance(grid_size, int) and grid_size > 0:
                ordered_rows = sorted(rows, key=lambda item: int(item["grid_index"]))
                for grid_index, row in enumerate(ordered_rows):
                    expected_x = expected_grid_values[grid_index]
                    if not close_or_equal(float(row["x"]), expected_x):
                        influence_errors.append(f"{group_id}.grid_x")
                        break
            deltas = [float(row["normalized_delta"]) for row in rows]
            dmax = max((abs(value) for value in deltas), default=None)
            drms = math.sqrt(sum(value * value for value in deltas) / len(deltas)) if deltas else None
            if not close_or_equal(group.get("dmax"), dmax) or not close_or_equal(group.get("drms"), drms):
                influence_errors.append(f"{group_id}.dmax_drms")
            high = any(
                finite_number(group.get(field)) and float(group[field]) >= limit
                for field, limit in expected_thresholds.items()
            ) or any(group.get(field) is True for field in state_change_fields)
            if group.get("status") != "sensitivity_only" or group.get("reason") is not None:
                influence_errors.append(f"{group_id}.available_state")
        else:
            high = False
            if rows:
                influence_errors.append(f"{group_id}.unexpected_grid")
            if (
                group.get("status") != "unassessable"
                or not isinstance(group.get("reason"), str)
                or any(group.get(field) is not None for field in expected_thresholds)
            ):
                influence_errors.append(f"{group_id}.unavailable_state")
        computed_high[group_id] = high
        if group.get("high_refit_influence") is not high or group.get("action") != "review_only":
            influence_errors.append(f"{group_id}.high_flag")
    if set(grid_by_group) != (set(observation_groups) if influence_available else set()):
        influence_errors.append("grid_group_set")
    if influence_block.get("group_count") != len(observation_groups):
        influence_errors.append("group_count")
    if influence_block.get("flagged_group_count") != sum(computed_high.values()):
        influence_errors.append("flagged_group_count")
    audit.add(
        "RESID",
        "RESID-INFLUENCE-022",
        not influence_errors,
        "influence grid independently recomputes Dmax/Drms; group metrics and flags inherit to every member row",
        bundle=bundle.name,
        evidence={
            "grid_rows": len(influence_rows),
            "group_count": len(observation_groups),
            "computed_high_groups": sorted(group for group, high in computed_high.items() if high),
            "errors": sorted(set(influence_errors)),
        },
    )

    summary_errors: list[str] = []
    diagnostic_rows = json_get(bundle.report, "diagnostics.rows", [])
    diagnostic_by_id = {
        row.get("row_id"): row
        for row in diagnostic_rows
        if isinstance(row, dict) and isinstance(row.get("row_id"), str)
    }
    computed_row_values: dict[str, dict[str, Any]] = {}
    expected_flagged_rows: list[dict[str, Any]] = []
    if set(diagnostic_by_id) != set(observation_by_id) or len(diagnostic_rows) != len(observations):
        summary_errors.append("diagnostic_row_set")
    for row_index, observation in enumerate(observations):
        row_id = observation["row_id"]
        diagnostic = diagnostic_by_id.get(row_id, {})
        selected = [
            appearances_by_key[(row_id, diagnostic_procedure, repetition)]
            for repetition in range(1, repetitions + 1)
            if diagnostic_procedure in {"one", "two"}
            and (row_id, diagnostic_procedure, repetition) in appearances_by_key
        ]
        predictions = [float(item["prediction_oof"]) for item in selected if item["prediction_oof"] is not None]
        residuals = [float(item["residual_oof"]) for item in selected if item["residual_oof"] is not None]
        scales = [float(item["robust_scale"]) for item in selected if item["robust_scale"] is not None]
        z_values = [float(item["z_oof"]) for item in selected if item["z_oof"] is not None]
        score = median_or_none([abs(value) for value in z_values])
        flag_rate = (
            sum(abs(value) >= float(threshold) for value in z_values) / len(z_values)
            if z_values and finite_number(threshold)
            else None
        )
        expected_summary = {
            "row_id": row_id,
            "source_row_id": observation.get("source_row_id"),
            "x": observation.get("x"),
            "y": observation.get("y"),
            "x_group_id": observation.get("x_group_id"),
            "segment_refit": observation.get("segment_refit"),
            "procedure": diagnostic_procedure,
            "oof_prediction_median": median_or_none(predictions),
            "oof_prediction_mean": mean_or_none(predictions),
            "oof_prediction_spread": spread_or_none(predictions),
            "oof_residual_median": median_or_none(residuals),
            "oof_residual_mean": mean_or_none(residuals),
            "oof_residual_spread": spread_or_none(residuals),
            "appearance_count": len(selected),
            "outer_fold_ids": [item["outer_fold_id"] for item in sorted(selected, key=lambda item: item["repetition"])],
            "scale_trace_index": 2 * row_index + (1 if diagnostic_procedure == "two" else 0),
            "z_median": median_or_none(z_values),
            "z_spread": spread_or_none(z_values),
            "score_median_abs_z": score,
            "threshold": 3.5,
            "flag_rate": flag_rate,
            "action": "review_only",
        }
        for key, expected_value in expected_summary.items():
            if not deep_close(diagnostic.get(key), expected_value):
                summary_errors.append(f"{row_id}.{key}")
        selected_scale = median_or_none(scales)
        robust_scale = diagnostic.get("robust_scale", {})
        expected_scale_reason = (
            derived_scales.get(
                (row_id, diagnostic_procedure),
                (None, "OOF_DIAGNOSTICS_UNAVAILABLE", 0, None),
            )[1]
            if diagnostic_procedure
            else "OOF_DIAGNOSTICS_UNAVAILABLE"
        )
        expected_scale_method = report_trace_by_key.get(
            (row_id, diagnostic_procedure), {}
        ).get("estimator", "qn_finite_sample")
        if (
            not isinstance(robust_scale, dict)
            or not close_or_equal(robust_scale.get("value"), selected_scale)
            or robust_scale.get("status") != ("defined" if selected_scale is not None else "undefined")
            or robust_scale.get("reason") != (None if selected_scale is not None else expected_scale_reason)
            or robust_scale.get("method") != expected_scale_method
            or robust_scale.get("scope") != "outer_train_inner_oof"
        ):
            summary_errors.append(f"{row_id}.robust_scale")
        refit_field = "pred_two_refit" if role == "two" else "pred_one_refit"
        refit_prediction = observation.get(refit_field)
        refit_residual = float(observation["y"]) - float(refit_prediction) if refit_prediction is not None else None
        if (
            not close_or_equal(diagnostic.get("refit_prediction"), refit_prediction)
            or not close_or_equal(diagnostic.get("refit_residual"), refit_residual)
            or diagnostic.get("refit_label") != ("descriptive_final_refit" if refit_prediction is not None else None)
        ):
            summary_errors.append(f"{row_id}.refit_series")
        group_id = observation["x_group_id"]
        if not deep_close(diagnostic.get("influence_group"), influence_by_group.get(group_id)):
            summary_errors.append(f"{row_id}.influence_inheritance")
        large = bool(score is not None and flag_rate is not None and score >= 3.5 and flag_rate >= 0.5)
        codes = (["LARGE_OOF_RESIDUAL"] if large else []) + (
            ["HIGH_REFIT_INFLUENCE"] if computed_high.get(group_id, False) else []
        )
        expected_typed_flags: list[dict[str, Any]] = []
        if large:
            expected_typed_flags.append(
                {
                    "code": "LARGE_OOF_RESIDUAL",
                    "severity": "extreme" if score is not None and score >= 5.25 else "review",
                    "reason": "ROBUST_STANDARDIZED_OOF_RESIDUAL",
                    "threshold": 3.5,
                    "policy_version": "prototype-policy-v1",
                    "explanation": "median |z_OOF| and appearance flag rate meet the review threshold",
                }
            )
        if computed_high.get(group_id, False):
            expected_typed_flags.append(
                {
                    "code": "HIGH_REFIT_INFLUENCE",
                    "severity": "review",
                    "reason": "LEAVE_ONE_X_GROUP_OUT_SENSITIVITY",
                    "threshold": 0.50,
                    "policy_version": "prototype-policy-v1",
                    "explanation": "at least one versioned influence threshold is met",
                }
            )
        typed_flags = diagnostic.get("flags", [])
        reported_codes = [item.get("code") for item in typed_flags if isinstance(item, dict)]
        if (
            reported_codes != codes
            or not deep_close(typed_flags, expected_typed_flags)
            or observation.get("flags") != codes
            or observation.get("action") != "review_only"
        ):
            summary_errors.append(f"{row_id}.flag_codes")
        if codes:
            expected_flagged_rows.append(
                {
                    "row_id": row_id,
                    "flag_codes": codes,
                    "flags": expected_typed_flags,
                    "action": "review_only",
                    "diagnostic_row_index": row_index,
                }
            )
        computed_row_values[row_id] = {
            "residual": median_or_none(residuals),
            "z_values": z_values,
            "score": score,
            "flag_rate": flag_rate,
            "refit_prediction": refit_prediction,
        }
    if not deep_close(json_get(bundle.report, "diagnostics.flagged_rows", []), expected_flagged_rows):
        summary_errors.append("flagged_rows")
    audit.add(
        "RESID",
        "RESID-SUMMARIES-FLAGS-028",
        not summary_errors,
        "row summaries and typed review-only flags are independently derived from appearance numbers and influence thresholds",
        bundle=bundle.name,
        evidence={
            "diagnostic_rows": len(diagnostic_rows),
            "computed_flagged_rows": expected_flagged_rows,
            "errors": sorted(set(summary_errors)),
        },
    )

    repeated_errors: list[str] = []
    repeated_groups = json_get(bundle.report, "diagnostics.repeated_x_groups", [])
    repeated_by_group = {
        group.get("x_group_id"): group
        for group in repeated_groups
        if isinstance(group, dict) and isinstance(group.get("x_group_id"), str)
    }
    if set(repeated_by_group) != set(observation_groups):
        repeated_errors.append("group_set")
    for group_id, group_rows in observation_groups.items():
        actual = repeated_by_group.get(group_id, {})
        ys = [float(row["y"]) for row in group_rows]
        residuals = [
            float(computed_row_values[row["row_id"]]["residual"])
            for row in group_rows
            if computed_row_values[row["row_id"]]["residual"] is not None
        ]
        z_values = [
            abs(float(value))
            for row in group_rows
            for value in computed_row_values[row["row_id"]]["z_values"]
        ]
        y_center = statistics.median(ys)
        expected_group = {
            "x_group_id": group_id,
            "x": group_rows[0]["x"],
            "n": len(group_rows),
            "mean_y": statistics.fmean(ys),
            "median_y": statistics.median(ys),
            "mean_oof_residual": mean_or_none(residuals),
            "median_oof_residual": median_or_none(residuals),
            "within_group_mad_y": 1.4826 * statistics.median(abs(value - y_center) for value in ys),
            "positive_residual_share": (
                sum(value > 0 for value in residuals) / len(residuals) if residuals else None
            ),
            "negative_residual_share": (
                sum(value < 0 for value in residuals) / len(residuals) if residuals else None
            ),
            "group_systematic_residual": bool(z_values and statistics.median(z_values) >= 3.5),
            "action": "review_only",
        }
        if not deep_close(actual, expected_group):
            repeated_errors.append(group_id)

    decomposition = json_get(bundle.report, "diagnostics.pure_error_lack_of_fit", {})
    repeated_present = any(len(rows) > 1 for rows in observation_groups.values())
    selected_refit_field = "pred_two_refit" if role == "two" else "pred_one_refit"
    refit_available = all(row.get(selected_refit_field) is not None for row in observations)
    if repeated_present and refit_available:
        sse_total = sum((float(row["y"]) - float(row[selected_refit_field])) ** 2 for row in observations)
        pure_error = 0.0
        lack_of_fit = 0.0
        for group_rows in observation_groups.values():
            mean_y = statistics.fmean(float(row["y"]) for row in group_rows)
            pure_error += sum((float(row["y"]) - mean_y) ** 2 for row in group_rows)
            lack_of_fit += len(group_rows) * (mean_y - float(group_rows[0][selected_refit_field])) ** 2
        expected_decomposition = {
            "status": "descriptive",
            "reason": None,
            "procedure": role or "one",
            "sse_total": sse_total,
            "sse_pure_error": pure_error,
            "sse_lack_of_fit": lack_of_fit,
            "test_performed": False,
        }
        if not close_or_equal(sse_total, pure_error + lack_of_fit):
            repeated_errors.append("sse_identity")
    else:
        expected_decomposition = {
            "status": "unavailable",
            "reason": "NO_REPEATED_X" if not repeated_present else "REFIT_UNAVAILABLE",
            "procedure": role,
            "sse_total": None,
            "sse_pure_error": None,
            "sse_lack_of_fit": None,
            "test_performed": False,
        }
    if not deep_close(decomposition, expected_decomposition):
        repeated_errors.append("pure_error_lack_of_fit")
    audit.add(
        "RESID",
        "RESID-REPEATED-DECOMP-024",
        not repeated_errors,
        "same-x summaries preserve every observation and descriptive SSE recomputes as pure error plus lack of fit",
        bundle=bundle.name,
        evidence={"group_count": len(observation_groups), "errors": sorted(set(repeated_errors))},
    )

    pattern_errors: list[str] = []
    diagnostic_scale_values = [
        float(row["robust_scale"]["value"])
        for row in diagnostic_rows
        if isinstance(row, dict)
        and isinstance(row.get("robust_scale"), dict)
        and row["robust_scale"].get("value") is not None
    ]
    global_scale = median_or_none(diagnostic_scale_values)
    ordered_repeated = sorted(repeated_groups, key=lambda item: float(item["x"]))
    group_residuals = [
        (float(item["x"]), float(item["mean_oof_residual"]))
        for item in ordered_repeated
        if item.get("mean_oof_residual") is not None
    ]
    bin_means: list[float] = []
    if repetitions > 0 and global_scale is not None and group_residuals:
        for bin_index in range(5):
            start = math.floor(len(group_residuals) * bin_index / 5)
            end = math.floor(len(group_residuals) * (bin_index + 1) / 5)
            values = [value for _, value in group_residuals[start:end]]
            if values:
                bin_means.append(statistics.fmean(values))
    longest_run = 0
    current_run = 0
    previous_sign = 0
    for value in bin_means:
        sign = 1 if value > 0 else -1 if value < 0 else 0
        current_run = current_run + 1 if sign and sign == previous_sign else 1 if sign else 0
        longest_run = max(longest_run, current_run)
        previous_sign = sign
    systematic_available = repetitions > 0 and global_scale is not None and bool(group_residuals)
    systematic_trigger = bool(
        systematic_available
        and longest_run >= 3
        and any(abs(value) >= 0.5 * float(global_scale) for value in bin_means)
    )
    scale_ratio = (
        max(diagnostic_scale_values) / min(diagnostic_scale_values)
        if diagnostic_scale_values and min(diagnostic_scale_values) > 0
        else None
    )
    hetero_available = repetitions > 0 and scale_ratio is not None
    hetero_trigger = bool(hetero_available and float(scale_ratio) >= 2.0)
    breakpoint_available = repetitions > 0 and diagnostic_procedure == "two" and global_scale is not None
    if breakpoint_available:
        boundary = float(json_get(bundle.report, "refit.p2.boundary"))
        lo, hi = [float(value) for value in json_get(bundle.report, "input.domain")]
        window_rows = [
            row
            for row in diagnostic_rows
            if abs(float(row["x"]) - boundary) <= 0.10 * (hi - lo or 1.0)
            and row.get("oof_residual_median") is not None
        ]
        window_residuals = [float(row["oof_residual_median"]) for row in window_rows]
        window_count = len(window_rows) * repetitions
        window_mean = mean_or_none(window_residuals)
        same_sign_share = (
            max(sum(value > 0 for value in window_residuals), sum(value < 0 for value in window_residuals))
            / len(window_residuals)
            if window_residuals
            else None
        )
        breakpoint_trigger = bool(
            window_count >= 5
            and same_sign_share is not None
            and same_sign_share >= 0.80
            and window_mean is not None
            and abs(window_mean) >= 0.5 * float(global_scale)
        )
    else:
        window_count = 0
        window_mean = same_sign_share = None
        breakpoint_trigger = False

    expected_patterns = {
        "SYSTEMATIC_OOF_RESIDUAL": {
            "status": "triggered" if systematic_trigger else "not_triggered" if systematic_available else "unavailable",
            "observed": {
                "five_bin_means": bin_means,
                "longest_same_sign_run": longest_run,
                "global_scale": global_scale,
            } if systematic_available else None,
            "threshold": "3 adjacent same-sign bins and |mean| >= 0.5*scale",
        },
        "HETEROSCEDASTIC_PATTERN": {
            "status": "triggered" if hetero_trigger else "not_triggered" if hetero_available else "unavailable",
            "observed": {
                "max_min_scale_ratio": scale_ratio,
                "outer_fit_trigger_rate": 1.0 if hetero_trigger else 0.0,
            } if hetero_available else None,
            "threshold": "ratio >= 2.0 in >= 0.80 outer fits",
        },
        "BREAKPOINT_LOCAL_BIAS": {
            "status": "triggered" if breakpoint_trigger else "not_triggered" if breakpoint_available else "unavailable",
            "observed": {
                "appearance_count": window_count,
                "same_sign_share": same_sign_share,
                "mean_residual": window_mean,
                "global_scale": global_scale,
            } if breakpoint_available else None,
            "threshold": "n >= 5, sign share >= 0.80, |mean| >= 0.5*scale",
        },
    }
    patterns = json_get(bundle.report, "diagnostics.patterns", [])
    if [item.get("code") for item in patterns if isinstance(item, dict)] != list(expected_patterns):
        pattern_errors.append("codes_or_order")
    for pattern in patterns:
        code = pattern.get("code")
        expected_pattern = expected_patterns.get(code)
        if expected_pattern is None:
            continue
        expected_reason = None if expected_pattern["status"] != "unavailable" else "OOF_DIAGNOSTICS_UNAVAILABLE"
        if (
            pattern.get("status") != expected_pattern["status"]
            or not deep_close(pattern.get("observed"), expected_pattern["observed"])
            or pattern.get("threshold") != expected_pattern["threshold"]
            or pattern.get("reason") != expected_reason
            or pattern.get("action") != "review_only"
        ):
            pattern_errors.append(code)
    audit.add(
        "RESID",
        "RESID-PATTERNS-025",
        not pattern_errors,
        "systematic, heteroscedastic and breakpoint pattern states recompute from retained diagnostic evidence",
        bundle=bundle.name,
        evidence={"expected": expected_patterns, "errors": pattern_errors},
    )

    svg_errors: list[str] = []
    residual_root = parse_svg(bundle.path / "figures" / "residuals.svg")
    panel_ids = [node.attrib.get("data-panel") for node in residual_root.iter() if "data-panel" in node.attrib]
    if (
        residual_root.attrib.get("data-figure-kind") != "residuals"
        or residual_root.attrib.get("data-panel-count") != "4"
        or panel_ids != ["1", "2", "3", "4"]
    ):
        svg_errors.append("four_panels")
    z_thresholds = [node.attrib.get("data-z-threshold") for node in residual_root.iter() if "data-z-threshold" in node.attrib]
    influence_thresholds = [
        node.attrib.get("data-influence-threshold")
        for node in residual_root.iter()
        if "data-influence-threshold" in node.attrib
    ]
    if z_thresholds != ["3.5"] or sorted(float(value) for value in influence_thresholds) != [0.25, 0.5]:
        svg_errors.append("thresholds")
    series_rows: dict[str, set[str]] = {}
    for node in residual_root.iter():
        if node.attrib.get("data-series") and node.attrib.get("data-row-id"):
            series_rows.setdefault(node.attrib["data-series"], set()).add(node.attrib["data-row-id"])
    all_row_ids = set(observation_by_id)
    scored_row_ids = {row_id for row_id, values in computed_row_values.items() if values["score"] is not None}
    refit_row_ids = {row_id for row_id, values in computed_row_values.items() if values["refit_prediction"] is not None}
    expected_series_rows = (
        {
            "oof_residual_vs_x": all_row_ids,
            "oof_residual_vs_prediction": all_row_ids,
            **({"absolute_z_vs_x": scored_row_ids} if scored_row_ids else {}),
        }
        if expected_oof_status == "available"
        else ({"refit_residual_descriptive": refit_row_ids} if refit_row_ids else {})
    )
    if series_rows != expected_series_rows:
        svg_errors.append("row_id_series")
    svg_influence_values = {
        (node.attrib.get("data-series"), node.attrib.get("data-x-group-id")): float(node.attrib["data-value"])
        for node in residual_root.iter()
        if node.attrib.get("data-series") in {"influence_dmax", "influence_drms"}
        and node.attrib.get("data-x-group-id")
        and node.attrib.get("data-value")
    }
    expected_svg_influence = {
        (series, group_id): float(group[field])
        for group_id, group in influence_by_group.items()
        for series, field in (("influence_dmax", "dmax"), ("influence_drms", "drms"))
        if group.get(field) is not None
    }
    if svg_influence_values != expected_svg_influence:
        svg_errors.append("influence_values")
    expected_panels = [
        "oof_residual_vs_x",
        "oof_residual_vs_prediction",
        "absolute_z_vs_x",
        "refit_influence_by_x_group",
    ]
    plot = json_get(bundle.report, "diagnostics.oof.plot", {})
    expected_plot_status = "four_separate_panels" if expected_oof_status == "available" else "typed_unavailable_with_descriptive_refit"
    if (
        plot.get("path") != "figures/residuals.svg"
        or plot.get("status") != expected_plot_status
        or plot.get("panels") != expected_panels
    ):
        svg_errors.append("plot_contract")
    audit.add(
        "RESID",
        "RESID-SVG-026",
        not svg_errors,
        "residual SVG contains four separate panels, numeric thresholds and row/group identifiers matching canonical diagnostics",
        bundle=bundle.name,
        evidence={"series_rows": {key: sorted(value) for key, value in series_rows.items()}, "errors": svg_errors},
    )

    html_errors: list[str] = []
    pointers = {
        node.attrs.get("data-source-pointer")
        for node in bundle.html.nodes
        if node.attrs.get("data-source-pointer")
    }
    required_pointers = {
        "/diagnostics/oof",
        "/diagnostics/oof/plot",
        "/diagnostics/rows",
        "/diagnostics/repeated_x_groups",
        "/diagnostics/pure_error_lack_of_fit",
        "/diagnostics/patterns",
        "/diagnostics/influence",
        "/diagnostics/oof/appearance_export",
        "/diagnostics/oof/scale_trace_export",
        "/diagnostics/influence/grid_export",
        *(f"/diagnostics/rows/{index}" for index in range(len(diagnostic_rows))),
        *(f"/diagnostics/repeated_x_groups/{index}" for index in range(len(repeated_groups))),
        *(f"/diagnostics/patterns/{index}" for index in range(len(patterns))),
    }
    if not required_pointers.issubset(pointers):
        html_errors.append(f"missing_pointers:{sorted(required_pointers - pointers)}")
    export_pointer_by_href = {
        "diagnostic-appearances.csv": "/diagnostics/oof/appearance_export",
        "diagnostic-scale-trace.csv": "/diagnostics/oof/scale_trace_export",
        "influence-grid.csv": "/diagnostics/influence/grid_export",
    }
    for href, expected_pointer in export_pointer_by_href.items():
        links = [node for node in bundle.html.by_tag("a") if node.attrs.get("href") == href]
        if len(links) != 1:
            html_errors.append(f"link_count:{href}")
            continue
        current: HtmlNode | None = links[0]
        ancestors: set[str] = set()
        while current is not None:
            if current.attrs.get("data-source-pointer"):
                ancestors.add(current.attrs["data-source-pointer"])
            current = current.parent
        if expected_pointer not in ancestors:
            html_errors.append(f"link_pointer:{href}")
    audit.add(
        "RESID",
        "RESID-HTML-027",
        not html_errors,
        "HTML diagnostic tables, figure and retained exports expose resolvable canonical source pointers",
        bundle=bundle.name,
        evidence={"required_pointer_count": len(required_pointers), "errors": html_errors},
    )


def audit_raw_reconciliation(bundle: Bundle, expected: dict[str, Any], audit: Audit) -> None:
    observations = bundle.report.get("observations", [])
    csv_rows = read_csv_rows(bundle.path / "observations.csv")
    n_used = json_get(bundle.report, "input.n_used")
    n_input = json_get(bundle.report, "input.n_input")
    n_excluded = json_get(bundle.report, "input.n_excluded")
    repeated_x_groups = json_get(bundle.report, "input.repeated_x_groups")
    x_counts: dict[float, int] = {}
    for observation in observations:
        x_value = float(observation["x"])
        x_counts[x_value] = x_counts.get(x_value, 0) + 1
    expected_repeated_x_groups = sum(count > 1 for count in x_counts.values())
    json_ids = [row.get("row_id") for row in observations if isinstance(row, dict)]
    used_csv_rows = [row for row in csv_rows if row.get("input_status") == "USED"]
    excluded_csv_rows = [row for row in csv_rows if row.get("input_status") == "EXCLUDED"]
    csv_ids = [row.get("row_id", "") for row in csv_rows]
    try:
        decoded_used_ids = [decode_export_id(row.get("row_id", "")) for row in used_csv_rows]
    except (TypeError, ValueError, UnicodeDecodeError):
        decoded_used_ids = []
    exclusion_reason_counts: dict[str, int] = {}
    for row in excluded_csv_rows:
        code = row.get("input_reason", "")
        exclusion_reason_counts[code] = exclusion_reason_counts.get(code, 0) + 1
    count_ok = (
        isinstance(n_used, int)
        and len(observations) == len(used_csv_rows) == n_used
        and isinstance(n_input, int)
        and isinstance(n_excluded, int)
        and len(csv_rows) == n_input
        and len(excluded_csv_rows) == n_excluded
        and n_input == n_used + n_excluded
        and repeated_x_groups == expected_repeated_x_groups
        and all(not row.get("x") and not row.get("y") and row.get("input_reason") for row in excluded_csv_rows)
        and exclusion_reason_counts == json_get(bundle.report, "input.invalid_reason_counts", {})
    )
    id_ok = (
        all(value.startswith("id:") for value in csv_ids)
        and len(set(json_ids)) == len(json_ids)
        and decoded_used_ids == json_ids
    )
    metadata_ok = all(
        (bundle.path / name).is_file()
        for name in ("observations.csv-metadata.json", "plot-data.csv-metadata.json")
    )
    audit.add(
        "RAW",
        "RAW-COUNTS-010",
        count_ok and id_ok and metadata_ok,
        "JSON/CSV row counts, exclusions, identifiers and CSVW sidecars reconcile",
        bundle=bundle.name,
        evidence={
            "n_input": n_input,
            "n_used": n_used,
            "n_excluded": n_excluded,
            "repeated_x_groups": repeated_x_groups,
            "expected_repeated_x_groups": expected_repeated_x_groups,
            "json_rows": len(observations),
            "csv_rows": len(csv_rows),
            "used_csv_rows": len(used_csv_rows),
            "excluded_csv_rows": len(excluded_csv_rows),
            "exclusion_reason_counts": exclusion_reason_counts,
            "id_prefix_ok": id_ok,
            "metadata_ok": metadata_ok,
        },
    )
    if bundle.name == "security_payloads":
        audit.add(
            "SECURITY-STATIC",
            "SECURITY-CSV-ID-020",
            bool(csv_ids)
            and csv_ids[0].startswith("id:%3C")
            and csv_rows[0].get("source_row_id", "").startswith("id:%3D"),
            "hostile identifiers are universally prefixed and percent-encoded for CSV",
            bundle=bundle.name,
            evidence={"row_id": csv_ids[0] if csv_ids else None, "source_row_id": csv_rows[0].get("source_row_id") if csv_rows else None},
        )
        exclusions = json_get(bundle.report, "input.exclusions", [])
        raw_value = exclusions[0].get("raw_value") if exclusions else None
        exclusion_text = (bundle.path / "input-exclusions.jsonl").read_text(encoding="utf-8")
        observations_text = (bundle.path / "observations.csv").read_text(encoding="utf-8")
        appearances_text = (bundle.path / "oof-appearances.csv").read_text(encoding="utf-8")
        html_source = (bundle.path / "report.html").read_text(encoding="utf-8")
        hostile_reason = next(
            (item.get("reason") for item in bundle.report.get("warnings", []) if item.get("code") == "SYNTHETIC_HOSTILE_REASON"),
            None,
        )
        audit.add(
            "SECURITY-STATIC",
            "SECURITY-HOSTILE-FIELDS-021",
            isinstance(raw_value, str)
            and raw_value in exclusion_text
            and raw_value not in observations_text
            and raw_value not in appearances_text
            and isinstance(hostile_reason, str)
            and hostile_reason not in html_source
            and "&lt;script&gt;alert(3)&lt;/script&gt;" in html_source
            and str(json_get(bundle.report, "input.filename")).startswith("../../"),
            "hostile path/reason/invalid raw payloads stay escaped data and invalid raw values stay out of spreadsheet CSV",
            bundle=bundle.name,
            evidence={
                "raw_in_jsonl": isinstance(raw_value, str) and raw_value in exclusion_text,
                "raw_in_observations_csv": isinstance(raw_value, str) and raw_value in observations_text,
                "raw_in_appearances_csv": isinstance(raw_value, str) and raw_value in appearances_text,
                "reason_escaped": isinstance(hostile_reason, str) and hostile_reason not in html_source,
            },
        )
    if bundle.name == "p2_no_balanced_split":
        coordinates = [(row.get("x"), row.get("y")) for row in observations]
        duplicate_count = len(coordinates) - len(set(coordinates))
        audit.add(
            "RAW",
            "RAW-REPEATS-011",
            duplicate_count >= 1 and len(observations) == n_used,
            "exact repeated coordinates remain separate logical observations",
            bundle=bundle.name,
            evidence={"duplicate_record_count": duplicate_count},
        )


def audit_csv_identifiers(bundle: Bundle, audit: Audit) -> None:
    total_values = 0
    identifier_columns_seen = 0
    error_count = 0
    error_examples: list[str] = []

    def record_error(message: str) -> None:
        nonlocal error_count
        error_count += 1
        if len(error_examples) < 50:
            error_examples.append(message)

    for csv_path in sorted(bundle.path.glob("*.csv")):
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames or []
            identifier_columns = [name for name in headers if name == "id" or name.endswith("_id")]
            metadata_path = csv_path.with_name(csv_path.name + "-metadata.json")
            if not metadata_path.is_file():
                record_error(f"{csv_path.name}:missing_metadata")
            else:
                metadata = strict_json(metadata_path)
                declared_columns = [
                    item.get("name")
                    for item in json_get(metadata, "tableSchema.columns", [])
                    if isinstance(item, dict)
                ]
                dialect = metadata.get("dialect", {})
                expected_dialect = {
                    "encoding": "utf-8",
                    "delimiter": ",",
                    "lineTerminators": ["\n"],
                    "quoteChar": '"',
                    "doubleQuote": True,
                    "header": True,
                    "null": [""],
                }
                if metadata.get("@context") != "http://www.w3.org/ns/csvw" or metadata.get("url") != csv_path.name:
                    record_error(f"{csv_path.name}:csvw_identity")
                if dialect != expected_dialect:
                    record_error(f"{csv_path.name}:dialect")
                if metadata.get("identifierEncoding") != "id: + UTF-8 percent encoding":
                    record_error(f"{csv_path.name}:identifierEncoding")
                if declared_columns != headers:
                    record_error(f"{csv_path.name}:metadata_columns")
            if not identifier_columns:
                continue
            identifier_columns_seen += len(identifier_columns)
            for row_index, row in enumerate(reader, 2):
                for column in identifier_columns:
                    value = row.get(column, "")
                    if value == "":
                        continue
                    total_values += 1
                    try:
                        decoded = decode_export_id(value)
                        if encode_export_id(decoded) != value:
                            record_error(f"{csv_path.name}:{row_index}:{column}:noncanonical")
                    except Exception as exc:
                        record_error(f"{csv_path.name}:{row_index}:{column}:{type(exc).__name__}")
    audit.add(
        "SECURITY-STATIC",
        "SECURITY-CSV-IDENTIFIERS-032",
        error_count == 0 and identifier_columns_seen > 0 and total_values > 0,
        "every nonempty value in every CSV identifier column uses canonical reversible id: UTF-8 percent encoding",
        bundle=bundle.name,
        evidence={
            "identifier_columns": identifier_columns_seen,
            "identifier_values": total_values,
            "error_count": error_count,
            "error_examples": error_examples,
        },
    )


def parse_svg(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


def svg_elements(root: ET.Element, name: str) -> list[ET.Element]:
    return [element for element in root.iter() if local_name(element.tag) == name]


def audit_geometry(bundle: Bundle, audit: Audit) -> None:
    one_root = parse_svg(bundle.path / "figures" / "one-function.svg")
    two_root = parse_svg(bundle.path / "figures" / "two-segment.svg")
    geometry_keys = ("data-xlim", "data-ylim", "data-plot-rect", "viewBox")
    shared_geometry = all(one_root.attrib.get(key) == two_root.attrib.get(key) for key in geometry_keys)
    one_points = [node for node in svg_elements(one_root, "circle") if node.attrib.get("data-row-id")]
    two_points = [node for node in svg_elements(two_root, "circle") if node.attrib.get("data-row-id")]
    one_layer = sorted((node.attrib.get("data-row-id"), node.attrib.get("data-x"), node.attrib.get("data-y")) for node in one_points)
    two_layer = sorted((node.attrib.get("data-row-id"), node.attrib.get("data-x"), node.attrib.get("data-y")) for node in two_points)
    report_layer = sorted(
        (str(row.get("row_id")), format(float(row["x"]), ".17g"), format(float(row["y"]), ".17g"))
        for row in bundle.report.get("observations", [])
    )
    n_used = json_get(bundle.report, "input.n_used")
    audit.add(
        "CMP",
        "CMP-GEOMETRY-011",
        shared_geometry and one_layer == two_layer == report_layer and len(one_layer) == n_used,
        "P1/P2 panels share exact geometry and immutable report.json point layer",
        bundle=bundle.name,
        evidence={"shared_geometry": shared_geometry, "p1_points": len(one_layer), "p2_points": len(two_layer), "json_points": len(report_layer), "n_used": n_used},
    )

    xlim = json_get(bundle.report, "visualization.geometry.xlim")
    p1_lines = [node for node in svg_elements(one_root, "polyline") if node.attrib.get("data-series") == "p1"]
    p1_expected = json_get(bundle.report, "refit.p1.status") == "certified"
    p1_ok = (len(p1_lines) == 1 and p1_lines[0].attrib.get("data-domain") == f"{xlim[0]},{xlim[1]}") if p1_expected else not p1_lines

    p2_status = json_get(bundle.report, "refit.p2.status")
    p2_lines = [node for node in svg_elements(two_root, "polyline") if node.attrib.get("data-series", "").startswith("p2_")]
    boundaries = [node for node in svg_elements(two_root, "line") if "data-boundary" in node.attrib]
    plot_rows = read_csv_rows(bundle.path / "plot-data.csv")
    p2_plot = [row for row in plot_rows if row.get("series", "").startswith("p2_")]
    p2_ok = False
    evidence: dict[str, Any] = {"p2_status": p2_status, "p2_lines": len(p2_lines), "boundaries": len(boundaries), "p2_plot_rows": len(p2_plot)}
    if p2_status == "certified":
        c = float(json_get(bundle.report, "refit.p2.boundary"))
        domains = {node.attrib.get("data-series"): [float(value) for value in node.attrib["data-domain"].split(",")] for node in p2_lines}
        by_series: dict[str, list[dict[str, str]]] = {}
        for row in p2_plot:
            by_series.setdefault(row["series"], []).append(row)
        endpoints_ok = (
            set(domains) == {"p2_left", "p2_right"}
            and domains["p2_left"] == [xlim[0], c]
            and domains["p2_right"] == [c, xlim[1]]
            and set(by_series) == {"p2_left", "p2_right"}
            and float(by_series["p2_left"][-1]["x"]) == c
            and float(by_series["p2_right"][0]["x"]) == c
            and math.isclose(float(by_series["p2_left"][-1]["y"]), float(by_series["p2_right"][0]["y"]), rel_tol=0, abs_tol=1e-12)
            and all(xlim[0] <= float(row["x"]) <= c for row in by_series["p2_left"])
            and all(c <= float(row["x"]) <= xlim[1] for row in by_series["p2_right"])
        )
        branch_labels = [node for node in svg_elements(two_root, "text") if node.attrib.get("data-branch-label")]
        label_roles = {node.attrib.get("data-branch-label") for node in branch_labels}
        p2_ok = (
            len(p2_lines) == 2
            and len(boundaries) == 1
            and float(boundaries[0].attrib["data-boundary"]) == c
            and endpoints_ok
            and label_roles == {"left", "right"}
        )
        evidence.update({"c": c, "domains": domains, "endpoints_ok": endpoints_ok, "branch_labels": sorted(label_roles)})
    else:
        p2_ok = not p2_lines and not boundaries and not p2_plot
    audit.add(
        "GEOM",
        "GEOM-CLIP-010",
        p1_ok and p2_ok,
        "P1 is domain-clipped; P2 branches meet at c or failed P2 has no fabricated line/boundary",
        bundle=bundle.name,
        evidence={"p1_ok": p1_ok, "p2_ok": p2_ok, **evidence},
    )

    left, top, width, height = json_get(bundle.report, "visualization.geometry.plot_rect")
    x0, x1 = json_get(bundle.report, "visualization.geometry.xlim")
    y0, y1 = json_get(bundle.report, "visualization.geometry.ylim")

    def transformed_points(rows: list[dict[str, str]]) -> str:
        return " ".join(
            f"{left + (float(row['x']) - x0) / (x1 - x0 or 1.0) * width:.3f},"
            f"{top + height - (float(row['y']) - y0) / (y1 - y0 or 1.0) * height:.3f}"
            for row in rows
        )

    plot_by_series: dict[str, list[dict[str, str]]] = {}
    for row in plot_rows:
        plot_by_series.setdefault(row["series"], []).append(row)
    svg_series = {
        node.attrib.get("data-series"): node.attrib.get("points")
        for node in [*p1_lines, *p2_lines]
    }
    expected_series = {
        name: transformed_points(rows)
        for name, rows in plot_by_series.items()
        if name in {"p1", "p2_left", "p2_right"}
    }
    audit.add(
        "GEOM",
        "GEOM-PLOT-DATA-011",
        svg_series == expected_series,
        "every rendered model polyline coordinate is independently derived from plot-data.csv and canonical geometry",
        bundle=bundle.name,
        evidence={"svg_series": sorted(svg_series), "expected_series": sorted(expected_series)},
    )

    if bundle.name == "p2_no_balanced_split":
        multiplicities = [int(node.attrib.get("data-multiplicity", "1")) for node in one_points]
        rugs = [node for node in svg_elements(one_root, "line") if node.attrib.get("data-rug-multiplicity")]
        labels = [node for node in svg_elements(one_root, "text") if node.attrib.get("data-count-label")]
        audit.add(
            "RAW",
            "RAW-MULTIPLICITY-012",
            max(multiplicities, default=1) >= 2 and bool(rugs) and bool(labels),
            "exact overplotting has a non-jitter multiplicity/rug/count encoding",
            bundle=bundle.name,
            evidence={"max_multiplicity": max(multiplicities, default=1), "rug_marks": len(rugs), "count_labels": len(labels)},
        )

    if bundle.name == "two_recommended_low_r2_05996":
        shares = [json_get(bundle.report, "refit.p2.left.share"), json_get(bundle.report, "refit.p2.right.share")]
        audit.add(
            "GEOM",
            "GEOM-BALANCE-012",
            shares == [0.4, 0.6],
            "the edge fixture exercises the exact 40/60 segment-share boundary",
            bundle=bundle.name,
            evidence={"shares": shares},
        )


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    current = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise KeyError(pointer)
    return current


def audit_source_pointers(bundle: Bundle, audit: Audit) -> None:
    pointers = sorted({
        node.attrs["data-source-pointer"]
        for node in bundle.html.nodes
        if node.attrs.get("data-source-pointer")
    })
    missing: list[str] = []
    for pointer in pointers:
        try:
            resolve_json_pointer(bundle.report, pointer)
        except (KeyError, IndexError, ValueError, TypeError):
            missing.append(pointer)
    audit.add(
        "JSON",
        "JSON-POINTERS-020",
        bool(pointers) and not missing,
        "every HTML data-source-pointer resolves in canonical report.json",
        bundle=bundle.name,
        evidence={"pointer_count": len(pointers), "missing": missing},
    )

    table_rows = bundle.html.by_tag("tr")
    rows_without_pointer: list[int] = []
    unresolved_rows: list[dict[str, Any]] = []
    for index, row in enumerate(table_rows):
        pointer = row.attrs.get("data-source-pointer")
        if not pointer:
            rows_without_pointer.append(index)
            continue
        try:
            resolve_json_pointer(bundle.report, pointer)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            unresolved_rows.append({"row_index": index, "pointer": pointer, "error": type(exc).__name__})
    audit.add(
        "JSON",
        "JSON-TR-POINTERS-021",
        bool(table_rows) and not rows_without_pointer and not unresolved_rows,
        "every HTML table row, including headers and artifact rows, carries a resolvable canonical data-source-pointer",
        bundle=bundle.name,
        evidence={
            "table_row_count": len(table_rows),
            "rows_without_pointer": rows_without_pointer,
            "unresolved_rows": unresolved_rows,
        },
    )

    status_badges = [
        node for node in bundle.html.nodes
        if node.tag == "span" and "status" in node.attrs.get("class", "").split()
    ]
    formula_nodes = [
        node for node in bundle.html.nodes
        if node.tag == "code" and ("f(x)" in node.text.casefold() or "F(x)" in node.text)
    ]
    warning_nodes = [
        node for node in bundle.html.nodes
        if "warning" in node.attrs.get("class", "").split()
    ]
    coverage_errors = {
        "status_without_pointer": sum(not node.attrs.get("data-source-pointer") for node in status_badges),
        "formula_without_pointer": sum(not node.attrs.get("data-source-pointer") for node in formula_nodes),
        "warning_without_pointer": sum(not node.attrs.get("data-source-pointer") for node in warning_nodes),
    }
    audit.add(
        "JSON",
        "JSON-VISIBLE-POINTERS-023",
        bool(status_badges) and not any(coverage_errors.values()),
        "visible status badges, formula code nodes and warning blocks carry their own canonical source pointer",
        bundle=bundle.name,
        evidence={
            "status_badges": len(status_badges),
            "formula_nodes": len(formula_nodes),
            "warning_nodes": len(warning_nodes),
            **coverage_errors,
        },
    )


def audit_report_contract_content(bundle: Bundle, audit: Audit) -> None:
    report = bundle.report
    html_text = bundle.html_text
    repetitions = json_get(report, "validation.repetitions", 0)
    expected_reason_codes = sorted({
        json_get(report, "decision.code"),
        *(item.get("code") for item in report.get("warnings", [])),
        *(item.get("code") for item in report.get("failures", [])),
        *([json_get(report, "validation.procedures.p2.reason")] if json_get(report, "validation.procedures.p2.reason") else []),
    })
    actual_reason_codes = json_get(report, "decision.related_reason_codes", [])
    json_checks = {
        "reason_codes": actual_reason_codes == expected_reason_codes,
        "invalid_reason_counts": isinstance(json_get(report, "input.invalid_reason_counts"), dict),
        "preprocessing_transforms": isinstance(json_get(report, "preprocessing.affine_transforms"), dict),
        "split_hash_count": len(json_get(report, "validation.split_id_hashes", [])) == repetitions,
        "repetition_distribution_count": len(json_get(report, "validation.split_sensitivity.repetition_distribution", [])) == repetitions,
        "policy_hash_reconciles": json_get(report, "validation.policy_sha256") == json_get(report, "provenance.policy_sha256") == json_get(report, "validation.p2_stability.policy_sha256"),
        "provenance_export": strict_json(bundle.path / "provenance.json") == json_get(report, "provenance"),
    }
    required_human_labels = (
        "analysis_status:", "recommendation_status:", "decision_state:",
        "Связанные reason codes:", "Invalid/missing/non-finite",
        "Exact duplicate records:", "Direction/family stability", "Repetition-level uplift distribution",
        "Hashes split IDs", "Dominant canonical pair", "central-80%", "policy SHA-256",
        "dependency-lock", "numerical", "work=", "Media type", "Size, bytes", "SHA-256",
    )
    html_checks = {
        label: label in html_text
        for label in required_human_labels
    }
    html_checks["model domain state"] = "Model domain:" in html_text or "Model domain/extrapolation:" in html_text
    audit.add(
        "JSON",
        "JSON-REPORT-CONTRACT-025",
        all(json_checks.values()) and all(html_checks.values()),
        "mandatory decision, data, validation, stability and reproducibility content is canonical and visibly rendered",
        bundle=bundle.name,
        evidence={"json_checks": json_checks, "html_checks": html_checks},
    )


def audit_provenance_hashes(bundle: Bundle, audit: Audit) -> None:
    report = bundle.report
    provenance = json_get(report, "provenance", {})
    payloads = provenance.get("hash_payloads", {}) if isinstance(provenance, dict) else {}
    errors: list[str] = []
    expected_input = {
        "logical_adapter_version": "prototype-synthetic-input-v1",
        "x_unit": json_get(report, "input.x_unit"),
        "y_unit": json_get(report, "input.y_unit"),
        "rows": [
            {
                "row_id": row.get("row_id"),
                "source_row_id": row.get("source_row_id"),
                "x": row.get("x"),
                "y": row.get("y"),
            }
            for row in report.get("observations", [])
        ],
        "excluded": json_get(report, "input.exclusions", []),
    }
    if not deep_close(payloads.get("input"), expected_input):
        errors.append("input_payload")
    expected_policy = {
        "policy_version": json_get(provenance, "policy_version"),
        "r2_warning_boundary": 0.60,
        "segment_share_bounds": [0.4, 0.6],
        "decision_gates": [
            {"id": item.get("id"), "threshold": item.get("threshold")}
            for item in json_get(report, "validation.decision_gates", [])
        ],
    }
    if not deep_close(payloads.get("policy"), expected_policy):
        errors.append("policy_payload")
    registry = payloads.get("registry")
    authoritative_core_family_ids = [
        "constant_v1", "poly1_v1", "poly2_v1", "poly3_v1",
        "exp_affine_v1", "log_shift_v1", "reciprocal_shift_pos_v1",
        "logistic_v1",
    ]
    if not (
        isinstance(registry, dict)
        and registry.get("registry_version") == json_get(provenance, "registry_version")
        and registry.get("artifact_scope") == "prototype_exercised_subset"
        and registry.get("authoritative_core_family_ids") == authoritative_core_family_ids
        and registry.get("included_family_ids") == ["constant_v1", "poly1_v1"]
        and registry.get("unexercised_family_ids") == authoritative_core_family_ids[2:]
        and registry.get("maximum_polynomial_degree") == 3
        and registry.get("incubator_excluded") is True
        and set(registry.get("families", {})) == {"constant_v1", "poly1_v1"}
    ):
        errors.append("registry_payload")
    environment = provenance.get("environment")
    dependency = payloads.get("dependency_manifest")
    if not (
        isinstance(environment, dict)
        and environment.get("python_implementation") == "CPython"
        and environment.get("python_version") == sys.version.split()[0]
        and environment.get("platform") == sys.platform
        and environment.get("thread_policy") == provenance.get("thread_policy")
        and isinstance(dependency, dict)
        and dependency.get("python_version") == sys.version.split()[0]
        and dependency.get("third_party_runtime_dependencies") == []
    ):
        errors.append("environment_or_dependency_payload")

    source_manifest = payloads.get("source_manifest", {})
    request_metadata = payloads.get("request_metadata")
    if not (
        isinstance(request_metadata, dict)
        and request_metadata.get("request_schema_version") == "prototype-synthetic-request-v1"
        and request_metadata.get("logical_adapter_version") == "prototype-synthetic-input-v1"
        and isinstance(request_metadata.get("result_bearing_scenario"), dict)
    ):
        errors.append("request_metadata_payload")
    repo_root = HERE.parents[2]
    source_errors: list[str] = []
    for item in source_manifest.get("files", []) if isinstance(source_manifest, dict) else []:
        relative = item.get("path")
        candidate = (repo_root / str(relative)).resolve()
        try:
            candidate.relative_to(repo_root.resolve())
        except ValueError:
            source_errors.append(f"escape:{relative}")
            continue
        if not candidate.is_file() or sha256_file(candidate) != item.get("sha256"):
            source_errors.append(str(relative))
    if len(source_manifest.get("files", [])) != 4 or source_errors:
        errors.append("source_manifest")

    expected_hashes = {
        "input_sha256": canonical_sha256(payloads.get("input")),
        "request_sha256": canonical_sha256(request_metadata),
        "policy_sha256": canonical_sha256(payloads.get("policy")),
        "registry_sha256": canonical_sha256(payloads.get("registry")),
        "source_sha256": canonical_sha256(payloads.get("source_manifest")),
        "dependency_lock_sha256": canonical_sha256(payloads.get("dependency_manifest")),
        "execution_environment_id": canonical_sha256(environment),
    }
    for name, value in expected_hashes.items():
        if provenance.get(name) != value:
            errors.append(name)
    if json_get(report, "input.input_sha256") != expected_hashes["input_sha256"]:
        errors.append("input_hash_alias")
    expected_analysis = canonical_sha256({
        "input_sha256": expected_hashes["input_sha256"],
        "request_sha256": expected_hashes["request_sha256"],
        "policy_sha256": expected_hashes["policy_sha256"],
        "registry_sha256": expected_hashes["registry_sha256"],
        "source_sha256": expected_hashes["source_sha256"],
        "dependency_lock_sha256": expected_hashes["dependency_lock_sha256"],
        "base_seed": provenance.get("base_seed"),
    })
    if provenance.get("analysis_id") != expected_analysis:
        errors.append("analysis_id")
    artifact_expectations = {
        "provenance/request-metadata.json": request_metadata,
        "provenance/resolved-policy.json": payloads.get("policy"),
        "provenance/registry.json": payloads.get("registry"),
        "provenance/source-manifest.json": payloads.get("source_manifest"),
        "provenance/dependency-manifest.json": payloads.get("dependency_manifest"),
        "provenance/environment.json": environment,
    }
    for relative, expected in artifact_expectations.items():
        if strict_json(bundle.path / relative) != expected:
            errors.append(f"artifact:{relative}")
    audit.add(
        "JSON",
        "JSON-PROVENANCE-HASHES-026",
        not errors,
        "input/request/policy/registry/source/dependency/environment/analysis hashes recompute from retained canonical payloads and source files",
        bundle=bundle.name,
        evidence={"errors": errors, "source_errors": source_errors, "expected_hashes": expected_hashes},
    )


def audit_manifest_and_artifacts(bundle: Bundle, audit: Audit) -> None:
    manifest_path = bundle.path / "prototype-manifest.json"
    manifest = strict_json(manifest_path)
    manifest_entries = manifest.get("files", []) if isinstance(manifest, dict) else []
    manifest_by_path = {
        item.get("path"): item
        for item in manifest_entries
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    actual_manifest_paths = {
        str(path.relative_to(bundle.path))
        for path in bundle.path.rglob("*")
        if path.is_file() and path.name != "prototype-manifest.json"
    }
    manifest_errors: list[str] = []
    if set(manifest_by_path) != actual_manifest_paths:
        manifest_errors.append("path_set")
    for relative, item in manifest_by_path.items():
        candidate = (bundle.path / relative).resolve()
        try:
            candidate.relative_to(bundle.path.resolve())
        except ValueError:
            manifest_errors.append(f"path_escape:{relative}")
            continue
        if not candidate.is_file():
            manifest_errors.append(f"missing:{relative}")
            continue
        if (
            item.get("media_type") != expected_media_type(candidate)
            or candidate.stat().st_size != item.get("size")
            or sha256_file(candidate) != item.get("sha256")
        ):
            manifest_errors.append(f"digest:{relative}")
    audit.add(
        "SECURITY-STATIC",
        "SECURITY-MANIFEST-030",
        manifest.get("prototype_only") is True and not manifest_errors,
        "prototype manifest contains every bundle file including report.json/report.html with safe paths, sizes and hashes",
        bundle=bundle.name,
        evidence={"entry_count": len(manifest_by_path), "actual_count": len(actual_manifest_paths), "errors": manifest_errors},
    )

    report_entries = bundle.report.get("artifacts", [])
    report_by_path = {
        item.get("path"): item
        for item in report_entries
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    actual_report_paths = {
        str(path.relative_to(bundle.path))
        for path in bundle.path.rglob("*")
        if path.is_file() and path.name not in {"report.json", "report.html", "prototype-manifest.json"}
    }
    report_errors: list[str] = []
    if set(report_by_path) != actual_report_paths:
        report_errors.append("path_set")
    for relative, item in report_by_path.items():
        candidate = bundle.path / relative
        if (
            not candidate.is_file()
            or item.get("media_type") != expected_media_type(candidate)
            or candidate.stat().st_size != item.get("size")
            or sha256_file(candidate) != item.get("sha256")
        ):
            report_errors.append(relative)
    audit.add(
        "JSON",
        "JSON-ARTIFACTS-021",
        not report_errors,
        "canonical report artifact inventory reconciles every non-circular bundle artifact",
        bundle=bundle.name,
        evidence={"entry_count": len(report_by_path), "actual_count": len(actual_report_paths), "errors": report_errors},
    )
    inventory_headers = all(
        label in bundle.html_text for label in ("Relative path", "Media type", "Size, bytes", "SHA-256")
    )
    inventory_values = all(
        str(item.get(key)) in bundle.html_text
        for item in report_entries
        for key in ("path", "media_type", "size", "sha256")
    )
    required_links = all(
        f'href="{path}"' in bundle.html_text
        for path in ("report.json", "report.schema.json", "provenance.json", "prototype-manifest.json")
    )
    audit.add(
        "JSON",
        "JSON-ARTIFACT-HTML-024",
        inventory_headers and inventory_values and required_links and 'href="None"' not in bundle.html_text,
        "human reproducibility section exposes path, media type, size, SHA-256 and required report/schema/provenance/manifest links",
        bundle=bundle.name,
        evidence={
            "inventory_headers": inventory_headers,
            "inventory_values": inventory_values,
            "required_links": required_links,
        },
    )

    permission_errors: list[str] = []
    for path in [bundle.path, *(item for item in bundle.path.rglob("*") if item.is_dir())]:
        if path.stat().st_mode & 0o777 != 0o700:
            permission_errors.append(f"dir:{path.relative_to(bundle.path)}")
    for path in (item for item in bundle.path.rglob("*") if item.is_file()):
        if path.stat().st_mode & 0o777 != 0o600:
            permission_errors.append(f"file:{path.relative_to(bundle.path)}")
    audit.add(
        "SECURITY-STATIC",
        "SECURITY-PERMISSIONS-031",
        not permission_errors,
        "prototype bundle directories/files are explicitly restricted to 0700/0600",
        bundle=bundle.name,
        evidence={"errors": permission_errors},
    )


def audit_model_preview(bundle: Bundle, audit: Audit) -> None:
    role = json_get(bundle.report, "recommendation.role")
    model_files = sorted((bundle.path / "models").glob("*.json"))
    errors: list[str] = []
    dossier_specs = {
        "p1": (bundle.path / "models" / "one-model.json", "one", "models/one-model.json"),
        "p2": (bundle.path / "models" / "two-segment-model.json", "two", "models/two-segment-model.json"),
    }
    dossier_paths: dict[Path, tuple[str, str]] = {}
    for refit_key, (path, dossier_role, relative) in dossier_specs.items():
        certified = json_get(bundle.report, f"refit.{refit_key}.status") == "certified"
        reported_path = json_get(bundle.report, f"refit.{refit_key}.model_path")
        if certified:
            dossier_paths[path] = (dossier_role, refit_key)
            if reported_path != relative:
                errors.append(f"model_path:{refit_key}")
        else:
            if reported_path is not None:
                errors.append(f"unavailable_model_path:{refit_key}")
            if path.exists():
                errors.append(f"forbidden_dossier:{dossier_role}")
    preview_path = (
        bundle.path / str(json_get(bundle.report, "recommendation.model_preview_path"))
        if role is not None
        else None
    )
    expected_files = set(dossier_paths) | ({preview_path} if preview_path is not None else set())
    if set(model_files) != expected_files:
        errors.append("model_path_set")
    for path, (dossier_role, refit_key) in dossier_paths.items():
        if not path.is_file():
            errors.append(f"missing_dossier:{dossier_role}")
            continue
        dossier = strict_json(path)
        expected_dossier = {
            "model_schema_version": "prototype-final-refit-dossier-1.0.0",
            "prototype_only": True,
            "usable_for_prediction": False,
            "notice": "SYNTHETIC FINAL-REFIT DOSSIER; not recommended-model.json",
            "report_id": bundle.report["report_id"],
            "role": dossier_role,
            "registry_version": json_get(bundle.report, f"refit.{refit_key}.registry_version"),
            "refit": json_get(bundle.report, f"refit.{refit_key}"),
            "extrapolation": "forbidden",
        }
        if not deep_close(dossier, expected_dossier):
            errors.append(f"dossier_reconciliation:{dossier_role}")
    if role is None:
        if json_get(bundle.report, "recommendation.model_preview_path") is not None:
            errors.append("preview_path_without_recommendation")
    elif preview_path is not None and preview_path.is_file():
        preview = strict_json(preview_path)
        refit = json_get(bundle.report, "refit.p2") if role == "two" else json_get(bundle.report, "refit.p1")
        for key, expected in (
            ("formula_display", json_get(bundle.report, "recommendation.formula_display")),
            ("domain", json_get(bundle.report, "recommendation.domain")),
            ("model_structure_hash", refit.get("model_structure_hash")),
            ("model_instance_hash", refit.get("model_instance_hash")),
            ("direction", refit.get("direction")),
        ):
            if preview.get(key) != expected:
                errors.append(key)
        if preview.get("prototype_only") is not True or preview.get("usable_for_prediction") is not False:
            errors.append("prototype_boundary")
        plot_rows = read_csv_rows(bundle.path / "plot-data.csv")
        expected_grid: dict[str, list[list[float]]] = {}
        selected = {"p1"} if role == "one" else {"p2_left", "p2_right"}
        for row in plot_rows:
            if row.get("series") in selected:
                expected_grid.setdefault(row["series"], []).append([float(row["x"]), float(row["y"])])
        actual_grid = preview.get("prediction_grid")
        if not isinstance(actual_grid, dict) or set(actual_grid) != set(expected_grid):
            errors.append("prediction_grid_series")
        else:
            for series, expected_values in expected_grid.items():
                actual_values = actual_grid.get(series)
                if len(actual_values) != len(expected_values) or any(
                    not close_or_equal(actual_pair[0], expected_pair[0]) or not close_or_equal(actual_pair[1], expected_pair[1])
                    for actual_pair, expected_pair in zip(actual_values, expected_values)
                ):
                    errors.append(f"prediction_grid:{series}")
    audit.add(
        "JSON",
        "JSON-MODEL-PREVIEW-022",
        not errors,
        "P1/P2 final-refit dossiers and conditional non-usable recommendation preview reconcile typed refits and plotted prediction grid",
        bundle=bundle.name,
        evidence={"role": role, "model_files": [path.name for path in model_files], "errors": errors},
    )


def audit_refit_dossiers(bundle: Bundle, audit: Audit) -> None:
    """Recompute the contract-bearing P1/P2 dossier without executing formula text."""

    report = bundle.report
    observations = report.get("observations", [])
    domain = json_get(report, "input.domain", [])
    errors: list[str] = []
    retained_registry = strict_json(bundle.path / "provenance" / "registry.json")
    registry_families = retained_registry.get("families", {}) if isinstance(retained_registry, dict) else {}
    checked_registry_asts: set[str] = set()
    if not (
        isinstance(domain, list)
        and len(domain) == 2
        and all(finite_number(value) for value in domain)
        and float(domain[1]) > float(domain[0])
    ):
        errors.append("input.domain")
        domain = [0.0, 1.0]
    lower, upper = map(float, domain)
    span = upper - lower
    y_values = [float(row["y"]) for row in observations]
    y_center = statistics.fmean(y_values) if y_values else 0.0
    y_scale = max([abs(value - y_center) for value in y_values] + [1.0])

    def expected_transform(offset: float, scale: float) -> dict[str, Any]:
        return {
            "kind": "affine_unit_interval",
            "offset": offset,
            "scale": scale,
            "target_interval": [0.0, 1.0],
            "invertible": True,
        }

    expected_y_transform = {
        "kind": "affine_center_scale",
        "offset": y_center,
        "scale": y_scale,
        "target_interval": None,
        "invertible": True,
    }

    def error_metrics(selected: list[dict[str, Any]], prediction_field: str) -> tuple[float | None, float | None]:
        if not selected or any(row.get(prediction_field) is None for row in selected):
            return None, None
        residuals = [float(row["y"]) - float(row[prediction_field]) for row in selected]
        return (
            math.sqrt(statistics.fmean(value * value for value in residuals)),
            statistics.fmean(abs(value) for value in residuals),
        )

    def expected_ast(family_id: str) -> dict[str, Any]:
        oracle = {
            "ast_id": f"registry_v1:{family_id}",
            "root": "parameter" if family_id == "constant_v1" else "add",
            "prefix": ["parameter:a"] if family_id == "constant_v1" else [
                "add", "parameter:a", "multiply", "parameter:b", "variable:t",
            ],
            "node_count": 1 if family_id == "constant_v1" else 5,
        }
        retained_family = registry_families.get(family_id, {}) if isinstance(registry_families, dict) else {}
        retained_ast = retained_family.get("canonical_ast") if isinstance(retained_family, dict) else None
        if family_id not in checked_registry_asts:
            checked_registry_asts.add(family_id)
            if not deep_close(retained_ast, oracle):
                errors.append(f"registry.{family_id}.canonical_ast")
        return oracle

    def check_certificate(
        certificate: Any,
        *,
        family_id: str,
        interval: list[float],
        derivative: float,
        direction: str,
        prefix: str,
    ) -> None:
        expected = {
            "status": "PASS",
            "reason": None,
            "method": "analytic_family_specific",
            "family_id": family_id,
            "polynomial_degree_after_simplification": 0 if family_id == "constant_v1" else 1,
            "interval": interval,
            "direction": direction,
            "analytic_derivative": repr(float(derivative)),
            "critical_points": [],
            "minimum_signed_derivative": derivative,
            "domain_margin": 1.0,
            "finite_function": True,
            "finite_derivative": True,
        }
        normalized_certificate = copy.deepcopy(certificate)
        try:
            analytic_derivative = float(normalized_certificate.get("analytic_derivative"))
            if not close_or_equal(analytic_derivative, derivative):
                raise ValueError("analytic derivative mismatch")
            normalized_certificate["analytic_derivative"] = repr(float(derivative))
        except Exception:
            pass
        if not deep_close(normalized_certificate, expected):
            errors.append(f"{prefix}.derivative_certificate")

    p1 = json_get(report, "refit.p1", {})
    if p1.get("status") == "certified":
        predictions = [float(row["pred_one_refit"]) for row in observations]
        p1_parameters = p1.get("parameters", {})
        if not (
            isinstance(p1_parameters, dict)
            and finite_number(p1_parameters.get("a"))
            and finite_number(p1_parameters.get("b"))
        ):
            errors.append("p1.parameters")
            p1_parameters = {"a": 0.0, "b": 0.0}
        a = float(p1_parameters["a"])
        b = float(p1_parameters["b"])
        expected_family = (
            "constant_v1"
            if abs(b) <= 1e-12
            else "poly1_v1"
        )
        slope = b / span
        if any(
            not close_or_equal(
                prediction,
                a + b * ((float(row["x"]) - lower) / span),
            )
            for row, prediction in zip(observations, predictions)
        ):
            errors.append("p1.predictions_from_typed_parameters")
        expected_formula = (
            f"f(x) = {repr(float(a))}"
            if expected_family == "constant_v1"
            else (
                f"f(x) = {repr(float(a))} + {repr(float(b))} * "
                f"((x - {repr(lower)}) / {repr(span)})"
            )
        )
        expected_p1 = {
            "registry_version": "registry_v1",
            "registry_role": "core",
            "incubator_excluded": True,
            "family_id": expected_family,
            "canonical_ast_id": f"registry_v1:{expected_family}",
            "canonical_ast": expected_ast(expected_family),
            "formula_display": expected_formula,
            "transforms": {
                "x": expected_transform(lower, span),
                "y": expected_y_transform,
            },
            "parameters": p1_parameters,
            "domain": [lower, upper],
            "direction": "flat" if expected_family == "constant_v1" else "nondecreasing",
            "certificate": "PASS",
            "model_path": "models/one-model.json",
            "n": len(observations),
            "n_unique_x": len({float(row["x"]) for row in observations}),
        }
        for key, expected in expected_p1.items():
            if not deep_close(p1.get(key), expected):
                errors.append(f"p1.{key}")
        p1_structure_payload = {
            "model_schema_version": "prototype-final-refit-dossier-1.0.0",
            "registry_version": "registry_v1",
            "canonical_ast": expected_ast(expected_family),
            "family_id": expected_family,
            "direction": "flat" if expected_family == "constant_v1" else "nondecreasing",
            "segment_count": 1,
            "domain": [lower, upper],
        }
        p1_instance_payload = {
            "structure": p1_structure_payload,
            "parameters": p1_parameters,
            "transforms": expected_p1["transforms"],
        }
        if p1.get("model_structure_hash") != canonical_sha256(p1_structure_payload):
            errors.append("p1.model_structure_hash")
        if p1.get("model_instance_hash") != canonical_sha256(p1_instance_payload):
            errors.append("p1.model_instance_hash")
        expected_statuses = {
            "identifiability_status": "CANONICAL_IDENTIFIED" if expected_family == "constant_v1" else "IDENTIFIED",
            "bound_status": "NO_ACTIVE_BOUNDS",
            "collapse_status": "CANONICAL_CONSTANT" if expected_family == "constant_v1" else "NO_COLLAPSE",
            "solver_status": "SUCCEEDED",
        }
        for key, expected in expected_statuses.items():
            if p1.get(key) != expected:
                errors.append(f"p1.{key}")
        check_certificate(
            p1.get("derivative_certificate"),
            family_id=expected_family,
            interval=[lower, upper],
            derivative=0.0 if expected_family == "constant_v1" else slope,
            direction="flat" if expected_family == "constant_v1" else "nondecreasing",
            prefix="p1",
        )
        expected_rmse, expected_mae = error_metrics(observations, "pred_one_refit")
        if not close_or_equal(reported_metric(report, "refit.p1.rmse_fit_all"), expected_rmse):
            errors.append("p1.rmse_fit_all")
        if not close_or_equal(reported_metric(report, "refit.p1.mae_fit_all"), expected_mae):
            errors.append("p1.mae_fit_all")
    else:
        for key in (
            "family_id", "canonical_ast_id", "canonical_ast", "formula_display",
            "transforms", "parameters", "domain", "model_structure_hash",
            "model_instance_hash", "n", "n_unique_x",
            "model_path",
        ):
            if p1.get(key) is not None:
                errors.append(f"p1.failed.{key}")

    p2 = json_get(report, "refit.p2", {})
    if p2.get("status") == "certified":
        p2_parameters = p2.get("parameters", {})
        if not (
            isinstance(p2_parameters, dict)
            and all(finite_number(p2_parameters.get(key)) for key in ("c", "mu", "q_left", "q_right"))
        ):
            errors.append("p2.parameters")
            p2_parameters = {"c": (lower + upper) / 2, "mu": 0.0, "q_left": 0.0, "q_right": 0.0}
        c = float(p2_parameters["c"])
        mu = float(p2_parameters["mu"])
        q_left = float(p2_parameters["q_left"])
        q_right = float(p2_parameters["q_right"])
        if not lower < c < upper:
            errors.append("p2.boundary_domain")
            c = (lower + upper) / 2.0
        if not close_or_equal(p2.get("boundary"), c) or not close_or_equal(p2.get("shared_mu"), mu):
            errors.append("p2.parameter_aliases")
        left = [row for row in observations if float(row["x"]) <= c]
        right = [row for row in observations if float(row["x"]) > c]
        expected_parameters = {"c": c, "mu": mu, "q_left": q_left, "q_right": q_right}
        if not deep_close(p2.get("parameters"), expected_parameters):
            errors.append("p2.parameters")
        expected_predictions = {
            row["row_id"]: (
                mu + q_left * (((float(row["x"]) - lower) / (c - lower)) - 1.0)
                if float(row["x"]) <= c
                else mu + q_right * ((float(row["x"]) - c) / (upper - c))
            )
            for row in observations
        }
        if any(
            not close_or_equal(row.get("pred_two_refit"), expected_predictions[row["row_id"]])
            for row in observations
        ):
            errors.append("p2.predictions_from_typed_parameters")
        expected_left_formula = (
            f"F(x) = {repr(mu)} + {repr(q_left)} * "
            f"(((x - {repr(lower)}) / {repr(c - lower)}) - 1.0)"
        )
        expected_right_formula = (
            f"F(x) = {repr(mu)} + {repr(q_right)} * "
            f"((x - {repr(c)}) / {repr(upper - c)})"
        )
        expected_pair_fields = {
            "registry_version": "registry_v1",
            "registry_role": "core",
            "incubator_excluded": True,
            "family_left": "poly1_v1",
            "family_right": "poly1_v1",
            "ordered_family_pair": "poly1_v1|poly1_v1",
            "canonical_ast_left": expected_ast("poly1_v1"),
            "canonical_ast_right": expected_ast("poly1_v1"),
            "formula_left": expected_left_formula,
            "formula_right": expected_right_formula,
            "transforms": {
                "left_x": expected_transform(lower, c - lower),
                "right_x": expected_transform(c, upper - c),
                "y": expected_y_transform,
            },
            "membership": "x<=c:left;x>c:right",
            "domain": [lower, upper],
            "direction": "nondecreasing",
            "continuity_residual": 0.0,
            "certificate": "PASS",
            "model_path": "models/two-segment-model.json",
            "identifiability_status": "IDENTIFIED",
            "bound_status": "NO_ACTIVE_BOUNDS",
            "collapse_status": "NO_COLLAPSE",
            "solver_status": "SUCCEEDED",
            "stability_status": "STABLE",
            "n": len(observations),
            "n_unique_x": len({float(row["x"]) for row in observations}),
        }
        for key, expected in expected_pair_fields.items():
            if not deep_close(p2.get(key), expected):
                errors.append(f"p2.{key}")
        expected_cells: list[dict[str, Any]] = []
        unique_x = sorted({float(row["x"]) for row in observations})
        for raw_index, (cell_lower, cell_upper) in enumerate(zip(unique_x, unique_x[1:]), 1):
            left_n = sum(float(row["x"]) <= cell_lower for row in observations)
            if math.ceil(0.4 * len(observations)) <= left_n <= math.floor(0.6 * len(observations)):
                expected_cells.append({
                    "cell_id": f"raw-cell-{raw_index:03d}",
                    "lower_x": cell_lower,
                    "upper_x_exclusive": cell_upper,
                    "left_n": left_n,
                    "right_n": len(observations) - left_n,
                    "left_share": left_n / len(observations),
                    "right_share": (len(observations) - left_n) / len(observations),
                    "ties_atomic": True,
                })
        expected_cell = next(
            (cell for cell in expected_cells if cell["lower_x"] <= c < cell["upper_x_exclusive"]),
            None,
        )
        if not deep_close(p2.get("membership_cell"), expected_cell):
            errors.append("p2.membership_cell")
        p2_structure_payload = {
            "model_schema_version": "prototype-final-refit-dossier-1.0.0",
            "registry_version": "registry_v1",
            "ordered_family_pair": "poly1_v1|poly1_v1",
            "canonical_ast_left": expected_ast("poly1_v1"),
            "canonical_ast_right": expected_ast("poly1_v1"),
            "direction": "nondecreasing",
            "segment_count": 2,
            "domain": [lower, upper],
            "membership_cell": expected_cell,
            "membership": "x<=c:left;x>c:right",
        }
        p2_instance_payload = {
            "structure": p2_structure_payload,
            "parameters": expected_parameters,
            "transforms": expected_pair_fields["transforms"],
        }
        if p2.get("model_structure_hash") != canonical_sha256(p2_structure_payload):
            errors.append("p2.model_structure_hash")
        if p2.get("model_instance_hash") != canonical_sha256(p2_instance_payload):
            errors.append("p2.model_instance_hash")
        check_certificate(
            p2.get("derivative_certificate_left"),
            family_id="poly1_v1", interval=[lower, c], derivative=q_left / (c - lower),
            direction="nondecreasing", prefix="p2.left",
        )
        check_certificate(
            p2.get("derivative_certificate_right"),
            family_id="poly1_v1", interval=[c, upper], derivative=q_right / (upper - c),
            direction="nondecreasing", prefix="p2.right",
        )
        for name, selected in (("left", left), ("right", right)):
            segment = p2.get(name, {})
            expected_rmse, expected_mae = error_metrics(selected, "pred_two_refit")
            expected_span = [
                min(float(row["x"]) for row in selected),
                max(float(row["x"]) for row in selected),
            ]
            expected_values = {
                "n": len(selected),
                "share": len(selected) / len(observations),
                "n_unique_x": len({float(row["x"]) for row in selected}),
                "span": expected_span,
            }
            for key, expected in expected_values.items():
                if not deep_close(segment.get(key), expected):
                    errors.append(f"p2.{name}.{key}")
            if not close_or_equal(reported_metric(report, f"refit.p2.{name}.rmse_fit"), expected_rmse):
                errors.append(f"p2.{name}.rmse_fit")
            if not close_or_equal(reported_metric(report, f"refit.p2.{name}.mae_fit"), expected_mae):
                errors.append(f"p2.{name}.mae_fit")
        expected_rmse, expected_mae = error_metrics(observations, "pred_two_refit")
        if not close_or_equal(reported_metric(report, "refit.p2.rmse_fit_all"), expected_rmse):
            errors.append("p2.rmse_fit_all")
        if not close_or_equal(reported_metric(report, "refit.p2.mae_fit_all"), expected_mae):
            errors.append("p2.mae_fit_all")
    else:
        for key in (
            "family_left", "family_right", "ordered_family_pair",
            "canonical_ast_left", "canonical_ast_right", "formula_left",
            "formula_right", "transforms", "parameters", "boundary",
            "shared_mu", "membership", "membership_cell", "domain",
            "model_structure_hash", "model_instance_hash", "model_path", "n", "n_unique_x",
        ):
            if p2.get(key) is not None:
                errors.append(f"p2.unavailable.{key}")

    for model_key, refit in (("p1", p1), ("p2", p2)):
        model_path = refit.get("model_path")
        if model_path is not None:
            candidate = (bundle.path / model_path).resolve()
            try:
                candidate.relative_to(bundle.path.resolve())
            except ValueError:
                errors.append(f"{model_key}.model_path_escape")
            else:
                if not candidate.is_file():
                    errors.append(f"{model_key}.model_path_missing")

    audit.add(
        "JSON",
        "JSON-REFIT-028",
        not errors,
        "typed P1/P2 refit dossiers reconcile the retained registry-subset ASTs, transforms, parameters, derivative certificates, statuses, counts and descriptive errors",
        bundle=bundle.name,
        evidence={
            "errors": errors,
            "registry_scope": retained_registry.get("artifact_scope") if isinstance(retained_registry, dict) else None,
            "checked_registry_asts": sorted(checked_registry_asts),
            "p1_status": p1.get("status"),
            "p2_status": p2.get("status"),
        },
    )


def audit_optimizer_trace(bundle: Bundle, audit: Audit) -> None:
    optimizer = json_get(bundle.report, "refit.p2.optimizer", {})
    observations = bundle.report.get("observations", [])
    errors: list[str] = []
    unique_x = sorted({float(row["x"]) for row in observations})
    cells: list[dict[str, Any]] = []
    for raw_index, (lower, upper) in enumerate(zip(unique_x, unique_x[1:]), 1):
        left_n = sum(float(row["x"]) <= lower for row in observations)
        if math.ceil(0.4 * len(observations)) <= left_n <= math.floor(0.6 * len(observations)):
            cells.append({
                "cell_id": f"raw-cell-{raw_index:03d}",
                "lower_x": lower,
                "upper_x_exclusive": upper,
                "left_n": left_n,
                "right_n": len(observations) - left_n,
                "left_share": left_n / len(observations),
                "right_share": (len(observations) - left_n) / len(observations),
                "ties_atomic": True,
            })

    trace_relative = optimizer.get("trace_path")
    trace_path = bundle.path / str(trace_relative)
    records: list[dict[str, Any]] = []
    raw_header = b""
    if trace_relative != "trace/fit-attempts.jsonl.gz" or not trace_path.is_file():
        errors.append("trace_path")
    else:
        raw_header = trace_path.read_bytes()[:10]
        if (
            len(raw_header) < 10
            or raw_header[:2] != b"\x1f\x8b"
            or raw_header[3] & 0x08
            or raw_header[4:8] != b"\x00\x00\x00\x00"
        ):
            errors.append("gzip_header_not_deterministic")
        try:
            with gzip.open(trace_path, "rb") as handle:
                payload = handle.read(5_000_001)
            if len(payload) > 5_000_000:
                errors.append("trace_uncompressed_size")
            else:
                for line_number, line in enumerate(payload.splitlines(), 1):
                    try:
                        record = json.loads(
                            line.decode("utf-8", errors="strict"),
                            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
                        )
                        if not isinstance(record, dict):
                            raise ValueError("record is not an object")
                        records.append(record)
                    except Exception as exc:
                        errors.append(f"trace_line:{line_number}:{type(exc).__name__}")
        except Exception as exc:
            errors.append(f"trace_read:{type(exc).__name__}")

    p2_status = json_get(bundle.report, "refit.p2.status")
    p2_reason = json_get(bundle.report, "refit.p2.reason")
    expected_status = (
        "SUCCEEDED" if p2_status == "certified"
        else "COLLAPSED" if p2_reason == "COLLAPSED_SEGMENTS"
        else "NO_FEASIBLE_CELL" if p2_reason == "NO_BALANCED_SPLIT"
        else "FAILED" if p2_reason == "OPTIMIZER_FAILURE"
        else "NOT_RUN"
    )
    attempts_run = expected_status in {"SUCCEEDED", "COLLAPSED", "FAILED"}
    starts_per_cell = 2 if attempts_run and cells else 0
    expected_attempt_count = len(cells) * starts_per_cell
    successful = [record for record in records if record.get("solver_status") == "SUCCEEDED"]
    failed = [record for record in records if record.get("solver_status") == "FAILED"]
    selected = [record for record in records if record.get("selected") is True]
    expected_summary = {
        "strategy": "PROFILE_CELLS",
        "scope": "full_data_p2_refit",
        "status": expected_status,
        "trace_path": "trace/fit-attempts.jsonl.gz",
        "tie_safe_cell_count": len(cells),
        "eligible_cell_count": len(cells),
        "starts_per_cell": starts_per_cell,
        "start_count": expected_attempt_count,
        "attempt_count": expected_attempt_count,
        "trace_record_count": len(records),
        "successful_attempt_count": len(successful),
        "failed_attempt_count": len(failed),
        "evaluation_count": sum(record.get("evaluations", 0) for record in records),
        "competing_basin_count": sum(record.get("competing_basin") is True for record in records),
    }
    for key, expected in expected_summary.items():
        if optimizer.get(key) != expected:
            errors.append(f"summary.{key}")
    if optimizer.get("reason") != (None if expected_status == "SUCCEEDED" else p2_reason):
        errors.append("summary.reason")
    expected_certificate = "PASS" if expected_status in {"SUCCEEDED", "COLLAPSED"} else "NOT_RUN"
    if optimizer.get("certificate_result") != expected_certificate:
        errors.append("summary.certificate_result")
    if optimizer.get("competing_basin_status") != ("NONE" if successful else "UNAVAILABLE"):
        errors.append("summary.competing_basin_status")
    if len(records) != expected_attempt_count or len({record.get("attempt_id") for record in records}) != len(records):
        errors.append("trace.attempt_identity")
    expected_record_keys = {
        "trace_version", "attempt_id", "strategy", "scope", "cell_id",
        "cell_lower_x", "cell_upper_x_exclusive", "left_n", "right_n",
        "left_share", "right_share", "ties_atomic", "family_left",
        "family_right", "direction", "start_id", "status", "solver_status",
        "objective_sse_scaled_y", "evaluations", "active_bounds", "optimality",
        "certificate_result", "selected", "competing_basin", "reason_codes",
        "warning_codes",
    }
    cell_by_id = {cell["cell_id"]: cell for cell in cells}
    for index, record in enumerate(records):
        if set(record) != expected_record_keys:
            errors.append(f"trace.{index}.keys")
            continue
        cell = cell_by_id.get(record.get("cell_id"))
        if cell is None or any(
            not deep_close(record.get(record_key), cell[cell_key])
            for record_key, cell_key in (
                ("cell_lower_x", "lower_x"),
                ("cell_upper_x_exclusive", "upper_x_exclusive"),
                ("left_n", "left_n"), ("right_n", "right_n"),
                ("left_share", "left_share"), ("right_share", "right_share"),
                ("ties_atomic", "ties_atomic"),
            )
        ):
            errors.append(f"trace.{index}.cell")
        if record.get("strategy") != "PROFILE_CELLS" or record.get("scope") != "full_data_p2_refit":
            errors.append(f"trace.{index}.strategy")
        if record.get("family_left") != "poly1_v1" or record.get("family_right") != "poly1_v1":
            errors.append(f"trace.{index}.family")
        if record.get("direction") != "nondecreasing" or record.get("evaluations", 0) < 1:
            errors.append(f"trace.{index}.solver")
        if record in successful:
            if (
                record.get("certificate_result") != "PASS"
                or not finite_number(record.get("objective_sse_scaled_y"))
                or float(record["objective_sse_scaled_y"]) < 0
                or not finite_number(record.get("optimality"))
            ):
                errors.append(f"trace.{index}.success")
        elif record in failed:
            if (
                record.get("certificate_result") != "NOT_RUN"
                or record.get("objective_sse_scaled_y") is not None
                or record.get("optimality") is not None
                or not record.get("reason_codes")
            ):
                errors.append(f"trace.{index}.failure")
    if len(selected) != (1 if expected_status == "SUCCEEDED" else 0):
        errors.append("trace.selected_count")
    trace_links = [
        node for node in bundle.html.nodes
        if node.tag == "a" and node.attrs.get("href") == "trace/fit-attempts.jsonl.gz"
    ]
    if not trace_links or not any(
        node.attrs.get("data-source-pointer") == "/refit/p2/optimizer/trace_path"
        for node in trace_links
    ):
        errors.append("html.trace_link")

    audit.add(
        "RAW",
        "RAW-OPT-TRACE-029",
        not errors,
        "PROFILE_CELLS summary independently reconciles tie-safe cells, deterministic compressed attempt trace and solver/certificate accounting",
        bundle=bundle.name,
        evidence={
            "status": optimizer.get("status"),
            "cells": len(cells),
            "records": len(records),
            "gzip_header_hex": raw_header.hex(),
            "errors": errors,
        },
    )


def audit_schema_and_html_reconciliation(bundle: Bundle, audit: Audit) -> None:
    schema = strict_json(bundle.path / "report.schema.json")
    canonical_schema = strict_json(CANONICAL_SCHEMA)
    required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
    report_keys_ok = required and required.issubset(bundle.report)
    gate_prefix = json_get(schema, "$defs.validation.properties.decision_gates.prefixItems", [])
    gate_schema_ok = (
        isinstance(gate_prefix, list)
        and len(gate_prefix) == len(DECISION_GATE_SPECS)
        and all(
            json_get(item, "properties.id.const") == gate_id
            and close_or_equal(json_get(item, "properties.threshold.const"), threshold)
            for item, (gate_id, threshold, _operator) in zip(gate_prefix, DECISION_GATE_SPECS)
        )
    )
    role_branches: dict[Any, dict[str, Any]] = {}
    failed_branch: dict[str, Any] | None = None
    for branch in schema.get("allOf", []) if isinstance(schema, dict) else []:
        condition = json_get(branch, "if.properties.recommendation.properties.role")
        if isinstance(condition, dict):
            if condition.get("type") == "null":
                role_branches[None] = branch
            elif "const" in condition:
                role_branches[condition["const"]] = branch
        if json_get(branch, "if.properties.status.properties.analysis_status.const") == "FAILED":
            failed_branch = branch
    one_gate_rule = json_get(role_branches.get("one", {}), "then.properties.validation.properties.decision_gates", {})
    two_gate_rule = json_get(role_branches.get("two", {}), "then.properties.validation.properties.decision_gates", {})
    conditional_schema_ok = (
        set(role_branches) == {None, "one", "two"}
        and failed_branch is not None
        and one_gate_rule.get("minContains") == 1
        and set(json_get(one_gate_rule, "contains.properties.status.enum", [])) == {"fail", "unavailable"}
        and len(two_gate_rule.get("prefixItems", [])) == len(DECISION_GATE_SPECS)
        and all(
            json_get(item, "properties.status.const") == "pass"
            for item in two_gate_rule.get("prefixItems", [])
        )
        and two_gate_rule.get("items") is False
    )
    schema_ok = (
        schema == canonical_schema
        and schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
        and schema.get("properties", {}).get("prototype_only", {}).get("const") is True
        and bool(schema.get("allOf"))
        and bool(schema.get("$defs", {}).get("metric", {}).get("allOf"))
        and gate_schema_ok
        and conditional_schema_ok
    )
    audit.add(
        "JSON",
        "JSON-SCHEMA-011",
        bool(report_keys_ok and schema_ok),
        "versioned conditional Draft 2020-12 schema equals the normative specification artifact",
        bundle=bundle.name,
        evidence={
            "missing_required": sorted(required - set(bundle.report)),
            "schema_ok": schema_ok,
            "gate_schema_ok": gate_schema_ok,
            "conditional_schema_ok": conditional_schema_ok,
        },
    )
    raw_nodes = [node for node in bundle.html.nodes if node.attrs.get("data-source-pointer") == "/recommendation/global_primary_r2_oos"]
    expected = json_get(bundle.report, "recommendation.global_primary_r2_oos.value")
    expected_raw = "null" if expected is None else format(expected, ".17g")
    reconcile = any(node.attrs.get("data-raw-value") == expected_raw for node in raw_nodes)
    audit.add(
        "METRIC",
        "METRIC-HTML-012",
        reconcile,
        "headline human metric preserves canonical raw value and JSON pointer",
        bundle=bundle.name,
        evidence={"expected_raw": expected_raw, "matching_nodes": len(raw_nodes)},
    )


def audit_schema_mutations(bundle: Bundle, audit: Audit) -> None:
    baseline_errors = contract_state_errors(bundle.report)
    mutations: dict[str, dict[str, Any]] = {}

    status_mutation = copy.deepcopy(bundle.report)
    current_status = json_get(status_mutation, "status.recommendation_status")
    status_mutation["status"]["recommendation_status"] = (
        "TWO_RECOMMENDED" if current_status != "TWO_RECOMMENDED" else "ONE_RECOMMENDED"
    )
    mutations["recommendation_status_transition"] = status_mutation

    role_mutation = copy.deepcopy(bundle.report)
    current_role = json_get(role_mutation, "recommendation.role")
    role_mutation["recommendation"]["role"] = "two" if current_role != "two" else "one"
    mutations["role_transition_without_coupled_state"] = role_mutation

    analysis_mutation = copy.deepcopy(bundle.report)
    analysis_mutation["status"]["analysis_status"] = (
        "SUCCEEDED" if json_get(analysis_mutation, "status.analysis_status") == "FAILED" else "FAILED"
    )
    mutations["terminal_analysis_transition"] = analysis_mutation

    gate_mutation = copy.deepcopy(bundle.report)
    gate_records = json_get(gate_mutation, "validation.decision_gates", [])
    if json_get(gate_mutation, "recommendation.role") == "two":
        gate_records[0]["status"] = "fail"
        gate_records[0]["reason"] = "THRESHOLD_NOT_MET"
    else:
        for gate in gate_records:
            gate["status"] = "pass"
            gate["reason"] = "THRESHOLD_MET"
    mutations["failed_gate_recommendation_transition"] = gate_mutation

    current_code = json_get(bundle.report, "decision.code")
    decision_codes = {
        "CLEAR_PRACTICAL_UPLIFT", "NO_UPLIFT_OR_HARM", "STATISTICAL_ONLY_SMALL",
        "PRACTICALLY_PROMISING_UNCERTAIN", "UNSTABLE_SELECTION",
        "NO_VALID_TWO_SEGMENT", "DESCRIPTIVE_ONLY", "PIPELINE_FAILURE",
    }
    for replacement_code in sorted(decision_codes - {current_code}):
        coupled_code_mutation = copy.deepcopy(bundle.report)
        coupled_code_mutation["status"]["decision_reason"] = replacement_code
        coupled_code_mutation["decision"]["code"] = replacement_code
        coupled_code_mutation["validation"]["uplift"]["decision"] = replacement_code
        mutations[f"coupled_code_swap:{replacement_code}"] = coupled_code_mutation

    mutation_errors = {name: contract_state_errors(document) for name, document in mutations.items()}
    rejected = {name: bool(errors) for name, errors in mutation_errors.items()}
    audit.add(
        "JSON",
        "JSON-STATE-MUTATIONS-025",
        not baseline_errors and all(rejected.values()),
        "independent state-machine negative mutations reject uncoupled transitions, a failed P2 gate and coupled decision-code swaps",
        bundle=bundle.name,
        evidence={
            "baseline_errors": baseline_errors,
            "mutations_rejected": rejected,
            "mutation_errors": mutation_errors,
        },
    )


def audit_crossfit_metamorphic(bundles: list[Bundle], audit: Audit) -> None:
    by_name = {bundle.name: bundle for bundle in bundles}
    required = {"crossfit_test_y_base", "crossfit_test_y_changed"}
    missing = sorted(required - set(by_name))
    errors: list[str] = []
    if missing:
        audit.add(
            "RESID",
            "RESID-CROSSFIT-023",
            False,
            "paired held-out-y metamorphic fixtures are present",
            evidence={"missing": missing},
        )
        return

    base = by_name["crossfit_test_y_base"]
    changed = by_name["crossfit_test_y_changed"]
    row_id = "row-04"

    def observation(bundle: Bundle) -> dict[str, Any]:
        return next(row for row in bundle.report["observations"] if row.get("row_id") == row_id)

    def diagnostic(bundle: Bundle) -> dict[str, Any]:
        return next(row for row in json_get(bundle.report, "diagnostics.rows", []) if row.get("row_id") == row_id)

    base_observation = observation(base)
    changed_observation = observation(changed)
    base_diagnostic = diagnostic(base)
    changed_diagnostic = diagnostic(changed)
    if (
        base_observation.get("x") != changed_observation.get("x")
        or base_observation.get("x_group_id") != changed_observation.get("x_group_id")
        or base_observation.get("y") == changed_observation.get("y")
    ):
        errors.append("paired_input_change")
    y_delta = float(changed_observation["y"]) - float(base_observation["y"])

    base_appearances = read_csv_rows(base.path / "diagnostic-appearances.csv")
    changed_appearances = read_csv_rows(changed.path / "diagnostic-appearances.csv")

    def selected_appearances(rows: list[dict[str, str]]) -> dict[tuple[str, int], dict[str, str]]:
        selected: dict[tuple[str, int], dict[str, str]] = {}
        for row in rows:
            if decode_export_id(row["row_id"]) == row_id:
                selected[(row["procedure"], int(row["repetition"]))] = row
        return selected

    base_selected = selected_appearances(base_appearances)
    changed_selected = selected_appearances(changed_appearances)
    expected_keys = {
        (procedure, repetition)
        for procedure in ("one", "two")
        for repetition in range(1, int(json_get(base.report, "validation.repetitions")) + 1)
    }
    if set(base_selected) != expected_keys or set(changed_selected) != expected_keys:
        errors.append("appearance_key_set")
    for key in sorted(expected_keys):
        before = base_selected.get(key, {})
        after = changed_selected.get(key, {})
        try:
            prediction_before = float(before["prediction_oof"])
            prediction_after = float(after["prediction_oof"])
            scale_before = float(before["robust_scale"])
            scale_after = float(after["robust_scale"])
            residual_before = float(before["residual_oof"])
            residual_after = float(after["residual_oof"])
            z_before = float(before["z_oof"])
            z_after = float(after["z_oof"])
            if not close_or_equal(prediction_before, prediction_after):
                errors.append(f"{key}.prediction_changed")
            if not close_or_equal(scale_before, scale_after):
                errors.append(f"{key}.scale_changed")
            if not close_or_equal(residual_after - residual_before, y_delta):
                errors.append(f"{key}.residual_delta")
            if not close_or_equal(residual_before, float(before["y"]) - prediction_before) or not close_or_equal(
                residual_after, float(after["y"]) - prediction_after
            ):
                errors.append(f"{key}.residual_identity")
            if not close_or_equal(z_before, residual_before / scale_before) or not close_or_equal(
                z_after, residual_after / scale_after
            ):
                errors.append(f"{key}.z_identity")
        except Exception as exc:
            errors.append(f"{key}:{type(exc).__name__}:{exc}")

    def selected_scale_trace(bundle: Bundle) -> list[dict[str, str]]:
        return [
            row
            for row in read_csv_rows(bundle.path / "diagnostic-scale-trace.csv")
            if decode_export_id(row["row_id"]) == row_id
        ]

    if selected_scale_trace(base) != selected_scale_trace(changed):
        errors.append("held_out_scale_trace_changed")
    for procedure in ("one", "two"):
        for field in (f"pred_{procedure}_oof", f"scale_{procedure}_oof"):
            if not close_or_equal(base_observation.get(field), changed_observation.get(field)):
                errors.append(f"observation.{field}")
        residual_field = f"resid_{procedure}_oof"
        if not close_or_equal(
            float(changed_observation[residual_field]) - float(base_observation[residual_field]), y_delta
        ):
            errors.append(f"observation.{residual_field}")

    base_score = float(base_diagnostic["score_median_abs_z"])
    changed_score = float(changed_diagnostic["score_median_abs_z"])
    base_expected_large = base_score >= float(base_diagnostic["threshold"]) and float(base_diagnostic["flag_rate"]) >= 0.5
    changed_expected_large = changed_score >= float(changed_diagnostic["threshold"]) and float(changed_diagnostic["flag_rate"]) >= 0.5
    base_codes = [flag.get("code") for flag in base_diagnostic.get("flags", [])]
    changed_codes = [flag.get("code") for flag in changed_diagnostic.get("flags", [])]
    if (
        base_expected_large
        or not changed_expected_large
        or ("LARGE_OOF_RESIDUAL" in base_codes) != base_expected_large
        or ("LARGE_OOF_RESIDUAL" in changed_codes) != changed_expected_large
    ):
        errors.append("numeric_flag_transition")

    audit.add(
        "RESID",
        "RESID-CROSSFIT-023",
        not errors,
        "changing one held-out y leaves that row's OOF predictions and training-only scales fixed while residual, z and numeric flag respond",
        evidence={
            "row_id": row_id,
            "base_y": base_observation.get("y"),
            "changed_y": changed_observation.get("y"),
            "base_score": base_score,
            "changed_score": changed_score,
            "base_flags": base_codes,
            "changed_flags": changed_codes,
            "errors": errors,
        },
    )


def audit_diagnostic_nonmutation(bundles: list[Bundle], audit: Audit) -> None:
    by_name = {bundle.name: bundle for bundle in bundles}
    required = {"diagnostic_nonmutation_base", "diagnostic_nonmutation_flagged"}
    missing = sorted(required - set(by_name))
    if missing:
        audit.add(
            "RESID",
            "RESID-NONMUTATION-027",
            False,
            "paired diagnostic non-mutation fixtures are present",
            evidence={"missing": missing},
        )
        return

    base = by_name["diagnostic_nonmutation_base"]
    flagged = by_name["diagnostic_nonmutation_flagged"]
    errors: list[str] = []
    if not deep_close(json_get(base.report, "input"), json_get(flagged.report, "input")):
        errors.append("input_contract")
    raw_fields = ("row_id", "source_row_id", "x", "y", "x_group_id", "input_status")
    base_identity = [tuple(row.get(field) for field in raw_fields) for row in base.report.get("observations", [])]
    flagged_identity = [tuple(row.get(field) for field in raw_fields) for row in flagged.report.get("observations", [])]
    if base_identity != flagged_identity:
        errors.append("observation_identity")
    for path in ("status", "validation", "recommendation", "decision", "refit"):
        if not deep_close(json_get(base.report, path), json_get(flagged.report, path)):
            errors.append(path)

    for role in ("one", "two"):
        base_preview_path = base.path / "models" / f"{role}-model-preview.json"
        flagged_preview_path = flagged.path / "models" / f"{role}-model-preview.json"
        if base_preview_path.exists() != flagged_preview_path.exists():
            errors.append(f"{role}.preview_presence")
        elif base_preview_path.exists():
            base_preview = strict_json(base_preview_path)
            flagged_preview = strict_json(flagged_preview_path)
            if (
                base_preview.pop("report_id", None) != base.report.get("report_id")
                or flagged_preview.pop("report_id", None) != flagged.report.get("report_id")
                or not deep_close(base_preview, flagged_preview)
            ):
                errors.append(f"{role}.preview_content")

    base_high_groups = [
        group
        for group in json_get(base.report, "diagnostics.influence_groups", [])
        if group.get("high_refit_influence")
    ]
    flagged_high_groups = [
        group
        for group in json_get(flagged.report, "diagnostics.influence_groups", [])
        if group.get("high_refit_influence")
    ]
    if base_high_groups or len(flagged_high_groups) != 1:
        errors.append("high_group_transition")
    expected_flagged_row_ids: list[str] = []
    if flagged_high_groups:
        group = flagged_high_groups[0]
        thresholds = json_get(flagged.report, "diagnostics.influence.thresholds", {})
        numeric_trigger = any(
            finite_number(group.get(field))
            and finite_number(thresholds.get(field))
            and float(group[field]) >= float(thresholds[field])
            for field in thresholds
        )
        state_trigger = any(
            group.get(field) is True
            for field in (
                "recommendation_changed",
                "decision_changed",
                "p2_status_changed",
                "family_changed",
                "direction_changed",
                "certificate_changed",
            )
        )
        if not (numeric_trigger or state_trigger) or group.get("action") != "review_only":
            errors.append("high_group_not_numeric")
        expected_flagged_row_ids = list(group.get("row_ids", []))

    flagged_rows = json_get(flagged.report, "diagnostics.flagged_rows", [])
    actual_flagged_row_ids = [row.get("row_id") for row in flagged_rows]
    if actual_flagged_row_ids != expected_flagged_row_ids or any(
        row.get("flag_codes") != ["HIGH_REFIT_INFLUENCE"]
        or row.get("action") != "review_only"
        or [item.get("code") for item in row.get("flags", [])] != ["HIGH_REFIT_INFLUENCE"]
        for row in flagged_rows
    ):
        errors.append("flagged_rows")
    base_flags = {
        row.get("row_id"): row.get("flags")
        for row in base.report.get("observations", [])
        if row.get("flags")
    }
    flagged_flags = {
        row.get("row_id"): row.get("flags")
        for row in flagged.report.get("observations", [])
        if row.get("flags")
    }
    if base_flags or flagged_flags != {row_id: ["HIGH_REFIT_INFLUENCE"] for row_id in expected_flagged_row_ids}:
        errors.append("observation_flags")

    audit.add(
        "RESID",
        "RESID-NONMUTATION-027",
        not errors,
        "a numeric influence-only review flag leaves input identity, validation, decision, recommendation and certified refits unchanged",
        evidence={
            "base_high_group_count": len(base_high_groups),
            "flagged_high_group_count": len(flagged_high_groups),
            "flagged_row_ids": actual_flagged_row_ids,
            "errors": errors,
        },
    )


def audit_analysis_identity(bundles: list[Bundle], audit: Audit) -> None:
    groups: dict[str, list[dict[str, Any]]] = {}
    for bundle in bundles:
        report = bundle.report
        decision = report.get("decision", {})
        state_payload = {
            "input": report.get("input"),
            "preprocessing": report.get("preprocessing"),
            "status": report.get("status"),
            "decision": {
                "code": decision.get("code"),
                "recommended_procedure": decision.get("recommended_procedure"),
                "related_reason_codes": decision.get("related_reason_codes"),
            },
            "recommendation": report.get("recommendation"),
            "validation": report.get("validation"),
            "refit": report.get("refit"),
            "diagnostics": report.get("diagnostics"),
            "warnings": report.get("warnings"),
            "failures": report.get("failures"),
            "observations": report.get("observations"),
        }
        analysis_id = json_get(report, "provenance.analysis_id")
        groups.setdefault(str(analysis_id), []).append({
            "bundle": bundle.name,
            "request_sha256": json_get(report, "provenance.request_sha256"),
            "state_sha256": canonical_sha256(state_payload),
        })

    duplicate_groups = {
        analysis_id: records
        for analysis_id, records in groups.items()
        if len(records) > 1
    }
    conflicts = {
        analysis_id: records
        for analysis_id, records in duplicate_groups.items()
        if len({record["request_sha256"] for record in records}) != 1
        or len({record["state_sha256"] for record in records}) != 1
    }
    audit.add(
        "JSON",
        "JSON-ANALYSIS-IDENTITY-033",
        not conflicts,
        "equal analysis_id values imply one canonical result-bearing request and identical statuses, recommendation, metrics, split/refit/diagnostic state",
        evidence={
            "bundle_count": len(bundles),
            "unique_analysis_id_count": len(groups),
            "duplicate_groups": duplicate_groups,
            "conflicts": conflicts,
        },
    )


def write_result(audit: Audit, bundle_names: Iterable[str]) -> int:
    result = audit.result(bundle_names)
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": result["status"], "results": str(RESULTS)}, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


def run_audit(audit: Audit) -> int:
    expected_fixtures = load_matrix(audit)
    bundle_paths = discover_bundles(audit)
    bundles = [bundle for path in bundle_paths if (bundle := load_bundle(path, audit)) is not None]

    found_names = {bundle.name for bundle in bundles}
    audit.add(
        "FAIL",
        "FAIL-MATRIX-003",
        found_names == set(expected_fixtures),
        "generated bundles exactly cover the declared fixture matrix",
        evidence={
            "expected": sorted(expected_fixtures),
            "found": sorted(found_names),
            "missing": sorted(set(expected_fixtures) - found_names),
            "unexpected": sorted(found_names - set(expected_fixtures)),
        },
    )

    for bundle in bundles:
        audit_prototype_boundary(bundle, audit)
        audit_html_security(bundle, audit)
        audit_svg_security_and_accessibility(bundle, audit)
        audit_html_accessibility(bundle, audit)
        audit_json_null_metrics(bundle, audit)
        expected = expected_fixtures[bundle.name]
        audit_fixture_state(bundle, expected, audit)
        audit_decision_contract(bundle, audit)
        audit_warning_semantics(bundle, expected, audit)
        audit_metric_scopes(bundle, audit)
        audit_statistical_reconciliation(bundle, audit)
        audit_uncertainty(bundle, audit)
        audit_p2_stability_ledger(bundle, audit)
        audit_split_sensitivity(bundle, audit)
        audit_diagnostics(bundle, audit)
        audit_raw_reconciliation(bundle, expected, audit)
        audit_csv_identifiers(bundle, audit)
        audit_geometry(bundle, audit)
        audit_source_pointers(bundle, audit)
        audit_report_contract_content(bundle, audit)
        audit_provenance_hashes(bundle, audit)
        audit_manifest_and_artifacts(bundle, audit)
        audit_model_preview(bundle, audit)
        audit_refit_dossiers(bundle, audit)
        audit_optimizer_trace(bundle, audit)
        audit_schema_and_html_reconciliation(bundle, audit)
        audit_schema_mutations(bundle, audit)

    audit_crossfit_metamorphic(bundles, audit)
    audit_diagnostic_nonmutation(bundles, audit)
    audit_analysis_identity(bundles, audit)

    for gate in REQUIRED_GATES:
        if not any(check.gate == gate for check in audit.checks):
            audit.add(gate, f"{gate}-UNIMPLEMENTED", False, "gate has no executable checks")

    return write_result(audit, [bundle.name for bundle in bundles])


def main() -> int:
    if RESULTS.exists():
        RESULTS.unlink()
    audit = Audit()
    try:
        return run_audit(audit)
    except Exception as exc:
        audit.add(
            "FAIL",
            "FAIL-AUDITOR-EXCEPTION-999",
            False,
            "the fail-closed auditor completed without an unhandled exception",
            evidence={"exception_type": type(exc).__name__, "message": str(exc)},
        )
        return write_result(audit, [])


if __name__ == "__main__":
    raise SystemExit(main())
