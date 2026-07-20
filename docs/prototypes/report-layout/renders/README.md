# Stored SVG previews

These PNG files are evidence previews of selected synthetic SVG states. They
were regenerated from the current SVGs with a cleared macOS Quick Look cache,
for example:

```bash
qlmanage -r cache
qlmanage -t -s 640 \
  -o two_recommended_low_r2_05996 \
  ../generated/two_recommended_low_r2_05996/figures/two-segment.svg
```

They are not canonical report artifacts. The SVG files and `report.json` in
`../generated/<fixture>/` remain canonical for prototype reconciliation.
