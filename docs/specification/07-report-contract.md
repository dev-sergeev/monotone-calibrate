# Контракт отчёта и графиков v1

**Статус:** delegated v1 принят исполнителем по статическим evidence: 15 bundles, 1,021 checks, 0 failures и два независимых финальных GO на одном digest; live-review пользователем не проводился, а production runtime/browser/platform gates зафиксированы тикетом 14 и остаются `NOT RUN` до implementation handoff  
**Дата:** 2026-07-16  
**Связанный тикет:** [12 — утвердить контракт отчёта и графиков](../../.scratch/monotone-curve-approximation/issues/12-prototype-report-contract.md)  
**Место прототипа:** [`docs/prototypes/report-layout/`](../prototypes/report-layout/)

Основания: [RQ5 — стандарты отчётности и визуализации](../research/topics/05-reporting-visualization.md), [математический контракт моделей](02-model-contract.md), [контракт validation, uplift и рекомендации](03-validation-uplift-contract.md), [контракт диагностики остатков и влияния](04-residual-diagnostics-contract.md), [вердикт по стратегии fitting](05-fitting-strategy-verdict.md) и [контракт формы поставки](06-delivery-surface-contract.md).

## 1. Решение и граница контракта

Для прототипной проверки выбран **вариант A — decision-first narrative**. Статический `report.html` сначала сообщает, получена ли проверенная рекомендация и почему, затем раскрывает данные, проверочное сравнение, графики, формулы и диагностику. Пользователь не должен искать отрицательный результат, низкое качество или отказ P2 в приложении.

`report.json` является единственным canonical report state. HTML, SVG, CSV и model artifacts только отображают или экспортируют уже вычисленное typed-состояние. Renderer не имеет доступа к solver и не может:

- выбирать P1/P2;
- пересчитывать метрики, uplift, flags или warning `0.60`;
- скрывать failure/fallback;
- менять округлённое отображение так, чтобы оно противоречило решению по неокруглённому значению;
- создавать формулу либо линию для несуществующей или несертифицированной модели.

Основной human-readable артефакт — локальный статический `report.html` без JavaScript. Обязательные графики — SVG из `figures/`. PDF и общий overlay P1/P2 не входят в обязательный v1: overlay может появиться позднее только как дополнительное представление и не заменяет две основные панели.

## 2. Точный порядок разделов `report.html`

Порядок фиксирован и не меняется между успешными, описательными и failure-отчётами. Недоступный раздел остаётся на своём месте и объясняет причину вместо исчезновения.

| № | HTML anchor | Заголовок | Назначение |
|---:|---|---|---|
| 1 | `#decision` | `Итог решения` | рекомендация или честное отсутствие рекомендации, основной reason, предупреждения и ссылка на готовую функцию |
| 2 | `#data` | `Данные и обработка` | вход, использованные и пропущенные строки, ties, единицы, диапазон и преобразования |
| 3 | `#validation` | `Проверочное сравнение процедур` | только pooled OOF-качество P1/P2, paired uplift, fallback и decision gates |
| 4 | `#curves` | `Графическое сравнение кандидатов` | два обязательных сопоставимых scatterplot: P1 и P2 |
| 5 | `#refit` | `Формулы финального refit` | формулы, сертификаты и только описательные in-sample показатели |
| 6 | `#diagnostics` | `Проблемные наблюдения и диагностика` | OOF-остатки, patterns, repeated-`x` и отдельно refit influence |
| 7 | `#stability` | `Устойчивость и неопределённость` | split sensitivity, bootstrap stability, устойчивость формы/границы и ограничения |
| 8 | `#reproducibility` | `Воспроизводимость и файлы` | policy, registry, solver trace, hashes, environment и machine-readable bundle |

Каждый раздел содержит видимый краткий итог, а затем детали. Язык человекочитаемого отчёта — русский (`lang="ru"`); стабильные machine codes остаются английскими и показываются рядом с переводом.

## 3. Раздел 1 — итог решения

### 3.1 Обязательная карточка

В верхней карточке всегда видны:

- `analysis_status`, `recommendation_status` и `decision_state`;
- короткий ответ: `рекомендуется одна функция`, `рекомендуются две функции` либо `проверенной рекомендации нет`;
- один основной reason и полный список связанных reason codes;
- global primary pooled `R²_OOS`, RMSE и MAE рекомендованной процедуры, если они определены;
- предупреждение `BELOW_PRODUCT_R2`, warning неопределённого `R²` или отсутствие такого warning;
- путь к `models/recommended-model.json` и формула на исходной шкале только при validated recommendation;
- observed domain и прямой запрет экстраполяции;
- ссылки-якоря на сравнение, графики, диагностику и machine artifacts.

