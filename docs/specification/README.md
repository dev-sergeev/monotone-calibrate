# Статус спецификаций

Текущий LLM/runtime amendment: [новые формулы и три независимых кандидата](../llm-sr-algorithm.md), 2026-09-12.


Дата актуализации: 2026-07-22.

Human-readable контракты `01`–`07` описывают математические инварианты и
дополнены текущей LLM-SR-политикой. Контракт `08` сохраняет границу более
широкого production handoff и не означает, что все перечисленные там gates уже
реализованы.

Текущий внешний LLM response проверяется по
[`llm-sr-hypotheses.schema.json`](llm-sr-hypotheses.schema.json) и более
строгими cross-field проверками typed parser-а. Актуальная policy находится в
[`../acceptance/llm-sr-policy-v2.json`](../acceptance/llm-sr-policy-v2.json).

Следующие machine schemas относятся к frozen design/acceptance v1 и не должны
использоваться для интерпретации текущего LLM-SR bundle:

- `llm-start-advice.schema.json` — старый numerical start-advisor;
- `production-report.schema.json`, `manifest.schema.json`,
  `resolved-policy.schema.json` — целевой production handoff, существенно
  шире текущего компактного runtime bundle;
- `report.schema.json` — synthetic prototype schema.

Фактический текущий bundle проверяется командой `monotone-calibrate verify` по
manifest hashes и semantic binding модели. Наличие исторической schema рядом с
кодом не является заявлением о runtime validation по этой schema.
