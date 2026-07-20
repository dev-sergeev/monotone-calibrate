# Контракт формы поставки и эксплуатационного контура v1

Статус: форма поставки, report prototype и acceptance specification утверждены; production execution выполняется отдельным implementation handoff  
Дата: 2026-07-16  
Связанный тикет: 13 — выбрать форму поставки и эксплуатационный контур (внутренний архив, не включён в публичный репозиторий)

Основания: [RQ5 — отчётность и визуализация](../research/topics/05-reporting-visualization.md), [контракт данных](01-data-contract.md), [контракт моделей](02-model-contract.md), [validation/uplift](03-validation-uplift-contract.md), [диагностика остатков и влияния](04-residual-diagnostics-contract.md) и [численный fitting verdict](05-fitting-strategy-verdict.md).

## 1. Решение

MVP поставляется как **локальный возобновляемый batch CLI** на CPython 3.12. Один запуск принимает один CSV-файл с одной кривой и создаёт один неизменяемый каталог отчёта. Основной human-readable артефакт — статический responsive `report.html` со SVG-графиками; canonical machine state — versioned `report.json`; готовая рекомендованная функция — отдельный типизированный `models/recommended-model.json`.

Веб-сервер, удалённый API, desktop GUI, notebook UI, обязательный PDF и стабильный публичный Python API в MVP не входят. Python-core остаётся внутренним тестируемым модулем, а CLI, CSV-reader, renderer и predictor — его адаптерами. Ни CLI, ни HTML не пересчитывают решение: все представления строятся из одного typed result.

Это deliberately не интерактивный сервис. Полный repeated grouped outer
validation, paired OOF group-bootstrap и selected-structure
leave-one-`x`-group refits могут занимать значительное время. CLI обязан
показывать прогресс, сохранять детерминированные checkpoints и возобновлять тот
же анализ; обещание latency до измерительного гейта запрещено.

## 2. Grilling: закрытые ветви решения

Пользователь передал дальнейшие решения в hands-off режим. Поэтому каждая ветвь ниже закрыта рекомендуемым default, а не выдана за отдельно подтверждённый пользовательский выбор.

| Вопрос | Принятое решение | Почему не альтернатива |
|---|---|---|
| Кто запускает MVP? | аналитик локально для одного файла | multi-user tenancy и auth не нужны для демонстрации `файл → функция → отчёт` |
| CLI, library или Web? | CLI; внутренний library-core без public compatibility promise | Web не уменьшает вычислительную стоимость и добавляет upload/privacy/server state |
| Sync или batch? | resumable batch | полный contract не имеет честного interactive SLA |
| Вход? | один UTF-8 comma-separated CSV | XLSX/TSV/autodetect увеличивают неоднозначность до доказательства core pipeline |
| Human report? | offline HTML + SVG | HTML поддерживает доступный текст, таблицы и печать; tagged PDF требует отдельного renderer gate |
| Machine report? | JSON/JSON Schema + CSV/CSVW + compressed traces | картинка и formula string не дают воспроизводимого typed state |
| Что означает «готовая функция»? | schema-valid model JSON + `predict` command | `eval(formula_display)` небезопасен и не задаёт domain/certificate semantics |
| Где выполняется анализ? | локальный CPU, без runtime network/telemetry | вход, остатки и row-level diagnostics по определению чувствительны |
| Можно ли перезаписать отчёт? | нет; новый каталог на каждый run | старый audit result должен сохраняться |
| Что с долгим run? | checkpoint/resume, deterministic work budget, без decision по wall-clock | лучший incumbent к timeout зависел бы от скорости машины |
| Что при низком `R²`? | готовая функция сохраняется, warning обязателен, exit остаётся успешным | пользователь прямо запретил превращать `0.60` в hard reject |
| Что при insufficient/failed validation? | полный failure/descriptive report, но без `recommended-model.json` | описательную формулу нельзя выдавать за подтверждённую рекомендацию |
| Экстраполяция? | запрещена; predictor возвращает typed out-of-domain result | сертификат действует только на наблюдаемом интервале |
| PDF? | не входит в MVP | browser print не равен tagged/accessibility-tested PDF |

## 3. Reference project и команды

Implementation-handoff обязан создать обычный source project:

```text
pyproject.toml
uv.lock
src/
tests/
docs/
examples/demo.csv
schemas/
```

Поддерживаемый Python range: `>=3.12,<3.13`. Прямые и транзитивные зависимости фиксируются lock-файлом; его SHA-256 входит в provenance.

Установка и reference execution — разные операции:

```bash
uv sync --frozen

uv run --frozen --no-sync monotone-calibrate run \
  examples/demo.csv \
  --output runs/demo
```