Низкий `R²` не отменяет validated recommendation. При `R²_OOS<0.60` карточка одновременно говорит: модель рекомендована по сравнению P1/P2, но её абсолютное проверочное качество ниже product boundary. Нельзя заменять это сообщением `успех` без оговорки либо скрывать готовую функцию.

Если recommendation отсутствует, заголовок не использует слова `лучшая`, `готовая` или `рекомендуемая функция`. `formula`, `domain` и model link отсутствуют/равны `null`; описательные fits могут быть показаны ниже только с явной маркировкой.

### 3.2 Статусы решения

| Decision state | Что показывает карточка |
|---|---|
| `CLEAR_PRACTICAL_UPLIFT` | `TWO_RECOMMENDED`, если full-data P2 прошла сертификаты |
| `NO_UPLIFT_OR_HARM` | P1 рекомендована; P2 показана как испытанный вариант |
| `STATISTICAL_ONLY_SMALL` | P1 рекомендована; улучшение P2 меньше practical gate |
| `PRACTICALLY_PROMISING_UNCERTAIN` | P1 рекомендована; P2 показана как неопределённая альтернатива |
| `UNSTABLE_SELECTION` | P1 рекомендована; перечислены нарушенные stability gates |
| `NO_VALID_TWO_SEGMENT` | P1 рекомендована, если валидна; P2 недоступна с typed reason |
| `DESCRIPTIVE_ONLY` | `NO_VALIDATED_RECOMMENDATION`; показаны только описательные fits |
| `PIPELINE_FAILURE` | `NO_VALIDATED_RECOMMENDATION`; показан failure report без готовой функции |

`BELOW_PRODUCT_R2`, `INVALID_ROWS_SKIPPED` и невозможность P2 являются warnings/ограничениями, а не самостоятельными recommendation states.

## 4. Раздел 2 — данные и обработка

Раздел показывает:

- безопасное имя входного файла, media type и SHA-256 без абсолютного пути;
- `n_input`, `n_used`, `n_excluded`, `n_unique_x` и количество repeated-`x` groups;
- observed domain `[L,U]`, единицы `x/y` или `unspecified`;
- число invalid/missing/non-finite строк по reason code;
- точные дубликаты, ties и факт их атомарности для validation/segmentation;
- сортировку model view при сохранении стабильного `row_id`;
- внутренние affine transforms и их обратимость;
- equal-weight policy и отсутствие автоматического удаления статистически отмеченных точек;
- warnings `LOW_X_RESOLUTION`, `NORMALIZED_X_COLLISION`, `INVALID_ROWS_SKIPPED` и другие input codes.

По умолчанию видна сводка. Полная input-exclusion table с canonical `row_id` и
reason codes доступна в статическом `<details>` и machine artifacts.
Невалидная строка без координат не появляется на scatterplot. Raw invalid
values хранятся только в bounded `input-audit.jsonl.gz` и не дублируются в
`report.json`, HTML или spreadsheet-facing CSV.

## 5. Раздел 3 — проверочное сравнение процедур

Раздел имеет заметный подзаголовок:

> Проверочное качество полной процедуры — pooled out-of-fold; не показатели финального fit.

### 5.1 Основная таблица P1/P2

P1 и P2 находятся в двух строках одной таблицы и используют одни outer appearances, splits и null predictions. Обязательные колонки:

| Поле | Семантика |
|---|---|
| Procedure status | deployable procedure: valid, fallback, unavailable или failed с typed reason; для P2 рядом отдельный `full_data_refit_status` |
| `R²_OOS` | pooled OOF относительно cross-fitted null; отрицательное не обрезается |
| `RMSE_OOF`, `MAE_OOF` | исходные единицы `y` |
| OOF appearances | общий denominator и число repetitions |
| Fallback/failure rate | denominator и основные codes |
| Direction/family stability | частоты, не выбранная на полном файле форма |
| Recommendation role | recommended, tested alternative либо unavailable |

Строка P2 в validation описывает **deployable procedure** «попытаться P2, иначе вернуть P1 prediction того же training scope». Поэтому failure попытки P2 не превращает primary P2 metrics в `NA`: fallback-predictions входят в тот же appearance denominator, procedure status становится `fallback`, показываются точный failure code и fallback rate. При 100% fallback P2 procedure может иметь те же pooled metrics, что P1, а full-data P2 refit одновременно остаётся `unavailable` без формулы, сегментов и линии. `NA — <reason>` допустимо только когда validation procedure в целом не вернула predictions, например при `DESCRIPTIVE_ONLY` или общем `PIPELINE_FAILURE`. Условное качество только успешных P2 attempts запрещено как primary metric.

### 5.2 Paired uplift

