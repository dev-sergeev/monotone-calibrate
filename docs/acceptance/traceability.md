# Acceptance traceability v1

This matrix maps every normative contract area to at least one future
implementation gate. A gate reference means the gate must test all invariants
in the cited section; a passing narrower example cannot satisfy it.

| Source | Contract area | Blocking gates |
|---|---|---|
| 01 §§2–4 | schema, row identity, invalid-row skip/audit | `DATA-CONTRACT-001`, `INPUT-SNAPSHOT-006`, `PRIVACY-014` |
| 01 §§5–8 | order, separate repeated `x`, atomic ties, sufficiency, degeneracy | `DATA-CONTRACT-001`, `MODEL-CONTRACT-002`, `STAT-CALIBRATION-003` |
| 01 §§9–12 | reversible scaling, audit block, invariants, physical CSV | `DATA-CONTRACT-001`, `IDENTITY-015`, `CSVW-018` |
| 02 §§1–3 | interval semantics, full core registry, parameter bounds | `MODEL-CONTRACT-002`, `PREDICT-012` |
| 02 §§4–5 | independent certificate, canonicalization, collapse and hashes | `MODEL-CONTRACT-002`, `IDENTITY-015`, `VERIFY-011` |
| 02 §§6–8 | P1/P2, exact continuity, balance, identifiability, multistart | `MODEL-CONTRACT-002`, `STAT-CALIBRATION-003`, `PREDICT-012` |
| 02 §§9–11 | complexity, forbidden forms and exhaustive implementation checks | `MODEL-CONTRACT-002`, `REPLAY-016`, `XPLAT-017` |
| 03 §§1–3 | full deployable procedures, exact repeated grouped outer splits, fallback | `STAT-CALIBRATION-003`, `REPLAY-016` |
| 03 §§4–5 | pooled OOF and descriptive refit metrics | `STAT-CALIBRATION-003`, `VERIFY-011`, `CSVW-018` |
| 03 §§6–8 | practical uplift, P2 stability and bootstrap | `STAT-CALIBRATION-003`, `BENCHMARK-019` |
| 03 §§9–11 | decision states, global-only 0.60 warning, invariants | `STAT-CALIBRATION-003`, `CLI-E2E-004`, `FAILURE-EXIT-010` |
| 04 §§1–4 | no automatic deletion, OOF/refit separation, cross-fitted scale, flags | `STAT-CALIBRATION-003`, `VERIFY-011` |
| 04 §§5–7 | repeated-`x` patterns and selected-structure LGO influence semantics | `STAT-CALIBRATION-003`, `BENCHMARK-019` |
| 04 §§8–10 | diagnostic exports, visual encoding and invariants | `CSVW-018`, `REPORT-SEC-013`, `VERIFY-011` |
| 05 §§1–2 | `PROFILE_CELLS`, conditional linear block, independent evaluator | `MODEL-CONTRACT-002` |
| 05 §§3–7 | deterministic accounting, identified truths, state/replay machine gates | `MODEL-CONTRACT-002`, `REPLAY-016`, `XPLAT-017` |
| 05 §§8–9 | implementation seam and all previously unproved cases | `MODEL-CONTRACT-002`, `STAT-CALIBRATION-003`, `BENCHMARK-019` |
| 06 §§1–3 | local resumable CLI, commands, locked/offline install/run | `CLI-E2E-004`, `OPS-NET-005` |
| 06 §3.1; 05 §8; 08 §3.5 | opt-in OpenAI-compatible start advisor, train-only summaries, strict bounded output, fallback and secret/network controls | `LLM-ADVISOR-020`, `OPS-NET-005`, `PRIVACY-014`, `ADMISSION-007`, `IDENTITY-015` |
| 06 §§4–5 | exact physical adapter, immutable snapshot, request/core seam | `DATA-CONTRACT-001`, `INPUT-SNAPSHOT-006`, `IDENTITY-015` |
| 06 §§6–8 | exact bundle, typed predictor, statuses and exits | `CLI-E2E-004`, `FAILURE-EXIT-010`, `PREDICT-012`, `VERIFY-011` |
| 06 §§9–10 | admission, checkpoints/locks, atomic publication and retention | `ADMISSION-007`, `CHECKPOINT-009`, `PUBLICATION-008`, `BENCHMARK-019` |
| 06 §§11–12 | offline security, identifiers and reproducibility IDs/platforms | `REPORT-SEC-013`, `PRIVACY-014`, `IDENTITY-015`, `REPLAY-016`, `XPLAT-017` |
| 06 §§13–14 | MVP boundary and complete implementation acceptance list | all 20 gates |
| 07 §§1–5 | decision-first hierarchy, data and validation rendering | `CLI-E2E-004`, `VERIFY-011`, `REPORT-SEC-013` |
| 07 §§6–8 | two plots, exact formulas, diagnostics | `CLI-E2E-004`, `PREDICT-012`, `REPORT-SEC-013`, `CSVW-018` |
| 07 §§9–14 | stability, LLM provenance/disclosure, OOF/refit firewall, threshold, failures, reconciliation | `STAT-CALIBRATION-003`, `LLM-ADVISOR-020`, `FAILURE-EXIT-010`, `VERIFY-011`, `IDENTITY-015`, `PRIVACY-014` |
| 07 §§15–16 | accessibility, offline HTML/SVG, XSS/path/CSV safety | `REPORT-SEC-013`, `PRIVACY-014`, `CSVW-018` |
| 07 §§17–19 | prototype boundary and production carry-forward | `CLI-E2E-004`, `VERIFY-011`, `REPORT-SEC-013` |

## Evidence rule

Each implementation evidence record must include:

- gate ID, caller-supplied release snapshot digest, frozen verifier-runtime
  lock hash and manifest hash;
- authoritative source-snapshot ZIP, source-manifest/tree, dependency-lock,
  policy/registry and fixture hashes;
- exact isolated argv, cwd/env, source targets, platform, typed raw numerical
  environment probe and recomputed execution environment ID;
- executed test IDs/counts, skipped/failed counts and exit status;
- measured or recomputed oracle values and tolerance policy ID;
- artifact/evidence hashes;
- explicit reviewer verdict.

An evidence record cannot change a fixture, oracle or threshold. Such a change
creates a new manifest version and invalidates prior PASS for affected gates.
