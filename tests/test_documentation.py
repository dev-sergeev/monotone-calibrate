from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import unquote

from jsonschema import Draft202012Validator

from monotone_calibrate.registry import FAMILY_IDS
from monotone_calibrate.symbolic_search import (
    OUTPUT_SCHEMA_VERSION,
    PROMPT_VERSION,
    SymbolicSearchOptions,
)


ROOT = Path(__file__).resolve().parents[1]
CURRENT_DOCUMENTS = (
    ROOT / "README.md",
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "demo-run.md",
    ROOT / "docs" / "llm-sr-algorithm.md",
    ROOT / "docs" / "research" / "README.md",
    ROOT / "docs" / "research" / "topics" / "06-llm-openai-compatible-integration.md",
    ROOT / "docs" / "specification" / "README.md",
    ROOT / "docs" / "acceptance" / "README.md",
    ROOT / "docs" / "acceptance" / "traceability-llm-sr-v2.md",
    ROOT / "examples" / "README.md",
)
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def test_current_documentation_has_no_broken_relative_links() -> None:
    failures: list[str] = []
    for document in CURRENT_DOCUMENTS:
        for raw_target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            target = raw_target.strip().removeprefix("<").removesuffix(">")
            if target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            path_text = unquote(target.split("#", 1)[0])
            if path_text and not (document.parent / path_text).resolve().exists():
                failures.append(f"{document.relative_to(ROOT)} -> {target}")
    assert not failures, "broken documentation links:\n" + "\n".join(failures)


def test_llm_sr_response_schema_matches_the_documented_typed_contract() -> None:
    schema_path = ROOT / "docs" / "specification" / "llm-sr-hypotheses.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["schema_version"]["const"] == OUTPUT_SCHEMA_VERSION
    assert schema["$defs"]["family_id"]["enum"] == list(FAMILY_IDS)
    validator = Draft202012Validator(schema)
    valid = {
        "schema_version": "llm-sr-hypotheses-v1",
        "hypotheses": [
            {"structure": "P1", "family_ids": ["logistic_v1"]},
            {
                "structure": "P2",
                "family_ids": ["poly1_v1", "poly2_v1"],
            },
        ],
    }
    validator.validate(valid)
    invalid = {
        "schema_version": "llm-sr-hypotheses-v1",
        "hypotheses": [
            {
                "structure": "P2",
                "family_ids": ["constant_v1", "constant_v1"],
            }
        ],
    }
    assert list(validator.iter_errors(invalid))


def test_quick_start_and_examples_describe_the_current_runtime() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    normalized_readme = " ".join(readme.split())
    assert "печатает JSON-заключение только после окончания" in normalized_readme
    assert "--llm-symbolic-search" in readme
    assert "если credentials находятся в `.env`." in normalized_readme

    report = json.loads(
        (ROOT / "examples" / "demo-output" / "report.json").read_text(encoding="utf-8")
    )
    provenance = report["llm_advisor"]
    assert report["schema_version"] == "monotone-report-v3"
    assert provenance["mode"] == "llm_formula_discovery"
    assert provenance["output_schema_version"] == "llm-formulas-v1"
    assert provenance["scope"] == "deterministic_full_registry"
    assert "llm-start-advice-v1" not in json.dumps(report, sort_keys=True)

    historical = (
        ROOT / "examples" / "README.md"
    ).read_text(encoding="utf-8")
    assert "## Исторические" in historical
    assert "не являются текущим contract" in " ".join(historical.split())


def test_example_environment_uses_documented_safe_defaults() -> None:
    values = dict(
        line.split("=", 1)
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    assert values == {
        "MONOTONE_CALIBRATE_LLM_ENABLED": "false",
        "MONOTONE_CALIBRATE_LLM_PROVIDER": "openai",
        "MONOTONE_CALIBRATE_LLM_MODEL": "my-model-id",
        "MONOTONE_CALIBRATE_LLM_BASE_URL": "https://provider.example/v1",
        "MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN": "your-access-token",
        "MONOTONE_CALIBRATE_LLM_TIMEOUT_SECONDS": "20",
        "MONOTONE_CALIBRATE_LLM_MAX_RETRIES": "1",
        "MONOTONE_CALIBRATE_LLM_SEARCH_ITERATIONS": "4",
        "MONOTONE_CALIBRATE_LLM_MAX_OUTPUT_TOKENS": "4096",
        "MONOTONE_CALIBRATE_LLM_REASONING_EFFORT": "",
        "MONOTONE_CALIBRATE_LLM_ALLOW_INSECURE_HTTP": "false",
    }


def test_historical_registry_policy_is_separate_from_frozen_v1() -> None:
    policy = json.loads(
        (ROOT / "docs" / "acceptance" / "llm-sr-policy-v2.json").read_text(
            encoding="utf-8"
        )
    )
    assert policy["status"] == "historical_registry_selector_policy"
    assert policy["superseded_by"] == "llm-formula-discovery-v1"
    assert policy["prompt"]["response_schema_version"] == OUTPUT_SCHEMA_VERSION
    assert policy["prompt"]["version"] == PROMPT_VERSION
    assert policy["supersedes_llm_policy"] == "llm-start-advisor-v1"
    options = SymbolicSearchOptions()
    assert policy["search"]["islands"] == options.num_islands
    assert policy["prompt"]["experience_examples"] == options.experiences_per_prompt
    assert policy["prompt"]["samples_per_prompt"] == options.samples_per_prompt
    assert policy["provider"]["arguments"]["temperature"] == options.generation_temperature
    assert (
        policy["search"]["cluster_sampling"]["initial_temperature"]
        == options.cluster_temperature
    )
    assert (
        policy["search"]["cluster_sampling"]["temperature_period"]
        == options.cluster_temperature_period
    )
    assert (
        policy["search"]["weak_island_reset_period_iterations"]
        == options.reset_period_iterations
    )
    assert policy["search"]["random_seed"] == options.random_seed
    acceptance_index = (ROOT / "docs" / "acceptance" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "исторический snapshot" in acceptance_index
    assert "не переписаны «на месте»" in acceptance_index
