# Frozen synthetic fixture catalogue v1

Status: definitions frozen by Wayfinder issue 14; execution is `NOT RUN` until
the separate implementation handoff.

All generators use binary64, canonical row order `(x,y,row_id)`, base seed
`20260716` and the seed derivation in `acceptance-manifest-v1.json`. Synthetic
truth is generated independently from the fitter. The generator emits the
input CSV, a truth JSON that is never visible to fitting, and a SHA-256 ledger.

## Data and binary64

`DATA-*` fixtures cover exact-limit and limit+1 parser states, UTF-8 BOM,
LF/CRLF, quoted comma/newline, physically blank lines, exponent syntax,
missing/duplicate headers, semicolon and decimal-comma input, malformed quotes,
invalid UTF-8, field/column/record/file limits, model-column rejection and
regular-file/no-follow behavior.

Logical fixtures pin the following independent states:

- valid `x,y` with absent, valid, blank and duplicate external `row_id`;
- missing, empty, text, overflow, `NaN` and infinity in either coordinate;
- all-invalid rows, `n_used=3`, two unique `x`, constant `x`, constant `y`;
- exact duplicate `(x,y)` rows and repeated `x` with different `y`;
- tie blocks whose only candidate is exactly 40/60 and tie blocks with no
  balanced split;
- row permutation with unchanged stable row IDs;
- ten distinct adjacent one-ULP values that remain distinct after scaling and
  emit `LOW_X_RESOLUTION`;
- raw values `[-10^16,-6,-5,…,5,6,10^16]` for which `unique(t)<unique(x)` and
  both P1/P2 fail closed with `NONINVERTIBLE_X_SCALE` plus
  `NORMALIZED_X_COLLISION` before the solver;
- candidate-specific log/reciprocal domain rejection without changing
  `n_used`.

Boundary files measure raw bytes including BOM, logical records excluding the
header and blank physical lines, header/row field count and post-unquoting
UTF-8 field bytes exactly as defined by `csv-parser-v1`.

## Model and fitting

The four base `x` designs are:

1. 31 equally spaced values on `[0,1]`;
2. 41 seeded jittered values with fixed endpoints;
3. 41 values concentrated near both endpoints;
4. 48 rows on 31 unique values with deterministic repeated-`x` blocks.

Noise regimes are: none; Gaussian `σ=0.02·range(y)`; Student-t with three
degrees of freedom rescaled to the same interquartile scale; heteroscedastic
Gaussian `σ(t)=(0.01+0.05t)·range(y)`; and 5% contamination of signed
`6σ` impulses. Values are not clipped.

P1 has one safe, identifiable truth for every core family. Parameters are in
the interior of registry bounds:

| Family | Increasing truth on local `t` |
|---|---|
| `constant_v1` | `0.75` |
| `poly1_v1` | `0.20+0.80t` |
| `poly2_v1` | `0.20+0.20t+0.60t²` |
| `poly3_v1` | `0.20+0.15t+0.20t²+0.45t³` |
| `exp_affine_v1` | `0.20+0.18exp(2t)` |
| `log_shift_v1` | `0.40+0.70log(t+0.50)` |
| `reciprocal_shift_pos_v1` | `1.20-0.80/(t+0.70)` |
| `logistic_v1` | `0.20+0.90σ(8(t-0.45))` |

The decreasing truth is the exact vertical reflection
`min(y)+max(y)-f(t)`; constant is tested once with canonical direction `flat`.
No acceptance oracle requires exact family recovery when another canonical
family is prediction-equivalent within the frozen collapse tolerance. The
oracle instead checks certificate validity, prediction recovery, canonical
complexity tie-breaking and truthful structure/family trace.

P2 enumerates every ordered pair of the eight core families for both global
non-flat directions. It uses 61 unique equally spaced `x`, `c∈{0.40,0.45,0.50,
0.55,0.60}` and centered branches with shared `μ`. Each nonconstant shape is
normalized to an endpoint change of one before a positive amplitude in
`[0.6,1.4]` is applied; a constant branch has zero amplitude. A case with two
constant or functionally indistinguishable branches must collapse to P1.

