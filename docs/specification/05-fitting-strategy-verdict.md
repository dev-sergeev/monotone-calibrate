# Численный вердикт по стратегии fitting v2.2

> Поправка 2026-09-12: этот документ сохраняет ограничения registry P1/P2.
> Активный LLM-вариант теперь независимо ищет новые деревья выражений;
> его контракт и validation описаны в [текущем алгоритме](../llm-sr-algorithm.md).


Статус: algorithmic seam и acceptance specification утверждены; confirmatory production execution выполняется отдельным implementation handoff  
Дата прогона: 2026-07-16; amendment LLM-SR: 2026-07-22
Связанный тикет: 11 — проверить осуществимость и выбрать стратегию совместной оптимизации (внутренний архив, не включён в публичный репозиторий)

## 1. Решение

Implementation default должен использовать **профильный поиск по допустимым tie-safe ячейкам границы** (`PROFILE_CELLS`). Монолитная оптимизация всех коэффициентов не выбирается. Это решение закрывает algorithmic seam тикета 11, но само по себе не разрешает production handoff.

Поиск разделяется на два уровня:

1. конечный перебор `family/direction/rate branch/tie-safe boundary cell`;
2. непрерывный поиск только `c` и bounded nonlinear shape-параметров внутри одной ячейки.

При каждом таком значении линейный блок решается условно:

- P1: `[a, q]`, где `q≥0`;
- P2: `[μ, q₁, q₂]`, где `q₁,q₂≥0`.

Для P2 используется centered-параметризация из модельного контракта, поэтому обе ветви дают ровно `μ` в `c`. После fit независимый evaluator/certificate заново строит формулы из параметров и отдельно проверяет конечность, domain, знак производной, support, баланс, атомарность ties, join, rank и conditioning. Он сравнивает собственные predictions с результатом fitter; fault injection подтвердил обнаружение `EVALUATOR_MISMATCH`, `JOIN_FAILURE`, `SHAPE_FAILURE`, `NONFINITE_FAILURE`, `RANK_FAILURE` и `DOMAIN_FAILURE`.

Global/stochastic search не входит в путь рекомендации. Он остаётся дорогим audit/shadow reference для калибровки manifest starts и обнаружения пропущенных basin.

## 2. Проверяемая гипотеза и честное сравнение

Прототип сравнил:

- `profile_cells`: tie-cell enumeration, conditional bounded least squares и детерминированный multistart по shape;
- `joint_cells`: для nonlinear cases те же family, direction, cells, start IDs и local budget, но совместная локальная оптимизация `c, μ, q₁, q₂, shape`;
- `global_profiled_reference`: три seed differential-evolution, при этом линейный блок также профилируется, чтобы reference не получал искусственных hard bounds.

Polynomial hinge является заранее объявленной специализацией: по каждой ячейке выполняется bounded scalar minimization и результат сравнивается с тремя global incumbents. Он не выдаётся за equal-start comparison с joint. Logistic представляет наиболее сложную локальную геометрию core registry; обе несвязные rate-ветви exponential проверены отдельно.

Первый exploratory-вариант был отклонён до решения: он не профилировал linear block, дал ложный training uplift `1.0` при `SSE₁=SSE₂=0`, включал runtime в model hash, смешал affine-equivariance с binary64 quantization и не имел честного global reference. Отклонённый код удалён; отрицательный результат сохранён в истории тикета.

Gate manifest v2.2 был зафиксирован после структурных независимых аудитов, которые обнаружили фиктивный failure short-circuit, неравное описание hinge baseline, зависимый certificate, отсутствие competing-basin state, склейку разных raw `x` при нормализации и зависимость decision от одиночного wall-clock. После исправлений выполнен новый полный batch. Поскольку часть synthetic matrix использовалась при отладке manifest, этот результат является engineering feasibility evidence, а не confirmatory coverage study. Тикет 14 зафиксировал расширенную untouched known-truth matrix, seeds и pass bands; её фактический повтор является gate `MODEL-CONTRACT-002`/`STAT-CALIBRATION-003` отдельного implementation handoff и до него честно имеет статус `NOT RUN`.

## 3. Воспроизводимый runtime

Полный batch запускается командой из [README прототипа](../../prototypes/fitting_strategy/README.md). Зафиксированная среда:

| Компонент | Значение |
|---|---|
| Python | `3.12.11` |
| NumPy | `2.4.2` |
| SciPy | `1.18.0` |
| BLAS/OMP threads | `1 / 1` |
| Seed | `20260716` |
| Платформа прогона | `macOS arm64` |

Decision-bearing efficiency gate детерминированно требует меньше outer evaluations у profile, чем у joint и каждого best-seed global reference, и отдельно проверяет точный учёт inner LSQ solves. Несопоставимые напрямую виды работы публикуются раздельно. Одиночный wall-clock сохраняется только как diagnostic provenance: он не участвует ни в PASS/FAIL, ни в `decision`. На закреплённом прогоне profile был быстрее обоих baseline во всех четырёх identified cases, но это платформенное наблюдение, не универсальный SLA.