Первый `uv sync` может обращаться к package index. Наличие `uv.lock` само по
себе не означает offline-installable bundle. После синхронизации analysis
command использует `--no-sync`. В default `LLM=off` application runtime не
выполняет DNS/HTTP, telemetry, update checks или загрузку assets. Явно
включённый LLM advisor — единственное исключение и может обращаться только к
настроенному OpenAI-compatible base URL.

Обязательные CLI subcommands:

```text
monotone-calibrate run INPUT.csv --output OUTPUT_DIR [--x-unit TEXT] [--y-unit TEXT] [--llm-start-advisor] [--resume] [--recover-stale-lock]
monotone-calibrate predict MODEL.json X.csv --output PREDICTION_DIR
monotone-calibrate verify REPORT_DIR
```

- `run` выполняет полный утверждённый pipeline и публикует bundle;
- `predict` вычисляет typed model artifact без повторного fitting;
- `verify` проверяет schema, hashes, marker полноты и межартефактную согласованность без повторного анализа. Это проверка integrity/consistency, а не доказательство подлинности без внешней подписи.

Численные policy, registry, seeds и solver budgets не являются россыпью CLI-флагов. CLI использует один bundled versioned policy manifest; полный resolved manifest и hash попадают в bundle. Изменение policy — новая версия анализа, а не настройка после просмотра результата.

### 3.1 Опциональный OpenAI-compatible LLM advisor

Production dependency — pinned `langchain-openai`; единственный adapter —
`langchain_openai.ChatOpenAI`. Advisor по умолчанию выключен и включается
`--llm-start-advisor` либо exact env `MONOTONE_CALIBRATE_LLM_ENABLED=true`.
Конфигурация читается из окружения:

| Variable | Rule |
|---|---|
| `MONOTONE_CALIBRATE_LLM_ENABLED` | optional exact boolean, default `false`; CLI flag также включает режим |
| `MONOTONE_CALIBRATE_LLM_BASE_URL` | обязательный absolute `https://` URL; loopback `http://` разрешён для локального/mock endpoint |
| `MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN` | обязательный непустой secret, передаётся явно как `api_key` |
| `MONOTONE_CALIBRATE_LLM_MODEL` | обязательный непустой provider model/deployment ID |
| `MONOTONE_CALIBRATE_LLM_TIMEOUT_SECONDS` | optional, default `20`, диапазон `1…120` |
| `MONOTONE_CALIBRATE_LLM_MAX_RETRIES` | optional, default `1`, диапазон `0…3` |
| `MONOTONE_CALIBRATE_LLM_ALLOW_INSECURE_HTTP` | optional boolean, default `false`; разрешает non-loopback `http://` только как явный риск пользователя |

Значения передаются `ChatOpenAI(model=…, base_url=…, api_key=…,
temperature=0, use_responses_api=False, streaming=False, stream_usage=False,
timeout=…, max_retries=…)` явно;
неявные ambient `OPENAI_*` aliases не читаются. LangSmith tracing, callbacks,
tools, remote files, conversation state и provider-specific `extra_body`
запрещены. Truthy `LANGSMITH_TRACING`/`LANGCHAIN_TRACING_V2` даёт
`LLM_TRACING_FORBIDDEN` до network call.

Для full-data и каждого outer-training scope строится bounded summary максимум
64 deterministic `x`-bins только из training `x/y`; одна chat request возвращает
strict JSON по installed schema. Допускаются только конечные parameter vectors
по [`llm-start-advice.schema.json`](llm-start-advice.schema.json) для заранее
известных replaceable start-slot IDs. Advisor не добавляет
family/AST, не пишет формулу, не меняет число starts/evaluations, bounds,
registry, splits, thresholds или certificate. Anchor starts сохраняются.

Exact ID templates, nonlinear vector order/length, inclusive bounds, anchor
ordinals, replaceable ordinals и deterministic fallback vectors заморожены в
[`start-slot-policy-v1.json`](../acceptance/start-slot-policy-v1.json). Его
SHA-256 входит в resolved policy, advisor prompt, ledger и `analysis_id`;
неизвестный/повторный slot либо одна неверная vector отклоняет весь response
данного scope с atomic deterministic fallback.

Base URL до создания клиента нормализуется только exact algorithm из
[`identity-policy-v1.json`](../acceptance/identity-policy-v1.json): userinfo,
query, fragment, percent-encoded/ambiguous path, Unicode host и dot-segments
fail closed; default port удаляется, IPv4/IPv6 canonicalized, redirects
отключены. Allowlist сравнивает normalized origin, а identity хранит hashes
origin и полного normalized endpoint. Raw response ограничен declared byte
limit и разбирается UTF-8 JSON parser-ом, который отклоняет duplicate keys и
non-finite tokens **до** JSON Schema; schema сама такую проверку не заменяет.