Separate exact oracles cover degree-four and forbidden AST rejection, exact
quadratic/cubic derivative minima, domain poles, non-finite values, rank and
condition failures, active bounds, join faults, evaluator mismatch, reversed
start order, competing basins and deterministic attempt/evaluation accounting.

The model/verifier mutation matrix changes exactly one field from a valid P1
or P2 artifact and requires fail-closed rejection: missing root/segment
`formula_display`; one-segment ID other than `single`; swapped or duplicated
`left/right`; wrong interval closure; boundary outside its cell; interval
endpoint unequal to domain/boundary; certificate family/AST/direction/interval
mismatch; continuity side unequal to evaluator output; candidate/recommended
role or decision mismatch; changed parameter with stale instance hash; changed
AST with stale structure hash; unknown key/version/family; non-finite number;
and a third segment. Schema handles structural cases, while the independent
verifier recomputes cross-field, numeric and hash invariants.

`STRICT-JSON-DUPLICATE` injects two conflicting copies of a key before schema
validation into each trusted JSON class: report, model, manifest, COMPLETE,
policy, checkpoint, LLM response, retained gate/reviewer evidence and final
acceptance results. Every copy fails `strict-json-v1`; last-key/first-key wins
behaviour is forbidden. `FALSE-PASS-AGGREGATE` starts from an otherwise valid
results shape, then separately substitutes a numeric gate ID, duplicates one
gate 20 times, changes a declared executor, mismatches an evidence hash,
changes a required platform, uses unequal reviewer digests and reuses one
reviewer ID. Дополнительный бывший counterexample использует source manifest
из одного файла `not an implementation`, произвольные policy/registry,
`test_id=fake`, один environment ID для двух платформ, oracle `trust me` и два
self-declared reviewer ID. Schema catches structural mutations and
`verify_acceptance_results.py` catches every cross-file/digest/key-distinctness
mutation; exact inventory, fixture ledger, execution policy, recomputed
environment identity и retained platform/oracle hashes связывают evidence, а
без двух валидных подписей под caller-selected distinct Ed25519 keys verifier
fail-closed. Подписи являются явной trust boundary для истинности исполнения и
научной интерпретации oracle; verifier не изображает повторное исполнение
произвольного workflow. No mutant may retain
`overall_status=PASS`. Separate filesystem
mutants replace the leaf and then each intermediate directory with a symlink,
swap a regular file during access, and supply a JSON file one byte above the
4 MiB acceptance-verifier limit. Descriptor-anchored no-follow traversal must
reject every symlink/mutation, and size must be rejected before allocating its
payload.

`RELEASE-ANCHOR-MUTATION` отдельно меняет по одному byte в final verifier,
results/evidence/environment/platform/oracle/review/source schema и любом
другом normative acceptance file. Caller-supplied out-of-band snapshot digest
обязан отклонить каждый mutant до загрузки project-local schema. Этот exact
digest также меняют независимо в results, gate evidence и каждом signed
reviewer payload; ни один вариант не сохраняет PASS.

`ENVIRONMENT-IDENTITY-MUTATION` начинает с schema-valid raw probe и меняет по
одному CPython build/compiler/executable hash, solver backend, CPU numerical
feature, BLAS/LAPACK vendor/version, thread-policy либо floating-point-mode
полю. Verifier заново строит identity payload из probe, canonical-hash packages
и shared source/lock/policy/registry refs; каждое изменение обязано давать иной
environment ID и запрещать checkpoint reuse. Несвязанный probe, duplicate или
несортированный package/CPU ledger отклоняется.