Отдельный блок показывает `ΔMSE`, `ΔRMSE`, `ΔMAE`, `ΔR²` и `rel_MSE_uplift`, где положительное значение означает преимущество P2. Рядом перечисляются все gates initial policy v1:

- practical uplift;
- доля repetitions с положительным uplift;
- 10-й percentile split sensitivity;
- paired OOF group-bootstrap stability bound;
- no-harm MAE;
- P2 stability/fallback gates.

Каждый gate имеет `pass`, `fail` или `unavailable`, фактическое значение, threshold policy version и reason. Training/refit `R²` в этом разделе запрещён.

## 6. Раздел 4 — два обязательных scatterplot

### 6.1 Общая геометрия

`figures/one-function.svg` и `figures/two-segment.svg` всегда расположены в таком порядке и используют:

- один immutable point layer из одних и тех же `n_used` строк;
- одинаковые `xlim`, `ylim`, transforms, plotting-area size, aspect ratio, ticks и units;
- полный observed domain без экстраполяции;
- одинаковые point size, opacity, порядок и repeated-point encoding;
- исходные точки поверх optional fills;
- caption с `n_used`, `n_unique_x`, direction и candidate status;
- данные линий только из `plot-data.csv`, сверенные с model artifacts.

Отдельный autoscale запрещён. Zoom разрешён лишь как дополнительный явно подписанный inset и не меняет основные панели. Линия не соединяет наблюдения.

### 6.2 Панель P1

Панель содержит все принятые точки и одну сертифицированную линию только на `[L,U]`. Direct label сообщает `P1`, `family_id`, direction и роль `final refit`. Формула и проверочное качество находятся в соседних разделах, чтобы график не смешивал OOF и refit semantics.

Если certified P1 отсутствует, панель сохраняет те же точки и оси, показывает крупный typed failure panel и не рисует линию.

### 6.3 Панель P2

Для валидной P2:

- левая ветвь рисуется только на `[L,c]`;
- правая ветвь рисуется только на `[c,U]`, при этом membership строк остаётся `x≤c` слева и `x>c` справа;
- обе plot-series содержат общую координату `(c,F(c))` для визуальной проверки continuity;
- граница `c` показана вертикальной линией `#B00020`, толщиной не меньше `3 px`, с коротким штрихом;
- рядом есть текст `граница c=<value>`; то же число, интервалы, `n` и shares повторяются в таблице;
- обе ветви имеют один цвет модели `#005A9C` и прямые labels `левая: <family_id>` / `правая: <family_id>`; различие ветвей не зависит от цвета;
- линия границы не используется как encoding статистической неопределённости.

Background — `#FFFFFF`, основной текст — `#111827`, grid — `#D1D5DB`, исходные точки — fill `#4B5563` с outline `#111827`, линии модели — `#005A9C`. Итоговый прототип обязан подтвердить контраст и различимость в grayscale/CVD; при необходимости допустима версия palette без изменения смысловых ролей и non-color encodings.

Если P2 неосуществима, collapsed, failed или запрещена сертификатом, панель сохраняет точки и общую геометрию, но не рисует ветви и красную границу. Вместо них видны status, reason, affected gate и ссылка на failure detail. Для `NONINVERTIBLE_X_SCALE` запрещены фиктивные формула, boundary и approximation line.

### 6.4 Flags и overplotting

Исходная точка остаётся видимой. Поверх неё используются:

- triangle outline — `LARGE_OOF_RESIDUAL`;
- diamond outline — `HIGH_REFIT_INFLUENCE`;
- star/double outline — оба признака;
- count/rug layer — multiplicity одинаковых координат;
- короткий `row_id` только для выбранных отметок, полная таблица — в diagnostics.

Цвет всегда дублируется формой, контуром и текстом. Jitter по умолчанию выключен; если прототип докажет необходимость, он допускается только как явно подписанная display-only копия.

Curve uncertainty bands в двух основных панелях v1 по умолчанию отсутствуют. Они могут быть включены будущей версией только после определения target, coverage, method, selection scope и simulation-calibrated coverage.

## 7. Раздел 5 — формулы финального refit

Раздел начинается предупреждением:

> Описательные показатели финального fit на всех использованных данных — не out-of-fold качество и не основание выбора P1/P2.

### 7.1 P1

Показываются:

- `family_id`, registry version, canonical AST identifier;
- формула в исходных `x/y` и внутренний transform;
- `[L,U]`, direction и запрет экстраполяции;
- coefficients/parameters;
- domain/derivative certificate, critical points и margin;
- `model_structure_hash` и `model_instance_hash`;
- `R²_fit,all`, RMSE, MAE, `n` и `n_unique_x`;
- identifiability, bound-hit, collapse и solver status;
- ссылка на `models/one-model.json`.

### 7.2 P2

Дополнительно показываются:

- обе формулы и ordered pair `family_left|family_right`;
- `c`, `[L,c]`, `(c,U]`, membership convention и tie-safe cell;
- shared `μ=F(c)` и continuity residual;
- общий direction и независимые derivative/domain certificates ветвей;
- `n`, share, `n_unique_x` и span каждого сегмента;
- общий `R²_fit,all`, оба локальных `R²_fit,left/right`, local RMSE и MAE;
- stability/identifiability/collapse statuses;
- ссылка на `models/two-segment-model.json`.

Локальные `R²` не усредняются и не сравниваются с `0.60`. При постоянном local denominator показывается `NA — UNDEFINED_LOCAL_R2`. Incubator family не может иметь recommendation role; её след остаётся только в audit/trace.

### 7.3 Формула как данные, а не код

Human formula строится только из registry AST и escaped numeric parameters. `eval`, dynamic import и исполнение `formula_display` запрещены. Параметры в основной копируемой формуле записываются shortest round-trip decimal representation для canonical binary64. Допускается отдельная компактная визуальная строка, но она помечается `приближённо`; точная формула и model JSON остаются рядом.

Если evidence-прототип исполняет только подмножество `registry_v1`, retained
registry artifact обязан прямо назвать себя `prototype_exercised_subset`,
перечислить включённые и неисполненные core families и хранить **те же exact
canonical AST objects**, которые используются refit/model hashes. Отдельная
упрощённая AST-нотация без формально проверенного отображения запрещена.

Optimizer summary сообщает `PROFILE_CELLS`, status, число tie-safe cells, starts/attempts/evaluations, competing basins, certificate result и reason/warning codes. Полный список попыток находится в `trace/fit-attempts.jsonl.gz`.

## 8. Раздел 6 — проблемные наблюдения и диагностика

Primary diagnostic относится к рекомендованной процедуре и использует OOF appearances. P1/P2 diagnostics остаются доступны в таблицах для сравнения.

`figures/residuals.svg` содержит отдельные подписанные панели:

1. OOF residual против `x` с нулевой линией;
2. OOF residual против OOF prediction;
3. `|z_OOF|` против `x` с threshold `3.5`;
4. refit influence по independent `x`-group, визуально отделённый заголовком `sensitivity финального refit`.

Рядом находятся:

- row table с raw OOF residual, robust scale, signed `z`, score, flag rate и appearance count;
- repeated-`x` group summary и descriptive pure-error/lack-of-fit decomposition, когда определимо;
- pattern flags `SYSTEMATIC_OOF_RESIDUAL`, `HETEROSCEDASTIC_PATTERN`, `BREAKPOINT_LOCAL_BIAS`;
- influence fields `Dmax`, `Drms`, `Δc`, parameter/share/certificate changes;
- typed flags с `severity`, policy version, explanation и обязательным `action="review_only"`.

OOF и refit residuals имеют разные labels, поля и series. Большой residual не означает high influence и наоборот. Ни один flag не изменяет `n_used`, модель, метрики либо рекомендацию текущего run.

При `DESCRIPTIVE_ONLY` OOF-панели показывают `OOF_DIAGNOSTICS_UNAVAILABLE`; разрешён только явно помеченный descriptive refit residual. Нулевая/неопределённая robust scale даёт `z=null` и reason, а не ноль или бесконечность.

## 9. Раздел 7 — устойчивость и неопределённость

Раздел публикует:

- outer-fold policy, число repetitions, group key и hashes split IDs;
- distribution repetition-level uplift под названием `split sensitivity`, не confidence interval;
- paired fixed-OOF group-bootstrap budget, defined/undefined resamples и
  central-90% **bootstrap stability interval**;
- P2 valid/fallback/failure rates;
- frequency direction и dominant canonical pair;
- distribution breakpoint, central-80% width и частоту 40/60 edge hits;
- `FLAT_PROFILE`, `MULTIPLE_NEAR_OPTIMA`, collapse и hard-failure frequencies;
- sensitivity estimands, если они запускались;
- все thresholds и policy hash.

До simulation-calibrated coverage слово `confidence interval` запрещено. Если uncertainty procedure не завершилась, выводится `UNCERTAINTY_UNAVAILABLE` с причиной; отсутствие интервала не маскируется. P2 в таком случае не получает `CLEAR_PRACTICAL_UPLIFT`.

Split sensitivity и bootstrap относятся к deployable P2 procedure, а не к
наличию отдельного full-data P2 refit. Если P2 attempt закономерно даёт
same-scope P1 fallback, это определённый procedure outcome и он остаётся в
repetition/resample denominator (полный fallback даёт uplift `0`, а не
`null`). `null + ZERO_P1_MSE` используется, когда relative effect математически
не определён из-за нулевого P1 MSE; positive-repetition share и его gate тогда
также `unavailable`, не фиктивный ноль/fail.

