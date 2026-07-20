# RQ4 — валидация, uplift и диагностика остатков

**Дата исполнения:** 2026-07-16  
**Дата отсечения:** 2026-07-16 23:59:59, Europe/Moscow  
**Область:** честное сравнение полной процедуры одной монотонной функции с полной процедурой двух непрерывно сопряжённых монотонных функций; одна таблица `x, y`; повторные `x`; максимум два сегмента с долями `40–60%`  
**Статус обзора:** decision-focused rapid-review с независимым аудитом ключевых решений. Достаточен для спецификации prototype, но не является публикационно полным систематическим обзором всех баз из [`00-evidence-review-protocol.md`](../00-evidence-review-protocol.md).

## Решение в одном абзаце

Одну и две функции следует сравнивать как **две полные процедуры выбора**, а не как два уже подобранных уравнения: в каждом внешнем fold заново выполняются preprocessing, выбор направления, семейства, параметров и — для двух функций — границы и пары семейств; все эти действия видят только outer-training `y`. Основные ошибки объединяются по наблюдениям на одинаковых outer splits; primary quality — pooled OOF MSE/RMSE и `R²_OOS`, определённый как улучшение squared error относительно cross-fitted constant baseline. Среднее fold-wise `R²` не использовать: знаменатели различны, а constant/малые folds делают показатель неустойчивым или неопределённым. Финальный refit дополнительно показывает общий и два локальных in-sample `R²`, но они лишь описывают fit, не заменяют OOF-оценку и не складываются в общий `R²`. Две функции рекомендуются только при заранее заданном практически значимом paired uplift, приемлемой неопределённости и устойчивости семейства/границы; численные пороги кроме уже принятого пользовательского warning для global primary `R²_OOS<0.60` литература для этого продукта не задаёт — их должен калибровать prototype и предметное решение.

## 1. Сначала определить estimand

