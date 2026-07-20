# Statistical reconciliation evidence

Date: 2026-07-16  
Scope: synthetic report-state prototype, not a claim of estimator performance

## Verdict

The current machine audit independently recomputes every decision-bearing loss
and fit metric from exported predictions. It does not merely compare two copies
of fixture constants.

For every bundle it reads `oof-appearances.csv` and verifies:

- `residual = y - prediction` for every P1/P2 appearance;
- pooled null SSE from the same-appearance null prediction;
- pooled P1/P2 MSE, RMSE, MAE and `R²_OOS`;
- `ΔMSE`, `ΔRMSE`, `ΔMAE`, `ΔR²` and relative MSE uplift;
- appearance and fallback denominators;
- the rule that a failed P2 attempt contributes the same-scope P1 fallback
  prediction instead of removing the appearance;
- repetition-level paired relative-MSE uplift, its p10 statistic and every
  split-ID hash from the long appearance ledger;
- descriptive full-data P1/P2 and local segment R² from row-level refit
  predictions;
- descriptive full-data and local RMSE/MAE, segment counts, unique-x counts and
  observed spans;
- absence of predictions, formulas, domain, hashes and model artifacts in typed
  pipeline failures. Excluded logical input rows remain in `observations.csv`
  with blank `x/y` and typed exclusion status/reason; raw invalid values remain
  confined to `input-exclusions.jsonl`.

The `JSON-REFIT-028` check additionally reconstructs predictions from typed AST
parameters and affine transforms (without executing `formula_display`), checks
both derivative/domain certificates, tie-safe membership cell, continuity,
identifiability/bound/collapse/solver states and reconciles both non-executable
final-refit model dossiers. Dossiers are conditional on certification (13 P1,
10 P2); structure and instance hashes are independently recomputed from their
typed content.

It separately reads `bootstrap-resamples.csv`, reconciles requested/successful/
failed counts, verifies reasons for failed resamples and recomputes the 5th/95th
linear-interpolated stability interval.

It reads all 15 `p2-stability-outer-fits.csv` files (630 outer-fit rows), then
recomputes the dominant family pair/direction, breakpoint distribution and
central 80% interval, plus separate flat-derivative, multiple-solution,
collapse and hard-failure frequencies. Thus P2 stability and partial fallback
are derived from a row-level ledger rather than fixture-level frequency
constants.

Procedure-level uncertainty is independent of the later full-data refit. For a
100% failed P2 attempt with same-scope P1 fallback, repetition effects and
bootstrap resamples remain defined procedure outcomes. The no-balanced-split
fixture therefore has 20 repetition effects at `0`, `p10=0`, and 200/200
successful bootstrap values with interval `[0,0]`. Conversely, constant-y has
P1 MSE `0`, so relative repetition effects, p10 and positive-repetition share
are all `null + ZERO_P1_MSE`, and the gate is `unavailable`, not zero/fail.

It also derives the exact ordered set of 14 decision gates from their canonical
metric sources, operators and unrounded thresholds. Recommendation status,
decision code, P1/P2 validation state, fallback state, certified full-data refit
and model-preview path are checked as one fail-closed state machine; independent
negative mutations must be rejected.

The provenance audit separately hashes the retained result-bearing synthetic
request metadata into `analysis_id`. A cross-bundle invariant verifies that an
equal ID implies the same request hash and canonical statuses, recommendation,
metrics, split/refit state and diagnostics. The stable matrix has 14 distinct
IDs; its sole duplicate represents two analytically identical scenarios and
has an identical state hash.

## Diagnostic reconciliation

The `RESID` gate contains 122 passing checks. It independently reads the long row × procedure × repetition
appearances, reconstructs outer-training local-bin finite-sample Qn scales
(MAD only when Qn is impossible) and zero tolerances, derives signed `z`, row/group summaries and
typed `review_only` flags, recomputes the three pattern states, and recomputes
`Dmax/Drms` from the leave-one-group-out grid. JSON, CSV, four-panel SVG and HTML
source pointers are reconciled.

Two paired tests are decision-bearing evidence rather than fixture labels:

- `RESID-CROSSFIT-023` changes only held-out `row-04.y`; OOF prediction and
  training-only scale remain fixed while residual, `z` and the numeric flag
  change;
- `RESID-NONMUTATION-027` changes only a numeric influence outcome; input,
  validation, decision, recommendation, certified refits and model hashes stay
  identical while a single `HIGH_REFIT_INFLUENCE` review flag appears.

## Concrete edge-state checks

| Fixture | Reconciled result |
|---|---|
| `two_recommended_low_r2_05996` | P1/P2 R² `0.43/0.5996`; RMSE `1.894083/1.587482`; relative MSE uplift `0.297544`; warning remains because unrounded recommended R² is below `0.60` |
| `one_recommended_exact_06000` | recommended R² is exactly `0.6000`; no low-quality warning |
| `p2_no_balanced_split` | P2 procedure status `fallback`, P1 and P2 pooled metrics are equal, fallback rate `1.0`; split p10 and 200/200 bootstrap effects are `0`; full-data P2 refit is independently `unavailable` with no line/boundary |
| `two_recommended_partial_fallback` | exactly one of 50 outer fits fails; its three affected appearances use same-scope P1 predictions (`3/120 = 0.025` fallback rate), while the independent full-data P2 refit remains certified and recommendable |
| `undefined_r2_constant_y` | P1/P2 RMSE and MAE are `0`; R² is typed `null` because the null denominator is zero; relative split effects/positive share are `null + ZERO_P1_MSE`; final formula is consistently `f(x)=4.0` |
| `descriptive_only` | zero OOF appearances and all validation metrics typed unavailable; only a clearly labelled descriptive P1 refit remains |
| `noninvertible_x_scale` | analysis fails during numerical admission; P1/P2 validation and refits are unavailable, and recommendation/model preview are absent |
| `hard_failure` | failed validation/refits contain no numerical losses, model family, formula, domain, hashes or prediction lines |

The exact per-bundle evidence and recomputed values are stored under check IDs
`METRIC-RECOMPUTE-020`, `METRIC-UPLIFT-021`, `METRIC-REFIT-022`,
`JSON-REFIT-028`, `RAW-OPT-TRACE-029`, `FAIL-FALLBACK-020` and
`FAIL-FORMULA-021` in `../audit-results.json`. Across the 15 bundles the
optimizer audit reconciles 40 eligible tie-safe cells and 72 attempt records.

## Schema boundary

`docs/specification/report.schema.json` is the normative conditional Draft
2020-12 schema. It enforces typed null metrics and recommendation/failure/
fallback transitions. Independent arithmetic, sum-of-shares, continuity and
hash equality are intentionally verifier checks because JSON Schema cannot
express them completely.
