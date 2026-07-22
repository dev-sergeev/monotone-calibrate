# Математический контракт моделей v1

Статус: утверждён для прототипа  
Дата: 2026-07-16; amendment LLM-SR: 2026-07-22
Связанный тикет: 08 — математический контракт допустимых моделей (внутренний архив, не включён в публичный репозиторий)

## 1. Область действия

Контракт задаёт конечное пространство функций, независимые сертификаты области и монотонности, а также точную семантику одной и двух функций. Он не выбирает модель по качеству: loss, validation, uplift и правило рекомендации задаёт следующий контракт.

Каждая функция на собственном fitted interval `[A,B]` оценивается во внутренней координате

`t = (x-A)/(B-A) ∈ [0,1]`.

Для P1 это `[A,B]=[L,U]`; для P2 левая и правая ветви используют `[L,c]` и `[c,U]`. Итоговая формула обязательно содержит обратное отображение в исходные единицы. Сертификат действует только на свой закрытый наблюдаемый интервал; экстраполяция по умолчанию запрещена.

## 2. Реестр `registry_v1`

Production перебирает только перечисленные шаблоны. Произвольное дерево элементарных операций не является допустимым кандидатом.

| `family_id` | Каноническая формула | Параметры P1 | Условие направления `s∈{+1,-1}` | Статус v1 |
|---|---|---:|---|---|
| `constant_v1` | `a` | 1 | одновременно оба направления; canonical direction `flat` | core/fallback |
| `poly1_v1` | `a+b·t` | 2 | `s·b ≥ 0` | core |
| `poly2_v1` | `a+b·t+c·t²` | 3 | точный минимум `s(b+2ct)` на `[0,1]` неотрицателен | core |
| `poly3_v1` | `a+b·t+c·t²+d·t³` | 4 | точный минимум `s(b+2ct+3dt²)` на концах и внутренней вершине неотрицателен | core, максимальная степень |
| `exp_affine_v1` | `a+b·exp(k·t)` | 3 | `s·b·k ≥ 0` | core |
| `log_shift_v1` | `a+b·log(t+d)` | 3 | `d>0`, `s·b ≥ 0` | core |
| `reciprocal_shift_pos_v1` | `a+b/(t+d)` | 3 | `d>0`, `s·(-b) ≥ 0` | core |
| `logistic_v1` | `a+b·σ(k(t-m))`, `σ(z)=1/(1+exp(-z))` | 4 | `k>0`, `s·b ≥ 0` | core с усиленной диагностикой |
| `reciprocal_shift_neg_v1` | `a+b/(t+d)` | 3 | `d<-1`, `s·(-b) ≥ 0` | incubator, не участвует в production-рекомендации v1 |

`reciprocal_shift_neg_v1` сохраняется в manifest для сравнительного прототипа: его несвязная область и зеркальная кривизна могут быть полезны, но включение в production требует доказанного recovery/uplift без неприемлемой неустойчивости. `logistic_v1` входит в core, потому что это единственная S-образная форма начального реестра, однако rank, saturation и multi-start checks для неё обязательны.

## 3. Числовая policy начального прототипа

После детерминированного affine scaling отклик имеет центр `0` и ненулевой scale. Начальные bounds являются версионируемыми product defaults, а не универсальными научными константами:

| Параметр | Bounds |
|---|---|
| линейные коэффициенты, intercept и amplitude | решаются условно-линейно в scaled-`y`; произвольный hard bound не вводится, обязательны finite/rank/conditioning checks |
| `exp` rate `k` | две solver-ветви `[-12,-0.10]` и `[0.10,12]` |
| logarithmic shift `d` | `[0.02,20]` |
| positive reciprocal shift `d` | `[0.05,20]` |
| negative reciprocal shift `d` | `[-21,-1.05]` |
| logistic rate `k` | `[0.25,20]` |
| logistic midpoint `m` | `[0,1]` |

Предсказания и производные должны оставаться конечными на полном интервале. Активная числовая граница получает `PARAMETER_BOUND_HIT`. Прототип проверяет sensitivity к расширению bounds; менять их после просмотра результата отдельного реального файла нельзя без новой версии registry.

## 4. Сертификат кандидата

Fit и сертификат являются разными операциями. Кандидат допускается к сравнению только после независимого `certify(params, interval, direction)` со следующим payload:

- `registry_version`, `family_id`, canonical AST и hash;
- закрытый интервал и фактическая область определения;
- аналитическая производная и выбранное направление;
- проверенные критические точки и нижняя граница `s·f′`;
- конечность функции и производной;
- степень полинома после упрощения;
- continuity payload для P2;
- статус и reason codes.

