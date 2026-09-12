# Примеры запусков

Все команды выполняются из корня репозитория после
`uv sync --frozen --no-editable`. Готовые отчёты можно открыть локально без
API-ключа. Каждый новый запуск требует ещё не существующий output-каталог.

## Текущие

| Пример | Вход | Готовый отчёт | Что показывает |
|---|---|---|---|
| Offline | [demo.csv](demo.csv) | [demo-output/report.html](demo-output/report.html) | Алгоритмические P1/P2; LLM отключена |
| LLM: плавная кривая | [llm-sqrt.csv](llm-sqrt.csv) | [llm-sqrt-output/report.html](llm-sqrt-output/report.html) | LLM рассмотрела обе структуры и выбрала одну кривую |
| LLM: два режима | [llm-two-regime.csv](llm-two-regime.csv) | [llm-two-regime-output/report.html](llm-two-regime-output/report.html) | LLM выбрала разные функции слева и справа от x=5 |

Все три отчёта используют `monotone-report-v3`. В LLM-каталогах доступны
`model-one.json`, `model-two.json`, `model-llm.json`, три графика, метрики и
история поиска. `recommended-model.json` выбирается между P1/P2;
новая LLM-формула доступна отдельно для сравнения и применения.

Оба LLM-примера — реальные запуски OpenRouter от 2026-09-12, модель
`deepseek/deepseek-v4-flash-0731`, prompt `llm-formula-discovery-v2`.
Пакеты сохранены без изменения содержимого,
SHA-256 исходных CSV совпадает с `report.json`. Это синтетические примеры,
не независимый benchmark. Повторный LLM-запуск может найти другие формулы.

| Holdout RMSE | P1 | P2 | LLM |
|---|---:|---:|---:|
| Плавная кривая | 0.006624 | 0.005849 | 0.005346 |
| Два режима | 0.144648 | 0.009506 | 0.060673 |

### Почему LLM выбрала одну или две функции

Каждый запрос явно предлагает LLM рассмотреть обе структуры. Она выбирает
выражения ветвей; оптимизатор подбирает коэффициенты и непрерывную границу.
Итоговый тип определяется по BIC на обучении, до просмотра holdout.

| Пример | Одноветочных / двухветочных гипотез | Лучший BIC одной / двух | Итог LLM |
|---|---:|---:|---|
| Плавная кривая | 4 / 8 | −471.567 / −460.914 | Одна корневая функция: улучшение ошибки от разбиения не окупает сложность |
| Два режима | 5 / 3 | −160.936 / −423.071 | Кусочная функция: экспоненциальная ветвь слева, сумма квадратичного и корневого членов справа |

У второй модели общий стык `x=5`, значения ветвей совпадают. В отчёте
«Почему выбрана такая LLM-функция» показаны train RMSE, число параметров и BIC;
в карточке LLM — отдельные формулы и интервалы. На графике ветви различаются
цветом, граница отмечена красным. Несмотря на улучшение LLM-модели,
алгоритмическая P2 на этом holdout остаётся точнее.

Поиск использовал соответственно 4 и 3 итерации LLM, профиль `fast`,
`--validation-repetitions 2 --bootstrap-resamples 40`. Holdout общий для
трёх вариантов, его целевые значения скрыты от LLM. Подробности —
[протокол проверки](../docs/quality/2026-09-12-formula-discovery-live.md).

## Offline-запуск

```console
MONOTONE_CALIBRATE_LLM_ENABLED=false uv run --frozen --no-sync monotone-calibrate run examples/demo.csv \
  --output outputs/example-offline --no-dotenv
uv run --frozen --no-sync monotone-calibrate verify outputs/example-offline
```

Явное `ENABLED=false` отключает LLM даже при включающей переменной в окружении.
`demo-output` пересобран 2026-09-12 с полным бюджетом проверки: 10 повторов,
200 bootstrap resamples. Для быстрой проверки можно добавить
`--validation-repetitions 2 --bootstrap-resamples 40`.