`SOURCE-CONTENT-RACE` меняет inode, mtime/ctime либо content раннего source
entry после первого hash и до reviewer signature verification. Завершающий
полный descriptor-safe source pass обязан обнаружить mutation даже при
неизменном наборе путей и даже если bytes были восстановлены. Каждый local
argv target обязан присутствовать одновременно в exact source manifest и
frozen `required_files`.

Authoritative oracle для этой группы — canonical
`evidence/acceptance/source-snapshot.zip`, а не mutable checkout. Отдельные
mutants добавляют duplicate/missing/extra/traversal/symlink-like/directory
members, compression, encryption/data-descriptor/unknown flags, comments,
extra fields, non-0644 mode, timestamp, size/CRC/hash mismatch и decompression
bomb. Exact `ZIP_STORED` member ledger сверяется с source manifest; archive
SHA-256 входит в source manifest, results, every gate, environment identity и
signed preimage. Mutation live path после PASS не меняет identity принятого
archive и потому не может подменить release artifact.
Exact executable catalogue находится в
`docs/acceptance/fuzz_source_archive.py`: три канонических архива принимаются,
а 12 независимых metadata/content-addressing mutations отклоняются под тем же
изолированным verifier runtime.

`SOURCE-INVENTORY-SUBSET` добавляет неучтённый root `conftest.py`, меняющий
pytest collection, и отдельно изменяет формулу/parameter names registry и
числовой threshold resolved policy при сохранении правильных semantic IDs.
Exact top-level allowlist и semantic schemas обязаны отклонить все варианты.
`ORACLE-SEMANTICS` меняет derived `<gate>::contract-v1`,
`<gate>::oracle-v1`, `<gate>::oracle-claim-v1`, fixture-ledger hash либо exact
nullable `tolerance_policy`; каждый вариант отвергается до reviewer verdict.

The production balance remains 40%. Sensitivity fixtures generate true shares
35/65, 40/60 and 45/55; only the first truth lies outside the admissible model
class, while the other two must preserve atomic membership.

## Validation operating characteristics

Each operating cell has 200 untouched replicates. The confirmatory seed
namespace is distinct from all exploratory prototype seeds except for the
top-level published base seed. Unless overridden below, use `n=50`,
`t_i=i/49`, `x=t`, row IDs `<cell>-<replicate>-001…050`, and increasing truth.

The independent noise generator is fixed: SHA-256 of
`acceptance-v1/<cell>/<replicate>/<row>/noise/<counter>` supplies consecutive
unsigned 53-bit integers `j`; map each to `u=(j+0.5)/2^53`, then use pairs in
the Box–Muller transform `z=sqrt(-2 ln u1)·cos(2πu2)`. The generated fixture
CSV bytes and truth JSON are frozen before invoking any fitter; their ledger is
the evidence input. `σ` below is multiplied by the noiseless `range(y)`.

