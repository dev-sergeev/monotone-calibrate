# Контракт диагностики остатков и влияния v1

Статус: утверждён для прототипа  
Дата: 2026-07-16  
Связанный тикет: [10 — правило подсветки и анализа остатков](../../.scratch/monotone-curve-approximation/issues/10-approve-residual-diagnostics.md)

## 1. Назначение и запрет автоматического удаления

Диагностика отвечает на три разные задачи:

1. где проверочный прогноз заметно ошибается;
2. остаётся ли систематическая структура ошибок;
3. насколько отдельная независимая `x`-group влияет на формулу и рекомендацию.

Флаг означает «исследовать», а не «удалить». Ни один residual, influence, heteroscedasticity или pattern flag не меняет использованные строки, fit, метрики либо рекомендацию текущего run. Статистически проблемное наблюдение остаётся валидным наблюдением.

Изменение/исключение строки допустимо только во внешнем источнике по предметной причине. Оно создаёт новый input hash и новый полный run, включая repeated grouped validation и все selection steps. Старый отчёт сохраняется; сравнение с новым называется sensitivity analysis, а не «исправленным R²» текущего run.

## 2. Два набора остатков

Для каждой процедуры хранятся отдельно:

- `e_i,r^OOF = y_i-ŷ_i,r^OOF` для каждого outer repetition `r` — primary predictive diagnostic;
- `e_i^fit = y_i-ŷ_i^final` — descriptive residual итогового full-data refit.

Канонический long export содержит одну запись на `row_id × procedure × repetition` с outer-fold ID. Pooled quality продолжает использовать все appearances, а row-level diagnostic summary является робастным:

```text
e_i^OOF   = median_r(e_i,r^OOF)
score_i   = median_r(|z_i,r|)
flag_rate = mean_r[|z_i,r|≥3.5].
```

Дополнительно сохраняются median/mean/spread OOF prediction и все appearance-level predictions/residuals. Флаг текущей рекомендации строится по её OOF residual; P1/P2 diagnostics остаются доступны для сравнения. OOF и refit series нельзя объединять или обозначать одинаково.

При `G<8`/`DESCRIPTIVE_ONLY` нет честного OOF-флага: выдаётся `OOF_DIAGNOSTICS_UNAVAILABLE`, показываются только `REFIT_RESIDUAL_DESCRIPTIVE` и явное ограничение.

## 3. Cross-fitted robust scale

Scale для outer-test residual оценивается без его `y`:

1. внутри соответствующего outer train получить residuals outer-trained модели
   на training rows; test `y` в scale estimation не используется;
2. вычислить finite-sample-corrected `Qn`; при невозможности — `s_MAD=1.4826·median|e-median(e)|`;
3. scale считается нулевым/неопределённым, если он не превышает `64·eps·max(1,max|y_train|)`; ненулевой outer residual сверх того же допуска получает `NONZERO_RESIDUAL_WITH_ZERO_SCALE`, а `z=null + SCALE_UNDEFINED`;
4. никакой искусственный positive floor не выдаётся за оценку шума.

Training-only local scale строится так:

- число соседних equal-group-count bins `B=max(1,min(4,floor(G_train/5)))`;
- в каждом bin вычисляется тот же robust scale;
- применяется `s(x)=max(s_bin,0.5·s_global)`;
- edges, counts, raw/final bin scales сохраняются в trace.

При `B=1` или недопустимом bin-scale используется global scale. Для каждого outer appearance:

`z_i,r = e_i,r^OOF / ŝ_outer-train(x_i)`.

Row-level score — `median_r|z_i,r|`; отчёт хранит signed median `z`, spread, flag rate и источник scale. Ни full-data residuals, ни test `y` не участвуют в построении scale, если flag используется для проверки качества.

## 4. Флаг большого OOF-остатка

Initial versioned threshold равен `τ_z=3.5`.

- `score_i≥3.5` и `flag_rate_i≥0.5` → `LARGE_OOF_RESIDUAL`;
- `0<flag_rate_i<0.5` → `SPLIT_SENSITIVE_RESIDUAL`, но не persistent large-residual flag;
- `score_i≥5.25` → дополнительная severity `extreme`.

