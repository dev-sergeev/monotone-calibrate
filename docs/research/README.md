# Исследовательская карта

Дата актуализации: 2026-07-22.

| Тема | Роль в текущем проекте |
|---|---|
| [RQ1 — monotone shape constraints](topics/01-monotone-shape-constrained.md) | глобальная монотонность и сертификаты |
| [RQ2 — continuous segmented regression](topics/02-continuous-segmented-regression.md) | P2, breakpoint и непрерывное сопряжение |
| [RQ3 — interpretable function search](topics/03-interpretable-function-search.md) | конечный registry и интерпретируемые семейства |
| [RQ4 — validation/uplift/residuals](topics/04-validation-uplift-residuals.md) | grouped OOF, uplift и диагностика |
| [RQ5 — reporting](topics/05-reporting-visualization.md) | decision-first отчёт и графики |
| [RQ6 — OpenAI-compatible LLM-SR](topics/06-llm-openai-compatible-integration.md) | typed skeleton selector, trust boundary и fallback |

RQ1–RQ5 были выполнены до LLM-SR и остаются действующими: selector не меняет
их математические thresholds и сертификаты. RQ6 полностью актуализирован под
текущий generate/evaluate/experience loop. Исторический numerical
start-advisor вынесен из активного research contract и сохранён только в
frozen acceptance v1 и архивном live-run.

Правила отбора и фиксации источников описаны в
[`00-evidence-review-protocol.md`](00-evidence-review-protocol.md).