| Cell | Exact truth and construction | Predeclared oracle |
|---|---|---|
| `VAL-P1-CLEAN` | `y=0.20+0.90σ(6(t-0.45))+0.04z` | P1 selection rate ≥0.90; P2 selections count toward false-P2 |
| `VAL-P1-NEAR-EQUIV` | `y=0.20+0.20t+0.60t²+0.03z` | P1/canonical collapse rate ≥0.90 |
| `VAL-P2-STRONG` | `c=19.5/49`; `F(c)=0.60`; left/right slopes `0.20/1.40`; `y=F(t)+0.03z` | P2 selection rate ≥0.80 and Wilson lower bound ≥0.75 |
| `VAL-P2-WEAK` | `c=24.5/49`; `F(c)=0.60`; left/right slopes `0.65/0.80`; `y=F(t)+0.12z` | P1/non-clear-P2 rate ≥0.90; observed effect is reported, never used to redefine fixture |
| `VAL-P2-HARM` | `y=0.25+0.80t+(0.02+0.10t)z` | P1 rate ≥0.90; any `CLEAR_PRACTICAL_UPLIFT` is false-P2 |
| `VAL-P2-UNSTABLE` | `y=0.20+0.20t+0.80·max(t-0.35,0)-0.80·max(t-0.65,0)+0.04z` | P2 cannot be recommended unless all frozen stability gates pass; expected state is `UNSTABLE_SELECTION` in ≥0.80 replicates |
| `VAL-P2-PARTIAL-FALLBACK` | same bytes as replicate 0 of `VAL-P2-STRONG`; test adapter injects `OPTIMIZER_FAILURE` only for outer scopes `(rep,fold)=(0,0),(0,1),(1,0)` | exactly 3/50 outer fits fallback; all `50·n` appearances retained; full-data refit remains independently certified |
| `VAL-P2-FULL-FALLBACK` | same bytes as replicate 0 of `VAL-P1-CLEAN`; adapter injects `NO_VALID_TWO_MODEL` in all outer and full-data P2 scopes | 50/50 fallback, P2 pooled predictions equal P1, uplift/p10/bootstrap point values exactly `0`, no P2 refit/formula |
| `VAL-P1-ZERO-MSE` | typed procedure-result fixture with 32 literal binary64 `y` values and P1/P2 prediction fields copied bit-for-bit from the same values; it does not depend on a fitter recovering zero | P1 OOF MSE exactly `0`; relative effects, positive share and bootstrap unavailable with `ZERO_P1_MSE` |
| `VAL-CONSTANT-Y` | no noise, `y=2.75` | `R²=null/UNDEFINED_R2_CONSTANT_Y`, RMSE/MAE retained, no generic P2 recommendation |
| `VAL-G7` | `n=7`, `x=i/6`, `y=0.2+0.8x` | `DESCRIPTIVE_ONLY` |
| `VAL-G8` | `n=16`, each `x=i/7` repeated twice, `y=0.2+0.8x±0.01` by row parity | exact 4 outer folds and 20 repetitions; ties atomic |
| `VAL-G10` | `n=20`, each `x=i/9` repeated twice, same parity noise | exact 5 outer folds and 10 repetitions; ties atomic |

`VAL-R2-5996` and `VAL-R2-6000` are typed core/renderer fixtures, not
outcome-tuned input datasets. Both retain an independently reconciled
appearance ledger with `SSE0=10000`; recommended SSE is respectively `4004`
and `4000`, giving exact raw `R²=0.5996` and `0.6000`. Only the first contains
`BELOW_PRODUCT_R2`. Mutation of formatting must not change this state.

The fault-injection adapter is available only to acceptance tests and records
the injected scope/code in evidence. It cannot be selected by a production
request, CLI option, input value or wall clock.

Metamorphic copies change outer-test `y`, input row order, start traversal,
output path, timezone, locale and process speed. They prove outer-test non-leakage,
permutation/operational invariance and the explicit semantic replay projection.

Group bootstrap samples `G` canonical group IDs with replacement and, with
multiplicity, sums their already frozen paired outer-OOF squared-loss
contributions across all repetitions. It never refits, reselects or predicts.
A resample is undefined only when its aggregated P1 MSE is zero or either loss
sum is non-finite; the interval is unavailable if fewer than 160 of 200
relative effects are defined.

## Diagnostics

Clean Gaussian, heavy-tail, heteroscedastic, repeated-`x` and contaminated
regimes reuse the independent generator above with 200 replicates per named
operating cell. Unless a row design below overrides it, `n=50`, `x_i=i/49`,
truth `y0=0.25+0.75x`, and `σ=0.03·range(y0)` with the same SHA/Box–Muller
noise stream. Indices below are zero-based and fixed before fitting.

Exact targeted fixtures are:

