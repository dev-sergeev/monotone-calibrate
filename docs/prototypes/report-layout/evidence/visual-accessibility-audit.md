# Visual and accessibility evidence

Date: 2026-07-16  
Scope: throwaway synthetic report-layout prototype, not production UI

## Verdict by evidence class

| Evidence class | Result | Meaning |
|---|---|---|
| Static HTML/SVG semantics | PASS | machine checks found headings, table semantics, SVG title/description, non-color markers and local-only assets |
| Shared plot geometry | PASS | P1/P2 panels use the same points, domain, axes, ticks, plot rectangle and aspect |
| Direct SVG raster inspection | PASS for sampled decision states | sampled plots are legible and consistent; this is not a browser-layout test |
| Calculated contrast | PASS | text and essential line/marker pairs exceed their contract thresholds |
| Target-browser HTML, 400% reflow and print | NOT RUN | no controllable in-app browser was available |
| Screen reader, forced colors and CVD simulation | NOT RUN | must be executed during implementation acceptance |

The `A11Y-STATIC` entry in `audit-results.json` means **static, machine-checkable
accessibility properties only**. It does not stand for a completed manual
accessibility conformance review.

## Raster samples inspected

The stored previews were regenerated from the current SVG files after clearing
the macOS Quick Look cache, preserving the SVG's explicit white background:

```bash
qlmanage -r cache
qlmanage -t -s 640 -o renders/<fixture> \
  generated/<fixture>/figures/<figure>.svg
```

Inspected samples:

- `two_recommended_low_r2_05996/one-function.svg.png`: all 10 raw points,
  P1 line, axes, units and distinct residual/influence markers are visible;
- `two_recommended_low_r2_05996/two-segment.svg.png`: the same points and
  geometry are retained; both blue branches stop at their intervals and the
  red dashed boundary is directly labelled `c=3.50`, both branches are
  directly labelled and the segment shares are exactly 40/60;
- `two_recommended_low_r2_05996/residuals.svg.png`: four separate panels show
  OOF residual-vs-x, residual-vs-prediction, `|z|` with the `3.5` line, and
  final-refit `Dmax/Drms` sensitivity with independent thresholds;
- `p2_no_balanced_split/two-segment.svg.png`: raw points remain visible while
  line and boundary are absent; the panel states `NO_BALANCED_SPLIT`;
- `p2_no_balanced_split/one-function.svg`: the exact duplicate remains two
  observations and receives a non-jitter `×2` rug/count mark;
- `security_payloads/two-segment.svg.png`: hostile units render as literal text
  and do not create markup, a remote fetch or executable content;
- `noninvertible_x_scale/two-segment.svg.png`: the typed numeric failure is
  represented without a fabricated P2 line or boundary.

An initial path-cached thumbnail displayed a stale black-background preview even
though the source SVG contained a full-canvas white rectangle. Clearing the
cache and rendering to fresh files reproduced the correct image; the stored
previews and canonical SVG/XML checks therefore do not rely on that stale view.

## Contrast calculations

WCAG relative-luminance ratios against the actual component backgrounds:

| Pair | Ratio | Required |
|---|---:|---:|
| body text `#111827` / white | 17.74:1 | 4.5:1 |
| model line `#005A9C` / white | 7.14:1 | 3:1 |
| boundary `#B00020` / white | 7.33:1 | 3:1 |
| point `#4B5563` / white | 7.56:1 | 3:1 |
| warning text / warning background | 10.20:1 | 4.5:1 |
| warning border / warning background | 6.26:1 | 3:1 |
| failure text / failure background | 12.23:1 | 4.5:1 |
| failure border / failure background | 8.76:1 | 3:1 |
| prototype-banner text / background | 15.00:1 | 4.5:1 |

Color is not the sole carrier of meaning: P2 branches differ by line pattern,
the boundary is dashed and labelled, residual/influence observations use
triangle/diamond/combined shapes, and warning/failure states include codes and
text.

## Deferred production acceptance

Issue 14 and the implementation handoff must run, record and retain:

1. supported-browser desktop and narrow-viewport screenshots;
2. 400% zoom/reflow without loss of verdict, warnings, tables or plots;
3. keyboard order, focus visibility and skip-link behavior;
4. at least one platform screen-reader pass over decision, tables and figures;
5. grayscale, common CVD and forced-colors checks;
6. browser print pagination and offline/no-network observation.
