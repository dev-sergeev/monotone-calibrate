# Адаптация LLM-SR для выбора калибровочной функции

Дата реализации: 2026-07-22.

Источники:

- локальная копия статьи: [`2404.18400v3.pdf`](2404.18400v3.pdf);
- статья: [LLM-SR: Scientific Equation Discovery via Programming with Large Language Models](https://arxiv.org/abs/2404.18400);
- официальный код: [deep-symbolic-mathematics/LLM-SR](https://github.com/deep-symbolic-mathematics/LLM-SR), изученный commit `41c212312df6c16d936c9cb395356a62774c47e3`.

## Алгоритм статьи

LLM-SR разделяет дискретный поиск формы уравнения и непрерывную оценку
коэффициентов.

1. **Hypothesis generation.** Prompt содержит инструкцию, предметное описание,
   evaluation/optimization function и несколько ранее найденных программ.
   LLM стохастически генерирует batch program skeletons вида
   `f(x, params)`, где `params` остаются placeholders.
2. **Data-driven evaluation.** Для каждого исполнимого skeleton оптимизируются
   числовые параметры (`numpy+BFGS` либо `torch+Adam`), после чего fitness равен
   отрицательному MSE. Ошибочные, неfinite и слишком долгие программы
   отбрасываются.
3. **Experience management.** Кандидаты и scores хранятся в десяти независимо
   развивающихся islands. Scores образуют clusters. Island выбирается случайно,
   cluster — Boltzmann sampling с предпочтением высокого score, программа
   внутри cluster — с предпочтением короткого кода.
4. **Iterative refinement.** Два sampled examples добавляются в следующий
   prompt как траектория улучшения. В экспериментах статья использует четыре
   samples на prompt, temperature `0.8`, до десяти параметров, 30-секундный
   evaluation timeout и примерно 2500 итераций. Периодически слабая половина
   islands перезапускается от лучших программ выжившей половины.
5. Лучший по fitness program возвращается как найденное уравнение.

Критические результаты ablation в статье: удаление iterative refinement или
разделения «skeleton + optimizer» резко ухудшает качество; single-island top-k
хуже multi-island sampling.

## Реализованный seam

Внешний interface нового deep module:

```python
run_symbolic_search(x, y, llm_config, fit_options) -> SymbolicSearchResult
```

Вся генерация prompt, strict parsing, fitness evaluation, clustering,
Boltzmann sampling и island reset скрыты внутри
[`symbolic_search.py`](../src/monotone_calibrate/symbolic_search.py). Результат
содержит конечный `HypothesisSpace`, статистику поиска и typed warning codes.
Application передаёт этот portfolio обычному fitting engine; validation
получает ровно тот же immutable portfolio через `CandidateSet`.

Machine-readable contracts:

- response schema:
  [`llm-sr-hypotheses.schema.json`](specification/llm-sr-hypotheses.schema.json);
- current policy:
  [`llm-sr-policy-v2.json`](acceptance/llm-sr-policy-v2.json);
- implementation traceability:
  [`traceability-llm-sr-v2.md`](acceptance/traceability-llm-sr-v2.md).

## Соответствие шагов

| LLM-SR | Реализация проекта |
|---|---|
| Equation program skeleton | `EquationHypothesis(structure, family_ids)` |
| Safe initial program | complete P1 registry plus P2-linear seed portfolio |
| `params` placeholders | параметры принадлежат registry family и оцениваются solver-ом |
| Batch `b=4` | `samples_per_prompt=4` |
| Generation temperature `0.8` | `ChatOpenAI(..., temperature=0.8)` |
| Negative MSE fitness | `-candidate.sse / n` после quantized certified fit |
| `m=10` islands | `SymbolicSearchOptions.num_islands=10` |
| `k=2` experiences | один P1 и один P2 example, затем fallback sampling |
| Score signature clusters | `(structure, round(score, 12))` |
| Boltzmann cluster selection | stable softmax, начальная temperature `0.1` |
| Short program preference | softmax по отрицательной normalized skeleton length |
| Weak-island reset | deterministic iteration-based reset, default через 32 итерации |
| Invalid program discard | schema/registry/solver/certificate failure discards hypothesis |
| Best equation | лучшие P1/P2 выбираются внутри найденного portfolio |

## Осознанные отличия

### Типизированные skeletons вместо произвольного Python

Официальная реализация вставляет generated body в Python template и вызывает
`exec` в дочернем процессе. Для публикуемой calibration model это несовместимо
с существующим typed runtime и security contract. Здесь LLM может назвать
только P1 family либо ordered pair P2 из `registry-v1`. Любой `code`, unknown
family, extra JSON field, duplicate key или nonfinite JSON atom отклоняет весь
ответ. `eval`, dynamic import и generated Python отсутствуют.

Это сохраняет центральную идею статьи — LLM выбирает дискретный skeleton, а
надёжный optimizer оценивает числовые placeholders — при существенно более
узком, аудируемом пространстве.

### Solver и ограничения продукта

Вместо универсальных BFGS/Adam используются уже существующие conditional
linear least squares, `L-BFGS-B` для nonlinear shape и breakpoint search. После
fit обязательны:

- округление коэффициентов и breakpoint до `0.001`;
- аналитический certificate domain/finiteness/монотонности;
- для P2 — баланс `40/60..60/40`, единое направление и непрерывное сопряжение;
- collapse вырожденной P2 в P1;
- запрет extrapolation в runtime.

LLM не может изменить эти правила, search profile, validation thresholds или
report recommendation policy.

### Бюджет

Paper-scale 2500 iterations неприемлемы для одного интерактивного CSV run.
Product default — четыре LLM calls, настраиваемые
`MONOTONE_CALIBRATE_LLM_SEARCH_ITERATIONS` в диапазоне `1..64`. Каждый call
может вернуть до четырёх skeletons. Numerical evaluation кэшируется по
canonical hypothesis ID.

### Две конкурирующие процедуры

Статья ищет одно уравнение. Проект обязан сравнить P1 и P2, поэтому каждый
island хранит отдельные best scores для обеих структур и prompt по возможности
получает по одному P1/P2 experience. Без этого более гибкая P2 систематически
вытесняла бы P1 из buffer ещё до внешней uplift policy.

### Validation scope

LLM-SR portfolio строится по полному training summary и full-data fitness,
затем замораживается и заново fit-ится внутри каждого grouped outer fold. Это
оценивает перенос коэффициентов, breakpoint и внутренний выбор по portfolio,
но не является untouched оценкой самой discovery-стадии: full-data `y`
повлиял на состав portfolio.

Поэтому каждый успешный LLM run получает warnings
`LLM_TRAINING_SUMMARY_DISCLOSED` и
`LLM_SR_PORTFOLIO_CONDITIONAL_VALIDATION`, а HTML прямо называет OOF conditional.
Полностью nested LLM-SR потребовал бы отдельного external search для каждого
outer-training scope (до сотен provider calls с текущей validation policy) и
оставлен будущей явно бюджетируемой версией.

## Fallback и provenance

Без LLM используется полный registry, то есть offline поведение остаётся
детерминированным. Ошибка конфигурации, transport/provider failure, malformed
response или отсутствие новых валидных skeletons также атомарно возвращает
полный registry; частичный случайный portfolio не используется как silent
fallback.

`report.json.llm_advisor` сохранён как исторически совместимое имя machine
field. Теперь его `mode` равен `llm_sr_typed_symbolic_search`; он содержит
только counts, policy versions и hashes. Access token, raw endpoint, prompts,
responses и provider diagnostics туда не попадают.

## Эксплуатационная граница

Offline quick start с `--no-dotenv` не включает этот selector. Для LLM-SR нужны
настроенный `.env` и `--llm-symbolic-search`; команда является блокирующей и
печатает JSON только после всех provider calls, локальных fits и validation.

Текущий код валидирует configured base URL и не включает tracing/callbacks сам,
но пока не имеет собственного redirect-origin HTTP transport и не отклоняет
ambient LangSmith tracing variables fail-closed. Поэтому formal claim о
сетевой изоляции относится только к будущему hardening, а не к реализованному
selector-у.
