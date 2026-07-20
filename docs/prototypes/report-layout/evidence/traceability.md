# Report-contract traceability

This matrix covers every normative section of
[`07-report-contract.md`](../../../specification/07-report-contract.md), then
expands the required fixture states and cross-cutting acceptance invariants.
The retained `audit-results.json` is the canonical machine verdict. Rows that
need a target browser/platform or the production publication seam are marked
`NOT RUN`; they are not part of the static PASS.

## Normative sections

| Contract requirement | Fixture(s) | Executable gate/check | Retained evidence |
|---|---|---|---|
| §1 canonical `report.json`; renderers cannot choose models, recompute decisions or invent failed models | all | JSON-SCHEMA-011, JSON-STATE-MUTATIONS-025, GEOM | `report.json`, normative schema, HTML/SVG reconciliation |
| §1 static HTML, two mandatory SVG panels, no mandatory overlay/PDF | all | SECURITY-STATIC, CMP, PROTOTYPE | `report.html`, `figures/one-function.svg`, `figures/two-segment.svg` |
| §2 fixed eight-section order; unavailable sections remain with typed reason; Russian human text and stable machine codes | all | A11Y-STATIC, FAIL, JSON | parsed HTML heading/anchor snapshot and failure reports |
| §3.1 decision-first card with statuses, reason, OOF quality, warnings, domain, no-extrapolation and model-preview link only for validated recommendation | all | PROTOTYPE, WARN, METRIC, JSON | `#decision`, `/status`, `/decision`, `/recommendation` |
| §3.2 exact decision-code → role/terminal-state transition | all | JSON-STATE-MUTATIONS-025, FAIL-STATE-010 | conditional schema plus coupled-code mutation oracle |
| §4 input counts, hashes/units/domain, invalid audit, repeated-`x` groups, stable rows, equal weights and no automatic outlier removal | invalid/repeated/security fixtures | RAW, WARN, SECURITY-STATIC | `/input`, `/preprocessing`; `observations.csv` includes excluded logical rows with empty numeric fields and typed reason; `input-exclusions.jsonl` retains raw invalid values |
| §5.1 P1/P2 share appearances/nulls; P2 deployable-procedure metrics include row-level P1 fallback; full-data refit status remains separate | all validation fixtures; full and partial fallback fixtures | METRIC-STAT-015, RAW, FAIL | `oof-appearances.csv`, `p2-stability-outer-fits.csv`, `/validation/procedures`, exact fallback fit/appearance denominators and reason codes |
| §5.2 paired uplift sign/units and all 14 policy gates | all validation fixtures | METRIC-STAT-015, JSON-STATE-MUTATIONS-025 | `/validation/uplift`, `/validation/decision_gates` |
| §6.1 immutable common point layer, axes, ticks, aspect and observed domain; lines reconciled to plot data | all | CMP, GEOM | two SVGs, `plot-data.csv`, DOM/geometry evidence |
| §6.2 P1 certified line or typed line-free failure panel | all | GEOM | `one-function.svg`, `/refit/p1/plot` |
| §6.3 P2 interval clipping, shared boundary coordinate, continuity, red dashed labelled boundary, direct branch labels, or typed line-free failure | all P2 success/failure fixtures | GEOM, A11Y-STATIC | `two-segment.svg`, `/refit/p2`, `plot-data.csv` |
| §6.4 residual/influence/combined shapes and exact-coordinate multiplicity without semantic jitter | diagnostic/repeated fixtures | RESID, A11Y-STATIC, GEOM | SVG markers, row/group tables, diagnostics JSON |
| §7.1 complete P1 refit dossier: registry/AST, source-scale formula, transform, parameters, domain/direction, derivative certificate, hashes, descriptive metrics/statuses and model link | all and only P1-certified fixtures | JSON-REFIT-028, METRIC | `/refit/p1`, conditional `models/one-model.json`, `#refit` |
| §7.2 complete P2 dossier: ordered families, formulas, tie-safe `c`, membership, shared μ, branch certificates, segment counts/shares/unique-x/spans/local metrics, stability/statuses and model link | all and only P2-certified fixtures | JSON-REFIT-028, GEOM, METRIC | `/refit/p2`, conditional `models/two-segment-model.json`, `#refit` |
| §7.3 formulas are exact registry-AST data, never code; optimizer is `PROFILE_CELLS` with reconciled cells/attempts/evaluations/basins/certificate/reasons and compressed attempt trace | all | RAW-OPT-TRACE-029, SECURITY-STATIC, JSON-REFIT-028 | `/refit/p2/optimizer`, `provenance/registry.json`, `trace/fit-attempts.jsonl.gz`, `#reproducibility`; retained registry is labelled an exercised subset and its exact AST objects equal refit/dossier/hash ASTs |
| §8 OOF diagnostics, Qn-first/MAD-fallback outer-training scale, local bins, signed z, patterns, repeated-x decomposition and separate full-refit influence | diagnostic, crossfit, constant-y fixtures | RESID-SCALE-021, RESID-CROSSFIT-023, RESID-NONMUTATION-027 | diagnostic long CSVs, four-panel SVG, `/diagnostics` |
| §9 split sensitivity, full-pipeline bootstrap counts/interval label and P2 stability/failure frequencies; unavailable uncertainty is typed | all | UNC, UNC-SPLIT-012 | `bootstrap-resamples.csv`, `p2-stability-outer-fits.csv`; independently recomputed split-ID hashes, repetition uplift distribution/p10/positive share, breakpoint central 80%, and separate flat/multiple/collapse/failure rates; 100% P1 fallback remains defined procedure evidence with uplift 0, while zero P1 MSE is typed unavailable |
| §10 provenance, hashes, work/failure counts, relative artifact manifest and sensitive-data warning | all | RAW-OPT-TRACE-029, JSON-PROVENANCE-HASHES-026, JSON-ANALYSIS-IDENTITY-033, SECURITY-STATIC, PROTOTYPE | `/provenance`, `/artifacts`, `provenance.json`, canonical input/request/policy/source payloads under `provenance/`, `prototype-manifest.json`, `#reproducibility`; hashes are recomputed from bytes/typed payloads and equal `analysis_id` implies identical result-bearing state |
| §11 OOF/refit namespace and visual firewall | all | METRIC, RESID, JSON | validation/refit metric scopes, distinct residual/influence exports and SVG panels |
| §12 raw `<0.60`, exact `0.6000`, negative and undefined R² semantics/formatting | threshold, negative and constant-y fixtures | WARN, METRIC, JSON | canonical metric objects and `data-raw-value` HTML nodes |
| §13 typed failures never become zero, blank, fake formula/line/boundary/recommendation or COMPLETE publication | all failure fixtures | FAIL, GEOM, PROTOTYPE, JSON | failure entries, line-free SVGs, absent recommendation previews |
| §14 canonical namespaces, typed nullable metrics, JSON pointers, conditional schema and JSON↔HTML/SVG/CSV/model/hash reconciliation | all | JSON, RAW, CMP, GEOM, METRIC | normative schema, report copies, manifest and source-pointer audit |
| §15 semantic HTML/SVG, headings/tables/descriptions, text/non-colour encodings and computed static contrast | all | A11Y-STATIC | `visual-accessibility-audit.md`, parsed HTML/SVG checks |
| §15 target-browser narrow viewport, 400% reflow, keyboard/focus, screen reader, grayscale/CVD/forced-colors and print | production implementation | `NOT RUN` | issue 14 acceptance matrix |
| §16 offline CSP, escaping, no executable formula/user strings, safe SVG/links/paths, encoded CSV identifiers and private-bundle warning | all; hostile fixture | SECURITY-STATIC, RAW | security audit, HTML/SVG parser checks, universal CSV identifier check |
| §16 runtime network observation, atomic publication, symlink resistance and target POSIX modes | production publication seam | `NOT RUN` | issue 14 acceptance matrix |
| §17.0 explicit prototype-only substitutions versus the exact production bundle | all | PROTOTYPE | candidate dossiers/previews, uncompressed evidence exports, `provenance/` and `prototype-manifest.json`; exact production tree/model schema/predict/compression/publication/`COMPLETE` are `NOT RUN` for issue 14 |
| §17.1 complete fixture matrix | 15 declared fixtures | FAIL-MATRIX-003 | `fixtures/matrix.json`, generated bundle set |
| §17.2 all decision-bearing gates, deterministic rebuild and two independent retained reviews | all | aggregate machine verdict plus deterministic hash comparison | `audit-results.json`, `decision-log.md`, independent review records |
| §18 delegated hands-off evidence gate without invented user approval | all | PROTOTYPE | prototype banners, README, issue 12 history and decision log |
| §19 acceptance invariants 1–15 | all and paired metamorphic fixtures | aggregate of checks above | `audit-results.json`; runtime-only portions carried to issue 14 |

