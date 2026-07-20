# Security and cross-artifact reconciliation evidence

Date: 2026-07-16  
Scope: synthetic static prototype only

## Machine verdict

`python3 audit_prototype.py` returned `PASS` for 15 declared bundles and 1,021
checks. The
machine-readable detail is `../audit-results.json`.

| Gate | Checks | Failures |
|---|---:|---:|
| A11Y-STATIC | 150 | 0 |
| CMP | 45 | 0 |
| FAIL | 94 | 0 |
| GEOM | 31 | 0 |
| JSON | 196 | 0 |
| METRIC | 105 | 0 |
| PROTOTYPE | 31 | 0 |
| RAW | 32 | 0 |
| RESID | 122 | 0 |
| SECURITY-STATIC | 138 | 0 |
| UNC | 45 | 0 |
| WARN | 32 | 0 |

All 15 reports also validated independently against their Draft 2020-12 JSON
Schema. All 45 generated SVGs parsed with `xmllint`.

## Security properties exercised

- static `report.html` has no JavaScript, forms, frames, embedded objects,
  remote styles/fonts or runtime network dependencies;
- its CSP denies scripts, connections, objects, frames, forms, base URLs,
  fonts and media, while allowing only same-bundle/data images and inline CSS;
- hostile path-like filename, unit, reason, invalid raw value and identifier
  payloads are HTML/XML escaped and appear only as literal text;
- SVG rejects script/event attributes, `foreignObject`, external URLs and
  stylesheet URL constructs;
- artifact links are manifest-shaped relative paths and contain no traversal;
- fixture IDs are allowlisted path components and resolved containment is
  checked before bundle creation;
- all 99,441 nonempty values across 285 identifier columns use canonical `id:`
  plus reversible UTF-8 percent encoding and matching CSVW metadata;
- all 15 compressed optimizer traces have deterministic headers, bounded
  decompression in the verifier and strict JSONL records;
- no bundle contains `COMPLETE` or `recommended-model.json`;
- 13 conditional P1 dossiers, 10 conditional P2 dossiers and 12 recommendation
  previews are explicitly non-usable; no dossier is emitted for an uncertified
  candidate and none can be mistaken for a deployable recommendation.

The variant-switcher pages under `generated/_variants/` are an isolated UI lab
and use inline JavaScript only for switching A/B/C. They are not the selected
static report and are never copied into a production-shaped bundle. The
selected `generated/<fixture>/report.html` remains no-JavaScript.

## Reconciliation properties exercised

- `report.json` is written first and then re-read before rendering human
  artifacts;
- JSON values are finite; undefined values use typed `null` plus status/reason;
- displayed raw threshold values reconcile with canonical JSON before
  rounding (`0.5996` warns, exactly `0.6000` does not);
- OOF and in-sample metric scopes are distinct;
- observation counts, row IDs, flags and `review_only` actions reconcile among
  JSON, CSV, HTML and SVG;
- P1/P2 plots share geometry and raw-point layers;
- P2 lines are clipped to their declared domains, meet at the declared
  boundary and are absent in typed failure states;
- model-dossier/preview paths and contents agree with typed P1/P2 and recommendation states.
- report artifact rows carry media types and cover the non-circular report
  evidence set; their 470 sizes and SHA-256 values are recomputed. The 500
  `prototype-manifest.json` rows additionally cover `report.json`,
  `report.html` and every non-self bundle file;
- all 1,710 HTML table rows carry resolvable pointers; all 3,120
  pointer-bearing nodes resolve, representing 2,827 pointers counted once per
  distinct value within each report;
- input, result-bearing request, policy, registry, source, dependency,
  environment and model hashes are independently regenerated from retained
  canonical payloads rather than trusted display strings; equal analysis IDs
  are rejected if any canonical status/recommendation/metric/split/refit or
  diagnostic state differs;
- the retained registry identifies itself as a prototype exercised subset of
  `registry_v1`; its exact constant/poly1 AST objects are reconciled to report,
  dossier and model-hash payloads rather than trusted shorthand;
- a negative tamper run removed one canonical `id:` prefix and produced a new
  fail-closed `FAIL` audit (including residual-scale, CSV-identifier,
  manifest/artifact failures); a clean rebuild restored the retained `PASS`;
- two clean builds are byte-identical over all 560 generated files. Their
  ledger digest is
  `3bfb9af9571e43223a9ed9e75840f293104de8c6a84b16e44dc81c60ca3df490`.

## Boundaries of this evidence

The prototype verifies its own explicit `0700` directory and `0600` file modes,
but does not prove the production publisher's atomicity, lock/resume behavior,
symlink resistance, cross-platform permissions, browser runtime network state
or spreadsheet-application behavior. Those require the real CLI/bundle
implementation and remain acceptance work under issue 14. This is not a
production security certification.