Порог является frozen engineering default v1, не normal-theory p-value.
Multiplicity, false-flag rate и detection power на Gaussian, heavy-tailed,
heteroscedastic, repeated-`x` и contaminated synthetic cases проверяет
implementation gate `STAT-CALIBRATION-003` по заранее зафиксированным pass
bands. Raw residual всегда показывается рядом: standardized score без единиц
не заменяет ошибку в единицах `y`.

## 5. Повторяющиеся `x` и систематические patterns

Для каждой same-`x` group отчёт показывает `n`, mean/median `y`, mean/median OOF residual, within-group MAD/Qn и долю residuals каждого знака. Все строки остаются отдельными.

Для final refit при наличии повторов выводится описательное разложение:

```text
SSE_total       = SSE_pure_error+SSE_lack_of_fit
SSE_pure_error  = Σ_g Σ_j(y_gj-ȳ_g)²
SSE_lack_of_fit = Σ_g n_g(ȳ_g-f(x_g))².
```

Оно не сопровождается наивным post-selection F-test. Группа получает `GROUP_SYSTEMATIC_RESIDUAL`, если median `|z|` по её строкам/appearances не меньше `3.5`; sign balance и доля flagged rows показываются отдельно.

Initial pattern flags:

- `SYSTEMATIC_OOF_RESIDUAL`: в пяти equal-group-count bins не менее трёх соседних bin means имеют один знак, и хотя бы один имеет абсолютную величину не меньше `0.5·s_global`;
- `HETEROSCEDASTIC_PATTERN`: отношение max/min положительных bin scales не меньше `2.0` как минимум в 80% outer fits;
- `BREAKPOINT_LOCAL_BIAS`: для P2 каждая appearance сопоставляется с outer-trained `c_r`; в окне 10% full `x`-span вокруг соответствующей границы минимум пять OOF appearances, не менее 80% residuals одного знака и `|mean residual|≥0.5·s_global`.

Это diagnostic triggers, не formal hypothesis tests. Smoother на графике только локализует pattern и никогда автоматически не становится новым семейством. Изменение registry/loss/weights после просмотра diagnostics требует нового честного validation run.

## 6. Влияние independent `x`-group

Большой residual и большое influence не эквивалентны. V1 оценивает именно
**conditional refit influence**: для каждой group `g` удаляются все строки
этого canonical `x`, после чего заново оцениваются параметры только исходных
сертифицированных full-data P1/P2 структур — те же family/pair, direction и,
для P2, заново оцениваемая tie-safe boundary из исходного admissible profile.
Полный registry selection, validation, bootstrap и recommendation не
пересчитываются. Результат имеет status `sensitivity_only`, называется
`leave-one-x-group selected-structure refit` и не заменяет исходный validated
run.

Это условная sensitivity финальной формы, а не counterfactual recommendation
analysis. Поэтому отчёт не заявляет, что без group изменилась проверочная
рекомендация или uplift. Sensitivity fit получает исходный `[L,U]` только как
фиксированный evaluation/certificate domain, чтобы удаление крайней group не
сокращало сравниваемую сетку; это не публикуется как доказательство
экстраполяции.

На общей сетке из 501 равномерного `x` плюс все наблюдаемые `x` и обе найденные границы вычисляются:

```text
Dmax_g = max_x |F_full(x)-F_-g(x)| / s_ref(x)
Drms_g = sqrt(mean_x[(F_full(x)-F_-g(x))/s_ref(x)]²)
Δc_g = |c_full-c_-g|/(U-L), если исходный и sensitivity full-data P2 refits сертифицированы.
```

Parameter sensitivity сохраняется, но не используется для recommendation.
Для каждого исходного selected-structure parameter в model-role/segment/
registry-parameter order export хранит compact strict JSON record с tagged
binary64 `full_value`, `leave_group_value`, `absolute_delta` и
`relative_delta=|leave-full|/max(1,|full|)`, а также `transform_changed`.
Если refit/certificate не получен, значения равны null со status
`UNAVAILABLE`; иначе aggregate `parameter_delta_linf_relative` — максимум
defined relative deltas. Per-segment transforms берутся из обоих model
artifacts, поэтому изменение coordinate system не скрывается. Отдельное поле
`certificate_changes` хранит typed transition codes. Эти поля позволяют
восстановить retained parameter sensitivity, не заявляя OOF/uplift delta.

