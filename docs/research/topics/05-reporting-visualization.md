# RQ5 — стандарты отчётности и визуализации калибровочной кривой

**Дата исполнения:** 2026-07-16  
**Дата отсечения:** 2026-07-16 23:59:59, Europe/Moscow  
**Область:** прозрачный человеко- и машиночитаемый отчёт сравнения одной монотонной функции и одной непрерывной двухсегментной монотонной модели для таблицы `x, y`  
**Статус обзора:** decision-focused rapid review с независимым аудитом ключевых решений. Он достаточен для контракта prototype, но не закрывает полный systematic-review gate протокола [`00-evidence-review-protocol.md`](../00-evidence-review-protocol.md).

## Решение в одном абзаце

Основной отчёт должен показывать **две отдельные сопоставимые панели**: одни и те же исходные точки и оси, поверх которых в первой панели проведена одна функция, а во второй — две ветви только внутри собственных интервалов. Точка сопряжения показывается красной вертикальной линией, но цвет дублируется штрихом, меткой `граница c=…` и числом в таблице. Общая OOF-оценка и uplift отделяются от описательных in-sample показателей финального refit; product warning срабатывает только по неокруглённому global primary `R²_OOS<0.60`. Неопределённость рисуется лишь при явном указании estimand, уровня, метода и того, повторял ли resampling весь поиск модели. Отчёт сохраняет формулы, домены, диагностику OOF-остатков, проблемные наблюдения, stability/failure states и provenance. Вместе с доступным HTML/SVG или эквивалентным статическим документом поставляются versioned JSON и row-level CSV; изображение не является единственным носителем чисел или смысла.

## 1. Что здесь называется «калибровкой»

В этом проекте **калибровочная кривая** — детерминированная регрессионная зависимость `y=f(x)`, подобранная по парным наблюдениям. Её основной график — scatterplot `y` против `x` с наложенной аппроксимацией.

