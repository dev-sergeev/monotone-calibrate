# Контракт validation, uplift и рекомендации v1

Статус: утверждён для прототипа  
Дата: 2026-07-16  
Связанный тикет: 09 — протокол качества и критерий uplift (внутренний архив, не включён в публичный репозиторий)

## 1. Что оценивается

Primary estimand — качество полной процедуры на новом уровне `x` из того же наблюдаемого диапазона и design distribution. Все строки с одинаковым каноническим `x` образуют одну atomic group и никогда не встречаются одновременно в train и test.

Сравниваются две deployable procedures:

- `P1(train)`: preprocessing, оба направления, весь core registry, параметры, сертификаты, complexity tie-break и refit одной функции;
- `P2(train)`: те же шаги плюс tie-aware граница, ordered family pair, exact continuity и все stability/failure rules.

В каждом outer-training scope весь поиск запускается заново. Test `y` не
участвует в scaling, выборе семейства, направления, границы, стартов,
threshold или обработке наблюдений. Проверка уже выбранной на полном файле
формулы не считается validation. Registry, solver budget и thresholds
заморожены до анализа и не являются data-tuned hyperparameters; поэтому
отдельный inner loop не нужен для честной оценки полной процедуры. Outer test
оценивает весь selection pipeline, выполненный только на outer train.

Опциональный `llm-start-advisor-v1` также вызывается отдельно для каждого
outer train и видит только bounded summary его training rows. Он может заменить
только заранее помеченные start slots внутри уже разрешённых family/pair,
direction, bounds и общего solver budget. Outer-test `x/y`, новые AST/families,
thresholds и готовый ответ модели ему недоступны. Невалидный/отсутствующий
ответ возвращает deterministic starts того же scope и typed warning.

Secondary estimands:

- `new_repeat_at_known_x` — только отдельный sensitivity run;
- `missing_x_block` — contiguous interior-block stress test;
- крайние `x`-blocks — явно помеченный extrapolation stress;
- time/batch blocking — только при наличии такой предметной metadata.

Если обоснованные split-estimands дают разные рекомендации, статус `SPLIT_SENSITIVE`; выбирать удобную схему post hoc запрещено.

## 2. Детерминированная repeated grouped outer validation

Пусть `G=n_unique_x`. Базовая policy:

| Условие | Outer folds | Repetitions | Статус |
|---|---:|---:|---|
| `G ≥ 10` | `5` | 10 | полный протокол |
| `8 ≤ G < 10` | `4` | 20 | полный протокол с `SMALL_GROUP_COUNT` |
| `G < 8` | — | — | `DESCRIPTIVE_ONLY`; validated uplift/recommendation не объявляются |

Эти пороги — консервативные product defaults, а не числа из литературы. Любой
фактический split дополнительно обязан оставлять training scopes,
удовлетворяющие контрактам данных и семейства; иначе число outer folds
уменьшается по заранее заданному порядку `5 → 4`, а затем состояние становится
`DESCRIPTIVE_ONLY`. Число folds не подбирается по полученному качеству.

Exact assignment, SHA-256 namespaces/preimages, digest order, greedy tie-break,
membership ledger, `training_scope_id`, `x_group_id`, `split_id` и executable
golden заморожены в
[`split-policy-v1.json`](../acceptance/split-policy-v1.json). Кратко: groups
идут в raw-binary64 порядке `x`, соседние strata имеют до `K` groups, порядок
внутри stratum получается counter-SHA-256 без `y`, а atomic group назначается
ещё не использованному в stratum fold по минимуму `(current row load, seeded
fold priority, fold integer)`. Любое отличие требует version bump.

Каждая строка получает одну OOF prediction за repetition. Primary pooled losses суммируют все `R·n_used` test appearances, поэтому каждая исходная строка имеет одинаковый вес. Отдельная distribution метрик по repetitions называется `split sensitivity`, а не confidence interval.

## 3. Failure и fallback внутри validation

P1 всегда пытается использовать `constant_v1` как operational fallback. Если даже P1 не даёт finite certified prediction, весь run получает `PIPELINE_FAILURE` и не публикует validation quality.

Если P2 невозможна или падает в конкретном training scope, она возвращает P1 prediction, обученную на том же scope, и сохраняет точный failure code. Таким образом сравнивается deployable procedure «попытаться P2, иначе P1», а test rows/folds не исчезают. Частота fallback входит в stability gate; условная метрика только по успешным P2 запрещена как primary.

Outer P1 и P2 используют одни splits, строки и null predictions. Failed fits, certificate failures и boundary/collapse states не выбрасываются из denominator.

## 4. Pooled OOF-метрики

Для каждой процедуры `m∈{1,2}` и всех OOF appearances:

```text
SSE_m  = Σ(y_i-ŷ_i,m)²
SAE_m  = Σ|y_i-ŷ_i,m|
MSE_m  = SSE_m/N
RMSE_m = sqrt(MSE_m)
MAE_m  = SAE_m/N
```

Primary null prediction для каждого test observation — среднее `y` только соответствующего outer-training fold. Затем

```text
SSE_0      = Σ(y_i-ŷ_i,0)²
R²_OOS,m   = 1-SSE_m/SSE_0.
```

