# RQ3 — поиск интерпретируемых элементарных функций

**Дата исполнения:** 2026-07-16  
**Дата отсечения:** 2026-07-16 23:59:59, Europe/Moscow  
**Область:** одна таблица `x, y`; одна функция либо по одной функции на каждый из двух непрерывных интервалов; полином степени не выше 3; глобальная монотонность на наблюдаемом `[L,U]`  
**Статус обзора:** rapid-review с независимым аудитом ключевых решений. Он достаточен для спецификации и постановки прототипа, но не является публикационно полным систематическим обзором по всем базам из [`00-evidence-review-protocol.md`](../00-evidence-review-protocol.md).

## Решение в одном абзаце

В production не следует искать по неограниченному пространству «всех элементарных функций». Начальный механизм должен детерминированно перебрать небольшой версионируемый реестр канонических шаблонов, отдельно: сгенерировать семейство, оценить его параметры, сертифицировать истинную область определения и знак аналитической производной на всём `[L,U]`, устранить эквивалентные/вырожденные формы и лишь затем сравнить допустимые модели по out-of-sample качеству и сложности. Свободная symbolic regression — GP, PySR или AI Feynman — полезна как исследовательский генератор новых гипотез и benchmark, но её формула не становится допустимой, пока не сведена к утверждённому шаблону и не прошла независимые сертификаты RQ1. Рекомендуемый стартовый реестр: константа; полиномы степеней 1–3; affine-exponential, shifted-log, shifted-reciprocal и logistic. Защищённые операторы, произвольное деление/степень, тригонометрия и произвольные вложенные композиции из production-грамматики исключаются.

## 1. Разделение ответственности: generation ≠ certification ≠ selection

Надёжный pipeline обязан различать четыре сущности:

1. **Генерация формы** возвращает идентификатор семейства или исследовательское выражение. Здесь допустимы конечный перебор, грамматика, GP/PySR, AI Feynman и эвристики.
2. **Оценивание параметров** решает ограниченную линейную или нелинейную задачу для уже фиксированной формы. Оно не доказывает, что выбрана глобально лучшая форма или найден глобальный optimum параметров.
3. **Сертификация** независимо проверяет математический смысл формулы: истинный домен, конечность, project-ограничение степени, каноническую форму и монотонность на всём непрерывном интервале. Правила сертификата заданы в [RQ1](01-monotone-shape-constrained.md).
4. **Выбор и рекомендация** сравнивает только сертифицированные модели на данных, не участвовавших в выборе. Конкретные uplift/validation правила относятся к RQ4; training `R²` не заменяет этот этап.

