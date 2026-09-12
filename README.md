# Monotone Calibrate

Локальный Python-инструмент принимает UTF-8 CSV с полями `x,y`, сравнивает
три варианта: алгоритмическую P1 (одна кривая), алгоритмическую P2 (две кривые)
и новую формулу LLM-SR с численной оптимизацией параметров. Создаёт отдельные
исполнимые модели, HTML/JSON-отчёт, общую таблицу сравнения и три графика.
LLM-вариант включается явно; P1/P2 работают полностью offline.

## Быстрый старт

Нужны Python 3.12 и [uv](https://docs.astral.sh/uv/).

```console
uv sync --frozen --no-editable
uv run --frozen --no-sync monotone-calibrate run examples/demo.csv --output outputs/my-run --no-dotenv
# Дождитесь итоговой JSON-строки, затем запускайте verify:
uv run --frozen --no-sync monotone-calibrate verify outputs/my-run
```

Установка `--no-editable` не зависит от `.pth`-файла, который macOS может
пометить скрытым. После правок исходников обновите установленную CLI командой
`uv sync --frozen --no-editable --reinstall-package monotone-calibrate`;
тестовая команда ниже проверяет непосредственно `src`.

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

Актуальные LLM-примеры с тремя вариантами сравнения:
[плавная кривая](examples/llm-sqrt-output/report.html) и
[два режима](examples/llm-two-regime-output/report.html).
Исходные CSV, команды OpenRouter/GigaChat и применение сохранённых моделей —
в [`examples/README.md`](examples/README.md).

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
4. P1/P2 всегда используют полный реестр: константа, полиномы степеней 1–3,
   экспонента, логарифм, обратная функция и логистика.
5. При включённом LLM-SR независимо ищется третий кандидат. LLM составляет
   новые деревья математических операций; численные коэффициенты и границу
   подбирает локальный оптимизатор. Готовые family IDs не ограничивают эту ветку.
   Дерево имеет максимум две ветви, степень ≤3 и структурный сертификат монотонности.
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
LLM-SR P1/P2 сохраняют тот же полный поиск. У третьего варианта отдельный
ограниченный бюджет: до четырёх формул на запрос, три старта оптимизатора,
до девяти допустимых границ, максимум 8000 вычислений на формулу.

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
- `model-one.json`, `model-two.json` — отдельные модели P1/P2 (P2 при наличии);
- `model-llm.json` — новая формула при успешном поиске;
- `plot-llm.svg` — новая формула или явный статус недоступности;
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

## Новые формулы LLM-SR через OpenRouter

Скопируйте `.env.example` в `.env`. Для OpenRouter используются следующие
имена настроек проекта (соответствуют `OPENAI_BASE_URL`, `OPENAI_API_KEY` и model):

```dotenv
MONOTONE_CALIBRATE_LLM_ENABLED=false
MONOTONE_CALIBRATE_LLM_PROVIDER=openai
MONOTONE_CALIBRATE_LLM_MODEL=deepseek/deepseek-v4-flash-0731
MONOTONE_CALIBRATE_LLM_BASE_URL=https://openrouter.ai/api/v1
MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN=your-access-token
MONOTONE_CALIBRATE_LLM_TIMEOUT_SECONDS=120
MONOTONE_CALIBRATE_LLM_MAX_RETRIES=1
MONOTONE_CALIBRATE_LLM_SEARCH_ITERATIONS=4
MONOTONE_CALIBRATE_LLM_MAX_OUTPUT_TOKENS=4096
MONOTONE_CALIBRATE_LLM_REASONING_EFFORT=none
```

Токен хранится только локально в игнорируемом `.env`; `false` исключает
неожиданные сетевые вызовы без флага. Для указанного DeepSeek явный
`REASONING_EFFORT=none` оставляет бюджет для JSON-ответа; без него модель может
потратить весь лимит на рассуждения (`LLM_OUTPUT_TRUNCATED`). Обычные `OPENAI_*` переменные процесс
не подхватывает. Запуск:

```console
uv run --frozen --no-sync monotone-calibrate run data.csv \
  --output outputs/with-llm --llm-symbolic-search
uv run --frozen --no-sync monotone-calibrate verify outputs/with-llm
uv run --frozen --no-sync monotone-calibrate predict \
  outputs/with-llm/model-llm.json examples/predict.csv --output outputs/llm-predictions.csv
```

В этой команде не следует указывать `--no-dotenv`, если credentials находятся
в `.env`. Блокирующий `run` печатает JSON-заключение только после окончания
всех запросов, численного подбора и проверки. Alias `--llm-start-advisor`
также включает новый поиск формул.

### GigaChat через langchain-gigachat

Зависимость `langchain-gigachat` включена в проект. После обновления выполните
`uv sync --frozen --no-editable`. Скопируйте
[`.env.gigachat.example`](.env.gigachat.example) в `.env.gigachat`, задайте
`MONOTONE_CALIBRATE_LLM_GIGACHAT_CREDENTIALS` (ключ авторизации GigaChat) и
`MONOTONE_CALIBRATE_LLM_MODEL` (точный идентификатор доступной вам модели).
В примере выбран `GigaChat-2`; модель не подменяется автоматически.

```console
cp .env.gigachat.example .env.gigachat
# Заполните ключ и модель в .env.gigachat, затем:
uv run --frozen --no-sync monotone-calibrate run data.csv \
  --dotenv .env.gigachat --llm-symbolic-search --output outputs/with-gigachat
uv run --frozen --no-sync monotone-calibrate verify outputs/with-gigachat
```

Провайдер выбирается через `MONOTONE_CALIBRATE_LLM_PROVIDER=gigachat`;
по умолчанию используется `openai`, включая OpenRouter. Конфигурации можно
хранить в отдельных локальных dotenv-файлах и выбирать через `--dotenv`.
Окружение процесса имеет приоритет над dotenv.

Для GigaChat SDK получает и обновляет OAuth-токен по `GIGACHAT_CREDENTIALS`.
Альтернатива — заполнить `MONOTONE_CALIBRATE_LLM_GIGACHAT_ACCESS_TOKEN`,
оставив credentials пустым; готовый токен после истечения нужно заменить.
`MONOTONE_CALIBRATE_LLM_GIGACHAT_SCOPE`: `GIGACHAT_API_PERS` (по умолчанию),
`GIGACHAT_API_B2B` или `GIGACHAT_API_CORP` согласно вашему договору.
Все имена переменных в примере имеют префикс `MONOTONE_CALIBRATE_LLM_`;
обычные `GIGACHAT_*` credentials/endpoints и `OPENAI_*` не используются.

TLS проверяется по умолчанию. При необходимости укажите PEM-файл доверенных
сертификатов через `MONOTONE_CALIBRATE_LLM_GIGACHAT_CA_BUNDLE_FILE`.
API и OAuth endpoints настраиваются отдельно через `GIGACHAT_BASE_URL` и
`GIGACHAT_AUTH_URL` с тем же префиксом. `REASONING_EFFORT` оставьте пустым,
если выбранная модель не поддерживает этот параметр.
Параметры SDK: [официальная документация langchain-gigachat](https://github.com/ai-forever/langchain-gigachat/blob/master/libs/gigachat/README.md).

GigaChat предлагает деревья формул; общие проверки и численный оптимизатор
сохраняют степень ≤3, максимум две ветви и монотонность. Каждый запуск
сохраняет сравнение P1/P2/LLM. При ошибке провайдера P1/P2 остаются доступны,
а LLM-кандидат получает явный статус недоступности. В отчёте фиксируются
провайдер, модель и расход токенов; денежную стоимость GigaChat не сообщает.

### Общий поиск формул

В каждом запросе LLM предлагается рассмотреть **одну кривую и две функции
по участкам**. Выражения левой и правой ветвей выбираются независимо:
например, логарифм слева и корень справа. Численный алгоритм подбирает
коэффициенты и непрерывный стык. Итог выбирается по обучающему BIC — ошибке
со штрафом за число параметров, включая границу. Если одной кривой достаточно,
она может выиграть у кусочной.

В отчёте раздел «Почему выбрана такая LLM-функция» показывает число гипотез,
ошибку и BIC каждого типа. При выборе двух ветвей отдельно показаны их интервалы
и формулы. Если LLM не предложила допустимые варианты обоих типов, отчёт явно
помечает сравнение неполным; следующий запрос получает информацию о пропуске.

LLM получает до 200 агрегированных интервалов **обучающих** точек и оценки
ранее оптимизированных выражений. Ответ — strict JSON с деревьями операций
`add`, `mul`, `scale`, `square`, `cube`, `expm1`, `log1p`, `sqrt1p`, `saturate`.
Например, сумма логарифмического и квадратичного членов или корневая
зависимость не требуют добавления нового семейства в исходный реестр.
Коэффициенты LLM не передаёт: `scale` обозначает численный placeholder.

Ограничения проверяются парсером и моделью: степень ≤3 (включая произведения
и вложенные степени), до двух ветвей, до 31 узла, глубина ≤8, до 10
коэффициентов. Монотонность обеспечена композицией монотонных операций на
неотрицательных аргументах. Вложенные нелинейные преобразования запрещены,
чтобы, например, `exp(k*log(1+t))` не скрывал полином высокой степени.
Произвольный Python, `eval`, условия и пользовательские числовые константы
отсутствуют. Это ограниченный поиск новых выражений, а не полная грамматика
программ из статьи.

Для данных с минимум 15 различными `x` примерно 20% внутренних групп `x`
откладываются до поиска. P1, P2 и LLM обучаются на одинаковой оставшейся
выборке и оцениваются на одинаковых контрольных точках. LLM не получает
контрольные ответы или метрики; форма выбирается по обучающему BIC.
После фиксации сравнения параметры выбранной формы повторно подбираются
по всем данным. В отчёте отдельно показаны общий holdout, refit и прежний
OOF для P1/P2. При меньшем числе групп новая формула остаётся описательной.
Рекомендация `recommended-model.json` по-прежнему относится к P1/P2;
третий кандидат сохраняется отдельно для сравнения и применения.

Ошибки провайдера и невалидные формулы не меняют P1/P2. Если новая допустимая
формула не найдена, отчёт явно показывает `UNAVAILABLE`; модель из реестра
не выдаётся за новый результат LLM. Если часть запросов не удалась, ранее
проверенные новые формулы сохраняются с предупреждением. Глобальный optimum
и превосходство LLM-варианта не гарантируются: качество оценивается на данных.

Подробности: [алгоритм и ограничения](docs/llm-sr-algorithm.md).

## Исследования, спецификация и тесты

Теоретическое обоснование и первичные источники собраны в
[`docs/research`](docs/research/README.md). Точные контракты данных, моделей, validation,
остатков и отчёта находятся в [`docs/specification`](docs/specification).
Статус formal acceptance и граница исторического snapshot описаны в
[`docs/acceptance/README.md`](docs/acceptance/README.md).

```console
PYTHONPATH=src uv run --frozen --no-sync pytest -q
```

Формальный release/security hardening из ранней acceptance-карты осознанно
отложен product-owner waiver для личного проекта. Математические ограничения,
аудит строк, model verification и воспроизводимый demo сохранены.

Если `.env` уже настроен:

```bash
uv run --frozen --no-sync monotone-calibrate run examples/demo.csv \
  --output outputs/with-llm \
  --llm-symbolic-search
```

Не добавляйте `--no-dotenv`: иначе настройки LLM из `.env` не загрузятся. Каталог `outputs/with-llm` не должен существовать до запуска.