## 4. Результаты identified truth

Все величины loss ниже — SSE в scaled-`y`. `best reference` — минимум трёх stochastic seed, а не доказанный глобальный optimum.

| Сценарий | Profile SSE | Joint SSE | Best reference SSE | Ошибка `c/(U-L)` profile | Outer calls: profile / joint / min reference |
|---|---:|---:|---:|---:|---:|
| hinge `poly1|poly1` | `1.01e-18` | `2.22e-14` | `7.00e-17` | `5.49e-10` | `163 / 2,715 / 2,312` |
| `exp(k>0)|exp(k>0)` | `1.96e-16` | `5.43e-12` | `1.40e-15` | `5.71e-9` | `3,936 / 35,959 / 14,604` |
| `exp(k<0)|exp(k<0)` | `1.08e-16` | `4.24e-14` | `3.50e-14` | `8.41e-9` | `3,448 / 19,481 / 14,484` |
| `logistic|logistic` | `8.52e-15` | `5.35e-14` | `5.12e-10` | `1.27e-6` | `11,328 / 28,710 / 27,396` |

Profile прошёл gate `loss_profile-loss_reference ≤ max(10⁻⁸,10⁻⁴·loss_reference)` во всех четырёх случаях и не имел catastrophic miss. Максимальная ошибка fitted values относительно noiseless truth составила от `3.51e-10` до `3.75e-8` scaled-`y`.

Несколько DE-запусков nonlinear reference исчерпали бюджет до формального convergence tolerance. Их лучшие конечные сертифицированные incumbents сохранены с `REFERENCE_BUDGET_EXHAUSTED`; ни один не улучшил profile. Gate дополнительно потребовал для каждого из трёх seed max error по noiseless truth `≤10⁻⁴` и ошибку границы `≤10⁻³`, поэтому произвольно плохой incumbent не мог подтвердить сравнение. Эксперимент выбирает детерминированный seam, но не объявляет математически доказанный global optimum.

## 5. Состояния и инварианты

| Fixture | Наблюдённый результат |
|---|---|
| exact line, представленная двумя ветвями | canonical replacement `poly1_v1`; произвольный `c` остаётся только trace-диагностикой; `FLAT_PROFILE` |
| constant `y` | canonical replacement `constant_v1`; `CONSTANT_LIMIT + FLAT_PROFILE`; uplift не вычисляется |
| истинный break вне 40–60 | допустимый fit на product edge + `BOUNDARY_HIT` |
| repeated `x`, включая ячейку ровно 40/60 | строки остались отдельными; cell `6/9`; `ties_atomic=true` |
| tie blocks без сбалансированной ячейки | `NO_BALANCED_SPLIT`, ноль solver calls |
| сбалансированные ячейки есть, но ветвям logistic не хватает `p+` support | `INSUFFICIENT_DATA_FOR_P2`, ноль solver calls |
| два удалённых практически равных basin | `MULTIPLE_NEAR_OPTIMA`; параметры каждого attempt сохранены |
| logistic saturation | `WEAK_IDENTIFIABILITY` + `PARAMETER_BOUND_HIT` |
| 15 разных binary64 `x`, часть которых схлопывается в одинаковое `t` | raw tie/cell enumeration сохранил 15 групп; `NONINVERTIBLE_X_SCALE + NORMALIZED_X_COLLISION`, ноль solver calls |
| реальный solver budget `maxiter=0` | 6 failed attempts, 12 outer evaluations, `OPTIMIZER_FAILURE`, готового кандидата нет |
| узкое нарушение производной cubic между 101 grid nodes | grid ошибочно прошёл; analytic vertex certificate дал `SHAPE_FAILURE`, минимум `-1.0e-6` |

P1 отдельно восстановил возрастающую и убывающую logistic, linear fit, canonical constant collapse и `INSUFFICIENT_DATA_FOR_P1` на трёх строках. Profiled logistic P1 имел SSE `3.00e-16`; best stochastic reference — `5.41e-14`. Mirrored decreasing P2 прошли для всех четырёх identified families; максимальный symmetry drift составил `3.97e-8` по prediction и `9.57e-7` по нормализованной границе, ниже gate `10⁻⁷/10⁻⁶`.

Все десять доступных P2 profile winners прошли независимый validity certificate. Полином выше третьей степени не входит даже в грамматику прототипа; exhaustive AST rejection остаётся acceptance-тестом реализации.

## 6. Reproducibility и scale

Для logistic P2 повтор, перестановка строк и обратный порядок starts дали один prototype semantic hash, нулевой drift prediction и нулевой drift `c`. В стенде solver input получает канонический порядок `(x,y)` и строки не агрегируются. Сам стенд не содержит `row_id` и не доказывает восстановление row order; production mapping по стабильному `row_id`, полный registry/AST hash и исключение hash-aliasing остаются обязательными acceptance-инвариантами контракта данных.