## 10. Раздел 8 — воспроизводимость и файлы

Раздел содержит:

- `analysis_id`, `report_id`, generated timestamp и timezone;
- input, result-bearing request metadata, policy, registry, source и
  dependency-lock hashes;
- solver/backend, environment, thread policy, seeds и numerical tolerances;
- validation/bootstrap/influence work counts и fallback/failure counts;
- LLM advisor mode/status, provider model, hashes normalized endpoint origin
  и полного normalized Base URL endpoint,
  prompt/output-schema versions, call/accepted/fallback counts и frozen ledger
  hash; access token и raw base URL запрещены;
- model hashes и certificate versions;
- manifest table: relative path, media type, size и SHA-256;
- ссылки на `report.json`, schemas, model artifacts, CSV/CSVW, SVG, compressed OOF/influence/trace exports и provenance;
- предупреждение, что bundle содержит чувствительные `x`, `y`, row IDs, остатки и влияние.

Если validated recommendation существует, `models/recommended-model.json` виден как основной machine artifact и поддерживается `predict`. При `DESCRIPTIVE_ONLY`, `NO_VALID_MODEL` или `PIPELINE_FAILURE` он отсутствует, и отчёт объясняет это явно.

При включённом advisor-е раздел содержит заметное
`LLM_TRAINING_SUMMARY_DISCLOSED`: численные summaries каждого training scope
были отправлены на настроенный endpoint. Свободный текст/рассуждения модели не
становятся report evidence; сохраняются только schema-valid принятые start
vectors, typed fallback reasons и hashes.

## 11. OOF/refit firewall

Следующие инварианты обязательны во всех представлениях:

1. `R²_OOS`, `RMSE_OOF`, `MAE_OOF`, paired uplift, fallback и stability находятся только в разделе validation и decision summary.
2. `R²_fit`, local segment `R²`, refit RMSE/MAE находятся только в разделе refit с label `описательно / in_sample`.
3. OOF residuals и refit residual/influence не используют общую series, legend label или JSON field.
4. Warning `0.60` никогда не читает training, local или fold-wise `R²`.
5. Final breakpoint не применяется к OOF rows; OOF local membership использует только outer-trained breakpoint.
6. Formula/plot линии — final refit; caption не называет их validation curves.

Prototype reconciliation должен падать, если значение пересечено между этими namespaces.

## 12. `R²=0.60`, undefined values и округление

Решение о warning выполняется один раз в core до rendering:

```text
if recommended.global_primary_R2_OOS.status == "defined"
   and recommended.global_primary_R2_OOS.value < 0.60:
    emit BELOW_PRODUCT_R2
```

- Ровно `0.6000` warning не вызывает.
- `0.5996` вызывает warning даже если обычное округление дало бы `0.600`.
- Отрицательный `R²` показывается отрицательным.
- Undefined denominator даёт `value=null`, `status`, `reason` и отдельный warning; сравнение с `0.60` не выполняется.
- Segment/local и refit `R²` не запускают warning.
- Текст поясняет: `0.60` — относительное уменьшение squared loss против cross-fitted null, не доля описанных точек.

Human formatting policy v1:

| Значение | Формат |
|---|---|
| `R²`, uplift и rates | 4 знака после десятичной точки; до 6 при необходимости показать сторону threshold |
| RMSE/MAE, `x/y`, `c`, spans | 6 значащих цифр и единица рядом |
| Shares | 1 знак после запятой в процентах и точные counts рядом |
| Counts | целые без сокращения |
| Formula parameters | shortest round-trip decimal; compact approximate view допускается отдельно |
| Machine JSON/CSV | достаточная round-trip precision; human rounding туда не переносится |

Если округлённое значение совпадает с decision threshold, но raw находится по одну из сторон, renderer обязан увеличить точность или дописать `ниже 0.60` / `не ниже 0.60`. Формулы и code-like значения используют decimal point независимо от русской локали; локализованный текст может использовать запятую только вне копируемых формул.

## 13. Typed failures и отсутствие фиктивного результата

Failure/warning entry содержит минимум `code`, `scope`, `stage`, `severity`, `reason`, `recommendation_effect` и массив `related_json_pointers`.

