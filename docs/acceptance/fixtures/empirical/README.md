# Empirical acceptance sentinels

These files are frozen parser/replay/end-to-end sentinels. They are observed
datasets, not known-truth P1/P2 fixtures, and their historical NIST model is
not part of `registry_v1`. Acceptance must not assert that a particular
recommended family, breakpoint or segment count is scientifically true for
either file.

## `nist-misra1a.csv`

- Source: NIST/ITL Statistical Reference Datasets, `Misra1a.dat`.
- Canonical source URL:
  `https://www.itl.nist.gov/div898/strd/nls/data/LINKS/DATA/Misra1a.dat`
- Dataset page:
  `https://www.itl.nist.gov/div898/strd/nls/data/misra1a.shtml`
- Retrieved: 2026-07-16.
- NIST description: observed dental-research monomolecular adsorption data;
  response is volume and predictor is pressure; 14 observations.
- Transformation: copied the published `y x` pairs without numeric rounding,
  reordered columns to `x,y`, added deterministic `row_id` values, and emitted
  UTF-8 comma-separated CSV with LF endings.
- Acceptance oracle: 14 valid rows, 14 unique `x`, finite values, deterministic
  run/replay, schema-valid report, no input warning. No family/segment oracle.

## `nist-thurber.csv`

- Source: NIST/ITL Statistical Reference Datasets, `Thurber.dat`.
- Canonical source URL:
  `https://www.itl.nist.gov/div898/strd/nls/data/LINKS/DATA/Thurber.dat`
- Dataset page:
  `https://www.itl.nist.gov/div898/strd/nls/data/thurber.shtml`
- Retrieved: 2026-07-16.
- NIST description: observed semiconductor electron-mobility data; response is
  mobility and predictor is log density; 37 observations.
- Transformation: copied the published `y x` pairs without numeric rounding,
  reordered columns to `x,y`, added deterministic `row_id` values, and emitted
  UTF-8 comma-separated CSV with LF endings.
- Acceptance oracle: 37 valid rows, 37 unique `x`, finite values, deterministic
  run/replay, schema-valid report, no input warning. No family/segment oracle.

The source files are maintained by the U.S. National Institute of Standards
and Technology. This repository preserves attribution and the exact local-file
hashes in `acceptance-manifest-v1.json`; users redistributing the sentinels
should retain this provenance notice.