Все scope requests выполняются и ответы проходят schema/bounds validation до
первого solver call. Accepted/fallback ledger замораживается, хешируется и
входит в `analysis_id`; fitting и resume читают только этот ledger. Timeout,
HTTP/refusal/malformed output дают `LLM_ADVISOR_UNAVAILABLE` и deterministic
default slots для затронутого scope, но не блокируют core pipeline. Access
token никогда не записывается, не хешируется и не логируется; raw base URL
заменяется SHA-256 normalized origin и normalized endpoint. Report
предупреждает, что training
summaries передавались выбранному пользователем endpoint.

## 4. Физический входной адаптер v1

MVP принимает обычный seekable файл. `stdin`, URL, object storage, XLSX, TSV и clipboard не поддерживаются.

CSV contract:

- UTF-8; UTF-8 BOM допускается и фиксируется в audit;
- разделитель `,`, quote character `"`, decimal separator `.`;
- LF и CRLF;
- первая logical record — header;
- все header names уникальны; case-sensitive `x` и `y` встречаются ровно по одному разу;
- `row_id` необязателен и также не может дублироваться;
- после CSV-unquoting вокруг `x/y` удаляются только ASCII space/tab; пустой после trim литерал невалиден, internal whitespace запрещён;
- числовая грамматика: `[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?`; locale-dependent literals, underscores, hex, `NaN` и infinity недопустимы;
- delimiter/encoding/locale autodetection запрещён;
- дополнительные столбцы не моделируются и не копируются в bundle, но их имена сохраняются;
- headers `weight`, `weights`, `uncertainty`, `sigma` дают `UNSUPPORTED_MODEL_COLUMN`.

Полностью пустая физическая строка не является data record. Logical record с пустыми полями является строкой и проходит обычный skip/audit contract. Невалидные `x/y` пропускаются с `INVALID_ROWS_SKIPPED`, если анализ остаётся вычислим; malformed CSV, duplicate header или invalid UTF-8 дают file-level failure.

Parser safety limits v1:

| Ограничение | Значение |
|---|---:|
| размер файла | `10 MiB` |
| logical data records после header | `10 000` |
| столбцы | `256` |
| размер одного поля | `64 KiB` |

Это пределы безопасного разбора, а не обещание fitting throughput. Превышение даёт `INPUT_LIMIT_EXCEEDED` до modelling; строки не усекаются. Exact inclusive byte/count semantics, quoted-newline и blank-line rules зафиксированы `csv-parser-v1` в [acceptance manifest](../acceptance/acceptance-manifest-v1.json).

Adapter открывает regular file один раз, проверяет отсутствие symlink, читает ограниченный byte array, вычисляет SHA-256 и разбирает **те же bytes**. `fstat` до/после чтения обнаруживает замену или изменение; `INPUT_CHANGED_DURING_READ` останавливает run. При `--resume` файл читается заново и обязан дать тот же hash. В provenance сохраняются basename, media type и SHA-256, но не абсолютный путь. Исходный файл не копируется в bundle.

## 5. Canonical request и глубокая граница core

Физический адаптер строит `CalibrationRequest`, содержащий:

- immutable logical rows и input audit;
- units (`unspecified` по умолчанию);
- policy/registry/solver manifest IDs и hashes;
- fixed base seed и deterministic task namespace;
- presentation locale `ru-RU` и machine timestamps в UTC;
- output/work paths как operational metadata, не часть математической задачи.

Core interface семантически равен:

```text
calibrate(CalibrationRequest) -> CalibrationResult
predict(ModelArtifact, x[]) -> PredictionResult
```

`CalibrationResult` — единственный источник recommendation, metrics, models, diagnostics, warnings и failures. Renderer не имеет доступа к solver и не может изменить threshold, округление решения или candidate selection.

## 6. Обязательный output bundle

Complete bundle имеет фиксированную структуру:

