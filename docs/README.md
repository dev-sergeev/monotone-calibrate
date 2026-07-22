# Карта документации

Дата актуализации: 2026-07-22.

## Текущая документация

- [`../README.md`](../README.md) — поддерживаемые команды и фактические
  артефакты текущей реализации;
- [`llm-sr-algorithm.md`](llm-sr-algorithm.md) — адаптация статьи LLM-SR,
  алгоритм, ограничения и validation scope;
- [`research/README.md`](research/README.md) — карта RQ1–RQ6 и текущий
  OpenAI-compatible trust boundary;
- [`specification/README.md`](specification/README.md) — статус human и
  machine-readable контрактов;
- [`demo-run.md`](demo-run.md) — воспроизводимый offline demo текущей версии;
- [`acceptance/README.md`](acceptance/README.md) — статус formal acceptance и
  граница исторического frozen snapshot.

Исследования RQ1–RQ5 и human-контракты 01–07 продолжают задавать математические
и отчётные инварианты. Поправки LLM-SR не меняют CSV contract, монотонность,
баланс P2, локальный solver, uplift thresholds или model runtime.

## Исторические материалы

Следующие артефакты сохранены для воспроизводимости истории, но не описывают
активный application pipeline:

- [`llm-live-run.md`](llm-live-run.md) и исторический статус в
  [`../examples/README.md`](../examples/README.md) — live-run
  удалённого numerical start-advisor от 2026-07-17;
- `docs/acceptance/*-v1.*`, старый gate `LLM-ADVISOR-020` и
  [`specification/llm-start-advice.schema.json`](specification/llm-start-advice.schema.json)
  — frozen design snapshot до замены advisor-а;
- [`specification/production-report.schema.json`](specification/production-report.schema.json)
  и связанные production schemas — целевой handoff v1, не schema текущего
  компактного bundle.

Исторические имена `report.json.llm_advisor` и CLI alias
`--llm-start-advisor` сохранены только для совместимости. Активный режим имеет
`mode=llm_sr_typed_symbolic_search` и output schema
`llm-sr-hypotheses-v1`.