Это не только архитектурная аккуратность. Grammar-based GP действительно позволяет задавать пространство выражений через типы или правила: strongly typed GP ограничивает допустимые программы типами ([Montana, 1995](https://doi.org/10.1162/evco.1995.3.2.199)), grammatical evolution отображает геном в правила BNF ([O'Neill & Ryan, 2001](https://doi.org/10.1109/4235.942529)), а детерминированный Prioritized Grammar Enumeration отделяет перебор форм от нелинейной подгонки коэффициентов ([Worm & Chiu, 2013](https://doi.org/10.1145/2463372.2463486)). Но ни грамматическая корректность, ни хорошая fitness сами по себе не являются сертификатом истинного домена и whole-interval монотонности.

## 2. Почему пространство должно быть ограниченным

Классический genetic programming Коza дал общий механизм поиска программ-деревьев ([Koza, 1992](https://mitpress.mit.edu/9780262527910/genetic-programming/)). Такая свобода полезна для discovery, но не даёт приемлемого product-контракта по трём причинам.

Во-первых, пространство растёт комбинаторно. AI Feynman прямо описывает экспоненциальный рост числа строк выражений с длиной и размером алфавита; его ускорения используют физические специальные структуры — размерности, симметрии, разделимость, полиномиальность ([Udrescu & Tegmark, 2020](https://doi.org/10.1126/sciadv.aay2631)). NP-hardness symbolic regression доказана для конкретной общей постановки Virgolin и Pissis; это не доказывает трудность каждого маленького проектного реестра, но исключает ожидание универсального эффективного exact-решателя для общей задачи ([TMLR, 2022](https://arxiv.org/abs/2207.01018)).

Во-вторых, разные деревья могут описывать одну функцию, а один и тот же шаблон — иметь неидентифицируемые параметры. Для достаточно богатых классов elementary expressions существуют неразрешимые задачи определения тождественного нуля/эквивалентности; theorem Richardson не относится ко всякому ограниченному реестру, но делает неверным обещание универсальной полной канонизации «всех элементарных функций» ([Richardson, 1968](https://doi.org/10.2307/2271358)).

В-третьих, найденное выражение может быть численно вычислимо лишь благодаря искусственной семантике protected operators. Такая формула выглядит простой, но не является той же математической функцией, которую пользователь прочитает в отчёте.

Итог: complete exhaustive search возможен и полезен **условно** — только при заранее фиксированном конечном базисе и пределе сложности. Exhaustive Symbolic Regression перечисляет выражения при таких ограничениях и строит полную Pareto-карту, но гарантия лучшего выражения дополнительно зависит от корректной оптимизации непрерывных параметров ([Bartlett, Desmond & Ferreira, 2024](https://doi.org/10.1109/TEVC.2023.3280250)). Для текущего одномерного продукта ещё сильнее и проще явный перебор шаблонов, а не произвольных AST.

## 3. Рекомендуемый начальный реестр

Перед fit координату следует нормировать:

`t = (x-L)/(U-L) ∈ [0,1]`.

Это делает параметры и bounds сопоставимее между наборами данных. Реестр задаётся manifest-файлом с неизменяемыми `family_id`, версией, формулой, числом параметров, допустимыми bounds, правилами canonicalization, fit-процедурой и независимыми `domain_certificate`/`derivative_certificate`.

| `family_id` | Канонический шаблон | Домен и монотонность | Вырождение/идентифицируемость | Статус |
|---|---|---|---|---|
| `constant_v1` | `a` | определена везде; одновременно nondecreasing/nonincreasing | единственный параметр | обязательный fallback |
| `poly1_v1` | `a+b t` | знак `b`; exact | при `b≈0` свести к constant | core |
| `poly2_v1` | `a+b t+c t²` | exact minimum линейной производной на `[0,1]` по RQ1 | при `c≈0` свести к poly1 | core |
| `poly3_v1` | `a+b t+c t²+d t³` | exact minimum квадратичной производной в концах и внутренней вершине | при `d≈0` свести к poly2 | core; жёсткий максимум степени |
| `exp_affine_v1` | `a+b exp(k t)` | домен весь `R`; знак производной `b·k` | shift в экспоненте запрещён как избыточный; при `k≈0`/`b≈0` collapse | core |
| `log_shift_v1` | `a+b log(t+d)` | начальная ветвь `d>0`; знак производной `b` | большой `d` почти линеаризует модель; фиксировать bounds | core с осторожностью |
| `reciprocal_shift_pos_v1` | `a+b/(t+d)` | `d>0`; знак производной `-b` | большой `|d|` почти линеаризует модель | core с осторожностью |
| `reciprocal_shift_neg_v1` | `a+b/(t+d)` | отдельная ветвь `d<-1`; знак производной `-b` | не смешивать две несвязные области одного параметра в одном запуске solver | incubator до проверки необходимости |
| `logistic_v1` | `a+b σ(k(t-c))`, `σ(z)=1/(1+exp(-z))` | канонически `k>0`; знак производной `b`; `c∈[0,1]` на первом этапе | симметрия удалена; saturation/малый `k` дают слабую идентифицируемость | core с диагностикой |

Почему именно такой старт:

- он покрывает постоянную, линейную, одно- и двухкривизные полиномиальные формы, выпуклую/вогнутую экспоненциальную, логарифмическую, гиперболическую и S-образную форму;
- для каждого семейства можно написать короткий аналитический сертификат без численной сетки;
- не более четырёх свободных параметров ограничивает локальную неидентифицируемость и стоимость двойного поиска из [RQ2](02-continuous-segmented-regression.md);
- полиномиальная степень проверяется **после упрощения**: выражение вроде `t*t*t*t` не должно обходить лимит синтаксисом;
- это предложение для prototype, а не эмпирически доказанный «универсально лучший» каталог. Окончательное включение `log`, reciprocal и logistic требует synthetic recovery/stability испытаний.

### 3.1 Что пока не включать

В production-реестр первого этапа не входят:

- `sin`, `cos`, `tan`: частотно-фазовые aliases, множество локальных минимумов и сложная сертификация монотонного интервала;
- произвольное `/`, arbitrary real `pow`, roots и произвольный `log(argument)`: домен зависит от вложенного выражения;
- `abs`, `sign`, `min`, `max`, условные операторы, `floor`, `ceil`: kink/discontinuity и неоднозначный derivative contract;
- вложенные transcendental compositions и более одного нелинейного атома;
- полиномы степени выше 3;
- protected division/log/sqrt как семантика финальной формулы;
- unrestricted ephemeral constants внутри произвольного дерева.

Как incubator после прототипа можно проверить `a+b(t+d)^p` при строго положительном основании и bounded `p`, Gompertz и `arctan`. У shifted power особенно много near-duplicates: `p≈0,1,2,3,-1` воспроизводит уже имеющиеся формы, поэтому без канонизации и stability evidence он расширит поиск сильнее, чем полезное разнообразие.

## 4. Если нужен исследовательский grammar-search

Exploration-грамматика должна быть отдельной от production registry. Рекомендуемый bounded вариант:

```text
Expr   := Affine | Polynomial | OuterNonlinear
Affine := a + b*t
Polynomial := a + b*t | a + b*t + c*t^2 | a + b*t + c*t^2 + d*t^3
OuterNonlinear := a + b*Atom
Atom   := exp(k*t) | log(t+d) | 1/(t+d) | sigmoid(k*(t-c))
```

Ограничения: одна переменная `t`; максимум один нелинейный атом; глубина не является единственным лимитом; максимум четыре fitted parameters; отдельные parameter bounds; запрещены рекурсивные правила, способные бесконтрольно порождать вложенность; после генерации обязательна канонизация AST и family recognition.

Strong typing и BNF удобны для кодирования допустимых структур, однако grammar itself вносит bias: частота/порядок правил влияет на вероятность получения форм. У O'Neill и Ryan BNF mapping допускает recursion/wrapping; поэтому даже синтаксически ограниченная рекурсивная грамматика должна иметь явные пределы глубины, числа expansion и wrapping ([2001](https://doi.org/10.1109/4235.942529)). Детерминированный PGE устраняет random choice и использует canonical trees/memoization, но число выражений всё равно растёт экспоненциально ([Worm & Chiu, 2013](https://doi.org/10.1145/2463372.2463486)). FFX показывает другой полезный шаблон — детерминированно построить фиксированный большой набор basis functions и разреженно выбрать их ([McConaghy, 2011](https://doi.org/10.1007/978-1-4614-1770-5_13)); для проекта это источник идеи, а не прямое решение, потому что линейная сумма нескольких basis уже выходит за контракт «одно каноническое семейство».

## 5. Канонизация, эквивалентные выражения и идентифицируемость

### 5.1 Обязательные algebraic rules

Для утверждённого реестра нужна не универсальная CAS-канонизация, а конечный список family-specific правил:

- упорядочить коммутативные операнды, объединить числовые коэффициенты, удалить `+0`, `*1`, нулевые члены и привести polynomial к коэффициентам по степеням;
- запретить redundant exponential shift: `b exp(k(t-c)) = [b exp(-kc)] exp(kt)`;
- для logistic зафиксировать `k>0`, поскольку
  `a+b σ(k(t-c)) = (a+b)-b σ(-k(t-c))`;
- не считать `t/(d+t)` новым семейством: `t/(d+t)=1-d/(d+t)` уже содержится в affine reciprocal;
- при нулевой/практически нулевой амплитуде или старшем коэффициенте явно свести модель к младшему семейству;
- каждой канонической формуле присваивать hash от `registry_version + canonical_AST + normalized_parameterization`, а не от печатной строки.

CAS simplification и численные fingerprints на dense/random points полезны для обнаружения duplicate-кандидатов, но не являются доказательством тождества. Ограничение Richardson относится к богатому общему классу, поэтому здесь вывод узкий: не обещать полную эквивалентность вне версионированного реестра; внутри реестра использовать доказанные family rules.

### 5.2 Structural и practical identifiability

Уникальный текст формулы ещё не означает уникальные параметры. Классическое определение identifiability требует, чтобы различные параметры задавали различные распределения/выходы; ранние общие условия обсуждает Rothenberg ([1971](https://doi.org/10.2307/1912253)), а derivative-rank критерии parameter redundancy для широкого класса нелинейных моделей дают Catchpole и Morgan ([1997](https://doi.org/10.1093/biomet/84.1.187)). Их конкретные модели не являются этим продуктом, но принцип переносится напрямую.

Для каждого fitted candidate сохраняются:

- rank и singular values Jacobian в optimum;
- condition number с заранее заданным warning threshold;
- параметры на bounds и active constraints;
- несколько distinct parameter starts с близкой objective;
- profile objective или bootstrap stability для ключевых nonlinear parameters;
- collapse test против вложенного более простого семейства по значениям и производной на `[0,1]`.

Если разные параметры дают практически неразличимую кривую, отчет не должен изображать точные параметры как устойчивое физическое объяснение. Кандидат может остаться прогнозной формулой с предупреждением, но более простая каноническая модель имеет приоритет при сопоставимом validated качестве.

## 6. Protected operators и истинная область определения

В GP часто используют totalized/protected division, log или square root, чтобы вычисление дерева не падало. Проблема в том, что правила вроде «при делении на ноль вернуть 1» или «`log(x)` заменить `log(abs(x))`» меняют математическую функцию, могут создавать разрыв и маскировать pole/domain violation. Keijzer ввёл interval arithmetic для улучшения SR и отсечения недопустимых выражений ([2003](https://doi.org/10.1007/3-540-36599-0_7)); Dick показал, что обычные protected operators могут иметь нежелательные семантические свойства, а простая interval evaluation сама по себе не гарантирует отсутствия invalid offspring ([2017](https://arxiv.org/abs/1704.04998)).

Production-правило:

1. protected wrappers разрешены только внутри исследовательского search engine, если иначе engine технически не работает;
2. экспортированное выражение повторно интерпретируется с обычной математической семантикой;
3. его домен сертифицируется на **всём** `[L,U]`, не только на строках таблицы;
4. mismatch между search semantics и export semantics либо pole/invalid argument отклоняет кандидат;
5. interval arithmetic может быть conservative prefilter, но dependency overestimation способна дать слишком широкий interval; для малого реестра окончателен family-specific analytic certificate RQ1.

Официальная документация PySR также требует, чтобы custom Julia operators не бросали исключения на типичных real inputs, и предупреждает, что увеличение набора operators экспоненциально расширяет поиск ([operators documentation](https://ai.damtp.cam.ac.uk/pysr/dev/operators)). Это удобный контракт движка, но не разрешение подменить опубликованную формулу protected-семантикой.

## 7. Монотонность как hard certificate, а не fitness

Есть два разных способа использовать shape constraint:

- **constraint-aware generation/optimization** уменьшает число заведомо плохих кандидатов;
- **independent certification** решает, можно ли показывать модель пользователю как глобально монотонную.

Shape-Constrained Symbolic Regression Kronberger et al. вычисляет интервальные bounds выражений и производных и отделяет feasible/infeasible populations. Работа демонстрирует реализуемость whole-domain constraints, но interval bounds консервативны, а constrained модели в её экспериментах не обязательно выигрывали по predictive error ([2022](https://doi.org/10.1162/evco_a_00294)). Soft-penalty multiobjective подход Haider et al. полезен для направления поиска, но penalty=small или проверка derivative на grid не доказывают constraint ([2021](https://arxiv.org/abs/2107.09458)). Counterexample-driven GP с SMT может формально проверять поддерживаемые ограничения и возвращать counterexamples ([Bladek & Krawiec, 2023](https://doi.org/10.1109/TEVC.2022.3205286)), однако solver theory не охватывает автоматически всякую transcendental grammar.

Поэтому для текущего реестра:

- generator может параметризовать нужный знак заранее и отбрасывать infeasible формы;
- вся принятая модель отдельно проходит analytic domain/derivative certificate RQ1;
- interval/SMT остаются дополнительными средствами для будущего расширения, а не единственным production proof;
- grid derivative checks — regression tests реализации и визуальная диагностика, не сертификат.

Для двух интервалов каждая ветвь сертифицируется на своей замкнутой области, а continuity и согласованное направление проверяются по RQ2. Пара семейств не получает исключений из registry или certificate rules.

## 8. Сложность, parsimony и MDL

Node count удобен, но не является универсальной мерой интерпретируемости. SRBench использует размер expression tree как воспроизводимый proxy; сама benchmark-работа предупреждает, что малый размер не гарантирует содержательно правильную или понятную формулу ([La Cava et al., 2021](https://datasets-benchmarks-proceedings.neurips.cc/paper_files/paper/2021/file/c0c7c76d30bd3dcaefc96f40275bdc0a-Paper-round1.pdf)). Vladislavleva, Smits и den Hertog показали, что альтернативная мера order of nonlinearity меняет Pareto-search и получаемые модели ([2009](https://doi.org/10.1109/TEVC.2008.926486)). Следовательно, нельзя спрятать product preference в одном произвольном «complexity score» без версии и объяснения.

Minimum Description Length — более строгий принцип: выбрать модель, которая кратко кодирует и саму модель, и оставшиеся данные/ошибки ([Rissanen, 1978](https://doi.org/10.1016/0005-1098%2878%2990005-5); [Barron, Rissanen & Yu, 1998](https://doi.org/10.1109/18.720554)). Но число узлов плюс `λ` не становится MDL от названия. Нужны явные коды для:

- family/AST;
- параметров, их precision и bounds;
- residuals или likelihood/noise model;
- любых segment/breakpoint данных;
- единиц измерения/предобработки, если они влияют на декодирование.

До определения такого кода рекомендуется сохранять Pareto-vector, а не псевдо-MDL scalar:

`(validated_error, family_rank, free_parameter_count, nonlinear_parameter_count, AST_nodes, instability_flags)`.

Начальный порядок `constant < poly1 < poly2 < poly3`, а transcendental families сравниваются отдельно с числом parameters/operators; точный tie-break и минимальный uplift определяет RQ4. В отчете показываются все nondominated сертифицированные кандидаты и причина финальной рекомендации. `R²<0.60` обязательно вызывает пользовательское предупреждение по уже принятому product-требованию, но сам порог не доказывает, что следует выбрать более сложную форму.

## 9. Нелинейная подгонка и воспроизводимость

### 9.1 Fit contract

Для каждого семейства нужны фиксированные bounds/reparameterization и одинаково строгий протокол:

1. сформировать детерминированный набор стартов из data-derived и fixed quantile points;
2. запускать bounded nonlinear least squares для каждого старта;
3. сохранять все статусы, objective, gradient/optimality, active bounds и число evaluations;
4. выбирать лучшее **feasible finite** решение по заранее заданной training objective;
5. независимо повторить domain/derivative/canonicalization checks;
6. при нескольких разнесённых near-optima выставлять `MULTIPLE_NEAR_OPTIMA`, а не скрывать их;
7. при rank deficiency/flat profile выставлять `WEAK_IDENTIFIABILITY`;
8. никогда не считать первый solver success доказательством global optimum.

Нелинейное least squares для coefficients внутри SR улучшает fit, но требует внимания к локальным optimum и parameter identification ([Kommenda et al., 2020](https://doi.org/10.1007/s10710-019-09371-3)). Точный solver/версии и robust-loss решение должны согласовываться с RQ1/RQ4.

### 9.2 Полный reproducibility record

На каждый запуск сохраняются:

- hash исходного файла и нормализованной таблицы;
- registry manifest version и hash;
- canonical grammar/operator list, complexity vector и parameter bounds;
- seed, число повторов, population/search budgets, wall-clock/iteration budgets;
- package, runtime, OS/architecture и floating-point precision;
- split/fold indices и hash всего selection configuration;
- все generated candidates, fit/certificate/failure statuses и final canonical AST;
- версия report renderer и timestamp.

Детерминизм означает повторяемость при зафиксированном execution contract, а не тождественность на любых CPU/BLAS. Для stochastic exploration нужны несколько заранее заданных seeds и selection frequencies. Для PySR официальный режим требует одновременно фиксировать `random_state`, `deterministic=True` и serial execution; иначе многопоточность и эволюционный поиск не обещают одинаковый результат ([PySR API](https://ai.damtp.cam.ac.uk/pysr/v1.5.9/api.html)).

## 10. Методы и реализации: что использовать, а что нет

| Метод/реализация | Что подтверждено | Роль в проекте | Ограничение переноса |
|---|---|---|---|
| GP / typed or grammar GP | гибкий search по expression trees; grammar/types задают inductive bias | историческая и алгоритмическая основа exploration | stochastic; bloat/duplicates; нет автоматического project-certificate |
| PGE | детерминированный grammar enumeration, canonicalization/memoization, Pareto priority | design reference для bounded enumeration | всё ещё экспоненциальный рост; старый research implementation не выбран dependency |
| FFX | детерминированная генерация fixed basis + sparse selection | design reference для separating generation/selection | линейные суммы bases могут нарушить контракт одного семейства |
| AI Feynman | symmetry, separability, dimensional analysis и brute-force layers; сильная exact recovery на physics equations | optional benchmark/candidate generator | benchmark ориентирован на known physics structure; recovery ухудшается с noise; не domain/monotonicity certifier |
| Exhaustive SR | полный bounded enumeration/Pareto при фиксированном basis/complexity | benchmark для очень маленькой grammar | continuous parameters всё равно требуют оптимизации; произвольная grammar быстро растёт |
| [SRBench](https://cavalab.org/srbench/) | открытая сравнительная платформа: 14 SR и 7 ML методов, 252 tasks в публикации | prototype benchmark protocol, seeds/budgets/metrics reference | его datasets не воплощают exact project constraints; tree size лишь proxy |
| [PySR](https://github.com/MilesCranmer/PySR) / [SymbolicRegression.jl](https://github.com/astroautomata/SymbolicRegression.jl) | maintained evolutionary SR, operators/constraints, constant optimization, Pareto hall of fame; Apache-2.0 | **shadow exploration only**; найденные формы — предложения в registry backlog | custom operator mappings могут расходиться; observed-point NaN handling не доказывает continuous domain; stochastic |

На cutoff официально зафиксированы stable PySR 1.5.10 (2026-03-30) и SymbolicRegression.jl 1.13.2 (2026-03-29); при prototype нужно pin exact versions, а не автоматически перейти на alpha/next major. Software paper PySR описывает evolve–simplify–optimize подход ([Cranmer, 2023](https://arxiv.org/abs/2305.01582)). Лицензия Apache-2.0 позволяет исследовательскую интеграцию, но transitive dependencies и deployment всё равно проходят отдельный legal/security review. Исходный AI Feynman code и benchmark assets нельзя включать в product только по научной ссылке: перед копированием требуется отдельная проверка конкретной версии и лицензии.

## 11. Что говорят benchmarks — и чего они не говорят

SRBench сравнил 14 SR и 7 ML методов на 252 задачах с повторными seeds и фиксированными budgets. В paper GP-based методы были сильны на black-box prediction; AI Feynman показывал сильную exact recovery без/при очень малом noise, но становился хрупким с ростом noise и слабее на black-box datasets. Авторы также показывают, что почти идеальный predictive score не означает exact symbolic recovery ([La Cava et al., 2021](https://arxiv.org/abs/2107.14351)).

Более ранний большой benchmark Orzechowski, La Cava и Moore обнаружил конкурентное predictive качество SR, но высокую вычислительную стоимость ([2018](https://doi.org/10.1145/3205455.3205539)). White et al. зафиксировали проблемы toy benchmarks, слабого experimental rigor и отсутствия консенсуса о единственном наборе задач ([2013](https://doi.org/10.1007/s10710-012-9177-2)). Matsubara et al. показали, что диапазоны sampling и metric symbolic similarity существенно меняют выводы; они предложили более реалистичные physics ranges и normalized edit distance ([2022](https://arxiv.org/abs/2206.10540)).

Отсюда для prototype нужен собственный benchmark matrix, а не один aggregate leaderboard:

- каждое семейство реестра, возрастающее и убывающее;
- чистые и noisy данные на нескольких уровнях;
- narrow ranges, где разные семьи почти эквивалентны;
- repeated `x`, outliers, малое число unique `x`;
- параметры у domain bounds и близко к collapse;
- misspecified curves вне реестра;
- одна функция и допустимая непрерывная пара по RQ2;
- exact recovery, predictive error, certificate pass rate, family confusion, stability и runtime;
- adversarial cases с pole/invalid log между наблюдаемыми точками.

Exact tree-match не является единственной истиной: эквивалентные parameterizations дают разные trees. Но numerical fit также недостаточен. Ground-truth benchmark должен оценивать `(canonical family, function distance on interval, derivative distance, domain/certificate status)`.

## 12. Независимый аудит и принятые уточнения

Независимый reviewer проверил decision-bearing выводы после основного поиска. В отчет интегрированы следующие замечания:

- добавлены strongly typed GP, BNF/recursion, deterministic PGE и fixed-basis FFX как разные механизмы ограничения формы;
- NP-hardness и theorem Richardson сформулированы с областью применимости, без ложного переноса на малый конечный реестр;
- усилен запрет protected operators в финальной семантике;
- registry manifest, canonical AST/hash, Jacobian/identifiability и полный reproducibility record стали обязательными;
- вместо одного arbitrary complexity score принят Pareto-vector; MDL разрешён как название только при явном коде;
- benchmark-результаты White, Orzechowski, SRBench и Matsubara представлены как evidence о протоколе оценки, а не как доказательство выбора production-алгоритма.

Аудит не выявил основания расширять production grammar до произвольных elementary functions или принимать формулу PySR/AI Feynman без RQ1-сертификата. Разногласия по окончательному составу `log`/reciprocal/logistic оставлены для prototype, поскольку литература не отвечает на project-specific trade-off данных и стабильности.

## 13. Матрица ключевых утверждений

| Claim ID | Вывод | Главная опора | Применимость |
|---|---|---|---|
| `C-RQ3-0001` | Общий SR search комбинаторно труден; ограниченный реестр необходим для auditability | Virgolin–Pissis; AI Feynman; Bartlett et al. | `direct` для design, не lower bound малого registry |
| `C-RQ3-0002` | Grammar/types задают допустимые структуры, но не сертифицируют домен/монотонность | Montana; O'Neill–Ryan; Worm–Chiu | `direct` |
| `C-RQ3-0003` | Универсальная полная canonicalization богатых elementary expressions невозможна в общем случае | Richardson | `partial`; restricted registry разрешим family rules |
| `C-RQ3-0004` | Protected semantics нельзя выдавать как обычную математическую формулу | Keijzer; Dick; direct semantic argument | `direct` |
| `C-RQ3-0005` | Whole-domain shape constraints можно учитывать при search, но нужен независимый certificate | Kronberger et al.; Haider et al.; Bladek–Krawiec; RQ1 | `direct` для принципа |
| `C-RQ3-0006` | Node count — proxy, а MDL требует явного кода модели, параметров и residuals | Rissanen; Barron et al.; Vladislavleva et al.; SRBench | `direct` |
| `C-RQ3-0007` | Parameter optimization и identifiability должны диагностироваться отдельно от form search | Kommenda et al.; Rothenberg; Catchpole–Morgan | `direct` как contract, методические детали требуют prototype |
| `C-RQ3-0008` | Benchmark performance не переносится автоматически на noisy univariate monotone curve | White; Orzechowski; La Cava et al.; Matsubara et al. | `direct` как ограничение переноса |
| `C-RQ3-0009` | PySR полезен как candidate generator при pin/seed/serial, но не production certifier | official PySR docs/software paper; RQ1 | `direct` |
| `C-RQ3-0010` | Малый шаблонный registry позволяет exact family-specific domain/derivative rules | algebraic derivations проекта; RQ1 | `direct`; empirical utility pending |

## 14. Что осталось зафиксировать в model contract и проверить прототипом

### До реализации

1. Утвердить `registry_v1`: включать ли обе reciprocal branches и входит ли logistic в core либо incubator.
2. Установить numerical bounds для `k`, `d`, `c`, coefficients и scale-aware tolerances collapse/domain/derivative.
3. Утвердить objective/robust loss, обработку weights/repeated `x` и minimum sample/rank rules совместно с RQ1.
4. Задать canonical family ranking и RQ4 rule выбора по nested/out-of-sample quality, включая uplift одной против двух функций.
5. Формализовать failure codes: `NO_VALID_FAMILY`, `DOMAIN_FAILURE`, `SHAPE_FAILURE`, `OPTIMIZER_FAILURE`, `WEAK_IDENTIFIABILITY`, `MULTIPLE_NEAR_OPTIMA`, `COLLAPSED_TO_SIMPLER`, `EXPLORATORY_ONLY`.
6. Определить формат manifest, canonical AST, certificate payload и reproducibility bundle.
7. Решить, разрешается ли PySR только offline R&D или также в reproducible shadow run; production recommendation от него не зависит.

### Прототипом

1. Synthetic recovery/confusion matrix для всех форм и направлений, включая collapse и narrow-domain cases.
2. Сравнение explicit enumeration с PySR shadow exploration при одинаковом time budget.
3. Проверка multi-start coverage и стабильности nonlinear parameters.
4. Property-based tests домена и derivative certificate против high-precision numerical probes, не заменяющих proof.
5. Injection tests на protected-operator mismatch, poles и invalid arguments между data points.
6. Вычислительная стоимость всех ordered family pairs и breakpoint profiles из RQ2.
7. Nested validation одной и двух функций и калибровка rule рекомендации RQ4.
8. Snapshot-reproducibility на поддерживаемых runtime/CPU и расхождения между platforms.

До закрытия этих пунктов вывод должен быть: «bounded registry рекомендован как архитектура», а не «конкретный список доказан оптимальным для любых данных».

## 15. Поиск, даты и ограничения покрытия

Канонические запросы протокола выполнены 2026-07-16:

```text
EN-A: "symbolic regression" OR "equation discovery" OR "functional form selection" OR "function family selection" OR "elementary function fitting" OR "interpretable nonlinear regression" OR "analytic function fitting"

EN-B: ("symbolic regression" OR "equation discovery" OR "functional form selection" OR "elementary function fitting" OR "interpretable nonlinear regression") AND (complexity OR parsimony OR interpretability OR identifiability OR "minimum description length" OR validation OR generalization) AND (constraint OR monotone OR domain OR polynomial)

RU-A: "символьная регрессия" OR "поиск уравнения" OR "открытие уравнений" OR "выбор вида функции" OR "выбор формы функции" OR "подбор элементарной функции" OR "интерпретируемая нелинейная регрессия" OR "аналитическая аппроксимация"

RU-B: ("символьная регрессия" OR "поиск уравнения" OR "выбор формы функции" OR "подбор элементарной функции") AND (сложность OR простота OR интерпретируемость OR идентифицируемость OR "минимальная длина описания" OR валидация OR обобщающая) AND (ограничение OR монотонность OR "область определения" OR полином)
```

Дополнительные targeted-запросы: symbolic regression grammar/typed GP; exhaustive/deterministic SR; protected operators/interval arithmetic; monotone/shape-constrained SR; expression equivalence/identifiability; MDL/parsimony; AI Feynman; SRBench; PySR official documentation/version/license; benchmark caveats. Проверялись первичные publisher/proceedings pages, DOI records, arXiv originals, official project documentation и repositories. Known-item metadata уточнялась через Crossref/DBLP/институциональные страницы, но выводы не опирались на вторичный snippet без первичного источника.

Targeted Russian public-web поиск

```text
site:mathnet.ru "символьная регрессия" OR "выбор формы функции" OR "аналитическая аппроксимация"
site:elibrary.ru "символьная регрессия" OR "подбор элементарной функции" OR "выбор вида функции"
```

не дал decision-bearing результатов. Это не равно полноценному внутреннему поиску eLIBRARY и означает слабое русскоязычное покрытие, а не отсутствие русскоязычных работ.

### Отклонения от полного протокола

- не выполнены полные экспортируемые запросы и dedup из Scopus/Web of Science/zbMATH/MathSciNet/eLIBRARY; нет PRISMA counts;
- arXiv/DBLP/Crossref/PubMed/Europe PMC использовались точечно, а не как полные выгрузки;
- gray literature ограничена официальной документацией и repositories; patents, theses и commercial engines систематически не сканировались;
- licenses проверены только для названных current implementations; redistribution прав статей/datasets и transitive dependencies не аудировались юридически;
- из-за требования одного отчета не создавались отдельные машинно-читаемые search log, bibliography и evidence table;
- выводы после cutoff не включались; абсолютная полнота не заявляется.

Главный evidence gap: нет независимого benchmark, который одновременно проверяет noisy univariate data, project registry, exact global monotonicity/domain certificates, polynomial degree ≤3, repeated `x`, одну против двух непрерывных функций и rule `40–60%`. Этот gap должен закрыть проектный prototype.
