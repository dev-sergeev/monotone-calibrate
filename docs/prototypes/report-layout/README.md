# PROTOTYPE — report layout comparison

Throwaway UI prototype for Wayfinder issue 12. It answers one question:

> Which report hierarchy makes the recommendation and its limitations obvious,
> while keeping every human view reconcilable with canonical `report.json`?

Build every synthetic fixture and all three layouts with one command:

```bash
python3 docs/prototypes/report-layout/build.py
```

Run the fail-closed static/reconciliation audit after the build:

```bash
python3 docs/prototypes/report-layout/audit_prototype.py
```

The retained verdict is `audit-results.json`. The current matrix contains 15
bundles and explicitly covers the decision-bearing report states, including a
partial P2-fallback case with an independently certified full-data refit, the
two sides of the unrounded `0.60` boundary, negative/undefined R², typed P2 and
pipeline failures, repeated/exact-duplicate observations, diagnostics,
uncertainty, hostile strings, a held-out-`y` metamorphic pair and a diagnostic
non-mutation pair.

`docs/specification/report.schema.json` is the normative conditional Draft
2020-12 schema. Each bundle carries an identical copy, and the build fails on
schema drift. Arithmetic and cross-artifact identities that JSON Schema cannot
express are recomputed by `audit_prototype.py` from the long CSV exports.
The audit currently retains `PASS` for 15 bundles, 1,021 checks and zero
failures. A typed non-executable P1 or P2 final-refit dossier exists only when
that candidate's full-data refit is certified (13 P1 dossiers and 10 P2
dossiers); 12 recommended fixtures additionally carry a non-usable preview.
Each bundle carries a deterministic `trace/fit-attempts.jsonl.gz`, genuine
content-derived input/request/provenance/model hashes and a row-level P2
outer-fit stability ledger. Equal `analysis_id` values are also checked to imply
identical result-bearing state. These remain prototype evidence, not usable
prediction artifacts.

Open a no-JavaScript prototype route with query parameters:

```bash
python3 docs/prototypes/report-layout/build.py --serve
```

Then visit:

```text
http://127.0.0.1:8765/prototype?variant=A&fixture=two_recommended_low_r2_05996
```

Variants:

- `A` — decision-first evidence narrative (proposed winner)
- `B` — symmetric P1/P2 candidate dossiers
- `C` — audit ledger keyed by claims and machine evidence

The generated production-shaped bundles live under `generated/<fixture>/`.
Their `report.html` is always winner A and contains no JavaScript or network
references. Files under `generated/_variants/` are prototype-only comparison
pages. Every artifact is synthetic and visibly labelled `PROTOTYPE`.

This directory is a throwaway primary source. Do not promote its templates to
production; carry only the validated report contract forward.

The exact production bundle shape is intentionally not claimed here. Section
17.0 of `docs/specification/07-report-contract.md` lists the prototype-only
substitutions; `recommended-model.json`, `predict`, production compression,
atomic publication and `COMPLETE` remain `NOT RUN` work for issue 14.

Evidence and limitations are recorded under `evidence/`. In particular, the
machine `A11Y-STATIC` gate covers static semantics and SVG properties; it is not a
claim that target-browser reflow, screen-reader, print or CVD checks ran. No
controllable in-app browser was available for those checks, so they remain
implementation-acceptance work. Variant A is a delegated hands-off default,
not a claim of user approval of a shown mock-up.