| Ситуация | Human report |
|---|---|
| P2 невозможна (`NO_BALANCED_SPLIT`, insufficient support) | P2 table row и plot panel остаются; typed reason, без ветвей/границы |
| P2 fit/certificate failed | failure/fallback rates видны в OOF; full-data panel без линии, если certified refit нет |
| P2 collapsed to P1 | объяснение collapse; фиктивный breakpoint не показывается |
| `NONINVERTIBLE_X_SCALE` | fail-closed numerical admission всей процедуры до solver: P1/P2 metrics и refits недоступны, рекомендации и model artifact нет, no fake formula/boundary/line |
| `DESCRIPTIVE_ONLY` | formulas только как descriptive; ready model отсутствует |
| `PIPELINE_FAILURE`/`NO_VALID_MODEL` | failure report и audit artifacts, без рекомендации |
| `BELOW_PRODUCT_R2` | заметный warning, но validated model и exit success сохраняются |
| Render/schema/hash/reconciliation failure | final bundle и `COMPLETE` не публикуются; повреждённый report не считается failure report |

Panel/table failure state должен оставаться машинно и визуально сопоставимым с успешным состоянием. Ноль, пустая строка и отсутствующая панель не заменяют typed failure.

## 14. Связь human report с `report.json`

Canonical top-level namespaces v1:

```text
/status
/decision
/recommendation
/input
/preprocessing
/validation
/refit
/diagnostics
/warnings
/failures
/artifacts
/provenance
```

Минимальная карта разделов:

| HTML anchor | Canonical JSON pointers |
|---|---|
| `#decision` | `/status`, `/decision`, `/recommendation`, `/warnings`, `/failures` |
| `#data` | `/input`, `/preprocessing` |
| `#validation` | `/validation/estimand`, `/validation/procedures/p1`, `/validation/procedures/p2`, `/validation/uplift` |
| `#curves` | `/refit/p1/plot`, `/refit/p2/plot`, `/artifacts/figures` |
| `#refit` | `/refit/p1`, `/refit/p2` |
| `#diagnostics` | `/diagnostics/oof`, `/diagnostics/patterns`, `/diagnostics/influence` |
| `#stability` | `/validation/split_sensitivity`, `/validation/bootstrap_stability`, `/validation/p2_stability` |
| `#reproducibility` | `/provenance`, `/artifacts` |

`/status` сохраняет разные поля `bundle_status`, `analysis_status`, `recommendation_status` и `decision_reason`; один generic status запрещён. Любая nullable metric имеет объект `{value, status, reason, scope, definition, unit}`. `NaN` и infinity в JSON запрещены.

Каждый metric, status, warning, formula, table row и figure в HTML получает `data-source-pointer` с JSON Pointer canonical source. Для данных model artifact допускается пара `data-artifact-path` + `data-source-pointer`. Эти атрибуты не являются пользовательским вводом и строятся только из schema-known paths.

`plot-data.csv`, model JSON, SVG labels, HTML tables и `report.json` сверяются до публикации. `verify` должен обнаруживать различие formula parameters, prediction grid, boundary, counts, metrics, warning codes, model role или artifact hash.

Нормативная production conditional schema v1 зафиксирована в
[`production-report.schema.json`](production-report.schema.json) и в source
project копируется byte-identically в bundle как `report.schema.json`. Она
кодирует typed nullable metrics, recommendation/failure transitions, различие
validation fallback и full-data refit, условное присутствие готовой функции и
отсутствие model state при pipeline failure. Схема необходима, но
недостаточна: вычислимые равенства `SSE/R²/RMSE/MAE/uplift`, сумма shares,
continuity и cross-artifact hashes отдельно пересчитывает verifier. Соседний
[`report.schema.json`](report.schema.json) остаётся только исторической схемой
статического прототипа и не разрешён production verifier-ом.

## 15. Accessibility contract

Обязательны:

- последовательная heading hierarchy и фиксированный reading order;
- `<caption>`, table headers и scope для таблиц;
- short accessible name и связанное long description/table для каждого complex SVG;
- формулы, metrics, boundary и flags как текст, а не только pixels;
- различение модели, границы, warning и observation flags не только цветом;
- contrast не менее `4.5:1` для обычного текста и `3:1` для essential lines/markers;
- сохранение смысла в grayscale, распространённых CVD simulations и при 400% zoom/reflow;
- отсутствие hover-only информации;
- прямые labels для линий и red boundary;
- warning icon + code + текст, а не только красный фон;
- доступная row table для всех отмеченных наблюдений.

Статический `<details>` допустим, но ключевой verdict, warning и failure не могут быть свёрнуты по умолчанию. Browser print является удобством, а не заявлением о tagged PDF accessibility.

## 16. Security и приватность

Отчёт должен открываться offline и не выполнять код:

- no JavaScript, forms, remote URLs, CDN, external fonts, frames или network requests;
- HTML autoescaping для filename, units, identifiers, reasons и raw values;
- formula text только из registry AST и typed numeric fields;
- SVG без `script`, event attributes, `foreignObject` и external references;
- все local artifact links — manifest-known relative paths без `..`, symlinks и path traversal;
- CSP: `default-src 'none'; script-src 'none'; connect-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'; font-src 'none'; media-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'`;
- raw invalid fields остаются только в bounded JSONL audit и не попадают в
  `report.json`, HTML или spreadsheet-facing CSV;
