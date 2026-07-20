# Live-проверка OpenAI-compatible LLM advisor

Дата: 2026-07-17. Конфигурация была загружена из `.env.example` с явным
`--llm-start-advisor`; access token и raw Base URL в результаты не записывались.

Команда:

```console
uv run monotone-calibrate run examples/demo.csv \
  --output examples/llm-demo-output \
  --dotenv .env.example \
  --llm-start-advisor
```

## Проверка совместимости

- `langchain_openai.ChatOpenAI` успешно выполнил один реальный запрос;
- provider model: `tencent/hy3:free`;
- обычный non-streaming Chat Completions-контур с `use_responses_api=False`
  отработал без fallback;
- ответ прошёл строгий локальный контракт `llm-start-advice-v1`;
- принято 12 из 12 заранее объявленных replaceable P1 start slots;
- unknown family, произвольная формула, изменение P2/validation или solver
  budget моделью не выполнялись;
- access token и raw Base URL отсутствуют во всех артефактах bundle.

Это подтверждает, что заданные model ID, Base URL и access token совместимы с
используемым форматом `ChatOpenAI`.

## Качество

LLM-starts не улучшили full-data P1 относительно deterministic baseline, поэтому
сработал предусмотренный guard: статус `NO_IMPROVEMENT`, baseline P1 сохранён.
Это не transport/schema failure и не ухудшение итоговой модели.

Финальный результат:

- решение: `CLEAR_PRACTICAL_UPLIFT`, рекомендация `P2`;
- P1 refit `R² = 0.997353`, grouped OOF `R² = 0.996226`;
- P2 refit `R² = 0.999986`, grouped OOF `R² = 0.999628`;
- relative OOF MSE uplift P2: `0.901340`;
- bootstrap 90% interval: `[0.841885, 0.949891]`;
- рекомендация, OOF-метрики и hash финальной модели совпали с offline baseline;
- `verify` подтвердил 8 артефактов;
- predictor: три `OK`, один `OUT_OF_DOMAIN`, один `INVALID_X`.

Вывод: качество всего calibration pipeline приемлемое с большим запасом выше
product threshold `R² = 0.60`; OpenAI-compatible интеграция функциональна и
безопасно не ухудшает результат. Однако этот запуск не даёт доказательства
положительной добавочной ценности именно LLM-starts — для такого утверждения
нужна отдельная ablation-выборка из нескольких типов кривых.