```text
report/
├── report.html
├── report.json
├── report.schema.json
├── model.schema.json
├── models/
│   ├── one-model.json              # если существует certified P1 refit
│   ├── two-segment-model.json      # если существует certified P2 refit
│   └── recommended-model.json      # только при validated recommendation
├── observations.csv
├── observations.csv-metadata.json
├── oof-appearances.csv.gz
├── oof-appearances.csv-metadata.json
├── p2-stability-outer-fits.csv.gz
├── p2-stability-outer-fits.csv-metadata.json
├── bootstrap-resamples.csv.gz
├── bootstrap-resamples.csv-metadata.json
├── influence-groups.csv
├── influence-groups.csv-metadata.json
├── plot-data.csv
├── plot-data.csv-metadata.json
├── input-audit.jsonl.gz
├── figures/
│   ├── one-function.svg
│   ├── two-segment.svg
│   └── residuals.svg
├── trace/
│   ├── fit-attempts.jsonl.gz
│   └── llm-advisor.jsonl.gz        # только если advisor был явно включён
├── provenance/
│   ├── request.json
│   ├── resolved-policy.json
│   ├── registry.json
│   ├── environment.json
│   ├── source.json
│   └── dependency-lock.json
├── manifest.json
└── COMPLETE
```

Правила:

1. `report.json` — canonical report state по JSON Schema Draft 2020-12; RFC 8259 non-finite numbers запрещены.
2. HTML, CSV, SVG и model artifacts строятся только из validated typed state.
3. `one-model.json`/`two-segment-model.json` появляются только для соответствующих finite certified full-data fits.
4. `recommended-model.json` появляется **только** при validated recommendation. `DESCRIPTIVE_ONLY`, `NO_VALID_MODEL` и `PIPELINE_FAILURE` не создают готовую рекомендованную функцию.
5. Отсутствующий или failed P2 остаётся видимой строкой/панелью с raw points и typed reason; фиктивной линии нет.
6. Long OOF, P2 outer-fit, bootstrap и attempt exports сжимаются gzip, но не выбрасываются; gzip header является operational metadata и не входит в semantic hashes. Appearance-level residual diagnostics находятся в `oof-appearances.csv.gz`, а не в скрытом дополнительном export.
   `trace/llm-advisor.jsonl.gz` появляется iff advisor включён и содержит
   только bounded request hashes, schema-valid accepted start vectors и typed
   fallback states — без access token, raw base URL и свободного ответа модели.
7. `manifest.json` валиден по production `manifest.schema.json`, содержит
   отсортированные по bytewise-ASCII path записи всех содержательных artifacts
   и не перечисляет себя либо `COMPLETE`. Exact bytes: UTF-8 JSON, keys каждого
   object в Unicode-scalar order, compact separators, один завершающий LF;
   duplicate keys и non-finite numbers запрещены.
8. `COMPLETE` имеет exact bytes compact canonical JSON плюс LF:
   `{"analysis_id":"<64 lowercase hex>","manifest_sha256":"<64 lowercase hex>","schema_version":"complete-v1"}\n`,
   валидный по `manifest.schema.json#/$defs/complete_marker`, где hash вычислен
   по exact bytes `manifest.json`. Marker означает полноту
   **bundle**, а не успешный анализ или наличие модели. Его наличие не заменяет
   schema/reconciliation verification.

Единый bounded decoder `strict-json-v1` применяется **до** JSON Schema,
hashing или business logic ко всем report/model/manifest/COMPLETE, installed
policy, checkpoint, LLM response, retained evidence и final acceptance-results
JSON. Он требует strict UTF-8 без BOM, один JSON value, отклоняет duplicate
keys, `NaN`/Infinity, trailing data и depth/member/item/byte-limit excess.
Draft 2020-12 `format` проверяется как assertion с FormatChecker; для machine
timestamps дополнительно обязателен canonical UTC suffix `Z` по schema pattern.

Bundle считается чувствительным: он содержит `x`, `y`, row identifiers, exclusions, residuals и influence. Публикация каталога раскрывает эти данные.

`observations.csv` содержит ровно одну строку на каждую logical data record после header, включая excluded records. Поля parsed `x/y` для invalid records равны empty/null по CSVW metadata; `input_status` и reason codes обязательны. Raw invalid strings остаются только в bounded `input-audit.jsonl.gz`; `report.json` и HTML показывают canonical `row_id` и reason codes, но не повторяют raw invalid payload. Поэтому `n_input`, used и excluded полностью reconciled без дублирования чувствительного сырого текста.

## 7. Typed model artifact и `predict`

Model JSON хранит данные, а не исполняемый текст:

- schema/model version, registry version, `model_structure_hash` и parameter-bearing `model_instance_hash`;
- recommendation role и decision state;
- P1 либо ordered P2 branches;
- `family_id`, canonical AST identifier, parameters и transforms;
- observed domain, branch intervals и exact membership convention;
- direction, derivative/domain/continuity certificate payload;
- units и warning references;
- `formula_display` только как presentation field.

`predict` выбирает evaluator по versioned `family_id`; `eval`, dynamic import и исполнение `formula_display` запрещены. На observed domain prediction должен совпадать с `report.json` и `plot-data.csv` по `formula_plot_original_y` из `numeric-acceptance-v1`.