На точно представимом dyadic affine fixture получены:

- drift нормализованного `x`: `0`;
- max prediction drift: `6.22e-15`;
- normalized breakpoint drift: `9.77e-15`;
- model hash совпал.

Stress `x'=10¹²+0.01x` изменил сам binary64-design: normalized drift `0.00610`, а `ulp/span=0.0122`. Affine scaling не способно восстановить уже потерянную точность. Поэтому контракт данных дополнен warning `LOW_X_RESOLUTION`; prototype threshold `ulp/span>10⁻⁶` калибруется в acceptance matrix и не вызывает автоматического удаления строк.

Tie-группы, support и допустимые 40/60 cells всегда перечисляются по исходным canonical binary64 `x`, а не по нормализованному `t`. Отдельный fixture из десяти различных `x` с шагом ровно один ULP сохранил `unique(x)=unique(t)=10`: fit продолжился с core warning `LOW_X_RESOLUTION`, без ложных ties. Контрпример `[-10¹⁶, -6…6, 10¹⁶]` имел 15 разных raw `x`, но только 9 разных `t`; P1/P2 завершились до solver со статусом `NONINVERTIBLE_X_SCALE` и warning `NORMALIZED_X_COLLISION`. Acceptance v1 окончательно выбирает именно этот fail-closed outcome до solver; альтернативное numerical representation требует новой версии контракта. Превращать разные `x` в одинаковые запрещено.

## 7. Machine gates

Batch вернул `decision=PROFILE_CELLS`. Все 11 обязательных gate прошли:

| Gate | Результат |
|---|---|
| exact validity | PASS |
| fair search | PASS |
| objective recovery | PASS |
| state truth | PASS |
| reproducibility | PASS |
| scale equivariance + separate quantization warning | PASS |
| P1 profiled seam | PASS |
| P2 symmetry для возрастающего/убывающего направлений | PASS |
| independent evaluator/certificate + cubic trap/fault injection | PASS |
| failure honesty | PASS |
| deterministic evaluation efficiency; wall-clock diagnostic-only | PASS |

## 8. Контракт implementation seam

Будущая реализация обязана:

1. канонически перечислять family, direction, disconnected parameter branches и tie-safe cells;
2. проверять family-specific достаточность данных до solver call;
3. профилировать все условно-линейные коэффициенты без произвольных `[-20,20]` bounds;
4. использовать manifest-defined starts, seed derivation, traversal/reduction order и pinned numeric tolerances;
5. сохранять каждую attempt, failure code, evaluations, active bounds и competing basin;
6. отделять fit от независимого analytic/high-precision certificate;
7. сворачивать constant, lower-degree и indistinguishable P2 в канонический простой объект до сравнения;
8. не включать runtime, warning presentation и traversal artifacts в model hash;
9. при явно включённом `llm-sr-registry-search-v1` разрешать stochastic search
   менять finite hypothesis portfolio, но только через типизированные P1 family
   IDs и ordered P2 family pairs из текущего registry;
10. оптимизировать параметры и breakpoint каждой LLM-гипотезы только локальным
    solver-ом, оценивать её через `-MSE` после quantization/certificate и
    передавать score обратно через multi-island experience buffer;
11. принимать LLM output только как strict JSON skeletons; coefficient values,
    formula text, code, tool calls, новые family/AST, `eval` и обход bounds
    запрещены. Provider/schema failure атомарно возвращает полный registry;
12. запускать fitting search заново внутри каждого outer-validation training
    scope по замороженному portfolio. Поскольку portfolio выбран по full-data
    fitness, такой запуск маркировать warnings
    `LLM_TRAINING_SUMMARY_DISCLOSED` и
    `LLM_SR_PORTFOLIO_CONDITIONAL_VALIDATION`, не выдавая OOF за untouched
    оценку самой discovery-стадии; bootstrap агрегирует только frozen OOF
    contributions.

## 9. Что этот прототип не доказал

Решение ограничено algorithmic seam. До production acceptance обязательны:

- recovery/confusion каждого core family, обоих направлений, всех ordered pairs, noise и `x` designs;
- отдельный nonlinear Jacobian/rank и parameter-stability анализ, high-precision domain/join fault injection, reciprocal/log singular-domain cases;
- калибровка start budget, condition/near-optimum/collapse/`LOW_X_RESOLUTION` tolerances;
- repeated grouped outer validation, paired OOF group-bootstrap, practical
  uplift, P2 stability/fallback;
- Qn/MAD residual scale, heteroscedastic bins и selected-structure LGO refit
  influence;
- cross-platform reproducibility и golden report checks.
- mock OpenAI-compatible/LangChain integration, multi-island sampling,
  schema/code rejection, scope leakage, secret redaction и deterministic
  fallback optional LLM-SR selector-а.

Эти пункты формализованы тикетом 14 в
[`08-acceptance-handoff.md`](08-acceptance-handoff.md) и machine manifest.
Они являются обязательными implementation gates и не наследуют PASS текущего
exploratory прототипа.