- identifiers во всех CSV/CSV.gz используют `id:` + обратимое percent-encoding из delivery contract;
- `formula_display` никогда не исполняется через `eval`;
- абсолютный input path, telemetry и runtime network запрещены.

Bundle приватный и чувствительный. Он не является автоматически redacted либо безопасным для публикации. POSIX mode `0700/0600` — operational default, но не шифрование.

## 17. Прототип и доказательства до разрешения тикета 12

### 17.0 Граница статического прототипа относительно production bundle

Тикет 12 проверяет canonical report state, human rendering, графики, failure semantics и cross-artifact reconciliation, но не реализует CLI-поставку из контракта 06. Поэтому только в `docs/prototypes/report-layout/generated/` разрешены следующие явно маркированные evidence-only подстановки:

- non-usable `models/*-model-preview.json` вместо production `models/recommended-model.json`; `predict` не реализован;
- conditional final-refit dossiers `models/one-model.json` / `models/two-segment-model.json` только для соответствующего certified synthetic refit, но без заявления об их пригодности для production predictor;
- prototype `report.schema.json` без production `model.schema.json`; в
  production source schemas живут в `schemas/`, а verified copies production
  `report.schema.json`/`model.schema.json` публикуются в корне bundle;
- несжатые `oof-appearances.csv`, `diagnostic-appearances.csv`, `influence-grid.csv` и `input-exclusions.jsonl` вместо окончательной production-упаковки `oof-appearances.csv.gz` (включая appearance diagnostics), `p2-stability-outer-fits.csv.gz`, `bootstrap-resamples.csv.gz`, `influence-groups.csv` и `input-audit.jsonl.gz`;
- reconciled `provenance.json` и hash-bearing prototype payloads в `provenance/`; exact production environment/lock semantics всё ещё проверяются тикетом 14;
- `prototype-manifest.json` без `COMPLETE` вместо атомарно опубликованного production bundle.

Исторический прототип использовал label `full-pipeline bootstrap`; acceptance
v1 заменяет estimand на paired resampling frozen OOF-loss contributions. Его
layout evidence сохраняется, но production report обязан использовать новое
точное название и ограничения. Эти исключения не меняют семантику готовой
функции и не разрешают фиктивный model artifact при failed/unavailable refit.
Exact production tree, schemas, usable `recommended-model.json`, `predict`,
compression/publication и `COMPLETE` заморожены acceptance-контрактом 08, но
их исполнение имеет статус `NOT RUN` до отдельного implementation handoff.

Все артефакты проверки размещаются в [`docs/prototypes/report-layout/`](../prototypes/report-layout/). Минимальная структура:

```text
docs/prototypes/report-layout/
├── README.md
├── build.py
├── audit_prototype.py
├── fixtures/
├── generated/
├── renders/
├── audit-results.json
└── evidence/
    ├── visual-accessibility-audit.md
    ├── security-reconciliation-audit.md
    └── decision-log.md
```

### 17.1 Обязательные fixture states

1. `ONE_RECOMMENDED` с качеством выше `0.60`.
2. `TWO_RECOMMENDED` с непрерывной границей, долями 40/60 и локальными `R²`.
3. P1 recommended при отклонённом/малом uplift P2.
4. `R²_OOS=0.5996` и `R²_OOS=0.6000`.
5. Negative и undefined `R²`, включая constant `y`.
6. `DESCRIPTIVE_ONLY`.
7. `NO_BALANCED_SPLIT` и `NO_VALID_TWO_SEGMENT`.
8. `NONINVERTIBLE_X_SCALE` без фиктивной линии.
9. `PIPELINE_FAILURE`/`NO_VALID_MODEL` без recommended artifact.
10. Invalid skipped rows, repeated `x`, exact duplicates и overplotting.
11. Отдельные large residual, high influence и combined flags.
12. Unavailable uncertainty и failed bootstrap resamples.
13. XSS/path/identifier payloads в filename, units, `row_id`, reasons и invalid values.

### 17.2 Исполняемые gates