За пределами observed domain predictor не экстраполирует: строка получает `prediction=null`, `status=OUT_OF_DOMAIN`. Невалидный `x` получает typed row failure. Для P2 граница и assignment точно совпадают с fitting contract.

Prediction input использует тот же UTF-8 CSV dialect, но требует только `x` и допускает `row_id`. Model JSON ограничен `1 MiB`, а CSV — теми же parser limits, что и `run`. Оба входа должны быть regular non-symlink files: каждый читается один раз в bounded immutable byte snapshot, хешируется и разбирается из тех же bytes с `fstat` до/после. Output является маленьким immutable directory с фиксированными `predictions.csv`, `predictions.csv-metadata.json`, `manifest.json` и `COMPLETE`. CSV содержит encoded `row_id`, исходный parsed `x`, `prediction`, `status` и `reason`. Predictor не читает `y`, не изменяет модель и не создаёт новый calibration report.

Перед вычислением `predict` fail-closed проверяет model schema, поддерживаемые schema/registry versions, recommendation role, recomputed `model_structure_hash`/`model_instance_hash` и независимый certificate. Кандидатный `one-model.json`/`two-segment-model.json` без роли `recommended` публичный predictor не принимает. Unknown family/version, hash mismatch или failed/indeterminate certificate не создают predictions.

Final prediction directory обязан отсутствовать; все четыре файла пишутся в sibling staging, проверяются и публикуются одним directory atomic rename без overwrite. Exit `0` означает schema-valid complete output с хотя бы одной finite prediction; exit `2` — complete output, но finite predictions нет; exit `3` — input/model/schema/version/hash/certificate error; exit `6` — output publication failure; `70/130` имеют те же operational meanings, что `run`. Progress идёт в `stderr`, а `stdout` содержит один JSON summary с counts и output directory.

`verify` ничего не изменяет. Exit `0` означает valid integrity/reconciliation; `2` — bundle существует, но verification failed; `3` — invocation/path error; `70` — internal failure. `stdout` содержит один JSON verdict, details идут в `stderr`.

## 8. Статусы и exit codes

Статусы разделены по namespaces:

- `bundle_status`: `COMPLETE`, `NOT_PUBLISHED`;
- `analysis_status`: `SUCCEEDED`, `NO_RECOMMENDATION`, `FAILED`, `CANCELLED`;
- `recommendation_status`: `ONE_RECOMMENDED`, `TWO_RECOMMENDED`, `NO_VALIDATED_RECOMMENDATION`;
- `decision_reason`: uplift/stability/failure state из validation contract;
- candidate fit/certificate status;
- отдельные массивы warnings и failures.

Один generic `status` не должен смешивать operational failure, выбор P1/P2 и качество модели.

| Exit | Analysis/process outcome | Final bundle |
|---:|---|---|
| `0` | `SUCCEEDED`; validated recommendation готова | обязательный verified bundle + `COMPLETE` + `recommended-model.json` |
| `2` | детерминированный `NO_RECOMMENDATION`, включая `DESCRIPTIVE_ONLY`/`NO_VALID_MODEL`/typed `PIPELINE_FAILURE` | обязательный verified failure/descriptive bundle + `COMPLETE`, без recommended model |
| `3` | invocation/file/CSV/schema/`NO_VALID_ROWS` | для `NO_VALID_ROWS` и deterministic parse/schema outcomes при известном writable output — обязательный failure bundle + `COMPLETE`; до разрешения input/output path trustworthy bundle может отсутствовать |
| `4` | caught fitting/validation infrastructure failure, не являющийся typed candidate/data outcome | при trusted input и writable resolved output обязателен verified failure bundle + `COMPLETE`; если его нельзя сформировать/опубликовать, process возвращает `6` или `70`, а не `4` |
| `5` | deterministic workload/workdir admission rejection либо OS/RAM/environment resource failure | final bundle отсутствует; private workdir сохраняется, если цел |
| `6` | output/render/schema/hash/reconciliation/publication failure | final bundle и `COMPLETE` отсутствуют |
| `70` | неожиданный internal failure | trustworthy final bundle не обещается |
| `130` | пользовательское прерывание | final bundle отсутствует; checkpoint сохраняется |

`BELOW_PRODUCT_R2`, `INVALID_ROWS_SKIPPED`, невозможный P2 и P1 после отклонённого uplift не меняют exit `0`, если validated recommended model существует.

