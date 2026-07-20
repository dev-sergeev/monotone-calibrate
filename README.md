# Monotone Calibrate

Локальный Python-инструмент принимает UTF-8 CSV с полями `x,y`, сравнивает
одну монотонную функцию P1 с непрерывной аппроксимацией P2 из двух функций и
создаёт готовую исполнимую модель, HTML/JSON-отчёт, таблицу результатов и два
scatterplot.

## Быстрый старт

Нужны Python 3.12 и [uv](https://docs.astral.sh/uv/).

```console
uv sync --frozen
uv run monotone-calibrate run examples/demo.csv --output outputs/my-run --no-dotenv
uv run monotone-calibrate verify outputs/my-run
```

Первая команда анализа печатает JSON-заключение с рекомендованной структурой,
формулой, refit/OOF `R²`, warning codes и путями к результатам. Главный
читаемый артефакт — `outputs/my-run/report.html`.

Каждый solver-кандидат перед сертификацией и сравнением проецируется на сетку
с шагом `0.001`. Поэтому и отображаемая формула, и фактические коэффициенты
исполнимой модели (а для P2 также точка сопряжения) ограничены тысячными. Ветви
P2 расходятся в общей точке не более чем на один шаг сетки и только в сторону,
которая сохраняет глобальную монотонность.

Готовый демонстрационный результат уже сохранён в
[`examples/demo-output`](examples/demo-output), а его численное заключение — в
[`docs/demo-run.md`](docs/demo-run.md).

Результат реальной проверки настроенного OpenAI-compatible endpoint сохранён в
[`docs/llm-live-run.md`](docs/llm-live-run.md) и
[`examples/llm-demo-output`](examples/llm-demo-output).

## Формат входа

Минимальный файл:

```csv
x,y
0,1.2
1,1.8
2,2.5
```

Опционально поддерживается `row_id`. Дополнительные обычные колонки
игнорируются и записываются в аудит. Строки с пропущенным, нечисловым или
нефинитным `x`/`y` пропускаются с warning, а расчёт продолжается. Одинаковые
`x` остаются отдельными наблюдениями, но вся группа одинакового `x` неделима
при разбиении и в cross-validation.

Для подбора нужны минимум четыре валидных наблюдения и три различных `x`.

## Что именно выбирается

1. Сначала подбирается P1 — одна функция на всём наблюдаемом диапазоне.
2. Затем рассматривается P2 — ровно две функции с общей монотонностью и точной
   непрерывностью в границе интервалов.
3. Каждый сегмент P2 обязан содержать от 40% до 60% наблюдений. Третьего
   сегмента и сегмента на несколько процентов точек нет.
4. Допустимы константа, полиномы степеней 1–3 и конечный реестр элементарных
   семейств (`exp`, `log`, reciprocal, logistic). Произвольные выражения и
   `eval` не используются.
5. P1 и P2 сравниваются repeated grouped OOF-проверкой. P2 рекомендуется только
   при практически существенном, устойчивом uplift; иначе остаётся P1.
6. Если проверочный `R²` рекомендации ниже `0.60`, отчёт обязательно содержит
   `BELOW_PRODUCT_R2`. Значение `0.60` — сигнал пользователю, а не запрет на
   выдачу результата.

Экстраполяция запрещена: модель возвращает результат только внутри
наблюдавшегося диапазона `x`.

## Профили поиска

По умолчанию используется детерминированный приближённый профиль `fast`. Он
проверяет полный реестр функций на грубой сетке допустимых границ, выбирает
перспективные пары и уточняет их breakpoint многоуровневым поиском. Размер CSV
не ограничивает число рассматриваемых семейств; меняется только вычислительный
бюджет поиска границы.

```console
uv run monotone-calibrate run data.csv --output outputs/fast --no-dotenv
uv run monotone-calibrate run data.csv --output outputs/balanced --no-dotenv --search-profile balanced
uv run monotone-calibrate run data.csv --output outputs/quality --no-dotenv --search-profile quality
```

`balanced` и `quality` используют более плотную coarse-сетку и уточняют больше
пар. `exhaustive` сохраняет полный перебор для небольших задач и сравнительных
тестов, но на больших файлах может работать значительно дольше. Выбранный
профиль, версия policy и фактическое число проверенных кандидатов записываются
в JSON-ответ CLI и `report.json`. Любая опубликованная модель независимо от
профиля проходит одинаковые проверки непрерывности и монотонности.

## Артефакты отчёта

В output directory создаются:

- `report.html` — автономное заключение и графики;
- `report.json` — полные refit/OOF-метрики, uplift, warning и provenance;
- `plot-one.svg` — точки и P1;
- `plot-two.svg` — точки, две интервальные линии и красная граница P2 либо
  явная причина недоступности P2;
- `observations.csv` — исходные точки, прогнозы и OOF residual diagnostics;
- `recommended-model.json` — типизированная исполнимая модель, если validation
  смогла дать рекомендацию;
- `model-comparison.json`, `input-audit.json`, `manifest.json`.

В отчёте отдельно показаны global `R²` P1/P2, `R²` каждого сегмента P2,
проверочный uplift и итоговая рекомендация. Наблюдения с большим OOF-остатком
подсвечиваются и попадают в таблицу `review_only`; автоматически они не
удаляются.

## Применение готовой модели

```console
uv run monotone-calibrate predict \
  examples/demo-output/recommended-model.json \
  examples/predict.csv \
  --output outputs/predictions.csv
```

Статусы строк: `OK`, `INVALID_X`, `OUT_OF_DOMAIN`. Повторяющиеся `x` не
объединяются. Пример результата сохранён в
[`examples/demo-predictions.csv`](examples/demo-predictions.csv).

Команда `verify` проверяет размеры и SHA-256 всех артефактов, а также
согласованность рекомендации, формулы и исполнимой typed model:

```console
uv run monotone-calibrate verify examples/demo-output
```

## Необязательная OpenAI-compatible модель

Математический pipeline полностью работает без LLM. Для экспериментального
советника стартовых значений скопируйте `.env.example` в `.env` и задайте:

```dotenv
MONOTONE_CALIBRATE_LLM_ENABLED=true
MONOTONE_CALIBRATE_LLM_MODEL=my-model-id
MONOTONE_CALIBRATE_LLM_BASE_URL=https://my-provider.example/v1
MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN=my-access-token
MONOTONE_CALIBRATE_LLM_TIMEOUT_SECONDS=20
MONOTONE_CALIBRATE_LLM_MAX_RETRIES=1
MONOTONE_CALIBRATE_LLM_ALLOW_INSECURE_HTTP=false
```

`BASE_URL` — API root OpenAI-compatible Chat Completions endpoint; token
указывается без префикса `Bearer`. Non-loopback HTTP по умолчанию запрещён;
для локального `http://127.0.0.1:...` отдельный opt-in не нужен.

Запуск:

```console
uv run monotone-calibrate run data.csv --output outputs/with-llm --llm-start-advisor
```

Интеграция использует `langchain_openai.ChatOpenAI`. Модели отправляется только
агрегированный training summary без `row_id`; она может заменить только заранее
объявленные P1 solver-start slots. Семейства, число стартов, P2, OOF-валидация,
порог решения и сертификаты модель изменить не может. Ошибка API, timeout или
невалидный JSON дают безопасный deterministic fallback. Access token и raw Base
URL не записываются в bundle.

## Исследования, спецификация и тесты

Теоретическое обоснование и первичные источники собраны в
[`docs/research`](docs/research). Точные контракты данных, моделей, validation,
остатков и отчёта находятся в [`docs/specification`](docs/specification).

```console
uv run pytest -q
```

Формальный release/security hardening из ранней acceptance-карты осознанно
отложен product-owner waiver для личного проекта. Математические ограничения,
аудит строк, model verification и воспроизводимый demo сохранены.