Cross-validation отвечает только на вопрос, закодированный split-схемой. Нельзя назвать любую CV «общей валидацией». Stone изначально разделял cross-validatory **choice** и **assessment** прогнозной процедуры ([1974](https://doi.org/10.1111/j.2517-6161.1974.tb00994.x)); Bates, Hastie и Tibshirani показали, что обычная CV в общем случае оценивает среднюю ошибку алгоритмов, обученных на других выборках уменьшенного размера, а не условную ошибку единственной финальной модели, refit на всей таблице ([2024](https://doi.org/10.1080/01621459.2023.2197686)).

Для проекта нужно различить минимум четыре цели:

| Estimand | Что считается новым | Подходящий split | Роль |
|---|---|---|---|
| `new_x_level` | новый отклик на уровне `x`, которого не было в train | все строки с одинаковым `x` только в одном fold | **primary conservative** для выбора формы кривой |
| `new_repeat_at_known_x` | новое повторное измерение на уже известном `x` | row-wise split допустим, но aggregation только внутри train | secondary; обычно оптимистичнее для формы |
| `missing_x_block` | целый внутренний диапазон `x` отсутствует в train | contiguous blocked-by-`x` folds | stress test интерполяции и локального misspecification |
| `future_or_ordered` | будущее наблюдение при реальном времени/порядке | forward/rolling split, возможно gap | только если порядок имеет предметный смысл |

Сортировка строк по `x` сама по себе не создаёт time series. `TimeSeriesSplit` сохраняет порядок и обучается на прошлых блоках ([scikit-learn 1.9.0](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)); применять его к обычной калибровочной координате без temporal deployment — менять вопрос на экстраполяцию. Напротив, при известных зависимостях random CV может быть оптимистична: Roberts et al. показывают необходимость согласовывать blocking со spatial/temporal/hierarchical structure ([2017](https://doi.org/10.1111/ecog.02881)), а Rabinowicz и Rosset формально показывают, что пригодность обычной CV при correlated data зависит от соответствия train/test correlation structure целевой prediction task ([2022](https://doi.org/10.1080/01621459.2020.1801451)).

**Рекомендуемый primary estimand:** predictive performance полной процедуры на новых уровнях `x` из того же наблюдаемого диапазона и design distribution. Все точные повторы `x` образуют atomic group. Если позже появятся `batch_id`, прибор, образец или временной порядок, group key должен отражать наибольшую единицу зависимости; из двух полей `x,y` это узнать нельзя, поэтому отсутствие metadata фиксируется как ограничение.

## 2. Почему нужна nested validation всей процедуры

Если сначала на всей таблице выбрать одну/две функции, направление, family, breakpoint или outliers, а затем cross-validate только уже выбранную формулу, test folds повлияли на selection и оценка оптимистична. Varma и Simon показали bias, когда одна CV одновременно выбирает модель и оценивает её, и существенное уменьшение bias при nested CV ([2006](https://doi.org/10.1186/1471-2105-7-91)). Cawley и Talbot показали, что можно overfit сам noisy selection criterion, причём ущерб бывает сопоставим с различиями между алгоритмами ([2010](https://www.jmlr.org/papers/v11/cawley10a.html)).

### 2.1 Две сравниваемые процедуры

`P1(train)`:

1. выполняет допустимый preprocessing только по outer-training данным;
2. проверяет оба направления монотонности;
3. во внутреннем CV перебирает весь registry RQ3 и fit-настройки;
4. refit выбранного кандидата на всём outer-training;
5. независимо сертифицирует domain/монотонность RQ1;
6. всегда имеет заранее заданный constant fallback.

`P2(train)` выполняет те же шаги, но внутри каждого inner-training заново:

- строит tie-atomic допустимые `40–60%` splits;
- ищет breakpoint и ordered pair семейств;
- проверяет continuity, единое направление и оба сертификата по RQ2;
- при отсутствии допустимой пары возвращает явный failure и operational fallback `P1`, если именно так будет вести себя продукт.

Outer-test `y` не участвует в selection, early stopping, выборе loss, удалении наблюдений, threshold tuning или выборе лучшего seed. Обе процедуры получают **те же outer splits** и предсказывают те же строки: это обеспечивает paired comparison. Финальная recommendation rule «оставить одну или перейти к двум» также должна быть настроена внутри inner loop или заранее зафиксирована; нельзя подобрать её по outer результатам и затем назвать те же результаты test quality.

### 2.2 Candidate nested scheme

1. Сформировать groups по точному каноническому `x`; не агрегировать `y` до split.
2. Создать несколько заранее зафиксированных outer partitions уникальных groups, распределяя диапазон `x` между folds без использования `y`.
3. В каждом outer-train создать group-disjoint inner partitions тем же принципом.
4. Выполнить `P1` и `P2` полностью и независимо внутри outer-train.
5. Сохранить row-level outer predictions, train-only null prediction, segment membership, failures и все selection outcomes.
6. Внутри каждой repetition объединить additive losses всех её outer folds; затем показать point estimate и разброс по repetitions.
7. После честной оценки refit рекомендованную процедуру на всей таблице; её in-sample показатели не заменяют outer estimate.

`GroupKFold` гарантирует, что одна group не встречается одновременно в train/test ([официальная документация scikit-learn 1.9.0](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupKFold.html)). Exact число folds/repetitions зависит от числа уникальных `x`, segment feasibility и вычислительного бюджета; литература не назначает его для этого design. Prototype должен сравнить bias/variance и failure rate нескольких вариантов. Leave-one-out не является автоматическим решением: близкие training sets дают коррелированные ошибки и часто высокую variance оценки test error; документация scikit-learn рекомендует предпочитать 5/10-fold в типичном i.i.d. случае, но проектный grouped small-sample режим требует собственной калибровки ([CV guide 1.9.0](https://scikit-learn.org/stable/modules/cross_validation.html)).

### 2.3 Ordered и blocked sensitivity

Primary grouped-random/stratified-by-`x` split проверяет новые уровни из того же диапазона. Дополнительно нужно выполнить:

- contiguous interior `x` blocks — может ли формула восстановить пропущенный участок;
- крайние blocks — явно маркированный extrapolation stress, не primary quality внутри полного `[L,U]`;
- batch/time blocks — только при наличии соответствующего metadata.

Если conclusions одной против двух функций меняются между reasonable split-estimands, результат `SPLIT_SENSITIVE`, а не выбор удобной схемы.

## 3. Определения метрик без скрытой смены знаменателя

Пусть в одной outer repetition для каждой строки есть OOF prediction `ŷ_i^(m)` модели `m∈{1,2}` и baseline prediction `ŷ_i^(0)`, равная weighted/unweighted mean только outer-training `y` соответствующего fold. Все суммы ниже считаются по одним и тем же test appearances.

### 3.1 Additive losses

```text
SSE_m  = Σ_i (y_i - ŷ_i^(m))²
SAE_m  = Σ_i |y_i - ŷ_i^(m)|
MSE_m  = SSE_m / n_test
RMSE_m = sqrt(MSE_m)
MAE_m  = SAE_m / n_test
```

Сначала объединяются squared/absolute errors, затем берутся mean/root. Среднее fold RMSE не равно pooled RMSE при разных fold errors/sizes. MSE/RMSE сильнее наказывают крупные ошибки и согласованы с оцениванием conditional mean при squared loss; absolute loss соответствует conditional median. Выбор scoring function должен соответствовать тому, что означает калибровочная кривая, а не делаться после просмотра результата ([Gneiting, 2011](https://doi.org/10.1198/jasa.2011.r10138)). Для проекта mean-curve и `R²` естественно делают MSE/RMSE primary, MAE — обязательной sensitivity к крупным остаткам. Если fit использует robust loss, нужно отдельно показать метрики той objective и обычные RMSE/MAE.

### 3.2 Primary out-of-sample `R²`

```text
R²_OOS,m = 1 - SSE_m / SSE_0,
SSE_0    = Σ_i (y_i - ŷ_i^(0))².
```

Это comparison двух deployable predictive procedures: положительное значение означает меньший pooled squared error, чем train-only constant baseline; отрицательное — baseline прогнозирует лучше. Hawinkel, Waegeman и Maere формализуют out-of-sample `R²` именно как отношение prediction loss модели и null model и показывают преимущество pooling перед усреднением fold-wise `R²` в их simulation framework ([2024](https://doi.org/10.1080/00031305.2023.2216252)). Их конкретная variance formula предполагает свой i.i.d./sampling setup; group-nested extension проекта требует проверки coverage.

Допустимо вторично показать распространённый

`Q²_global = 1 - SSE_OOF / Σ_i(y_i-ȳ_all)²`,

но только с другим именем: он использует другой null denominator. Нельзя смешивать его с primary cross-fitted-null `R²_OOS` или in-sample `R²`.

### 3.3 Почему не среднее fold-wise `R²`

Вычисление `R²` отдельно в каждом fold использует разные test means и variances; fold с малой variance может дать огромное отрицательное число и непропорционально изменить среднее. При constant `y_true` обычный denominator равен нулю: математический результат `NaN` для perfect prediction и `-Inf` иначе; scikit-learn по умолчанию маскирует их в `1/0` через `force_finite=True` ([`r2_score`, 1.9.0](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.r2_score.html)). Для аудита нужно `force_finite=False`/собственная формула и явный `UNDEFINED_R2`, а не silent replacement.

`R²` может быть отрицательным и не является квадратом корреляции вне специального OLS-with-intercept случая. Для nonlinear/constrained/weighted/robust fits альтернативные definitions неэквивалентны ([Kvålseth, 1985](https://doi.org/10.1080/00031305.1985.10479448)). Если вся `y` постоянна или `SSE_0=0`, `R²_OOS` не определён; сообщаются RMSE/MAE и факт constant outcome. Значение `0.60` означает 60% reduction **squared loss относительно указанного null** только при этой формуле — не «60% точек описано».

Официальная документация предупреждает, что `cross_val_predict` нельзя бездумно превращать в generalization score, поскольку predictions приходят от разных models, а fold averaging и pooling отличаются ([scikit-learn CV guide](https://scikit-learn.org/stable/modules/cross_validation.html)). В проекте OOF predictions сохраняются для диагностики, а primary point estimate строится намеренно из decomposable pooled losses complete outer procedure, отдельно по каждой repetition.

## 4. Общий и сегментные `R²`

После финального refit на всей таблице пользовательское требование закрывают три **in-sample descriptive** показателя:

```text
R²_fit,all = 1 - Σ_all e_i² / Σ_all (y_i-ȳ_all)²

R²_fit,s   = 1 - Σ_{i∈s} e_i² / Σ_{i∈s} (y_i-ȳ_s)²,
s ∈ {left,right}.
```

Правила:

- граница и tie-membership берутся из final refit;
- у каждого segment свой mean/denominator;
- если segment имеет меньше двух observations или constant `y`, его `R²` — `NA`, рядом показываются `n`, unique `x`, RMSE и MAE;
- segment `R²` может быть отрицательным;
- нельзя усреднить два local `R²` и получить общий: total variation содержит within-segment и between-segment components;
- высокий общий `R²` может возникнуть из-за большого различия уровней между интервалами при слабом local fit; поэтому локальные metrics нужны, но малый within-segment variance делает их нестабильными;
- OOF segment metrics используют segment membership, определённый **outer-trained** breakpoint для каждой test prediction. Применить final breakpoint ко всем OOF rows — leakage.

OOF local `R²` — secondary: при varying breakpoints он описывает условную работу процедуры, а не фиксированные финальные интервалы; некоторые folds/repetitions могут не иметь достаточной local variance. Primary recommendation опирается на overall paired losses.

Для unweighted least-squares fit, где constant family действительно включена и найден global optimum той же SSE, отрицательный training `R²_fit,all` указывает на solver failure или mismatch objective/weights. При robust/weighted fitting unweighted `R²` может быть отрицательным без программной ошибки; labels должны явно различать weighted/unweighted metrics.

## 5. Uplift одной против двух функций

На одинаковых outer predictions определить:

```text
ΔMSE  = MSE_1 - MSE_2
ΔRMSE = RMSE_1 - RMSE_2
ΔMAE  = MAE_1 - MAE_2
ΔR²   = R²_OOS,2 - R²_OOS,1 = (SSE_1-SSE_2)/SSE_0
rel_MSE_uplift = 1 - MSE_2/MSE_1,  если MSE_1>0.
```

Положительные значения означают преимущество двух функций. Primary comparison следует делать на loss scale (`ΔMSE` либо paired squared-error difference), потому что `ΔR²` наследует случайный null denominator. Обязательно показывать абсолютный uplift в units `y` через `ΔRMSE/ΔMAE` и relative uplift; одно процентное число скрывает исходный error scale.

### 5.1 Paired uncertainty

Каждая test row даёт paired loss difference

`d_i = loss(y_i, ŷ_i^(1)) - loss(y_i, ŷ_i^(2))`.

Но folds и repetitions не независимы: training sets перекрываются. Обычный `t`-test по fold scores или `sd(folds)/sqrt(K)` занижает uncertainty. Bengio и Grandvalet доказали отсутствие универсального unbiased estimator variance K-fold CV и объяснили роль коррелированных errors ([2004](https://www.jmlr.org/papers/v5/grandvalet04a.html)). Bates et al. предложили nested-CV variance scheme и показали плохое coverage обычных CV intervals в рассмотренных задачах ([2024](https://doi.org/10.1080/01621459.2023.2197686)); Bayle et al. дали asymptotic CV intervals при algorithmic stability ([2020](https://proceedings.neurips.cc/paper/2020/hash/bce9abf229ffd7e570818476ee5d7dde-Abstract.html)). Breakpoint/family selection может быть именно нестабильной, поэтому условия нельзя считать выполненными автоматически.

Candidate protocol:

1. report paired point estimates на одних outer splits;
2. показать distribution по заранее заданным outer repetitions как **split sensitivity**, не называть percentiles confidence interval;
3. реализовать bootstrap **независимых `x`/dependency groups**, в каждом resample заново выполняя обе nested pipelines целиком, либо Bates-style nested variance как prototype alternative; row-level bootstrap при зависимых повторах запрещён;
4. simulation-calibrate coverage на synthetic registry/breakpoint cases до публикации «95% CI»;
5. при отдельном untouched test set использовать paired group bootstrap test losses, явно отделив uncertainty фиксированных fitted models от variability model-development procedure.

Corrected resampled tests Nadeau–Bengio учитывают overlap лучше naive test ([2003](https://doi.org/10.1023/A%3A1024068626366)), но correction и target зависят от resampling design; это sensitivity method, не универсальная гарантия для grouped nested breakpoint search. Diebold–Mariano допускает serially correlated forecast losses ([1995](https://doi.org/10.1080/07350015.1995.10524599)), но относится к forecast sequence, а не автоматически к repeated grouped CV.

### 5.2 Практическая и статистическая значимость

`δ_practical` должен быть заранее задан в понятных единицах ошибки: допустимое уменьшение RMSE/MAE, стоимости или tolerance калибровки. ASA подчёркивает, что statistical significance не измеряет размер/важность эффекта ([Wasserstein & Lazar, 2016](https://doi.org/10.1080/00031305.2016.1154108)); equivalence/noninferiority подходы требуют заранее задать smallest effect size of interest ([Lakens, 2017](https://doi.org/10.1177/1948550617697177)). Эти источники не дают project-specific числа.

Консервативный candidate gate для prototype асимметричен: нижняя граница проверенного paired interval для primary squared-loss uplift должна превышать `δ_practical`, а MAE должна пройти отдельно заданное no-material-harm условие. Это сильнее требования «point estimate положителен» и может часто давать `inconclusive` при малой выборке; численные `δ` и coverage метода всё равно требуют калибровки.

Decision states вместо одного p-value:

- `CLEAR_PRACTICAL_UPLIFT`: эффект превышает predeclared `δ` и uncertainty совместима с устойчивым преимуществом;
- `STATISTICAL_ONLY_SMALL`: различие различимо, но меньше practically relevant `δ`;
- `PRACTICALLY_PROMISING_UNCERTAIN`: point uplift велик, но data недостаточны;
- `NO_UPLIFT_OR_HARM`: нулевой/отрицательный paired uplift;
- `UNSTABLE_SELECTION`: средний uplift есть, но family/breakpoint/failure behavior нестабилен.

Точные границы этих состояний — decision/prototype ticket. До их утверждения default recommendation остаётся одной функцией, а двухфункциональный результат показывается как alternative evidence, если он валиден.

## 6. Как учитывать сложность

Nested outer evaluation уже включает optimism от внутреннего поиска family, direction и breakpoint, **если весь поиск действительно повторён внутри**. Простого штрафа «число коэффициентов» недостаточно: model search добавляет degrees of freedom сверх размера выбранной модели даже в более простых settings ([Tibshirani, 2015](https://doi.org/10.5705/ss.2014.147)).

AIC и BIC имеют ясные исходные цели при likelihood/regularity assumptions ([Akaike, 1974](https://doi.org/10.1109/TAC.1974.1100705); [Schwarz, 1978](https://doi.org/10.1214/aos/1176344136)). Но в этом проекте unknown breakpoint отсутствует под null, constraints активны, families выбираются дискретно, а nonlinear fits могут быть неидентифицируемы. Поэтому:

- AIC/AICc/BIC допустимы только secondary для моделей с одной и той же явной likelihood и документированным parameter count;
- adjusted training `R²` не корректирует family/breakpoint search и не выбирает production pipeline;
- нельзя сравнивать information criteria, посчитанные по разным losses/weights;
- primary complexity control — bounded registry + one-breakpoint scope + honest outer loss + practical uplift/stability gate;
- при practically equivalent quality выбирается одна функция как заранее заданный simplicity preference.

Shao показал, что CV-схемы могут вести себя различно для predictive ability и consistent identification true linear model ([1993](https://doi.org/10.1080/01621459.1993.10476299)). Цель продукта — предсказательная и описательная полезность ограниченного registry, не доказательство «истинной физической семьи»; family label при near-equivalence следует сопровождать uncertainty/stability.

## 7. Устойчивость формы, направления и границы

Для каждой outer repetition/fold сохраняются:

- выбрана одна функция или valid pair/fallback;
- `family_id` P1 и ordered pair P2;
- направление;
- breakpoint в raw `x`, normalized position и percentile/point fraction;
- objective gap до второго кандидата;
- boundary hits, near-optima, certificate/optimizer failures;
- segment counts, unique `x` и spans;
- collapse pair→one и change относительно leave-group-out fits.

Отчёт показывает selection frequencies, а для breakpoint — distribution/interval, multimodality и долю попаданий на `40/60` bounds. Не следует усреднять breakpoints от разных family/direction regimes без stratification. Breiman показал, что небольшие изменения данных могут сильно менять selected model и что instability является самостоятельным свойством selection procedure ([1996](https://doi.org/10.1214/aos/1032181158)). Riley и Collins демонстрируют практический bootstrap-подход: повторить **все** model-building steps и сравнивать resulting predictions/structures, а не только фиксированные coefficients ([2023](https://doi.org/10.1002/bimj.202200302)); их clinical setting переносится как stability design, не как численный threshold. Numeric minimum selection-frequency или maximum breakpoint spread литература для данного registry не задаёт; prototype калибрует их на known-truth и perturbation cases.

## 8. Остатки: OOF для качества, refit для объяснения

Нужно хранить два разных residual set:

- `e_i^OOF = y_i-ŷ_i^OOF` — primary predictive diagnostic; prediction получен без outer-test `y`;
- `e_i^fit = y_i-ŷ_i^final` — descriptive diagnostic финальной формулы, оптимистичен после selection.

OOF plots строятся отдельно для P1/P2 и включают:

1. residual vs `x` с zero line и diagnostic smooth;
2. residual vs predicted;
3. `|residual|`/squared residual vs `x` и predicted для variance pattern;
4. residual distributions по outer-trained segment membership и отдельно около breakpoint;
5. group summaries для repeated `x`: count, mean/median residual, within-group scale;
6. acquisition-order/autocorrelation plot только при реальном order metadata;
7. histogram/QQ как проверку noise assumption, не как общий quality score.

Smooth в residual plot лишь локализует systematic pattern; он не становится новой скрытой моделью. Любое решение изменить registry/loss/weights после просмотра outer residuals означает, что outer results стали development data; после изменения нужен новый untouched assessment либо новый nested run с зафиксированным решением.

### 8.1 Повторы `x`: pure error и lack of fit

Поскольку одна функция даёт одинаковый прогноз всем строкам с одинаковым `x_g`, для final refit выполняется точное разложение:

```text
SSE_total = SSE_pure_error + SSE_lack_of_fit
SSE_pure_error = Σ_g Σ_j (y_gj-ȳ_g)²
SSE_lack_of_fit = Σ_g n_g (ȳ_g-f(x_g))².
```

NIST описывает replicate-based pure-error estimator и lack-of-fit comparison и подчёркивает, что model-independent variance требует повторных измерений ([e-Handbook, раздел 4.4.4.6](https://www.itl.nist.gov/div898/handbook/pmd/section4/pmd446.htm)). Разложение нужно показывать описательно. Classical F-test требует iid homoscedastic Gaussian errors, fixed preselected functional form и корректные degrees of freedom; после registry/breakpoint selection его naive p-value не является post-selection inference. При отсутствии повторов pure-error component не оценивается.

### 8.2 Studentization и robust scale

Для fixed linear OLS internal/external studentized residuals используют residual variance и leverage. После nonlinear constrained family/breakpoint selection обычные leverage formulas и reference t-distribution не переносятся автоматически. Поэтому project-wide flag должен использовать clearly labeled robust standardized OOF residual:

`z_i^robust = e_i^OOF / s_robust`,

где `s_robust` — заранее выбранный MAD/Qn-like scale, возможно отдельный по cross-fitted variance regime. MAD имеет высокий breakdown, но может быть нулевым/неэффективным в отдельных distributions; альтернативные robust scales анализируют Rousseeuw и Croux ([1993](https://doi.org/10.1080/01621459.1993.10476408)). Numeric `|z|` threshold нельзя брать из normal 2/3-sigma folklore без multiple-testing/noise calibration. В отчет входят raw residual, scale method и reason for flag.

### 8.3 Heteroscedasticity

Breusch–Pagan ([1979](https://doi.org/10.2307/1911963)), его studentized modification Koenker, исправляющая size вне строго Gaussian errors ([1981](https://doi.org/10.1016/0304-4076%2881%2990062-2)), и White ([1980](https://doi.org/10.2307/1912934)) дают formal tests/robust covariance для linear-regression assumptions. Они полезны как reference, но p-values после nonlinear model/breakpoint selection не следует объявлять подтверждением/опровержением heteroscedasticity. Primary flags — OOF absolute-residual patterns и replicate scales. Если variance меняется с `x`, prototype сравнивает:

- неизменённый unweighted fit;
- заранее заданную variance model/weights, estimated только внутри folds;
- robust loss sensitivity.

Weights, derived на всей таблице до CV, создают leakage. Изменение loss меняет estimand; все метрики и null baseline нужно пересчитать тем же nested protocol.

## 9. Выбросы и влияние — без автоматического удаления

Большой residual и большое influence — разные свойства. Cook's distance измеряет изменение fixed OLS fit при удалении observation ([Cook, 1977](https://doi.org/10.1080/00401706.1977.10489493)); его textbook cutoff нельзя напрямую переносить на selected nonlinear segmented pipeline.

Общая project-диагностика — `leave-one-x-group-out` refit **всей** процедуры на полной development table:

- изменение predictions по общей evaluation grid: max absolute и integrated squared difference;
- изменение one/two recommendation и family/direction;
- изменение breakpoint и segment balance;
- изменение validated/error proxy;
- переход certificate/failure status.

Для very small data это дорого, но реестр конечен. Строка/group подсвечивается, если residual/robust scale или refit influence велики по predeclared prototype rule. Она никогда не удаляется автоматически. Отчёт показывает исходный результат, sensitivity without group и причину; исключение требует внешней причины (ошибка измерения/данных) и нового полного validation run. Анализ только «после удаления неудобной точки» создаёт selection bias.

## 10. Candidate recommendation protocol

1. **Feasibility:** P1 имеет valid fallback; P2 должен пройти tie-balance, continuity, domain и monotonic certificates. Failure folds не выбрасываются.
2. **Honest quality:** на paired repeated grouped nested splits посчитать pooled OOF RMSE, MAE и explicit-null `R²_OOS`; сохранить negative/undefined cases.
3. **Uplift:** посчитать paired `ΔMSE`, `ΔRMSE`, `ΔMAE`, `ΔR²`, relative MSE; не выбирать метрику post hoc.
4. **Uncertainty:** report split sensitivity; только проверенный prototype-method может называться CI. Не считать folds независимыми.
5. **Practical gate:** candidate rule требует lower bound primary paired uplift выше заранее заданного domain `δ`, плюс отдельный MAE no-material-harm gate; statistical evidence без размера эффекта не заменяет их.
6. **Stability gate:** проверить frequencies family/direction, breakpoint distribution, boundary/fallback/failure/collapse rates.
7. **Residual gate:** сравнить OOF patterns P1/P2, heteroscedasticity, breakpoint-local bias, repeated-`x` pure error и influence.
8. **Recommendation:** P2 рекомендуется лишь при valid, practically meaningful и sufficiently stable uplift; иначе P1. При uncertainty — P1 основной, P2 показывается как alternative/inconclusive.
9. **Final refit/report:** refit выбранные P1 и, если обоснован, P2 на всех данных; показать OOF metrics отдельно от final overall/local in-sample metrics.
10. **Product warning:** если **глобальный primary `R²_OOS`** рекомендованной процедуры `<0.60`, обязательно предупредить пользователя, но не объявлять модель автоматически invalid и не скрывать RMSE/MAE/uncertainty. Local segment и in-sample `R²` остаются диагностическими и не запускают этот product threshold сами по себе.

## 11. Failure states

| Код | Условие | Результат |
|---|---|---|
| `UNDEFINED_R2` | zero null SSE/constant target или local denominator | `R²=NA`; report RMSE/MAE |
| `NEGATIVE_OOS_R2` | model worse than cross-fitted null | явное предупреждение; не clamp to zero |
| `BELOW_PRODUCT_R2` | global primary `R²_OOS<0.60` | обязательное product warning; threshold не научная универсалия |
| `NO_VALID_TWO_SEGMENT` | нет valid P2 в outer train/full refit | P1 fallback; failure входит в stability |
| `NO_PRACTICAL_UPLIFT` | paired effect не проходит утверждённый `δ` | рекомендовать P1 |
| `UPLIFT_UNCERTAIN` | interval/sensitivity совместимы с пользой и вредом | P1 primary, P2 inconclusive |
| `SPLIT_SENSITIVE` | решение меняется между justified estimands/splits | предупреждение, требуется больше данных/решение deployment |
| `SELECTION_UNSTABLE` | family/direction/breakpoint/fallback сильно меняются | не рекомендовать P2 без обоснования |
| `SYSTEMATIC_OOF_RESIDUAL` | заметный pattern vs x/fitted/segment | model-misspecification warning; не auto-expand registry |
| `HETEROSCEDASTIC_PATTERN` | scale зависит от x/fitted | sensitivity weights/robust loss внутри nested CV |
| `INFLUENTIAL_GROUP` | leave-group-out materially меняет результат | подсветить; не удалять автоматически |
| `INSUFFICIENT_UNIQUE_X` | group nested validation/segment metrics неидентифицируемы | report limitation; не выдавать точный uplift |

## 12. Матрица ключевых утверждений

| Claim ID | Вывод | Главная опора | Применимость |
|---|---|---|---|
| `C-RQ4-0001` | Selection и assessment должны быть разделены; весь family/breakpoint search выполняется внутри outer train | Stone; Varma–Simon; Cawley–Talbot | `direct` |
| `C-RQ4-0002` | Split должен соответствовать estimand/dependence; exact repeated x группируются для new-level оценки | Roberts et al.; Rabinowicz–Rosset; official GroupKFold | `direct` как conservative design; row-wise — другой estimand |
| `C-RQ4-0003` | CV оценивает performance алгоритма на hypothetical training sets, не точную conditional error final refit | Bates et al. | `direct` |
| `C-RQ4-0004` | OOS `R²` — explicit model-vs-null loss comparison; pooling предпочтительнее fold-wise averaging | Hawinkel et al.; Kvålseth; official `r2_score` semantics | `direct`, variance extension `partial` для grouped nested CV |
| `C-RQ4-0005` | Negative/undefined `R²` содержательны и не должны silently заменяться | official scikit-learn; algebra | `direct` |
| `C-RQ4-0006` | Overall и local `R²` имеют разные denominators; local values не агрегируются в overall | sum-of-squares decomposition; project algebra | `direct` |
| `C-RQ4-0007` | RMSE/MSE и MAE соответствуют разным losses/functionals | Gneiting | `direct` |
| `C-RQ4-0008` | Folds/repeats зависимы; naive SE/t-test невалиден универсально | Bengio–Grandvalet; Bates et al.; Bayle et al. | `direct`; конкретный CI pending prototype |
| `C-RQ4-0009` | Практический uplift threshold должен быть domain-defined, отдельно от significance | ASA statement; Lakens | `direct` как decision principle |
| `C-RQ4-0010` | Parameter count/AIC/BIC/adjusted R² не заменяют honest assessment полного search | Akaike; Schwarz; Tibshirani; RQ2 nonregularity | `direct` как caveat |
| `C-RQ4-0011` | Selection stability — отдельное evidence, особенно для breakpoint/family; bootstrap повторяет весь build | Breiman; Riley–Collins; RQ2 | `direct` как design, thresholds pending |
| `C-RQ4-0012` | Replicates позволяют разложить residual SSE на pure error/lack of fit | NIST; algebra | `direct`; formal F-test `partial` after selection |
| `C-RQ4-0013` | Classical studentization/Cook/BP/Koenker/White assumptions не переносятся автоматически на selected nonlinear pipeline | Cook; Breusch–Pagan; Koenker; White | `direct` как limitation |
| `C-RQ4-0014` | Outlier/influence flags ведут к sensitivity review, не automatic deletion | influence framework; selection-bias logic | `direct` |

## 13. Что обязан решить prototype

Литература не определяет следующие project numbers:

1. число outer/inner folds и repetitions при разных `n_unique_x`;
2. practically meaningful `δ_RMSE`, `δ_MAE` или relative MSE uplift;
3. нужен ли lower uncertainty bound выше `0` или выше `δ` для recommendation;
4. допустимые family/direction selection frequencies, breakpoint spread, boundary/fallback rates;
5. residual/influence/heteroscedastic flag thresholds и robust scale method;
6. minimum local `n`, unique `x`, span/variance для segment metrics;
7. CI method и empirical coverage в grouped nested, constrained, nonregular breakpoint regime;
8. primary deployment estimand, если реальная задача — known-`x` repeats, new `x`, missing range или future order;
9. operational P2 failure policy и то, оценивается ли «attempt P2 + fallback P1» или только условно valid pair;
10. metric weighting при unequal replicate counts/measurement precision.

Simulation matrix должна включать истинную одну/две функции, no-change boundary, near-equivalent families, weak/strong uplift, repeated `x`, heteroscedasticity, outliers, influential endpoint, small local variance, flat/constant `y`, multiple near-optimal breakpoints и solver failures. Для каждого candidate protocol измеряются bias, recommendation error, interval coverage, failure handling и stability — после этого numeric rules фиксируются до real-data use.

## 14. Независимый аудит

Первая попытка запустить background reviewer была отклонена orchestration с `agent thread limit reached`. До финализации parent orchestrator предоставил отдельный independent audit decision-bearing выводов; замечания проверены по первичным источникам и интегрированы:

- добавлены Rabinowicz–Rosset для correlated/grouped estimand и Riley–Collins для полного bootstrap stability build;
- уточнено, что uncertainty bootstrap resamples independent groups и повторяет обе nested pipelines, а не только пересчитывает готовые residuals;
- candidate recommendation gate сделан асимметричным: lower bound выше practical delta плюс MAE no-harm и stability;
- `0.60` закреплён только как warning для global primary OOF `R²`, не universal adequacy rule и не local-segment cutoff;
- Cook residual/influence distinction и ограничения BP/Koenker/White после selection сделаны явными.

Аудит не дал project-specific чисел и подтвердил необходимость simulation coverage/stability calibration. Независимый reviewer не редактировал файл; ответственность за синтез и ссылки остаётся у исполнителя обзора.

## 15. Поиск, даты и ограничения покрытия

Канонические запросы протокола выполнены 2026-07-16:

```text
EN-A: "out-of-sample R-squared" OR "predictive R-squared" OR "coefficient of determination" OR "nested cross-validation" OR "blocked cross-validation" OR "ordered cross-validation" OR "model selection bias" OR "residual diagnostics" OR "influence diagnostics" OR "prediction stability" OR "breakpoint uncertainty"

EN-B: ("model comparison" OR "model selection" OR validation OR "cross-validation") AND (regression OR "curve fitting" OR "segmented regression" OR changepoint) AND ("R-squared" OR RMSE OR MAE OR uplift OR residual OR influence OR uncertainty OR stability OR complexity)

RU-A: "проверочный R-квадрат" OR "прогнозный R-квадрат" OR "коэффициент детерминации" OR "вложенная кросс-валидация" OR "блочная кросс-валидация" OR "смещение выбора модели" OR "диагностика остатков" OR "диагностика влияния" OR "устойчивость прогноза" OR "неопределенность точки изменения"

RU-B: ("сравнение моделей" OR "выбор модели" OR валидация OR "кросс-валидация") AND (регрессия OR "подбор кривой" OR "сегментированная регрессия" OR "точка изменения") AND ("R-квадрат" OR RMSE OR MAE OR улучшение OR остатки OR влияние OR неопределенность OR устойчивость OR сложность)
```

Targeted expansion: CV estimands/nested selection bias; grouped/repeated/ordered/blocked/correlated CV; OOS and pooled-vs-fold `R²`; constant/negative `R²`; PRESS; paired CV uncertainty; practical significance/equivalence; AIC/BIC/search degrees of freedom; full-build stability; OOF/PRESS residuals; pure error/lack of fit; robust scale/studentization; heteroscedasticity including Koenker; influence/Cook; official scikit-learn semantics. Проверялись publisher/DOI pages, JMLR, NeurIPS/PMLR, PubMed/PMC, NIST e-Handbook и official scikit-learn 1.9.0 documentation.

Русскоязычные broad queries не дали отдельного primary decision-bearing корпуса; найденные термины использовались для bilingual coverage, но основные основания — международные первичные статистические работы. Полный внутренний поиск eLIBRARY/Math-Net не выполнен, поэтому русскоязычное покрытие слабое.

### Отклонения от полного протокола

- не выполнены полные выгрузки/PRISMA counts/dedup из Scopus, Web of Science, zbMATH, MathSciNet и eLIBRARY;
- Crossref, DBLP, PubMed/Europe PMC и citation chasing использовались targeted, не как исчерпывающие API exports;
- не созданы отдельные `runs.csv`, raw exports и evidence-matrix, поскольку ticket разрешает ровно один topical report;
- самостоятельный child-audit сначала был заблокирован orchestration limit, но компенсирован независимым parent-orchestrated review до финализации;
- grouped nested CI coverage и numeric thresholds не выводились из несовместимых benchmarks;
- implementation/license audit ограничен scikit-learn semantics; production library здесь не выбирается;
- absolute completeness не заявляется, работы после cutoff не включались.

Главный evidence gap: нет прямого независимого исследования, которое одновременно охватывает finite elementary registry, analytic monotonicity/domain certificates, unknown continuous breakpoint, exact `40–60%` tie rule, repeated `x`, grouped nested CV, one-vs-two recommendation и OOF influence diagnostics. Поэтому структура протокола обоснована общими результатами, а его numeric operating characteristics обязан доказать project prototype.