Progress и sanitized logs идут в `stderr`. После завершения `stdout` содержит один UTF-8 JSON object с exit meaning, bundle/analysis/recommendation statuses, plain-text formula, domain, model path, report path и warning codes; при отсутствии рекомендованной модели `formula`, `domain` и `model_path` равны JSON `null`. Наблюдения и raw invalid values туда не попадают.

## 9. Long-running execution, checkpoints и resources

Reference mode: один process, `jobs=1`, BLAS/OpenMP threads `1`. Независимые задачи имеют стабильные IDs, seed derivation и reduction order.

До первого solver call строится полный conservative upper bound по
[`work-policy-v1.json`](../acceptance/work-policy-v1.json) для всех стадий,
которые могут потребоваться данному request: full-data fits, repeated grouped
outer validation, 200 aggregation-only OOF bootstrap resamples и
selected-structure leave-one-group refits. Граф считает rows/groups, tie-safe
boundary cells, registry branches, starts, solver evaluation budgets,
certificates и bounded serialization. Early failures и observed convergence не
уменьшают admission estimate.

Operational policy v1 задаёт три жёстких, версионных предела:

| Admission guard | v1 limit |
|---|---:|
| `max_work_units` | `2 000 000 000` |
| `max_task_nodes` | `50 000 000` |
| `max_workdir_bytes` | `8 GiB` |

`work_unit` — одна заранее budgeted evaluation одной candidate formula/certificate на одной training/grid row; vectorized call на `n` rows считается как `n`, а не как один. Высокоточные certificates и fixed-grid diagnostics имеют manifest-defined worst-case row-equivalent weights. `max_workdir_bytes` считается по uncompressed schema maxima; writers дополнительно отказывают до превышения byte quota. Арифметика forecast checked; overflow сам даёт rejection.

Если хотя бы один bound превышен, solver не запускается: exit `5`, `WORKLOAD_LIMIT_EXCEEDED`, final bundle отсутствует. В пределах admission forecast публикуется в `stderr`/checkpoint; `LONG_RUNNING_WORKLOAD` остаётся warning и не сокращает budget. Ни CLI, ни остаток диска не могут поднять limits; их изменение требует versioned policy и новый `analysis_id`. Acceptance обязана доказать, что known-P2-uplift demo полностью помещается в v1 limits; иначе policy непригодна к выпуску.

Operational input envelope v1 задан parser limits раздела 4: один файл до `10 MiB` и `10 000` logical data records после header. Это не modeling guarantee: parser-valid request дополнительно должен пройти admission guards выше. Для admitted fitting-а намеренно нет latency SLA, wall-clock cutoff или обещания завершения за интерактивное время: полный deterministic pipeline выполняется до конца либо получает явный OS/RAM/environment failure. Это и есть ответ о времени реакции — resumable long-running batch, а не service request. Benchmark protocol зафиксирован тикетом 14 как `BENCHMARK-019`, а фактические runtime/RSS измерения собираются на готовой реализации; они не являются предварительным условием закрытия decision-карты и не создают цикл с implementation-handoff.

Default wall-clock timeout отсутствует. Wall time/RSS — diagnostics и не участвуют в model choice, hash или fallback. Ctrl-C/operational abort не публикует лучший текущий incumbent.

Рабочий каталог `<OUTPUT>.work`:

- создаётся с private permissions;
- захватывается одним process через atomic exclusive lock; второй `run --resume` получает `WORKDIR_LOCKED`; stale-lock recovery только явной командой/флагом с audit record;
- хранит только versioned JSON/JSONL и numeric array files с `allow_pickle=false`; `pickle`, `joblib`, `cloudpickle` и исполнение checkpoint payload запрещены;
- каждый task checkpoint пишется во временный sibling, получает schema version, `analysis_id`, `execution_environment_id`, task/dependency IDs и SHA-256, затем commit-ится atomic rename;
- перед reuse все schemas/hashes/dependencies проверяются; mismatch/corruption даёт `CHECKPOINT_INVALID` fail-closed, а не частичный reuse;
- явно маркирован `INCOMPLETE` и не является отчётом;
- `--resume` разрешён только при точном совпадении `analysis_id`, `execution_environment_id`, policy/source/lock hashes и input hash;
- возобновлённый run обязан дать тот же semantic result, что непрерывный;
- после успешной публикации удаляется; после interruption сохраняется для явного resume/removal;
- concurrent resume, corrupt checkpoint, stale lock и symlink task paths входят в acceptance fixtures.

## 10. Атомарная публикация и retention

Final output path обязан отсутствовать. `--force`, in-place update и auto-delete старых reports запрещены.

Renderer создаёт private sibling staging directory на том же filesystem. До commit выполняются:

1. JSON/model schema validation;
2. formula/prediction/plot/row reconciliation;
3. HTML/SVG safety checks;
4. artifact hashes и manifest validation;
5. проверка обязательных файлов и failure semantics.

Только затем staging публикуется одним dirfd-anchored native no-replace commit:
Linux `renameat2(..., RENAME_NOREPLACE)`, macOS
`renameatx_np(..., RENAME_EXCL)`. Все parent descriptors открыты с no-follow;
path-oriented или overwrite-capable fallback запрещён, а отсутствие primitive
даёт publication failure. При handled failure текущий process удаляет
собственный staging; crash может оставить private sibling с `INCOMPLETE`,
который никогда не считается/публикуется отчётом и не удаляется автоматически
следующим run. Final path при этом не появляется. Symlink или существующий path
даёт `OUTPUT_EXISTS`. При двух writers ровно один может commit.

Автоматической retention policy, общей базы и скрытого cross-run cache нет. Владелец output/work paths управляет хранением. На POSIX каталоги/файлы создаются с `0700/0600`; на других системах наследуется ACL без заявления об эквивалентной защите.

## 11. HTML/SVG и безопасность данных

Human report открывается локально без server и сети:

- no JavaScript, forms, remote URLs, CDN или external fonts;
- template autoescaping для filename, units, row IDs, reasons и raw values;
- формулы строятся из registry fields и показываются как текст;
- SVG не содержит script, event attributes, `foreignObject` или external references;
- CSP: `default-src 'none'; script-src 'none'; connect-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'; font-src 'none'; media-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'`;
- hover не является единственным носителем смысла.

Exact logical identifiers сохраняются в JSON; HTML использует escaped printable representation, а control characters показывает как `\uXXXX`. Во **всех** spreadsheet-facing CSV/CSV.gz, включая observations, OOF, influence и predictions, каждый identifier сериализуется как `id:` + обратимое UTF-8 percent-encoding; `%` всегда кодируется, а байты вне unreserved ASCII `[A-Za-z0-9._~-]` кодируются. Универсальный alphabetic prefix защищает также numeric/date-like IDs (`001`, `1E3`, `2026-07-16`) от spreadsheet coercion. CSVW metadata фиксирует encoding, после decode значение обязано побайтно совпасть с logical ID. Raw invalid field values хранятся только в bounded `input-audit.jsonl.gz` и не дублируются в `report.json`, HTML либо spreadsheet CSV.

Шифрование, secure deletion, публичный redacted bundle и автоматическая отправка отчёта не входят в MVP.

## 12. Reproducibility IDs

Разделяются:

- `analysis_id`: SHA-256 canonical payload из raw input hash, logical adapter
  version, canonical `x_unit/y_unit` и иных result-bearing request metadata,
  resolved policy/registry, source artifact hash, dependency-lock hash, seeds и,
  если advisor включён, его sanitized config/prompt/schema и frozen accepted-or-
  fallback ledger hash;
- `report_id`: идентификатор конкретного execution/report instance;
- `execution_environment_id`: SHA-256 exact-resume payload из OS/version, architecture, CPython implementation/build, resolved package versions, solver backend, CPU numerical features, BLAS/LAPACK vendor/version, thread policy и floating-point mode. Он хранится в checkpoint/provenance и запрещает exact checkpoint reuse в другой numerical environment, но не подменяет semantic `analysis_id`.

Reference execution использует `single-thread-numerics-v1`: effective max
threads и `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`,
`VECLIB_MAXIMUM_THREADS`, `NUMEXPR_NUM_THREADS` равны `1`. Typed raw probe
дополнительно фиксирует CPython build/compiler/executable hash, canonical
installed-package ledger, exact SciPy `least_squares(method="trf")`, sorted CPU
numerical features, BLAS/LAPACK vendor/version и rounding/FTZ/DAZ/FMA mode.
`execution_environment_id` вычисляется только как canonical hash projection
этого probe плюс source/lock/policy/registry hashes; self-declared ID запрещён.

Модель имеет два разных hash:

- `model_structure_hash` — registry/schema versions, canonical AST/family order, direction, segment structure, stable tie-safe membership-cell ID, exact bounding raw-`x` values и membership convention без fitted floating coefficients;
- `model_instance_hash` — тот же payload плюс canonical IEEE-754 representation fitted parameters/transforms; это parameter-bearing hash, требуемый модельным контрактом.

`generated_at`, report ID, elapsed time, RSS, absolute paths и platform diagnostics не входят в `analysis_id` или model hashes. При отсутствии Git source revision — hash canonical source-tree manifest либо установленного wheel/source archive; Git commit добавляется только как provenance. Source-tree manifest сортирует relative paths/content hashes и исключает work/output/cache.