Fold-wise `R²` не усредняется. Если `SSE_0=0`, значение равно JSON `null` со статусом `UNDEFINED_R2`; RMSE/MAE сохраняются. Отрицательный `R²` не обрезается. Secondary `Q²_global` с full-data mean допускается только под отдельным именем и не участвует в рекомендации.

Uplift считается на тех же appearances:

```text
ΔMSE          = MSE_1-MSE_2
ΔRMSE         = RMSE_1-RMSE_2
ΔMAE          = MAE_1-MAE_2
ΔR²           = R²_OOS,2-R²_OOS,1
rel_MSE_uplift = 1-MSE_2/MSE_1, если MSE_1>0.
```

Положительный знак означает преимущество P2. Primary effect — paired squared-loss/`rel_MSE_uplift`; RMSE, MAE и `ΔR²` обязательны, но не выбираются post hoc.

## 5. Описательные метрики финального refit

После выбора обе процедуры refit на всех используемых строках. Эти числа маркируются `in_sample` и не заменяют OOF quality:

```text
R²_fit,all = 1-Σ_all e²/Σ_all(y-ȳ_all)²
R²_fit,s   = 1-Σ_s e²/Σ_s(y-ȳ_s)², s∈{left,right}.
```

P2 показывает общий `R²_fit,all` и оба локальных `R²_fit,s` вместе с `n`, share, `n_unique_x`, span, RMSE и MAE. Локальные `R²` не усредняются. При constant denominator значение `null + UNDEFINED_LOCAL_R2`; общий `R²` всё равно вычисляется независимо.

OOF local metrics являются secondary и используют breakpoint, выбранный только соответствующим outer train. Применять final breakpoint к OOF rows запрещено.

## 6. Практический uplift: initial policy v1

Поскольку предметная допустимая ошибка в единицах `y` пока не задана, generic MVP использует dimensionless conservative gate. P2 получает `CLEAR_PRACTICAL_UPLIFT` только одновременно при:

1. pooled `rel_MSE_uplift ≥ 0.10`;
2. не менее 90% repetition-level `rel_MSE_uplift` положительны;
3. 10-й percentile repetition-level uplift не меньше `0` — это split-stability bound, **не CI**;
4. нижняя граница central-90% paired `x`-group bootstrap stability interval по
   уже полученным OOF loss contributions не меньше `0.05`;
5. `ΔRMSE > 0`;
6. `MAE_2 ≤ 1.02·MAE_1`;
7. выполнен stability gate раздела 7.

Если `MSE_1=0`, relative uplift не определён и P2 не может получить practical-uplift status: P1 уже имеет нулевую squared error. Если cross-fitted `R²` не определён, решение всё ещё может показать absolute RMSE/MAE, но generic P2 recommendation требует отдельной предметной policy и по умолчанию не выдаётся.

Если пользователь позднее задаёт метрологически содержательный `δ_RMSE`/`δ_MAE` в исходных единицах, он версионированно заменяет generic practical gate, но не ослабляет требования paired uncertainty/stability без новой калибровки.

Для внутреннего выбора сертифицированные кандидаты с разницей pooled MSE не более 1% от лучшего считаются practically tied и разрешаются complexity vector из модельного контракта. Это `selection_tie_tolerance=0.01`, также подлежащее synthetic calibration.

## 7. Stability gate P2

По outer fits/repetitions должны выполняться все initial defaults:

| Проверка | Gate |
|---|---:|
| valid P2 без operational fallback | не менее 90% |
| согласие направления | не менее 80% |
| частота dominant canonical ordered pair | не менее 60% |
| ширина central-80% breakpoint distribution | не более 25% full `x` span |
| попадание границы на допустимые 40/60 edges | не более 20% |
| `FLAT_PROFILE`, `MULTIPLE_NEAR_OPTIMA` или segment collapse | суммарно не более 10% |
| hard fit/certificate failures | не более 10% |

Частота pair считается среди non-fallback P2 fits; все fallback отдельно уменьшают success rate. Direction frequency считается среди non-flat non-fallback P2 fits. Breakpoint distribution и width считаются внутри dominant pair/direction regime; бессмысленное среднее разных regimes не используется. Любой hard failure в final full-data refit запрещает P2 независимо от stability frequency.

Thresholds этого раздела заморожены как engineering policy v1. Их untouched
known-truth simulation по false-P2/false-P1 rates, breakpoint recovery, noise,
ties, outliers и near-equivalent families является gate
`STAT-CALIBRATION-003` отдельного implementation handoff; pass bands и seeds
заранее заданы acceptance manifest.

## 8. Неопределённость

Primary отчёт показывает paired point effect и split-sensitivity distribution. Нельзя вычислять `sd(folds)/sqrt(K)`, применять обычный t-test к folds или называть repetition percentiles «95% CI».