Это не то же самое, что **probability calibration plot / reliability diagram**. Последний сопоставляет предсказанную вероятность с наблюдаемой частотой события; например, в официальной документации scikit-learn 1.9.0 по оси `x` идёт средняя предсказанная вероятность в bin, а по `y` — доля положительных исходов ([Probability calibration](https://scikit-learn.org/stable/modules/calibration.html)). В первичном исследовании Niculescu-Mizil и Caruana такие графики использовались для оценки качества вероятностей классификаторов ([2005](https://doi.org/10.1145/1102351.1102430)).

Следствия для терминологии отчёта:

- заголовок графика: `Аппроксимация калибровочной зависимости y=f(x)`, а не просто `calibration plot`;
- оси подписываются фактическими именами и единицами `x`, `y`, а не `predicted probability / observed frequency`;
- диагональ `y=x`, probability bins, Brier score и доля положительных исходов не добавляются, если проект позже отдельно не решает задачу вероятностной калибровки;
- metadata получает `report_type: "curve_calibration_regression"`, чтобы машинный потребитель не спутал два смысла.

## 2. Почему одних метрик недостаточно

Anscombe построил четыре набора с почти одинаковыми стандартными сводками и линейными регрессиями, но с принципиально различными нелинейностью и влияющими точками; это прямое основание показывать наблюдения вместе с fit, а не выдавать одну формулу и `R²` ([1973](https://doi.org/10.1080/00031305.1973.10478966)). NIST также требует сначала наложить predicted values на исходные данные, а затем проверять остатки; высокий `R²` сам по себе не гарантирует адекватную модель ([model validation](https://www.itl.nist.gov/div898/handbook/pmd/section4/pmd44.htm), [predicted values with original data](https://www.itl.nist.gov/div898/handbook/eda/section4/eda4234.htm)).

Систематическая проверка 703 статей Weissgerber et al. показала, что агрегированные bar/line displays могут скрывать распределение данных; авторы рекомендуют показывать individual observations для малых выборок ([2015](https://doi.org/10.1371/journal.pbio.1002128)). Их более поздняя работа расширяет принцип `reveal, don't conceal` на прозрачную подачу точек, размера выборки и распределения ([2019](https://doi.org/10.1161/CIRCULATIONAHA.118.037777)). Перенос на регрессионный scatterplot `direct` по смыслу показа исходных пар, хотя их эмпирический corpus в основном не о segmented regression.

Эксперименты Cleveland и McGill показали, что точность чтения зависит от perceptual encoding и особенно высока для положения на общей шкале ([1984](https://doi.org/10.1080/01621459.1984.10478080)). Correll, Bertini и Franconeri обнаружили, что усечение оси меняет воспринимаемый размер эффекта даже при визуальных подсказках ([2020](https://doi.org/10.1145/3313831.3376222)). Поэтому сравниваемые панели обязаны иметь общие пределы, шкалы и aspect ratio; отдельный autoscale каждой модели недопустим.

## 3. Обязательная структура human-readable отчёта

Порядок фиксируется, чтобы не спрятать отрицательный результат в приложении.

1. **Итог решения:** `ONE_RECOMMENDED`, `TWO_RECOMMENDED`, `INCONCLUSIVE` или `NO_VALID_MODEL`; основная причина; product warning; дата и идентификатор запуска.
2. **Данные:** источник, единицы, наблюдаемый домен, `n`, число уникальных `x`, повторы, missing/invalid rows и все преобразования.
3. **Сравнение процедур:** OOF `R²`, RMSE, MAE, paired uplift, неопределённость, stability и failures для одной и двух функций.
4. **Две сопоставимые панели:** одна функция и две ветви; если P2 неосуществима, вторая панель сохраняет исходные точки и явно показывает failure вместо выдуманной линии.
5. **Описание финального refit:** формулы, коэффициенты, домены, направление, граница, segment shares, общий и локальные in-sample metrics.
6. **Диагностика:** OOF-остатки, repeated-`x`/lack-of-fit сведения и таблица проблемных наблюдений; influence финального refit отдельно.
7. **Неопределённость и устойчивость:** interval target/method, distribution границы и частоты выбора форм.
8. **Воспроизводимость:** validation design, registry, solver, tolerances, seeds, версии ПО, hashes и ссылки на machine-readable artifacts.

TRIPOD разрабатывался для клинических multivariable prediction models, а не для этой калибровочной кривой, поэтому его применимость `context_only/partial`. Тем не менее его требования полно сообщать model specification, validation и performance с uncertainty поддерживают такую структуру ([TRIPOD, 2015](https://doi.org/10.1136/bmj.g7594)).

## 4. Контракт двух основных scatterplots

### 4.1 Общая геометрия

Обе панели должны использовать один и тот же immutable point layer и одновременно удовлетворять условиям:

- одинаковые `xlim`, `ylim`, linear/log transformations, aspect ratio, размеры plotting area, tick positions и единицы;
- полный наблюдаемый диапазон `x`; линия не продолжается за `[L,U]`, если экстраполяция отдельно не утверждена;
- один и тот же порядок строк, фильтры, point size, opacity и способ обработки повторов `x`;
- raw points рисуются выше uncertainty fill и остаются различимыми; число показанных точек равно числу строк, прошедших задокументированные input rules;
- подпись содержит `n`, `n_unique_x`, направление монотонности и статус модели;
- zoom допустим только как дополнительный inset/third view с явно указанными пределами, а не как различающийся масштаб двух основных панелей.

Ondov et al. экспериментально сравнили overlay, стандартные и mirror-symmetric small multiples и animation: эффективность зависит от comparison task, а overlay часто силён для низкоуровневого сопоставления ([2018](https://doi.org/10.1109/TVCG.2018.2864884)). Для этого продукта primary design всё же две juxtaposed панели: overlay трёх линий и band может скрыть исходные точки и интервальную принадлежность. Дополнительный overlay разрешён как secondary view при тех же осях и прямых labels, но не заменяет две панели.

### 4.2 Панель одной функции

Показываются:

- все принятые наблюдения `(x_i,y_i)`;
- одна линия финального refit, вычисленная только на сертифицированном домене `[L,U]`;
- прямой label с `family_id` и `one-function refit`;
- проблемные наблюдения с redundant marker encoding;
- при наличии валидного uncertainty band — его точный смысл в legend/caption.

Линия не должна соединять исходные точки и не должна выглядеть как интерполяционный polygon. Plot data для линии генерируются из формулы на dense deterministic grid, включая `L` и `U`, и сохраняются отдельно.

### 4.3 Панель двух функций

Показываются те же точки и:

- левая ветвь только на `[L,c]`;
- правая ветвь только на `[c,U]`;
- обе ветви содержат координату `(c,F(c))`, чтобы визуально проверить continuity;
- красная вертикальная граница `x=c`, обрезанная plotting area;
- рядом с линией видимый label `граница c=<display value>`;
- segment domains, `n`, доли строк и `n_unique_x` в caption или соседней таблице;
- визуально различимые `family_left` и `family_right`, но без намёка, что любая ветвь действует вне своего интервала.

Соглашение membership совпадает с RQ2: `x≤c` относится слева, `x>c` справа; строка на границе не учитывается дважды. Математический и вычислительный контракт continuity описан в [`02-continuous-segmented-regression.md`](02-continuous-segmented-regression.md).

**Красный цвет — product convention, не научно оптимальный encoding.** WCAG 2.2 SC 1.4.1 запрещает использовать цвет как единственный носитель смысла, а SC 1.4.11 требует для необходимых графических объектов контраст не менее `3:1` относительно соседних цветов ([WCAG 2.2 Recommendation](https://www.w3.org/TR/WCAG22/), [non-text contrast](https://www.w3.org/WAI/WCAG22/understanding/non-text-contrast.html)). Поэтому boundary одновременно:

- красная;
- штриховая и толще grid lines;
- текстово подписана;
- записана числом в таблице;
- остаётся распознаваемой в grayscale и при симуляции распространённых color-vision deficiencies.

Научные палитры должны быть perceptually ordered и CVD-safe. Nuñez, Anderton и Renslow построили и оценили CVD-оптимизированную `cividis` ([2018](https://doi.org/10.1371/journal.pone.0199239)); Crameri, Shephard и Heron документировали и иллюстрировали риски неравномерных rainbow/red-green maps ([2020](https://doi.org/10.1038/s41467-020-19160-7)). Эти работы не отменяют требуемую красную границу, но запрещают зависеть от красно-зелёного различия и поддерживают redundancy/contrast tests.

### 4.4 Повторные точки и overplotting

Точные повторы `(x,y)` могут визуально выглядеть одной точкой. Default:

- исходные координаты не jitter;
- используются outline и умеренная opacity, а multiplicity доступна через label/tooltip и row table;
- при сильном наложении добавляется marginal count/rug или отдельная count-encoding layer;
- jitter разрешён только как явно подписанная display-only копия; machine data и axis coordinates сохраняют исходные значения;
- aggregation mean-by-`x` не заменяет raw observations.

Это сохраняет соответствие графика входной таблице. Конкретные alpha/size и порог включения count layer должен определить prototype на small/large/repeated-`x` fixtures.

### 4.5 Проблемные наблюдения

`outlier`, `large_oof_residual`, `high_refit_influence`, `domain_input_issue` и другие причины не сворачиваются в один красный класс. Наблюдение сохраняет базовую точку и получает, например, тёмный контур плюс форму (`triangle`, `diamond`, `cross`) и короткий `row_id`; полная причина находится в таблице. Ни один flag не означает автоматическое удаление.

Hover не может быть единственным способом узнать значение: interactive plot обязан быть keyboard-accessible, а статический отчёт — содержать таблицу `row_id, x, y, OOF prediction, OOF residual, refit influence, flags`.

## 5. Метрики: строго разделить OOF и refit

### 5.1 Основная comparison table

Для обеих полных процедур P1/P2 показываются на одних outer splits:

| Поле | Обязательная квалификация |
|---|---|
| `R²_OOS` | `pooled OOF`, null baseline и estimand |
| `RMSE_OOF`, `MAE_OOF` | единицы `y`, weighting и число test appearances |
| `ΔR²`, `ΔRMSE`, `ΔMAE`, relative MSE uplift | направление `P2 minus/improvement over P1` |
| uncertainty interval | уровень, метод, resampling unit и повторялся ли весь nested search |
| failure/fallback rate | denominator и operational policy |
| family/direction/breakpoint stability | частоты, spread и boundary hits |

Точные определения и честная nested/grouped validation заданы в [`04-validation-uplift-residuals.md`](04-validation-uplift-residuals.md). Caption не может называть in-sample fit «validation».

### 5.2 Product warning `0.60`

Правило отчёта:

```text
if recommended_procedure.global_primary_R2_OOS is defined
   and recommended_procedure.global_primary_R2_OOS < 0.60:
    emit BELOW_PRODUCT_R2 warning
```

- сравнение выполняется на полном неокруглённом числе;
- проверяется итоговая рекомендуемая deployable procedure; значения P1 и P2 при этом оба остаются в comparison table;
- в comparison table у каждого определённого candidate отдельно виден статус `below/at-or-above 0.60`; если recommendation отсутствует, ни один sub-0.60 candidate не скрывается за состоянием `INCONCLUSIVE`;
- `R²=0.60` не означает «60% точек описано»: для принятого определения это relative reduction squared loss против явно указанного cross-fitted null;
- local segment `R²`, training/refit `R²` и альтернативный denominator не запускают warning;
- отрицательное значение показывается как отрицательное, а не заменяется нулём;
- при undefined denominator выводится `NA — constant/undefined baseline`, RMSE/MAE и failure status; warning не подделывается;
- порог — принятое product rule, а не универсальный научный критерий качества.

Если, например, internal value `0.5996` округлился бы до `0.600`, human display автоматически добавляет знаки до видимой стороны порога или пишет `0.5996; ниже 0.60`. Нельзя показывать одновременно `R²=0.60` и необъяснённый красный warning.

### 5.3 Таблица финального refit

Она отделена заголовком `Описательные показатели финального fit — не OOF quality` и содержит:

- P1: global `R²_fit`, RMSE, MAE, `n`, `n_unique_x`;
- P2: global `R²_fit` плюс local `R²_fit,left/right`, local RMSE/MAE, counts, shares, unique `x` и spans;
- `NA` и reason для constant/local-degenerate denominator;
- objective/weights, если fit не совпадает с unweighted SSE;
- связь каждой строки с конкретным `model_id` и формулой.

Local `R²` не усредняются и не сравниваются с global threshold. Высокий global fit при слабом local fit должен оставаться видимым.

## 6. Неопределённость без ложной уверенности

Belia et al. в эксперименте с 473 исследователями нашли частое непонимание CI и SE error bars ([2005](https://doi.org/10.1037/1082-989X.10.4.389)). Correll и Gleicher показали, что encoding mean/error меняет решения зрителей ([2014](https://doi.org/10.1109/TVCG.2014.2346298)); Hullman, Resnick и Adar показали task-dependent преимущество hypothetical outcome plots над error bars/violin plots ([2015](https://doi.org/10.1371/journal.pone.0142444)). Прямой перенос этих экспериментов на nonlinear curve bands ограничен, но они надёжно опровергают идею «любая полоса очевидна сама по себе».

Каждый interval или band обязан сообщать:

1. target: mean curve, prediction for a new observation, OOF metric, uplift или breakpoint stability;
2. coverage/credible level;
3. pointwise или simultaneous coverage;
4. conditional on selected family/breakpoint или включает повторный selection;
5. resampling unit (`x`/dependency group, не строка при зависимых повторах);
6. algorithm и число успешных/неуспешных resamples;
7. домен, внутри которого band валиден.

Правила rendering:

- band каждой ветви P2 обрезается тем же interval clip, что и линия;
- band не продолжается за `[L,U]` и не пересекает boundary как единая гладкая лента, если метод этого не определяет;
- boundary uncertainty показывается отдельным interval/rug/distribution, а не толщиной красной линии;
- если bootstrap не повторял preprocessing, family/direction/breakpoint search, caption говорит `conditional refit uncertainty`, а не `uncertainty полной процедуры`;
- при невозможности валидной оценки показывается `UNCERTAINTY_UNAVAILABLE` и причина; отсутствие band не маскируется;
- в primary panels допускается максимум одна хорошо определённая band на модель; конкурирующие интервалы уходят в отдельную sensitivity panel/table.

Ни одна найденная работа не даёт готового coverage для constrained elementary-family search с неизвестной границей `40–60%`. Поэтому prototype должен simulation-calibrate interval coverage; до этого percentiles resamples называются `stability distribution`, а не автоматически `95% CI`.

## 7. Обязательная диагностика остатков

NIST называет graphical residual analysis главным инструментом проверки regression model и рекомендует residuals against predictors/fitted values для поиска misspecification и non-constant variance ([model fit](https://www.itl.nist.gov/div898/handbook/pmd/section4/pmd44.htm), [functional sufficiency](https://www.itl.nist.gov/div898/handbook/pmd/section4/pmd441.htm)). Li, Cook, Tanaka и VanderPlas в visual-inference experiment показали, что residual plots в lineup могут обнаруживать несколько типов departure и избегать части чрезмерной чувствительности conventional tests ([2024](https://doi.org/10.1080/10618600.2024.2344612)). Их lineup protocol не становится обязательным production UI, но усиливает требование не заменять графическую диагностику одним тестом.

Минимальный diagnostic block:

1. `OOF residual = y-ŷ_OOF` против `x` с горизонтальной линией `0`;
2. OOF residual против OOF prediction;
3. `|OOF residual|` или robust standardized OOF residual против `x` для scale pattern;
4. optional run/order plot, только если исходный порядок имеет предметный смысл;
5. final-refit influence table/plot отдельно, с ясным label `in-sample refit influence`;
6. repeated-`x` pure-error/lack-of-fit summary, если он определим.

OOF и refit residuals нельзя смешивать в одной series. Exploratory smoother допускается, но подписывается как diagnostic smoother, его bandwidth/method сохраняется, а нулевая reference line визуально отличается. Point IDs и flags доступны в таблице; large residual и high influence — разные признаки.

## 8. Формула, домен и ограничения модели

Для каждого `model_id` human report показывает:

- семейство и формулу на исходной шкале `x,y`;
- все transformations и inverse transformations;
- коэффициенты с единицами/смыслом, а не только библиотечный parameter vector;
- observed domain `[L,U]` и запрет/статус extrapolation;
- направление `nondecreasing` или `nonincreasing`;
- вид сертификата whole-interval monotonicity и tolerance;
- objective, loss, weights и parameter count;
- для P2: `c`, `[L,c]`, `(c,U]`, обе формулы, общий `F(c)`, continuity residual, shares/counts и active `40/60` constraint;
- polynomial degree, никогда выше `3`.

Формула не хранится только как растровая картинка. В HTML она должна иметь selectable text/plain-text representation; MathML допустим как enhancement с fallback. MathML Core на cutoff имеет статус W3C Candidate Recommendation, а не финальной Recommendation, поэтому conformance к нему не объявляется без отдельного renderer test ([MathML Core status](https://www.w3.org/TR/mathml-core/)).

Machine representation хранит отдельные `family_id`, parameters, transformations, domains и boundary; строка `formula_display` не является исполняемым кодом. Потребитель не должен `eval`-ить формулу из отчёта.

## 9. Provenance и воспроизводимость

Peng предложил computational reproducibility как минимальный стандарт, когда полная независимая replication недоступна ([2011](https://doi.org/10.1126/science.1213847)). FAIR требует rich metadata, persistent identifiers и machine-actionability ([Wilkinson et al., 2016](https://doi.org/10.1038/sdata.2016.18)). W3C PROV разделяет `Entity`, `Activity` и `Agent` и задаёт interoperable provenance model ([PROV-O Recommendation](https://www.w3.org/TR/prov-o/)); W3C Data on the Web Best Practices поддерживает metadata, provenance и machine-readable access ([DWBP Recommendation](https://www.w3.org/TR/dwbp/)).

Для каждого запуска обязательны:

- `report_id`, `schema_version`, `generated_at`, timezone и locale;
- input URI/name, media type, SHA-256, schema, row count и stable `row_id`;
- `n_input`, `n_used`, `n_excluded`, exclusion reasons, missing/duplicate/repeated-`x` summary;
- units, transformations, sorting и weighting;
- candidate registry version/hash и полный перечень фактически проверенных families;
- validation estimand, group key, outer/inner split IDs/hashes, repetitions и null baseline;
- optimizer/solver names and versions, tolerances, starts, seeds, convergence/failure logs;
- source revision/container/environment, OS/runtime и dependency lock/hash;
- time budget, warning/recommendation policy versions и неокруглённые thresholds;
- link/hash каждого JSON, CSV, SVG и human report artifact;
- actor/software activity relation в PROV-compatible форме либо эквивалентной простой provenance table.

Полные input data не обязательно встраивать в публичный report, если есть privacy/licensing restriction; тогда сохраняются access status, hash и агрегированные counts. Но визуальная панель не может тихо исключить строки, отсутствующие в fit.

## 10. Machine-readable bundle

Candidate bundle:

```text
calibration-report/
├── report.html                 # или другой утверждённый human artifact
├── report.json
├── report.schema.json
├── observations.csv
├── observations.csv-metadata.json
└── figures/
    ├── one-function.svg
    ├── two-segment.svg
    └── residuals.svg
```

`report.json` следует RFC 8259: UTF-8, JSON numbers, без `NaN`/`Infinity`; неопределённое число кодируется `null` вместе с обязательными `status` и `reason` ([RFC 8259](https://www.rfc-editor.org/info/rfc8259/)). Структура валидируется versioned JSON Schema Draft 2020-12 ([official specification](https://json-schema.org/draft/2020-12)). Timestamps соответствуют RFC 3339 ([RFC 3339](https://www.rfc-editor.org/info/rfc3339/)).

Минимальные top-level groups:

```json
{
  "schema_version": "1.0.0",
  "report_type": "curve_calibration_regression",
  "report_id": "...",
  "generated_at": "2026-07-16T00:00:00+03:00",
  "status": {"code": "ONE_RECOMMENDED", "reason": "..."},
  "input": {},
  "validation": {},
  "models": {"one": {}, "two_segment": {}},
  "comparison": {},
  "diagnostics": {},
  "warnings": [],
  "failures": [],
  "provenance": {},
  "artifacts": []
}
```

`observations.csv` содержит минимум:

```text
row_id,x,y,x_group_id,input_status,
pred_one_oof,resid_one_oof,pred_two_oof,resid_two_oof,
pred_one_refit,pred_two_refit,segment_oof,segment_refit,
residual_flags,influence_flags
```

CSV следует устойчивому подмножеству RFC 4180 с header и единообразным quoting ([RFC 4180](https://www.rfc-editor.org/info/rfc4180/)); datatypes, units, null values и column semantics задаются соседним CSVW metadata по W3C Recommendation ([CSVW model](https://www.w3.org/TR/tabular-data-model/)). CSV — row-level exchange, JSON — вложенная модель/report state; один не заменяет другой.

SVG используется как scalable rendering, но сам по себе не гарантирует accessibility. Каждый SVG имеет title/description, programmatic name, явные текстовые labels и связанную long description/table; W3C описывает `title`/`desc` и accessibility mapping ([SVG 2 document structure](https://www.w3.org/TR/SVG/struct.html), [SVG accessibility support](https://www.w3.org/TR/SVG/access)). SVG-AAM на cutoff остаётся Working Draft, поэтому acceptance проверяется реальными target browser/screen-reader combinations, а не заявлением о draft conformance.

## 11. Округление и локализация

Обязательная политика:

- все fits, comparisons, thresholds и warning decisions используют full internal precision;
- JSON/CSV сохраняют достаточную round-trip precision; human rounding не переносится обратно в расчёт;
- одна метрика имеет одинаковое число знаков во всех сравниваемых строках;
- estimate и interval endpoints округляются согласованно;
- `R²` candidate default — три decimal places, с автоматическим увеличением precision около `0.60`;
- RMSE/MAE candidate default — precision, согласованная с единицей/разрешением `y`, а не механически три знака;
- `c` показывается не точнее, чем допускает measurement resolution `x`, но machine value сохраняется полностью;
- scientific notation используется последовательно для очень больших/малых величин;
- machine decimal separator всегда `.`, presentation locale может показывать запятую.

JCGM 100 рекомендует удерживать дополнительные цифры в промежуточных вычислениях и согласовывать округление результата с неопределённостью ([GUM 2008](https://www.bipm.org/en/committees/jc/jcgm/wg/jcgm-wg1-gum)). Это метрологическое руководство имеет `context_only` применимость: оно не назначает число знаков для `R²`. Финальные правила precision обязаны быть утверждены с предметными units и tested near-threshold fixtures.

## 12. Accessibility acceptance contract

Цель для web report — WCAG 2.2 Level AA. Минимальные проверки:

| ID | Проверка |
|---|---|
| `A11Y-01` | SC 1.1.1: у каждой complex figure есть short alt/name и доступная long description/table |
| `A11Y-02` | SC 1.4.1: model, boundary, warning и observation flags не различаются только цветом |
| `A11Y-03` | SC 1.4.3: text/labels проходят minimum contrast |
| `A11Y-04` | SC 1.4.11: essential lines, markers и controls имеют не менее `3:1` к соседним цветам |
| `A11Y-05` | интерактивные controls доступны с клавиатуры; hover content доступен также focus/text/table |
| `A11Y-06` | meaning сохраняется при grayscale, zoom/reflow и CVD simulation |
| `A11Y-07` | reading order связывает heading, figure, caption, warning и data table |
| `A11Y-08` | formulas, metrics и boundary доступны как text, не только pixels |

W3C tutorial для complex images рекомендует short description плюс видимую long description с scales, values, relationships и trends, часто в `figcaption`/таблице ([Complex Images](https://www.w3.org/WAI/tutorials/images/complex/)). Automated contrast/schema checks дополняются ручной keyboard, screen-reader, 200–400% zoom, print/grayscale и color-vision review.

## 13. Failure states должны быть видимыми

| Код | Условие | Что обязан показать отчёт |
|---|---|---|
| `INVALID_INPUT` | нет валидных `x,y`, non-finite/domain errors | counts, row reasons; без recommendation |
| `INSUFFICIENT_UNIQUE_X` | невозможно fit/validate family | raw plot, причина и требования |
| `NO_VALID_ONE_MODEL` | все P1 candidates провалили fit/certificates | `NO_VALID_MODEL`; без фиктивной линии |
| `NO_BALANCED_SPLIT` | нет tie-aware P2 split `40–60%` | вторая панель с raw points и status; P2 metrics `null` |
| `NO_VALID_TWO_MODEL` | domain/continuity/monotonicity/solver failure P2 | причины и fallback policy |
| `UNDEFINED_R2` | constant null denominator | `R²=null`, RMSE/MAE и reason |
| `BELOW_PRODUCT_R2` | global primary unrounded `R²_OOS<0.60` | обязательный текстовый warning |
| `NO_PRACTICAL_UPLIFT` | P2 не прошла uplift gate | обе панели; P1 recommended; величина/uncertainty причины |
| `UNSTABLE_SELECTION` | family/direction/boundary instability | distribution/frequencies; `INCONCLUSIVE` по policy |
| `UNCERTAINTY_UNAVAILABLE` | метод невалиден/insufficient resamples | interval/band отсутствует с явной причиной |
| `RENDER_OR_A11Y_FAILURE` | artifact/schema/accessibility check failed | bundle не помечается complete |

Failure panel не должна выглядеть как пустой успешно рассчитанный график. Она сохраняет одинаковые axes/raw points, содержит крупный status label и machine-readable code. Operational fallback P2→P1 отображается как fallback, а не как успешная двухсегментная модель.

## 14. Риски вводящего в заблуждение отчёта

| Риск | Контрмера |
|---|---|
| разные axes делают P2 визуально лучше | immutable shared scale/aspect/ticks; geometry test |
| линия продолжается вне своего domain | hard clip линии и band; endpoint test |
| overlay скрывает точки/границу | две primary panels; overlay только secondary |
| красный — единственный код | dashed line + label + table + contrast/grayscale test |
| проблемные точки тихо удалены | raw point count reconciliation + exclusion table |
| `0.60` трактуется как доля точек | точное определение `R²`, null baseline и текст warning |
| training `R²` выдан за generalization | отдельные sections и explicit `OOF`/`refit` labels |
| local `R²` усреднены в общий | показать denominators и запрет aggregation |
| band неясного типа | target/level/method/selection scope обязательны либо band отсутствует |
| conditional band выглядит как full-pipeline uncertainty | прямой caption `conditional` и stability panel |
| округление скрывает сторону threshold | decision full precision; adaptive display precision |
| hover — единственный доступ к данным | keyboard + visible/associated table |
| raster формула/график — единственный artifact | text formula, SVG semantics, JSON/CSV |
| failure исчезает из отчёта | stable failure code и незаполненная, но видимая comparison row/panel |

## 15. Acceptance criteria для prototype

Независимый аудит предложил короткие IDs; они принимаются как executable report tests.

| ID | Pass condition |
|---|---|
| `RAW` | число point records/табличных строк согласовано с `n_used`; исключения перечислены |
| `CMP` | P1/P2 panels используют идентичные data, axes, transform, aspect и tick geometry |
| `GEOM` | P1 ограничена `[L,U]`; P2 branches/bands clipped; boundary включена в обе ветви и не создаёт gap/jump |
| `METRIC` | OOF и refit metrics разнесены; definition/baseline/units/denominators machine-readable |
| `WARN` | `R²_OOS<0.60` проверяется до rounding только для global primary metric |
| `RESID` | OOF residual charts/table существуют; refit influence отдельно; flags traceable to `row_id` |
| `A11Y` | WCAG contract выше проходит automated и manual target-platform checks |
| `JSON` | JSON Schema valid; RFC 8259; no non-finite numbers; every `null` metric has status/reason |
| `FAIL` | каждый synthetic failure fixture даёт ожидаемый code, visible panel и no false recommendation |
| `UNC` | каждый interval/band содержит target, level, method, unit, selection scope и successful-resample count |

Дополнительные tests:

- screenshot/pixel or DOM geometry regression для clip и shared axes;
- fixture `R²=0.5996` и `0.6000`;
- constant `y`, repeated `x`, all-identical `x`, no balanced split, boundary at allowed 40/60 edge;
- model with a visible influential observation à la Anscombe;
- CVD/grayscale and 400% zoom snapshot/manual inspection;
- JSON→render→table reconciliation and deterministic rerun from stored seed/environment.

## 16. Матрица ключевых утверждений

| Claim ID | Утверждение | Основание | Applicability |
|---|---|---|---|
| `C-RQ5-0001` | raw observations должны сопровождать regression summaries | Anscombe; NIST; Weissgerber | `direct` |
| `C-RQ5-0002` | P1/P2 сравниваются на общих axes/data | Cleveland–McGill; Correll et al.; Ondov et al. | `direct/partial`; exact two-panel layout — product decision |
| `C-RQ5-0003` | branches и bands P2 рисуются только в собственных domains | математический контракт RQ2 | `direct` |
| `C-RQ5-0004` | красная boundary требует non-color redundancy и `3:1` contrast | WCAG 2.2 | `direct` для web accessibility; hue red — convention |
| `C-RQ5-0005` | OOF и refit/local metrics нельзя смешивать | RQ4; NIST | `direct` |
| `C-RQ5-0006` | uncertainty encoding без определения target/method двусмыслен | Belia; Correll–Gleicher; Hullman et al. | `partial`, разные tasks |
| `C-RQ5-0007` | residual graphics обязательны рядом с metrics | NIST; Li et al. | `direct/partial` |
| `C-RQ5-0008` | provenance и machine-readable metadata нужны для reproducibility | Peng; FAIR; W3C PROV/DWBP | `direct` как engineering contract |
| `C-RQ5-0009` | JSON non-finite metrics нельзя сериализовать числами | RFC 8259; JSON Schema | `direct` |
| `C-RQ5-0010` | probability reliability diagram — другой тип графика | scikit-learn; Niculescu-Mizil–Caruana | `direct` distinction |
| `C-RQ5-0011` | `0.60`, red hue и exact panel ordering не выводятся из науки | project decisions + audit | `direct` governance statement |

Оценка не сворачивалась в score. Empirical perception studies имеют сильную internal validity для своих tasks, но layout/participants не идентичны этому продукту; перенос помечен `partial`. W3C/IETF standards нормативны для соответствующих web/data formats, но не доказывают statistical validity. NIST — официальное статистическое руководство. Product conventions не маскируются как научные thresholds.

## 17. Что должен решить prototype или пользователь

Литература не задаёт следующие project-specific числа и форматы:

1. основной human artifact: responsive HTML, tagged PDF или оба;
2. уровень uncertainty (`90/95%`), interval method и simulation-proven coverage target;
3. resampling budget и поведение при failed bootstrap fits;
4. practically meaningful uplift/no-harm thresholds;
5. display precision с учётом реальных units/resolution `x,y`;
6. конкретный red hex, CVD-safe supporting palette, line widths/dashes и minimum print resolution;
7. point alpha/size, overplotting threshold и правила label collision;
8. где показывать rejected P2: всегда в main comparison, либо main summary + appendix; данный контракт рекомендует main comparison для прозрачности;
9. допустимы ли interactive controls и какие browser/screen-reader targets поддерживаются;
10. privacy/redaction rules для `row_id`, input URI и observation-level CSV;
11. язык/locale отчёта и формат единиц;
12. numerical/display domain extrapolation policy — default здесь `none`;
13. user-facing wording для `INCONCLUSIVE`, `UNSTABLE_SELECTION` и undefined metric;
14. thresholds для residual/influence flags, которые RQ4 оставляет prototype calibration;
15. storage/versioning/retention policy для report bundles.

Эти выборы должны быть versioned policy, а не скрытыми renderer defaults.

## 18. Независимый аудит

Первая попытка запустить child reviewer была отклонена orchestration с `agent thread limit reached`. До финализации parent orchestrator выполнил отдельный независимый аудит и передал decision-bearing замечания. После проверки они интегрированы:

- добавлены Anscombe, Cleveland–McGill и Ondov et al. для raw-data/comparison design;
- добавлены Correll–Gleicher и Belia для риска двусмысленной uncertainty;
- добавлен Li et al. для empirical residual-plot evidence;
- уточнены точные WCAG 2.2 requirements: цвет не единственный код, `3:1` для essential non-text graphics и textual alternative;
- добавлены Nuñez/Crameri для CVD-safe/perceptually uniform color guidance;
- provenance contract согласован с W3C PROV/DWBP и FAIR;
- JSON `null + status/reason` согласован с RFC 8259/JSON Schema;
- product conventions (`две панели`, `красный`, `0.60`) явно отделены от научных выводов;
- приняты executable IDs `RAW/CMP/GEOM/METRIC/WARN/RESID/A11Y/JSON/FAIL/UNC`.

Независимый reviewer не редактировал файл; окончательная ответственность за синтез и корректность ссылок остаётся у исполнителя.

## 19. Поиск, даты и ограничения покрытия

Все строки ниже исполнены **2026-07-16, Europe/Moscow** с cutoff `2026-07-16 23:59:59`. Канонические query families протокола:

```text
EN-A: "regression reporting guideline" OR "curve fitting visualization" OR "segmented regression plot" OR "calibration plot" OR "residual plot" OR "scatterplot accessibility" OR "uncertainty visualization" OR "statistical graphics accessibility"

EN-B: (reporting OR visualization OR plot OR graph) AND (regression OR "curve fitting" OR "segmented regression" OR calibration) AND (scatterplot OR interval OR breakpoint OR residual OR uncertainty OR accessibility OR reproducibility OR warning)

RU-A: "стандарт отчетности регрессии" OR "визуализация подбора кривой" OR "график сегментированной регрессии" OR "калибровочный график" OR "график остатков" OR "доступность диаграмм" OR "визуализация неопределенности"

RU-B: (отчетность OR визуализация OR график OR диаграмма) AND (регрессия OR "подбор кривой" OR "сегментированная регрессия" OR калибровка) AND ("диаграмма рассеяния" OR интервал OR граница OR остатки OR неопределенность OR доступность OR воспроизводимость OR предупреждение)
```

Targeted exact queries:

```text
"Graphs in Statistical Analysis" Anscombe 1973 DOI
site:science.org Cleveland McGill graphical perception position length angle 1984
site:plos.org Weissgerber raw data bar line graphs 2015
site:dl.acm.org deceptive visualizations axis truncation empirical study
Cleveland McGill 1984 graphical perception elementary perceptual tasks DOI
Pandey How Deceptive Are Deceptive Visualizations 2015 DOI
Correll Truncating the Y-Axis Threat or Menace 2020 DOI
Cleveland McGill McGill shape parameter two-variable graph banking 45 degrees DOI
site:w3.org/WAI/WCAG22 use of color non-text contrast charts graphs
site:w3.org/WAI/tutorials/images/complex charts graphs long description data table
site:itl.nist.gov/div898/handbook regression residual plots scatter plot model validation
site:nist.gov uncertainty rounding significant digits official guide
NIST SP 811 rounding numbers significant digits section 7.9 PDF
site:bipm.org JCGM 100 2008 rounding uncertainty significant digits
site:w3.org/TR/WCAG22/#use-of-color WCAG 2.2 Recommendation
site:w3.org/WAI/WCAG22/Understanding/use-of-color charts color alone
site:rfc-editor.org RFC 8259 JSON NaN Infinity interoperability
site:w3.org/TR/csvw CSV on the Web Recommendation metadata schema
site:w3.org/TR/prov-o W3C Recommendation provenance ontology
site:json-schema.org draft 2020-12 specification official
site:rfc-editor.org RFC 4180 CSV common format MIME type
site:rfc-editor.org RFC 3339 date time Internet timestamps
site:w3.org/TR/SVG2 accessibility title desc SVG 2
site:w3.org/TR/svg-aam SVG Accessibility API Mappings
Correll Gleicher Error Bars Considered Harmful DOI 2014
Hullman Resnick Adar hypothetical outcome plots reliability error bars DOI
Belia researchers misunderstand confidence intervals standard error bars DOI 2005
Fernandes Walls Munson Hullman Kay uncertainty displays quantile dotplots DOI
Weissgerber Reveal Don't Conceal data visualization transparency 2019 DOI
show raw data alongside summary statistics empirical visualization study DOI
data visualization raw observations overplotting transparency regression scatterplot study
Anscombe quartet same regression statistics different graphs original paper claims
TRIPOD statement transparent reporting model formula performance measures confidence intervals BMJ 2015
official reporting guideline regression model formula predictor transformations performance uncertainty reproducibility
Peng reproducible research computational science Science 2011 DOI
FAIR Guiding Principles machine actionable metadata 2016 DOI
site:scikit-learn.org/stable/modules/calibration.html calibration curves reliability diagrams official
Niculescu-Mizil Caruana predicting good probabilities supervised learning calibration curves DOI 2005
Van Calster calibration hierarchy probabilistic prediction models calibration plot DOI 2019
calibration plot observed outcomes predicted probabilities definition primary paper
site:w3.org/TR/mathml-core W3C MathML Core Recommendation formulas accessibility
site:w3.org/WAI math equations accessibility MathML official
site:w3.org/TR/WCAG22 images of text equations chart labels
site:w3.org/TR/SVG2 desc title accessible charts vector graphics
Ondov Gleicher visual comparison design separate juxtaposition superposition DOI
Li residual diagnostic plots regression visualization DOI
Nunez Anderton Renslow color vision deficiency scientific colormaps DOI
Crameri Shephard Heron misuse colour science communication DOI
```

Проверялись первичные publisher/DOI pages, PubMed/PMC, PLOS, JMLR/ACM/IEEE metadata, NIST e-Handbook, W3C Recommendations/drafts, IETF RFC Editor, BIPM/JCGM, official JSON Schema и scikit-learn 1.9.0 documentation. Search terms расширялись по backward references ключевых работ и по независимому аудиту. Русскоязычные broad queries не дали отдельного decision-bearing primary corpus; они поддержали terminology coverage, но не заменяют полный внутренний поиск Math-Net/eLIBRARY.

### Отклонения от полного протокола

- не выполнены полные API exports, pagination, PRISMA counts и дедупликация всех обязательных OpenAlex, zbMATH, arXiv, DBLP, PubMed/Europe PMC, Math-Net и eLIBRARY runs;
- web discovery был targeted/ranked, а не исчерпывающим просмотром всех результатов;
- не созданы отдельные `runs.csv`, raw exports и evidence-matrix, поскольку ticket разрешает ровно один topical report;
- child audit был заблокирован thread limit, но компенсирован независимым parent-orchestrated audit до финализации;
- correction/retraction chasing выполнен targeted для decision-bearing работ, не как полный bibliometric export;
- renderer/browser/screen-reader interoperability не тестировалась: это acceptance work prototype;
- численные palettes, rounding, uplift, uncertainty и flag thresholds не выводились из несопоставимых исследований;
- absolute completeness не заявляется; материалы после cutoff не включались.

Главный evidence gap: нет прямого исследования, одновременно проверяющего raw scatterplot, arbitrary elementary one-vs-two monotone fit, unknown tie-aware `40–60%` breakpoint, nested OOF metrics, selection-aware bands, accessibility и machine-readable reporting. Поэтому statistical semantics берутся из RQ2/RQ4, perceptual/accessibility/data-format решения — из отдельных первичных работ и стандартов, а целостный contract обязан пройти prototype tests `RAW…UNC` на реальных и синтетических fixtures.