| Gate | Pass condition |
|---|---|
| `RAW` | point/table counts согласованы с `n_used`, exclusions перечислены |
| `CMP` | две панели имеют одинаковые data, axes, transforms, ticks и aspect |
| `GEOM` | линии clipped к domains; P2 continuity и boundary reconciliation проходят |
| `METRIC` | OOF/refit namespaces разделены; values/definitions/denominators совпадают с JSON |
| `WARN` | `0.5996` предупреждает, `0.6000` — нет; undefined обрабатывается отдельно |
| `RESID` | OOF, patterns и refit influence раздельны и traceable к `row_id` |
| `A11Y-STATIC` | semantic HTML/SVG, truthful alt/captions, textual/non-color encoding и вычисленный contrast проходят |
| `JSON` | schema valid, RFC 8259, no non-finite numbers, nullable metrics имеют status/reason |
| `FAIL` | каждый failure fixture видим и не создаёт ложной рекомендации/линии |
| `UNC` | каждый interval имеет target/level/method/unit/selection scope/count либо typed unavailable |
| `SECURITY-STATIC` | CSP/XSS/SVG/path/CSV-injection, containment, manifest/hash и prototype-permission fixtures проходят |

Дополнительно обязательны:

- JSON → HTML/SVG/table/model reconciliation;
- deterministic rerender из одного `report.json`;
- DOM/geometry snapshots двух панелей;
- отдельный audit статистической семантики и отдельный visual/accessibility/security audit;
- traceability matrix от каждого требования этого контракта к fixture, gate и artifact;
- отсутствие незакрытых decision-bearing placeholders в contract candidate.

Следующие проверки требуют target browser/platform либо production publication seam и поэтому являются обязательными acceptance-gates тикета 14 и implementation handoff, а не фиктивными PASS тикета 12: narrow viewport, 400% reflow, keyboard/focus, screen reader, grayscale/CVD/forced-colors, browser print, наблюдение runtime network, atomic publication, symlink resistance и cross-platform replay. Их статус до выполнения — `NOT RUN`.

## 18. Hands-off delegated evidence gate

В диалоге пользователь явно зафиксировал общие продуктовые требования: две отдельные панели, исходные точки, красную границу P2, линии только внутри интервалов, segment/global `R²`, сравнение P1/P2 и warning ниже `0.60`. Позднее пользователь передал оставшиеся решения в hands-off режим. Это разрешает выбрать обратимые report defaults и проверить их объективными артефактами, но **не является реакцией пользователя на уже показанный макет**.

Поэтому:

1. Этот документ не помечается `одобрен пользователем`.
2. Исследование RQ5, предыдущие контракты, automated gates, renders и независимые audits могут заменить live-review как **делегированный evidence gate качества**, но не как доказательство пользовательского UX-предпочтения.
3. До разрешения [тикета 12](../../.scratch/monotone-curve-approximation/issues/12-prototype-report-contract.md) должны существовать все статические артефакты раздела 17 и проходить decision-bearing gates `RAW/CMP/GEOM/METRIC/WARN/RESID/A11Y-STATIC/JSON/FAIL/UNC/SECURITY-STATIC`; runtime/platform gates выше остаются явно `NOT RUN` и передаются в тикет 14.
4. В истории тикета необходимо явно записать, что исходное требование live reaction было заменено последующей hands-off инструкцией, а не выполнено фиктивно.
5. Resolution wording: `контракт принят исполнителем как delegated v1 default по evidence gate; live user review не проводился`.
6. Если strict Wayfinder HITL-семантика типа `prototype` сохраняется без изменения, тикет нельзя выдавать за разрешённый через user reaction; tracker должен честно зафиксировать reclassification/supersession либо оставить live UX acceptance отдельным будущим gate.

Независимый reviewer и subagent не выступают от имени пользователя. Их замечания, входные артефакты, принятые/отклонённые изменения и остаточные ограничения записываются в `evidence/decision-log.md`.

## 19. Acceptance-инварианты контракта

1. На первом экране всегда понятны recommendation status, основной reason и warning качества.
2. P1 и P2 остаются видимыми как сравнивавшиеся процедуры даже при failure/fallback P2.
3. Две обязательные панели используют одинаковые точки и геометрию; P2 не рисуется вне собственных интервалов.
4. Красная граница имеет dash, label, число в таблице и достаточный contrast.
5. OOF quality, refit metrics, OOF residuals и refit influence не смешиваются.
6. Общий и оба локальных `R²_fit` P2 показаны, но local values не запускают threshold.
7. Warning читает только неокруглённый global primary `R²_OOS` рекомендации.
8. `0.5996`, `0.6000`, negative и undefined `R²` отображаются без противоречия decision.
9. Failure не превращается в ноль, пустую панель, фиктивную формулу или recommended model.
10. Каждый human value имеет canonical JSON source pointer и проходит cross-artifact reconciliation.
11. Observation flags доступны без цвета/hover и всегда имеют `review_only` semantics.
12. Offline HTML/SVG не выполняют пользовательские данные как code и не обращаются к сети.
13. Complete bundle без validated recommendation не содержит `recommended-model.json`.
14. Renderer failure не публикует `COMPLETE`.
15. Разрешение тикета 12 опирается на сохранённые prototype evidence и не утверждает несуществующее пользовательское одобрение.