## OpenRouter: новый поиск формул

Скопируйте [`.env.example`](../.env.example) в `.env.openrouter`, заполните
ключ, модель и endpoint согласно [настройкам OpenRouter](../README.md#новые-формулы-llm-sr-через-openrouter).
Для сохранённых примеров использованы `REASONING_EFFORT=none`,
`MAX_OUTPUT_TOKENS=4096`, `TIMEOUT_SECONDS=120`; у всех настроек префикс
`MONOTONE_CALIBRATE_LLM_`. Локальный `.env.openrouter` игнорируется Git.

```console
MONOTONE_CALIBRATE_LLM_SEARCH_ITERATIONS=4 uv run --frozen --no-sync monotone-calibrate run examples/llm-sqrt.csv \
  --dotenv .env.openrouter --llm-symbolic-search --output outputs/example-openrouter-sqrt \
  --validation-repetitions 2 --bootstrap-resamples 40

MONOTONE_CALIBRATE_LLM_SEARCH_ITERATIONS=3 uv run --frozen --no-sync monotone-calibrate run examples/llm-two-regime.csv \
  --dotenv .env.openrouter --llm-symbolic-search --output outputs/example-openrouter-two-regime \
  --validation-repetitions 2 --bootstrap-resamples 40
```

## GigaChat: тот же поиск через langchain-gigachat

Скопируйте [`.env.gigachat.example`](../.env.gigachat.example) в `.env.gigachat`,
заполните ключ авторизации и идентификатор доступной модели. При необходимости
задайте scope и путь к CA bundle. [Подробная настройка](../README.md#gigachat-через-langchain-gigachat).

```console
uv run --frozen --no-sync monotone-calibrate run examples/llm-sqrt.csv \
  --dotenv .env.gigachat --llm-symbolic-search --output outputs/example-gigachat-sqrt \
  --validation-repetitions 2 --bootstrap-resamples 40
uv run --frozen --no-sync monotone-calibrate verify outputs/example-gigachat-sqrt
```

Готового live-пакета GigaChat пока нет: credentials не предоставлялись.
Интеграция проверена тестами с настоящим SDK и подменённым HTTP-транспортом.
Оба провайдера используют общие ограничения: степень ≤3, максимум две ветви,
монотонность и численный подбор параметров. При сбое API алгоритмические
P1/P2 сохраняются, а LLM-вариант получает явный статус недоступности.

## Проверка и применение готовых LLM-моделей без API

```console
uv run --frozen --no-sync monotone-calibrate verify examples/llm-sqrt-output
uv run --frozen --no-sync monotone-calibrate verify examples/llm-two-regime-output
uv run --frozen --no-sync monotone-calibrate predict \
  examples/llm-sqrt-output/model-llm.json examples/llm-predict.csv \
  --output outputs/example-llm-predictions.csv
```

[llm-predict.csv](llm-predict.csv) содержит три точки внутри диапазона,
одну вне диапазона и одну нечисловую строку. Сохранённые прогнозы:
[корневая формула](llm-sqrt-predictions.csv),
[формула для двух режимов](llm-two-regime-predictions.csv).
Ожидаемые статусы: `3 OK / 1 OUT_OF_DOMAIN / 1 INVALID_X`.
Для offline-модели остаются [predict.csv](predict.csv) и
[demo-predictions.csv](demo-predictions.csv).

## Исторические

[llm-demo-output](llm-demo-output) и [llm-demo-predictions.csv](llm-demo-predictions.csv)
сохраняют audit старого numerical start-advisor от 2026-07-17.
Его `llm-start-advice-v1` и `langchain_openai_start_advisor`
не являются текущим contract.

Внутри готовых output-каталогов нельзя добавлять или редактировать файлы:
`manifest.json` проверяет точный состав и SHA-256 артефактов.