V1 выполняет **paired `x`-group bootstrap фиксированных OOF loss
contributions**. После завершения repeated grouped outer validation для каждого
canonical `x` и repetition уже существуют paired P1/P2 squared losses,
полученные без использования test `y` при fitting. Один из 200 resamples
выбирает `G` canonical groups с replacement, сохраняет все строки и все
repetitions выбранной group с multiplicity и заново агрегирует pooled
`MSE_1/MSE_2` и `rel_MSE_uplift`; fitting, family/boundary selection и
prediction внутри bootstrap не повторяются.

Sampling является cross-platform counter-based, без зависимости от library
PRNG. Группы имеют индексы `0…G-1` в canonical raw-binary64 порядке `x`
(`-0` уже нормализован в `0`). Для resample `r=0…199`:

```text
seed_r = uint63(first_8_bytes(SHA256(
  UTF8("paired-fixed-oof-bootstrap-v1\0") ||
  uint64_be(base_seed=20260716) || uint32_be(r))))

digest_rj = SHA256(
  UTF8("group-draw-v1\0") || uint64_be(seed_r) || uint32_be(j)), j=0…G-1

group_index_rj = floor(uint256_be(digest_rj) * G / 2^256)
```

`seed_r` записывается десятичным integer. Export каждого resample также хранит
compact RFC 8259 JSON-массив `[x_group_id, multiplicity]` в canonical group
order, опуская нулевые multiplicity. Сумма multiplicity обязана равняться `G`;
`n_distinct_x_groups` равен длине массива. Поэтому verifier независимо
восстанавливает draws, multiplicity и эффект из frozen OOF export без solver-а.

Поэтому interval оценивает устойчивость наблюдаемого paired OOF effect к
design/group composition **conditional on frozen OOF predictions**. Он не
является coverage statement для повторного model selection и не называется
`full-pipeline` либо confidence interval. P2 fallback уже содержится в OOF
predictions и остаётся в resample; полный fallback даёт effect `0`. Resample с
нулевым P1 MSE получает `null + ZERO_P1_MSE`; central 90% interval строится по
5/95 percentiles при минимум 160 определённых resamples, иначе uncertainty
равна `UNCERTAINTY_UNAVAILABLE`.

Если uncertainty procedure не выполнена, P2 не получает `CLEAR_PRACTICAL_UPLIFT`; возможен только `PRACTICALLY_PROMISING_UNCERTAIN` с рекомендацией P1. Repeated-split distribution остаётся отдельным источником procedure-selection stability и не подменяется bootstrap conditional effect.

## 9. Состояния решения

| State | Рекомендация |
|---|---|
| `CLEAR_PRACTICAL_UPLIFT` | P2, если full-data refit сертифицирован |
| `NO_UPLIFT_OR_HARM` | P1; P2 показывается как испытанный вариант |
| `STATISTICAL_ONLY_SMALL` | P1: улучшение меньше practical gate |
| `PRACTICALLY_PROMISING_UNCERTAIN` | P1 primary; P2 как inconclusive alternative |
| `UNSTABLE_SELECTION` | P1; перечислить нарушенные stability gates |
| `NO_VALID_TWO_SEGMENT` | P1; P2 unavailable с причиной |
| `DESCRIPTIVE_ONLY` | нет validated model recommendation; показать только явно описательные fits |
| `PIPELINE_FAILURE` | готовой функции нет; сформировать failure report |

P1 является минимально достаточной default-рекомендацией, а не автоматически «хорошей»: низкое или отрицательное качество остаётся видимым. Валидная P2 показывается в сравнении даже при рекомендации P1.

## 10. Product warning `0.60`

После выбора recommendation warning проверяет только неокруглённый global primary `R²_OOS` рекомендованной deployable procedure:

```text
if R2_OOS.status == "defined" and R2_OOS.value < 0.60:
    emit BELOW_PRODUCT_R2
```

Ровно `0.60` warning не вызывает. Значение `0.5996`, отображаемое округлённо, обязано показать дополнительные знаки или явный текст «ниже 0.60». Undefined `R²` получает отдельный warning и не сравнивается с порогом. Local, fold-wise и in-sample `R²` threshold не запускают.

`0.60` означает относительное уменьшение squared loss против cross-fitted null, а не «доля описанных точек» и не универсальный научный критерий допустимости.

## 11. Acceptance-инварианты

1. P1/P2 используют идентичные outer rows, splits и null predictions.
2. Перемешивание `y` outer-test или его изменение не влияет на fitted outer-train procedure.
3. Одинаковые `x` не пересекают outer train/test ни в одном repetition.
4. Весь family/direction/breakpoint search повторяется внутри каждого training scope.
5. Pooled RMSE/R² воспроизводятся из row-level OOF export; среднее fold `R²` отсутствует.
6. P2 failure создаёт P1 fallback prediction и failure record, а не потерянную test row.
7. Constant `y` даёт `R²=null`, а не `0`/`1`; negative `R²` сохраняется.
8. Local refit `R²` не усредняются и не запускают product threshold.
9. Gate использует числа до округления и заранее pinned policy/hash.
10. Ни один unstable/uncertain/failed P2 не становится recommendation из-за training `R²`.
11. `0.5996` предупреждает, `0.6000` — нет; undefined обрабатывается отдельно.
12. Повтор с теми же input/config/code hashes и seeds воспроизводит splits, OOF rows, metrics и decision state.