Для полиномов степени 0–3 применяется точный алгоритм RQ1. Для остальных core-семейств знак производной следует из знаков параметров и строго положительного множителя. Domain margin для `log`/reciprocal проверяется аналитически; стабильная реализация logistic не заменяет обычную математическую семантику формулы.

Grid probes высокой плотности и property-based numerical probes обязательны как тест реализации, но не являются сертификатом. Если округлённая арифметика не может доказать знак или домен, проверка повторяется с повышенной точностью; интервал, пересекающий ноль или границу домена, даёт `CERTIFICATE_INDETERMINATE`, а не молчаливый pass.

## 5. Канонизация и collapse

1. Полином приводится к коэффициентам по степеням; нулевой старший коэффициент последовательно понижает степень.
2. `b=0` сводит affine/nonlinear семейство к `constant_v1`.
3. Сдвиг внутри экспоненты запрещён: он поглощается амплитудой.
4. Logistic всегда имеет `k>0`; эквивалентная форма с отрицательным `k` преобразуется заменой baseline/amplitude.
5. `t/(t+d)` не образует семейство, поскольку канонически является affine reciprocal.
6. Практически вырожденная nonlinear-кривая сравнивается с refit более простого вложенного кандидата по значениям и производной на всём `[0,1]`. При неразличимости используется простой кандидат и код `COLLAPSED_TO_SIMPLER`.
7. Parameter-bearing `model_instance_hash` строится из версии registry, canonical AST, canonical IEEE-754 representation нормализованной параметризации и направления, а не из форматированной строки. Отдельный `model_structure_hash` исключает floating coefficients и используется для cross-platform structural replay; он не заменяет instance hash конкретного fit.

Scale-free collapse tolerance v1 заморожен acceptance manifest: максимум
разности прогнозов не больше `10⁻⁶` scaled-`y` и максимум разности производных
не больше `10⁻⁶` scaled-`y` на единицу `t`, подтверждённые high-precision
interval/probe procedure. Его untouched operating-characteristic проверка
исполняется implementation handoff.

## 6. Односегментный класс P1

Для каждого core family независимо оцениваются `s=+1` и `s=-1`, затем выполняются domain, derivative, rank, conditioning и canonicalization checks. Constant оценивается один раз с направлением `flat`.

`P1` обязана содержать `constant_v1` как operational fallback. При постоянном `y` канонический результат — `f(x)=y₀`, `R²=null` со статусом `UNDEFINED_R2_CONSTANT_Y`, RMSE/MAE показываются. Успешный сертификат не означает, что семейство выбрано или хорошо идентифицировано.

## 7. Двухсегментный класс P2

Пусть `u_j<u_{j+1}` — соседние уникальные `x`, а накопленное число строк слева удовлетворяет `ceil(0.4n_used) ≤ W_j ≤ floor(0.6n_used)`. Для этой membership-ячейки:

- `c ∈ [u_j,u_{j+1})` оптимизируется совместно с параметрами; binary64-реализация использует верхнюю границу `nextafter(u_{j+1},u_j)`;
- строки `x≤c` принадлежат только левому сегменту, `x>c` — только правому;
- группа одинаковых `x` неделима;
- каждая ветвь проходит ограничения достаточности данных из контракта v1;
- обе ветви имеют одно `s`; плато допустимо;
- непрерывность производной не требуется.

Для левой ветви `t₁=(x-L)/(c-L)`, для правой `t₂=(x-c)/(U-c)`. Непрерывность значения обеспечивается конструкцией

```text
F(x) = μ + h₁(t₁;θ₁) - h₁(1;θ₁),  x ≤ c,
       μ + h₂(t₂;θ₂) - h₂(0;θ₂),  x > c,
```

где intercept каждой ветви удалён, а `μ` — общее значение в границе. `c`, `μ`, обе shape-параметризации, направление и ordered pair семейств являются частью одного совместного fit. Положительный множитель `dt/dx` сохраняет знак производной. Мягкий penalty за разрыв запрещён.

После fit независимая проверка повторно вычисляет обе формулы в `c` с повышенной точностью. Допуск renderer/solver равен `10⁻¹⁰·max(1,|μ|)` в scaled-`y`; превышение даёт `JOIN_FAILURE`. Математический объект при этом всё равно определяется exact centered construction.

Разрешены одинаковые семейства слева и справа и одна постоянная ветвь. Две постоянные ветви либо практически совпадающие ветви сводятся к P1 (`COLLAPSED_SEGMENTS`). Если допустимой membership-ячейки нет, статус `NO_BALANCED_SPLIT`; если ячейка есть, но ни одна пара не проходит fit/certificates, статус `NO_VALID_TWO_MODEL`.

## 8. Идентифицируемость и оптимизация