В одной reference platform/environment одинаковый `analysis_id` обязан точно воспроизвести split/task IDs, statuses, recommendation, membership cell, оба model hashes и canonical model JSON. Между macOS/Linux точно совпадают decision state и `model_structure_hash`; coefficients, predictions и metrics сравниваются по именованной policy `numeric-acceptance-v1` из [acceptance manifest](../acceptance/acceptance-manifest-v1.json), поэтому `model_instance_hash` может различаться и не является cross-platform pass condition. Bundle bytes, timestamps и runtime совпадать не обязаны. Canonical serialization, Unicode, binary64 byte order и `-0` semantics заданы `canonical-hash-json-v1` того же manifest.

Reference support matrix для handoff: macOS 14+ arm64 и Ubuntu 24.04 LTS x86_64, CPython 3.12. Windows остаётся unsupported до filesystem, encoding и numerical replay acceptance. Native `uv` — обязательный путь; OCI/single-binary могут быть добавлены позже, но не заменяют core tests.

## 13. Граница MVP

В MVP входят:

- один локальный CSV → один immutable report bundle;
- полный P1/P2 pipeline по утверждённым contracts;
- Russian human report и stable English machine codes;
- ready model JSON и local predictor при validated recommendation;
- offline static report, verification, checkpoint/resume и audit trace.
- optional explicitly configured OpenAI-compatible start advisor через
  `langchain-openai`, не расширяющий registry и имеющий deterministic fallback.

Не входят:

- Web/API/server, auth, multi-user queue и database;
- XLSX/TSV/URL/stdin/autodetection;
- weights/uncertainties и несколько `y`;
- mandatory PDF;
- public stable Python library API;
- extrapolation;
- telemetry, implicit upload или cloud execution; явная отправка bounded
  training summaries только configured LLM endpoint является отдельным opt-in;
- custom post-hoc thresholds/registry through CLI;
- Windows guarantee;
- третий сегмент.

Новый adapter обязан принимать/возвращать те же logical request/result/model schemas и не менять математический, validation или report contracts.

## 14. Обязательные проверки до implementation-handoff

Тикет 14 фиксирует acceptance **спецификацию и fixtures**, а не притворяется прогоном ещё не созданного production-проекта. Проверки, которым нужен готовый код, исполняются затем в отдельном implementation-handoff до его приёмки. Матрица должна включить минимум:

1. clean locked install и отдельный end-to-end run с заблокированной сетью;
2. demo `examples/demo.csv → recommended-model.json + report.html`, затем `predict` и `verify`;
3. BOM/LF/CRLF/quoted fields и rejection malformed/semicolon/decimal-comma/duplicate-header/invalid-UTF-8/oversized input;
4. XSS/CSP/SVG fixtures для filename, units, row IDs и exclusion values;
5. formula/numeric/date-like IDs во всех CSV и round-trip `id:` percent decoding;
6. immutable input snapshot, kill/render fault перед commit и resume, равный uninterrupted result;
7. existing/symlink output, simultaneous writers/resume, stale lock, corrupt checkpoint и rejection resume при другом `execution_environment_id`;
8. JSON↔model↔CSV↔SVG↔HTML reconciliation и integrity/corruption detection;
9. exit-code matrix, включая `<0.60`, skipped rows, impossible P2 и descriptive-only;
10. model predictor conformance, boundary membership и out-of-domain nulls;
11. same-platform instance-hash replay и macOS/Linux structure-hash/tolerance replay;
12. доказательство отсутствия runtime network в default mode и зависимости
decision/hash от wall-clock;
13. полный benchmark outer-validation + OOF-loss bootstrap +
selected-structure influence, публикующий task/work counts, wall time и peak
RSS на named reference hardware без выдуманного latency pass threshold v1;
14. pre-solver rejection по каждому admission guard, checked-overflow test и полный known-P2-uplift demo ниже всех v1 limits;
15. CSVW metadata и round-trip identifier encoding для **каждого** CSV/CSV.gz artifact;
16. доказательство, что PDF отсутствует в mandatory manifest и не маскируется browser print.
17. hostile local OpenAI-compatible mock для `ChatOpenAI`: exact env wiring,
scope non-leakage, strict start output, endpoint-only network, token/raw-URL
redaction, frozen replay и deterministic fallback.

Acceptance specification утверждена тикетом 14 в
[`08-acceptance-handoff.md`](08-acceptance-handoff.md) и machine manifest.
Перечисленные production/runtime проверки имеют начальный статус `NOT RUN` и
должны получить retained `PASS` только в отдельном implementation-handoff.