## Required fixture states

| Required state | Fixture(s) | Gate(s) | Evidence artifact |
|---|---|---|---|
| one-function recommendation at/above 0.60 | `one_recommended_exact_06000`, `one_no_uplift` | METRIC, WARN | decision card and canonical metrics |
| two-function recommendation, continuity and 40/60 balance | `two_recommended_low_r2_05996`, `two_recommended_partial_fallback` | CMP, GEOM, METRIC | P2 JSON/model/plot dossier |
| partial P2 failure with <100% appearance fallback and independent certified full refit | `two_recommended_partial_fallback` | METRIC-STAT-015, RAW | OOF appearances, fallback outer-fit IDs/reason counts and P2 refit |
| no uplift keeps P1 | `one_no_uplift` | METRIC, FAIL | comparison table and decision state |
| unrounded threshold sides | `two_recommended_low_r2_05996`, `one_recommended_exact_06000` | WARN, JSON | raw-value pointers in HTML/JSON |
| negative and undefined R² | `negative_r2`, `undefined_r2_constant_y` | METRIC, WARN, JSON | typed metric objects |
| descriptive-only state | `descriptive_only` | FAIL, PROTOTYPE | no recommendation/model preview |
| no balanced split | `p2_no_balanced_split` | FAIL, GEOM | typed failure panel without P2 line |
| non-invertible x scale | `noninvertible_x_scale` | FAIL, GEOM | fail-closed report without lines/formulas |
| hard pipeline failure | `hard_failure` | FAIL, PROTOTYPE | no false formula or recommendation artifact |
| invalid skipped row audit | `two_recommended_low_r2_05996` | RAW, WARN | input counts, exclusion and warning |
| repeated x and exact duplicates remain distinct | `p2_no_balanced_split` | RAW, CMP | observation rows and multiplicity layer |
| residual/influence/combined and zero-scale states | diagnostic, low-R², repeated, negative and constant-y fixtures | RESID, A11Y-STATIC | long diagnostic CSVs, four-panel SVG and tables |
| held-out `y` cannot affect its prediction/scale | `crossfit_test_y_base`, `crossfit_test_y_changed` | RESID-CROSSFIT-023 | appearance and scale-trace CSVs |
| diagnostic flags cannot mutate fit/decision | `diagnostic_nonmutation_base`, `diagnostic_nonmutation_flagged` | RESID-NONMUTATION-027 | invariant input/validation/decision/refit blocks plus flag delta |
| unavailable uncertainty and failed resample denominator | every fixture | UNC | bootstrap CSV and typed reason counts |
| hostile filename/unit/row/reason/raw strings remain data | `security_payloads` | SECURITY-STATIC, JSON, RAW | escaped HTML/SVG/JSONL and encoded CSV |

## Explicitly deferred acceptance evidence

The following are specified but not executed here: target-browser viewport and
400% reflow, keyboard/focus, screen-reader traversal, grayscale/CVD and forced
colours, browser print, observed runtime network silence, exact production
bundle/model schema and predictor, compression, atomic publication, `COMPLETE`,
symlink resistance, POSIX permission enforcement and cross-platform replay.
Their only valid status in issue 12 is `NOT RUN`; issue 14 must assign each an
environment, executor, artifact and pass/fail criterion.