Для линейных по параметрам коэффициентов применяется data-derived conditional
constrained least squares; эти коэффициенты не являются nonlinear start slots.
Для nonlinear shape-параметров используется только фиксированный
детерминированный multi-start из frozen
[`start-slot-policy-v1.json`](../acceptance/start-slot-policy-v1.json).
Сохраняются все запуски, а не только победитель. Опциональный LLM-SR selector
может сузить finite portfolio typed family/pair skeletons, но не меняет starts,
solver budget или certificate policy.

Для каждого finite feasible optimum фиксируются Jacobian rank, singular values, condition number, active bounds, gradient/optimality, число evaluations и расстояние до конкурирующих решений. Статусы:

| Код | Допустимость |
|---|---|
| `DOMAIN_FAILURE`, `SHAPE_FAILURE`, `JOIN_FAILURE` | кандидат недопустим |
| `OPTIMIZER_FAILURE`, non-finite objective | кандидат недопустим |
| `RANK_FAILURE` | кандидат недопустим |
| `CERTIFICATE_INDETERMINATE` | кандидат недопустим до успешной high-precision проверки |
| `WEAK_IDENTIFIABILITY` | P1 видим с warning и требует stability gate; для P2 нестабильность границы/ветвей запрещает рекомендацию |
| `MULTIPLE_NEAR_OPTIMA`, `FLAT_PROFILE`, `BOUNDARY_HIT` | кандидат видим; flat/multiple breakpoint делает P2 non-recommendable, остальные случаи решает validation/stability contract |
| `COLLAPSED_TO_SIMPLER`, `COLLAPSED_SEGMENTS` | заменяется канонической простой моделью |

Числовая policy v1 заморожена acceptance manifest: `κ(J)>10⁸` даёт warning,
`κ(J)>10¹²` либо rank deficiency — `WEAK_IDENTIFIABILITY`; technical objective
tie и near-optimum tolerances заданы именованными формулами
`numeric-acceptance-v1`. Confirmatory calibration этих engineering defaults
является blocking implementation gate, а не оставшимся design-time выбором.

## 9. Сложность и ничьи

Модели не сворачиваются в псевдо-MDL score. Для каждого кандидата сохраняется Pareto-vector:

`(validation_loss, segment_count, free_parameter_count, nonlinear_parameter_count, AST_nodes, instability_flags, family_ids)`.

Критерий практически значимого качества задаёт validation/uplift contract. Среди кандидатов, признанных им практически неразличимыми, выбирается лексикографически меньший structural vector без `validation_loss`; остаточная техническая ничья разрешается стабильным `family_id`/canonical-hash. P1 имеет приоритет над P2 при отсутствии доказанного uplift.

Все недоминируемые сертифицированные кандидаты и причины отбраковки сохраняются в machine-readable trace. Training `R²` не разрешает ничью и не отменяет предупреждение `<0.60`.

## 10. Запрещённые production-формы

В v1 запрещены:

- полиномы степени выше 3;
- `sin`, `cos`, `tan`;
- arbitrary division, arbitrary real powers и roots;
- `abs`, `sign`, `min/max`, conditions, `floor/ceil`;
- protected division/log/sqrt;
- вложенные transcendental compositions и более одного nonlinear atom;
- формулы symbolic-regression engine, не сведённые к core family;
- автоматические сдвиги/удаление строк ради прохождения domain certificate;
- три и более сегмента и разрывы значения.

PySR/GP разрешены только как offline shadow-generator. Найденная ими форма становится кандидатом будущего registry лишь после отдельного решения, канонизации, сертификата и version bump; текущая production-рекомендация от shadow run не зависит.

## 11. Обязательные проверки прототипа

1. Recovery/confusion для каждого core family, обоих направлений и разных noise/`x` designs.
2. Collapse к более простым формам и near-equivalent families.
3. Narrow-domain, pole, overflow и protected-semantics injection.
4. Exact polynomial certificate против high-precision derivative minimization.
5. Multi-start coverage, Jacobian conditioning и parameter stability nonlinear families.
6. Logistic saturation и positive/negative reciprocal comparison.
7. Все ordered core-family pairs по допустимым boundary cells, continuity и runtime.
8. Permutation, repeated-`x`, boundary `40/60` и no-balanced-split fixtures.
9. Reproducibility snapshot с registry manifest/hash, seeds и environment.
10. Degree-4 AST всегда отклоняется; точный нулевой старший коэффициент канонизирует полином к меньшей степени.
11. Обе ветви P2 дают `μ` в `c` в пределах pinned join tolerance; скачок производной при этом разрешён.
12. Domain/non-finite/shape/join/rank/optimizer failure не достигает рекомендации даже при лучшем training `R²`.
13. Перестановка строк и порядка multi-start не меняет canonical winner/hash вне pinned objective tolerance.

Если core family не проходит эти проверки, она переводится в incubator новой версией; silently изменять registry после запуска нельзя.