| ID | Construction | Typed oracle |
|---|---|---|
| `DIAG-CLEAN` | common design, no injection | residual row false-flag rate over all `50·200` rows ≤0.02 |
| `DIAG-RESIDUAL-ONLY` | common design; add signed `+6σ` after base noise to row `24` | row 24 has `LARGE_OOF_RESIDUAL` in ≥0.80 replicates and its group is below every influence threshold in ≥0.80 |
| `DIAG-INFLUENCE-ONLY` | 41 groups: `x_i=i/200`, `i=0…39`, with `y=0.25+0.10x+0.01z`, plus endpoint `(1,1.25)`; same selected-structure refit protocol | endpoint has ordinary residual but `HIGH_REFIT_INFLUENCE` in ≥0.80 replicates; clean interior group false-influence rate ≤0.10 |
| `DIAG-BOTH` | same endpoint-leverage design, endpoint response additionally shifted by `+6·0.03·range(y0)` | endpoint has both flags in ≥0.80 replicates |
| `DIAG-ZERO-SCALE` | typed appearance ledger: every outer-training residual is literal binary64 `0`; test residual is separately literal `0` and `0.001` | both have `z=null/SCALE_UNDEFINED`; neither emits infinity or a fabricated zero score |
| `DIAG-SYSTEMATIC` | typed OOF ledger of five equal-count bins with means `[0,0.6s,0.6s,0.6s,0]` | `SYSTEMATIC_OOF_RESIDUAL`; a separate `[0,0.49s,0.49s,0.49s,0]` copy supplies the predeclared false side |
| `DIAG-HETERO-THRESHOLD` | ten typed outer-fit scale ledgers; eight have exact max/min ratio `1.99`, then a separate copy has exact ratio `2.01`; remaining two are `1.0` | first copy has no flag, second has `HETEROSCEDASTIC_PATTERN` (8/10 meets `≥2.0`) |
| `DIAG-BREAKPOINT-LOCAL` | typed P2 OOF ledger with `c=0.5`, five appearances in `[0.45,0.55]`, four residuals `+0.7s` and one `-0.1s` | count=5, same-sign share=0.8 and mean=`0.54s` trigger `BREAKPOINT_LOCAL_BIAS`; changing one `+0.7s` to `-0.1s` supplies the false side |
| `DIAG-LGO-UNASSESSABLE` | four unique groups with a selected cubic structure; delete one group | remaining three unique supports give `INFLUENCE_UNASSESSABLE`, never zero influence |

Every diagnostic copy carries a byte-identical serialized analytic state
captured before diagnostics. After diagnostic computation, `n_used`, input and
model hashes, validation/refit metrics, decision and recommendation bytes must
match it exactly; only `/diagnostics`, diagnostic exports and presentation
pointers may differ.

## Report additions

The 15 frozen report-contract states remain the semantic golden basis. The
production implementation adds explicit full-bundle fixtures for
`LOW_X_RESOLUTION` and fail-closed `NORMALIZED_X_COLLISION`, plus `COMPLETE`,
usable model, prediction, compressed stability/bootstrap exports and exact
CSVW sidecars. Goldens compare canonical JSON, required DOM/pointer state and
geometry invariants; generated timestamps, report IDs, runtime and gzip header
bytes are never byte-golden fields.

## Frozen demo

`DEMO_STRONG_P2_V1` has 30 unique integer `x=0…29`, boundary `c=11.5` and
exactly 12/18 membership (40/60):

```text
F(x) = 2 + 0.15x,                         x <= 11.5
       2 + 0.15*11.5 + 1.20*(x-11.5),    x > 11.5
```

Add the deterministic noise cycle
`[0.00, 0.04, -0.03, 0.02, -0.04, 0.01]`. Row IDs are `demo-001` through
`demo-030`. The exact frozen fixture is
[`fixtures/demo-strong-p2-v1.csv`](fixtures/demo-strong-p2-v1.csv), SHA-256
`2cb8c3455e3ad236cc5844696ba2d9f36cf46d64d7f984e1b8d1e869afa4dcad`.
The oracle is a fully admitted exit-0 run with `TWO_RECOMMENDED`, a
certified continuous P2 model, both segment shares, red clipped boundary plot,
ready model, successful in-domain prediction and `verify` PASS. No fast/demo
policy may replace the production policy.
