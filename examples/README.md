# Статус примеров

## Текущие

- [`demo.csv`](demo.csv) — входной CSV;
- [`demo-output`](demo-output) — пересобранный 2026-07-22 offline bundle
  текущего runtime с provenance `llm-sr-hypotheses-v1`;
- [`predict.csv`](predict.csv) и
  [`demo-predictions.csv`](demo-predictions.csv) — пример применения текущей
  рекомендованной typed model.

## Исторические

[`llm-demo-output`](llm-demo-output) — неизменённый audit bundle старого
numerical start-advisor от 2026-07-17. Его `llm-start-advice-v1` и
`langchain_openai_start_advisor` не являются текущим contract. Каталог нельзя
дополнять marker-файлами: `manifest.json` требует exact artifact set.

Текущий LLM-SR live provider bundle ещё не опубликован. Алгоритм и offline
fallback документированы в
[`../docs/llm-sr-algorithm.md`](../docs/llm-sr-algorithm.md).