`s_ref(x)` — зафиксированный OOF scale исходного run; нулевой scale обрабатывается отдельным status, а не делением.

Группа получает `HIGH_REFIT_INFLUENCE`, если истинно хотя бы одно:

- изменился certificate/failure state соответствующего selected-structure
  refit;
- `Dmax_g ≥ 0.50`;
- `Drms_g ≥ 0.25`;
- `Δc_g ≥ 0.05`;
- segment share изменилась минимум на `0.05`.

Если после удаления данных недостаточно, статус `INFLUENCE_UNASSESSABLE`, а не
нулевое влияние. Все строки group наследуют group-level influence fields.
Thresholds заморожены как engineering defaults v1; endpoint-leverage и
known-influence FPR/power проверяются `STAT-CALIBRATION-003` по acceptance
manifest.

## 7. Семантика сочетаний

| Residual | Influence | Интерпретация |
|---|---|---|
| большой | низкое | необычный отклик, но форма устойчива |
| небольшой | высокое | leverage/design point, удерживающий форму или границу |
| большой | высокое | приоритетная проверка источника/измерения |
| небольшой | низкое | нет индивидуального флага; aggregate pattern всё ещё возможен |

Слова `outlier` и `bad row` не являются автоматическими verdicts. Canonical reasons — `LARGE_OOF_RESIDUAL`, `GROUP_SYSTEMATIC_RESIDUAL`, `HIGH_REFIT_INFLUENCE`, `SYSTEMATIC_OOF_RESIDUAL`, `HETEROSCEDASTIC_PATTERN`, `BREAKPOINT_LOCAL_BIAS` и input-validation codes.

## 8. Обязательные поля отчёта

Row-level diagnostics CSV/JSON содержит:

- `row_id`, `source_row_id`, `x`, `y`, repeated-`x` group и segment membership;
- OOF prediction median/mean/spread, raw median OOF residual, appearance count и outer-fold IDs;
- robust scale value/method/scope, signed `z` median/spread, `score=median|z|`, threshold и flag rate;
- procedure/repetition-level fallback statuses в long export;
- final refit prediction/residual с label `descriptive`;
- influence group, `Dmax`, `Drms`, `Δc`, share delta, parameter delta и
  certificate changes; family/direction, OOF/uplift/recommendation deltas
  отсутствуют, потому что conditional refit sensitivity их не переоценивает;
- массив typed flags с severity, reason, threshold policy version и explanation;
- `action="review_only"` для всех статистических flags.

Каждый plot marker связывается с тем же `row_id`. Точная невалидная строка, не имеющая координат, остаётся в input-exclusion table, а не подделывается на scatterplot.

## 9. Визуальное кодирование

Исходная точка всегда остаётся видимой. Дополнительные признаки:

- triangle outline — большой OOF residual;
- diamond outline — high influence;
- star/double outline — оба признака;
- отдельный count/rug layer — multiplicity одинаковых координат;
- label выбранных `row_id` плюс полная доступная таблица.

Цвет дублируется формой, контуром и текстом; hover не является единственным источником. Jitter возможен только как подписанная display-only копия, исходные координаты и machine data не меняются. Flags разных причин не сворачиваются в один красный класс.

## 10. Acceptance-инварианты

1. Каждый OOF flag воспроизводится из appearance-level export без использования собственного test `y` при оценке scale.
2. OOF и refit residuals имеют разные поля, labels и series.
3. Перемена одного outer-test `y` не меняет его prediction/scale, но меняет residual и flag детерминированно.
4. Каждая same-`x` group остаётся отдельными rows и имеет единый influence result.
5. `LARGE_OOF_RESIDUAL` сам по себе не вызывает `HIGH_REFIT_INFLUENCE`, и наоборот.
6. Ни один statistical flag не меняет `n_used`, model hash, metrics или recommendation текущего run.
7. Любое исправление входа создаёт новый hash и полный validation run; старый отчёт не перезаписывается.
8. Zero/undefined scale выдаёт явный status, не `z=0` и не бесконечность в JSON.
9. Visual flag однозначно сопоставляется `row_id` и typed JSON reason; смысл доступен без цвета/hover.
10. Изменение registry/loss/weights по diagnostics не переиспользует прежние OOF-метрики как untouched assessment.
