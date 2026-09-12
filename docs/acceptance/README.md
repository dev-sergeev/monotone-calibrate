# Статус acceptance-артефактов

Поправка 2026-09-12: актуальная ветка LLM описана в
[алгоритме новых формул](../llm-sr-algorithm.md), результаты интеграционной
проверки — [OpenRouter live-run](../quality/2026-09-12-formula-discovery-live.md).
Registry-only policy v2 ниже является исторической.


Дата актуализации: 2026-07-22.

## Текущая LLM-SR-политика

Активный selector описан в
[`llm-sr-policy-v2.json`](llm-sr-policy-v2.json), response schema — в
[`../specification/llm-sr-hypotheses.schema.json`](../specification/llm-sr-hypotheses.schema.json),
а исполняемые проверки находятся в `tests/test_symbolic_search.py`,
`tests/test_application.py` и `tests/test_validation_decision.py`.

Эти тесты подтверждают текущую реализацию, но ещё не являются подписанным
двухплатформенным release evidence из handoff-контракта 08.

## Frozen acceptance v1

`acceptance-manifest-v1.json`, связанные schemas/policies/evidence и gate
`LLM-ADVISOR-020` — исторический snapshot от 2026-07-16. Он описывает удалённый
numerical start-advisor и не является acceptance policy текущего LLM-SR.

Файлы v1 намеренно не переписаны «на месте»: их internal hashes, gate IDs и
evidence bindings относятся к прежнему design snapshot. Изменение их смысла
без новой версии создало бы ложную цепочку доказательств. Для LLM-SR введена
отдельная policy v2; полный новый 20-gate release manifest остаётся будущей
versioned acceptance-работой.

Любое упоминание `llm-start-advice-v1`, `start_advisor`,
`trace/llm-advisor.jsonl.gz` или `LLM-ADVISOR-020` внутри v1-файлов следует
читать только в этом историческом контексте.
