# Delegated decision log: report contract v1

Date: 2026-07-16  
Wayfinder issue: `12-prototype-report-contract`  
Decision owner for this reversible v1 default: executor under the user's hands-off instruction

## Decision

Adopt variant A, **decision-first evidence narrative**, as the v1 report hierarchy.
It leads with recommendation status, reason and quality warning; then presents
input handling, the paired out-of-fold comparison, two comparable plots, final
refit formulas, diagnostics, uncertainty and reproducibility artifacts.

This is a delegated design default. It is not recorded as user approval of a
shown mock-up, and no independent reviewer is treated as the user's proxy.

## Alternatives considered

| Variant | Organizing principle | Benefit | Why not selected as the default |
|---|---|---|---|
| A | decision-first narrative | the answer and limitations are visible before technical detail | selected |
| B | symmetric P1/P2 dossiers | strong candidate-by-candidate inspection | delays the verdict and makes an unavailable P2 look too symmetric with a valid P1 |
| C | claim/evidence audit ledger | strongest machine-to-human reconciliation affordance | too dense as the primary human report |

Variant A incorporates two useful elements from the alternatives: explicit P1
and P2 model cards from B, and stable JSON pointers/reconciliation claims from
C. The comparison pages remain throwaway prototype artifacts under
`generated/_variants/`; they are not production templates.

## Evidence used

- research-backed report requirements in `docs/research/topics/05-reporting-visualization.md`;
- model, validation, diagnostics, fitting and delivery contracts 02–06;
- 15 synthetic fixture bundles declared in `fixtures/matrix.json`, including a
  partial P2-fallback procedure with an independently certified full-data refit;
- complete section/fixture/invariant coverage in `traceability.md`;
- machine audit in `audit-results.json`: `PASS`, 15 bundles, 1,021 checks, 0 failures;
- independent JSON Schema validation: 15/15 reports valid under Draft 2020-12;
- deterministic clean-rebuild tree digest
  `3bfb9af9571e43223a9ed9e75840f293104de8c6a84b16e44dc81c60ca3df490`
  on two consecutive complete builds. The digest is SHA-256 over a
  byte-sorted ledger of each generated relative path, byte size and file
  SHA-256;
- 15 deterministic `trace/fit-attempts.jsonl.gz` ledgers containing 72 reconciled
  PROFILE_CELLS attempts; every gzip header has `mtime=0` and no filename field;
- 15 `p2-stability-outer-fits.csv` ledgers containing 630 canonical outer-fit
  records; the audit independently reconstructs split-ID hashes,
  repetition-level uplift and P2 stability/failure frequencies;
- independently recomputed input, result-bearing request, policy, registry, source, dependency,
  environment and model structure/instance hashes from retained canonical
  payloads;
- the retained registry artifact is explicitly an exercised subset of
  authoritative `registry_v1`; its constant/poly1 AST objects are byte-for-byte
  the canonical objects used by refits, dossiers and model hashes;
- 14 distinct `analysis_id` values across 15 fixtures; the only duplicate is an
  intentionally identical analytic scenario, and an independent global check
  proves that equal IDs have the same request hash and canonical
  status/recommendation/metric/split/refit/diagnostic state;
- `xmllint` parse of all generated SVG figures;
- direct raster inspection of the selected P1, selected P2, residual, typed P2
  failure and hostile-label figures recorded in `visual-accessibility-audit.md`;
- independent arithmetic reconciliation recorded in
  `statistical-reconciliation-audit.md`;
- local server smoke: variants A/B/C and the selected static report returned
  HTTP 200; an unknown variant returned 404;
- separate statistical/contract and security/accessibility/operations reviews;
  their final outcomes are recorded only after both reviewers inspect this
  exact stable snapshot.

The prototype is deliberately synthetic and never produces `COMPLETE` or a
usable `recommended-model.json`. Candidate dossiers
`models/one-model.json`/`models/two-segment-model.json` are emitted only for a
certified synthetic refit; a recommendation preview is named
`*-model-preview.json`. Every model artifact has `prototype_only=true` and
`usable_for_prediction=false`.

The allowed prototype-only substitutions are normative in report-contract
§17.0. Exact production paths/schema, usable recommendation model, `predict`,
compression, atomic publication and `COMPLETE` are explicitly `NOT RUN` and
belong to issue 14; this decision does not assert the delivery surface exists.

## Delegated gate and remaining implementation checks

The original ticket was created as an interactive prototype requiring a shown
mock-up and user reaction. The later hands-off instruction superseded that
interaction as a decision prerequisite. Tracker resolution must therefore say
that the executor accepted a delegated v1 default on evidence; it must not say
that the user approved the mock-up.

No controllable in-app browser was available in this environment. Consequently
HTML reflow at 400%, target-browser print, forced-colors, CVD simulation and
screen-reader behavior were not executed and are not claimed as passing. They
remain explicit implementation-acceptance items for Wayfinder issue 14 and the
subsequent implementation handoff. Static structure, SVG rendering, contrast,
escaping and cross-artifact reconciliation were checked now because they bear
directly on the report-design decision.

## Independent review outcomes

- Statistical/report-contract review returned NO-GO in its earlier rounds and
  exposed hard-coded metric/fallback states, insufficient conditional schema,
  missing cross-fit/non-mutation probes, an incomplete partial-fallback state,
  a decision-code oracle gap, MAD-only diagnostics, incomplete P1/P2 refit
  dossiers and the absent optimizer trace. Accepted fixes added independent
  arithmetic/state mutation checks, the paired probes, the 15th fixture,
  Qn-first scales, strict dossier schema/HTML/model artifacts and a reconciled
  deterministic PROFILE_CELLS ledger. A later review found `analysis_id`
  collisions between materially different synthetic scenarios; the canonical
  result-bearing request payload/hash and a cross-bundle identity invariant now
  close that defect. The same review then found that a 100% P1-fallback P2
  procedure had been deleted from split/bootstrap evidence and that the
  retained registry used a divergent shorthand AST. Procedure-level
  repetition/bootstrap effects now retain full fallback as uplift `0`, zero-P1
  MSE is typed `ZERO_P1_MSE`, and registry/refit AST identity is independently
  enforced. The independent final review returned **GO** for digest
  `3bfb9af9571e43223a9ed9e75840f293104de8c6a84b16e44dc81c60ca3df490`:
  two clean builds and the retained 560-file tree were byte-identical; all
  15 bundles and 1,021 checks passed with no blocker or major finding. The
  signed-off detail is retained in
  `independent-statistical-report-review.md`.
- Security/accessibility/operations review returned NO-GO in its earlier
  rounds and exposed failure-caption, pointer/hash, fixture/containment and
  CSV-identifier defects, plus the mismatch between an interactive tracker
  type and the later hands-off instruction. Accepted fixes made failure panels
  typed and line-free, closed manifest/source-pointer/path checks, universally
  verified every CSV identifier/sidecar, restricted bundle permissions and
  reclassified issue 12 to a delegated evidence task without inventing user
  approval. The independent final review returned **GO** for the same digest:
  two byte-identical clean builds passed, four targeted tamper scenarios failed
  closed, and no blocker or major finding remained. The signed-off detail is
  retained in `independent-security-operations-review.md`.
- Tracker-semantics review: reclassify issue 12 from interactive `prototype` to
  a delegated evidence `task`; preserve the absent live-user-review fact.
