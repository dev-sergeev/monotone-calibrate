# Monotone Calibrate

Локальный Python-инструмент принимает UTF-8 CSV с полями `x,y`, сравнивает
одну монотонную функцию P1 с непрерывной аппроксимацией P2 из двух функций и
создаёт готовую исполнимую модель, HTML/JSON-отчёт, таблицу результатов и два
scatterplot.

## Быстрый старт

Нужны Python 3.12 и [uv](https://docs.astral.sh/uv/).

```console
uv sync --frozen
uv run --frozen --no-sync monotone-calibrate run examples/demo.csv --output outputs/my-run --no-dotenv
# Дождитесь итоговой JSON-строки, затем запускайте verify:
uv run --frozen --no-sync monotone-calibrate verify outputs/my-run
```

Это **offline baseline**: `--no-dotenv` выключает LLM-SR и использует полный
реестр функций. `run` является блокирующей batch-командой и печатает
JSON-заключение только после окончания расчёта. Для demo с default validation
это обычно занимает десятки секунд. `Ctrl+C` отменяет запуск; благодаря
атомарной публикации незавершённый `outputs/my-run` не создаётся, и `verify`
после такого прерывания закономерно вернёт `INVALID_BUNDLE_DIRECTORY`.

Для короткой проверки установки можно временно сократить статистический
budget (результат не эквивалентен default analysis):

```console
uv run --frozen --no-sync monotone-calibrate run examples/demo.csv \
  --output outputs/smoke-run --no-dotenv \
  --validation-repetitions 1 --bootstrap-resamples 0
```

Успешный `run` печатает рекомендованную структуру, формулу, refit/OOF `R²`,
warning codes и пути к результатам. Главный читаемый артефакт —
`outputs/my-run/report.html`.

Каждый solver-кандидат перед сертификацией и сравнением проецируется на сетку
с шагом `0.001`. Поэтому и отображаемая формула, и фактические коэффициенты
исполнимой модели (а для P2 также точка сопряжения) ограничены тысячными. Ветви
P2 расходятся в общей точке не более чем на один шаг сетки и только в сторону,
которая сохраняет глобальную монотонность.

Готовый демонстрационный результат уже сохранён в
[`examples/demo-output`](examples/demo-output), а его численное заключение — в
[`docs/demo-run.md`](docs/demo-run.md).

Разбор статьи LLM-SR, точное соответствие её шагов реализации и осознанные
ограничения адаптации описаны в
[`docs/llm-sr-algorithm.md`](docs/llm-sr-algorithm.md). Карта актуальной и
исторической документации находится в [`docs/README.md`](docs/README.md).

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
5. При включённом LLM-SR семейства P1 и ordered-пары P2 выбираются итеративным
   generate/evaluate/refine search. LLM называет только типизированные skeleton
   IDs; коэффициенты и breakpoint оценивает локальный solver.
6. P1 и P2 сравниваются repeated grouped OOF-проверкой. P2 рекомендуется только
   при практически существенном, устойчивом uplift; иначе остаётся P1.
7. Если проверочный `R²` рекомендации ниже `0.60`, отчёт обязательно содержит
   `BELOW_PRODUCT_R2`. Значение `0.60` — сигнал пользователю, а не запрет на
   выдачу результата.

Экстраполяция запрещена: модель возвращает результат только внутри
наблюдавшегося диапазона `x`.

## Профили поиска

По умолчанию LLM выключен, и используется детерминированный приближённый профиль
`fast`: полный реестр проверяется на грубой сетке допустимых границ, после чего
перспективные пары уточняют breakpoint многоуровневым поиском. При включённом
LLM-SR тот же профиль применяется уже к найденному portfolio skeletons.

```console
uv run --frozen --no-sync monotone-calibrate run data.csv --output outputs/fast --no-dotenv
uv run --frozen --no-sync monotone-calibrate run data.csv --output outputs/balanced --no-dotenv --search-profile balanced
uv run --frozen --no-sync monotone-calibrate run data.csv --output outputs/quality --no-dotenv --search-profile quality
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
uv run --frozen --no-sync monotone-calibrate predict \
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
uv run --frozen --no-sync monotone-calibrate verify examples/demo-output
```

## LLM-SR selector через OpenAI-compatible модель

Математический pipeline полностью работает без LLM. Чтобы заменить полный
перебор семейств на адаптацию алгоритма LLM-SR, скопируйте `.env.example` в
`.env` и задайте:

```dotenv
MONOTONE_CALIBRATE_LLM_ENABLED=true
MONOTONE_CALIBRATE_LLM_MODEL=my-model-id
MONOTONE_CALIBRATE_LLM_BASE_URL=https://my-provider.example/v1
MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN=my-access-token
MONOTONE_CALIBRATE_LLM_TIMEOUT_SECONDS=20
MONOTONE_CALIBRATE_LLM_MAX_RETRIES=1
MONOTONE_CALIBRATE_LLM_SEARCH_ITERATIONS=4
MONOTONE_CALIBRATE_LLM_ALLOW_INSECURE_HTTP=false
```

`BASE_URL` — API root OpenAI-compatible Chat Completions endpoint; token
указывается без префикса `Bearer`. Non-loopback HTTP по умолчанию запрещён;
для локального `http://127.0.0.1:...` отдельный opt-in не нужен.

Запуск:

```console
uv run --frozen --no-sync monotone-calibrate run data.csv \
  --output outputs/with-llm --llm-symbolic-search
```

В этой команде не следует указывать `--no-dotenv`, если credentials находятся
в `.env`. LLM-SR добавляет provider latency и локальную оценку предложенных
skeletons, поэтому run может выполняться заметно дольше offline baseline и
также печатает JSON только после завершения.

`--llm-start-advisor` оставлен как совместимый alias, но теперь включает тот же
symbolic search, а не старую подстановку solver starts.

Реализация повторяет четыре ключевых шага статьи:

1. десять islands получают полный безопасный P1 baseline и linear P2 skeleton;
2. на каждой итерации LLM с temperature `0.8` предлагает до четырёх новых
   типизированных гипотез по problem specification, агрегированному training
   summary и двум scored examples из выбранного island;
3. локальный solver независимо оптимизирует параметры каждой структуры и
   присваивает fitness `-MSE` только после проверки конечности, монотонности,
   quantization и непрерывности;
4. experience buffer хранит валидные гипотезы, выбирает score-clusters по
   Boltzmann distribution и внутри cluster предпочитает более короткий skeleton.

LLM не получает `row_id`, token или raw endpoint и не возвращает исполняемый
Python. Допустим только strict JSON по
[`llm-sr-hypotheses-v1`](docs/specification/llm-sr-hypotheses.schema.json) с
IDs из versioned registry. Ошибка API, timeout, невалидный JSON или отсутствие
новых валидных гипотез атомарно возвращают полный deterministic registry.

Важное ограничение: найденный на полном CSV portfolio замораживается и
повторно fit-ится внутри outer folds. Поэтому OOF-метрики честно оценивают
параметры и выбор внутри этого portfolio, но условны относительно самой
full-data LLM discovery-стадии. Отчёт явно добавляет
`LLM_TRAINING_SUMMARY_DISCLOSED` и
`LLM_SR_PORTFOLIO_CONDITIONAL_VALIDATION`; это не скрывается как полностью
независимая validation всего stochastic search.

## Исследования, спецификация и тесты

Теоретическое обоснование и первичные источники собраны в
[`docs/research`](docs/research/README.md). Точные контракты данных, моделей, validation,
остатков и отчёта находятся в [`docs/specification`](docs/specification).
Статус formal acceptance и граница исторического snapshot описаны в
[`docs/acceptance/README.md`](docs/acceptance/README.md).

```console
uv run --frozen --no-sync pytest -q
```

Формальный release/security hardening из ранней acceptance-карты осознанно
отложен product-owner waiver для личного проекта. Математические ограничения,
аудит строк, model verification и воспроизводимый demo сохранены.
