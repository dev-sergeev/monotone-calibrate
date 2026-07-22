# Traceability LLM-SR policy v2

Дата: 2026-07-22. Статус: current implementation checks, не подписанное
cross-platform release evidence.

| Текущий контракт | Реализация | Проверка |
|---|---|---|
| Strict typed P1/P2 skeleton response | `hypotheses.py`, `symbolic_search.py` | `tests/test_symbolic_search.py` |
| Generation temperature `0.8`, batch `4`, 10 islands, 2 experiences | `SymbolicSearchOptions`, `ChatOpenAIHypothesisSampler` | `test_chat_sampler_requests_stochastic_typed_hypotheses_without_executable_code` |
| Local parameter optimization and `-MSE` fitness | `engine.fit_hypothesis`, `run_symbolic_search` | `test_search_iteratively_evaluates_hypotheses_and_returns_a_frozen_portfolio` |
| Invalid code/family/JSON rejection | `_parse_hypotheses` | `test_chat_sampler_atomically_rejects_code_unknown_families_and_malformed_output` |
| Full-registry atomic fallback | `run_symbolic_search`, `application._resolve_hypothesis_space` | `test_failed_sampling_falls_back_to_the_complete_deterministic_registry` |
| Frozen portfolio replay in grouped outer folds | `CandidateSet.hypothesis_space`, `validation.validate_candidates` | `test_validation_replays_the_full_fit_search_policy` |
| Conditional validation and disclosure warnings | `SymbolicSearchResult.warning_codes`, report renderer | application/report tests |
| Secret/raw endpoint exclusion | redacted application provenance and report allowlist | `test_enabled_llm_sr_search_supplies_a_typed_portfolio_used_by_fit_and_validation` |
| Offline default | full registry, no client construction | application/config/CLI tests |

Primary policy:
[`llm-sr-policy-v2.json`](llm-sr-policy-v2.json). Response schema:
[`../specification/llm-sr-hypotheses.schema.json`](../specification/llm-sr-hypotheses.schema.json).

## Open acceptance gaps

- новый signed 20-gate manifest и evidence aggregate не созданы;
- нет двухплатформенного provider-enabled network trace;
- нет fail-closed ambient LangSmith tracing guard;
- нет project-owned HTTP redirect origin allowlist;
- нет полностью nested LLM-SR validation;
- live provider compatibility текущего response schema отдельно не доказана.

Историческая [`traceability.md`](traceability.md) относится к frozen acceptance
v1 и gate `LLM-ADVISOR-020`; она не должна использоваться для текущего
selector-а.
