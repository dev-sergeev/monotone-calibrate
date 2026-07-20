# Acceptance-контракт и граница implementation handoff v1

**Статус:** frozen design-time contract; production gates имеют начальный статус
`NOT RUN` и исполняются только отдельной implementation-картой  
**Дата:** 2026-07-16  
**Связанный тикет:** 14 — утвердить приёмочные критерии и границу handoff (внутренний архив, не включён в публичный репозиторий)  
**Machine manifest:** [`acceptance-manifest-v1.json`](../acceptance/acceptance-manifest-v1.json)

## 1. Решение и честная граница

Wayfinder фиксирует исчерпывающую исполняемую acceptance-спецификацию: exact
fixtures, oracles, численные допуски, platform/process gates, структуру bundle
и retained evidence paths. Он не может объявить PASS проверке кода, которого
ещё нет.

Отдельный implementation handoff обязан:

1. создать обычный source project из delivery-контракта;
2. реализовать один production pipeline, без отдельного «быстрого demo»;
3. выполнить все `P0` и `P1` gates manifest на требуемых платформах;
4. оставить frozen design manifest неизменным с `NOT_RUN`; после появления
   retained evidence создать отдельный финальный
   `evidence/acceptance/acceptance-results-v1.json`, валидный по
   [`acceptance-results.schema.json`](../acceptance/acceptance-results.schema.json),
   где ровно те же 20 gate records имеют `PASS`, exact fixture/oracle/test
   bindings, по одному raw run на каждой platform, hashes и reconciled counts;
5. не принимать релиз при blocker/major, пропущенном gate либо изменённом
   manifest без новой версии.

`reviewed_evidence_digest` имеет единственный preimage: canonical compact JSON
с sorted keys и полями `acceptance_id`, `release_snapshot_digest`,
`verifier_runtime_lock_sha256`,
`design_manifest_sha256`, `source_manifest_sha256`,
`source_archive_sha256`, `dependency_lock_sha256`, `policy_sha256`,
`registry_sha256`, `source_inventory_policy_sha256`, `gate_evidence`; последний
является manifest-order массивом ровно из 20 объектов
`{gate_id,evidence_sha256}`. SHA-256 этого UTF-8 payload должен совпасть у
aggregate и обоих reviewer records.

Финальный verifier принимает `PASS` только с двумя обязательными внешними
`--reviewer-public-key AXIS=/absolute/path` для осей `statistical_model` и
`security_operations`. Пути к ключам должны быть лексически вне project tree,
а raw Ed25519 keys — разными; каждый подписывает canonical reviewer payload.
Это машинно доказывает валидность подписей под двумя caller-selected ключами.
Организационная независимость custodians и отсутствие hardlink alias внутрь
project tree остаются явной обязанностью вызывающей стороны, а не выводятся из
пути или самодекларированных строк `reviewer_id`.

Обязательная команда имеет форму:
`UV_PROJECT_ENVIRONMENT=/ABSOLUTE/CALLER/monotone-verifier-v1
uv --no-config run --project docs/acceptance/verifier-runtime
--frozen --offline --no-dev python -I
docs/acceptance/verify_acceptance_results.py
evidence/acceptance/acceptance-results-v1.json --project-root .
--trusted-release-digest <OUT-OF-BAND-NORMATIVE-SNAPSHOT-SHA256>
--reviewer-public-key statistical_model=/ABSOLUTE/statistical.pub
--reviewer-public-key security_operations=/ABSOLUTE/security.pub`.
Digest нельзя брать из проверяемого project tree: его передаёт release handoff
через независимый канал. До загрузки project-local schemas verifier
descriptor-safe пересчитывает exact normative ledger, включающий собственные
bytes и все acceptance schemas, и сравнивает его с этим anchor. Тот же digest
входит в results, каждый gate evidence, общий evidence preimage и обе Ed25519
подписи. Без anchor/любого ключа, с изменённым verifier/schema snapshot,
лексически project-contained key path, одинаковыми raw-key fingerprints либо
невалидной подписью команда завершается fail-closed. Произвольный изменённый
скрипт, который игнорирует anchor, не является этим acceptance protocol.
Verifier дополнительно требует CPython 3.12 isolated flags, exact
`jsonschema==4.26.0` и `cryptography==49.0.0` из frozen
[`verifier-runtime/uv.lock`](../acceptance/verifier-runtime/uv.lock). Lock с
wheel/sdist hashes входит в release snapshot; его SHA также присутствует в
results, every gate, reviewed preimage и обеих подписях. Runtime готовится
отдельной caller-owned командой из manifest; dependency resolution, ambient
`PYTHONPATH` и сеть во время verification запрещены.

`P0/P1` задают порядок реализации и риск, а не обязательность: обе категории
блокируют завершение implementation handoff. Текущий fitting prototype остаётся
exploratory evidence; его fixtures использовались при исправлении manifest и
не подменяют untouched confirmatory run.

## 2. Frozen артефакты

Нормативны:

- [machine schema](../acceptance/acceptance-manifest.schema.json) и
  [manifest v1](../acceptance/acceptance-manifest-v1.json);
- caller-owned [frozen verifier runtime](../acceptance/verifier-runtime/README.md)
  с отдельными `pyproject.toml`/`uv.lock`, exact dependency versions и artifact
  hashes; verification выполняется isolated/offline, не из implementation
  environment;
- [final implementation-results schema](../acceptance/acceptance-results.schema.json),
  [retained gate-evidence schema](../acceptance/acceptance-evidence.schema.json)
  с [platform-run](../acceptance/acceptance-platform-run.schema.json),
  [environment](../acceptance/acceptance-environment.schema.json),
  [raw environment probe](../acceptance/acceptance-environment-probe.schema.json),
  [oracle-result](../acceptance/acceptance-oracle-result.schema.json) и
  [signed-review](../acceptance/acceptance-review.schema.json) schemas;
- [exact gate execution policy](../acceptance/gate-execution-policy-v1.json)
  вместе с [schema](../acceptance/gate-execution-policy.schema.json): для каждого
  gate зафиксированы platform order, isolated argv, runner/cwd/env, полный
  список command source targets, единственный `<gate>::contract-v1` test ID и
  nullable tolerance policy;
- [exact source inventory policy](../acceptance/source-inventory-policy-v1.json)
  вместе с [policy schema](../acceptance/source-inventory-policy.schema.json) и
  [source-manifest schema](../acceptance/source-manifest.schema.json): manifest
  обязан быть равен descriptor-safe обходу всех declared roots, а не
  произвольному непустому subset; unknown top-level file/directory, включая
  root `conftest.py`, запрещён, а operational exclusions перечислены исчерпывающе;
- authoritative implementation source публикуется как один canonical
  `evidence/acceptance/source-snapshot.zip`: только uncompressed `ZIP_STORED`,
  exact sorted manifest members, Unix regular `0644`, fixed 1980 timestamp,
  нулевые comments/extra/опасные flags и frozen size limits. Verifier читает
  каждый member из уже открытого archive, сверяет bytes/hash с source manifest,
  а archive SHA входит в manifest/results/every gate/environment/signed
  preimage. Live checkout — лишь defense-in-depth materialization и не может
  переопределить content-addressed release после PASS;
- [resolved policy schema](resolved-policy.schema.json) и
  [registry schema](registry.schema.json), фиксирующие exact runtime constants,
  family order, canonical AST IDs, parameter names и formulas, а не только
  semantic ID/hash произвольного файла;
- обязательный fail-closed
  [`verify_acceptance_results.py`](../acceptance/verify_acceptance_results.py),
  который не разрешает переписывать design-time `NOT_RUN`, хешировать refs
  без per-file/total limits либо собирать ложный PASS из incomplete source tree,
  несвязанных fixtures/oracle claims/platform artifacts/hashes и unsigned
  reviewer IDs. Verifier подтверждает структуру, provenance, identity и
  signatures; истинность OS execution и научного oracle attests внешний
  подписывающий reviewer на основании retained command/log/result artifacts;
- [exact work/admission policy](../acceptance/work-policy-v1.json);
- [canonical identity/origin policy and byte goldens](../acceptance/identity-policy-v1.json);
- [exact grouped outer-split/scope-ID policy and executable golden](../acceptance/split-policy-v1.json);
- [exact LLM replaceable-start catalogue](../acceptance/start-slot-policy-v1.json);
- [synthetic fixture catalogue](../acceptance/synthetic-fixtures-v1.md);
- [полный CSVW dictionary](../acceptance/csvw-contract-v1.json);
- два empirical sentinel-файла с происхождением и hashes:
  [NIST Misra1a](../acceptance/fixtures/empirical/nist-misra1a.csv) и
  [NIST Thurber](../acceptance/fixtures/empirical/nist-thurber.csv);
- принятые контракты 01–07 и report prototype digest
  `3bfb9af9571e43223a9ed9e75840f293104de8c6a84b16e44dc81c60ca3df490`.

Synthetic known truth используется для recovery, operating characteristics и
causal fault injection. Empirical sentinels проверяют parser, end-to-end,
replay и честное поведение на наблюдаемых данных; историческая NIST-модель не
входит в `registry_v1`, поэтому segment/family oracle для них запрещён.

## 3. Закрытые неоднозначности

### 3.1 Порог `R²=0.60`

Product warning читает только неокруглённый global primary pooled `R²_OOS`
рекомендованной deployable procedure. Local segment и full-data refit `R²`
всегда видимы как диагностика, но не сравниваются с `0.60` и не запускают
warning. Это канонизирует сводную спецификацию по контрактам 03 и 07.

### 3.2 Где выполняется confirmatory matrix

Тикет 14 замораживает catalogue, seeds, pass bands и evidence contract.
Untouched recovery/confusion, simulation calibration, browser, process,
cross-platform и benchmark runs выполняются implementation handoff. До этого
их единственный допустимый статус — `NOT RUN`.

### 3.3 Numerical scaling

V1 выбирает fail-closed ветвь. Если разные raw canonical binary64 `x`
схлопнулись в одинаковый нормализованный `t`, обе процедуры прекращаются до
solver с `NONINVERTIBLE_X_SCALE + NORMALIZED_X_COLLISION`. Реализация не имеет
права незаметно перейти к округлению, arbitrary precision либо склеить ties.

### 3.4 Production schemas и model shape

В source project schemas живут в `schemas/`; source-known
[`production-report.schema.json`](production-report.schema.json),
[`model.schema.json`](model.schema.json) и
[`manifest.schema.json`](manifest.schema.json), а для optional advisor также
[`llm-start-advice.schema.json`](llm-start-advice.schema.json), являются
normative. Verified
byte-identical copies первых двух публикуются в корне bundle как
`report.schema.json` и `model.schema.json`. Verifier использует installed
source-known schemas, а не доверяет произвольной схеме из проверяемого
каталога.

`model.schema.json` v1 задаёт закрытый object:

- `schema_version=model-v1`, `registry_version=registry-v1`, role
  `candidate_one|candidate_two|recommended`;
- decision/recommendation state, units, observed closed domain и запрет
  экстраполяции;
- direction и массив `segments` длины ровно 1 или 2;
- каждый segment содержит closure, raw interval, `family_id`, canonical AST
  object/id, shortest-round-trip parameters, affine transforms и independent
  domain/derivative certificate;
- P2 дополнительно содержит `c`, exact `x<=c / x>c` membership,
  tie-safe-cell bounds, shared `μ` и continuity certificate;
- `model_structure_hash` и parameter-bearing `model_instance_hash`;
- display formula является обязательным presentation-текстом для готовой
  функции и никогда не исполняется.

Recommended role разрешён только validated P1/P2. Unknown keys, family/version,
non-finite number, inconsistent intervals/hash/certificate или больше двух
segments fail closed.

JSON Schema теперь отклоняет все выразимые state-transition противоречия,
включая `TWO_RECOMMENDED` без available P2/uplift/stability, unavailable
recommended metrics, wrong segment direction, обе constant branches и
недостающие plot artifacts. Тринадцать арифметических/evaluator-инвариантов,
которые Draft 2020-12 выразить не может (включая breakpoint/cell/join,
continuity evaluation, certificate identity/hash, functional collapse,
count/share sums, raw R² warning, LLM call-count sum, selected metrics,
gate/bootstrap reconciliation и recomputation split-effects), обязательны для source-known
`monotone_calibrate.verification.validate_report_and_model`. Их exact negative
fixtures публикует
[`fuzz_model_report_schemas.py`](../acceptance/fuzz_model_report_schemas.py), а
`MODEL-CONTRACT-002`, `CLI-E2E-004` и `VERIFY-011` удерживают runtime evidence.
Актуальный machine catalogue фиксирует 23 schema-reject, 13 semantic-only и
15 legitimate schema-accept cases, включая все восемь decision states.

### 3.5 Optional LLM start advisor

Default policy остаётся `off` и полностью offline. Явный
`--llm-start-advisor` либо `MONOTONE_CALIBRATE_LLM_ENABLED=true` использует
только `langchain_openai.ChatOpenAI` и
обязательные `MONOTONE_CALIBRATE_LLM_BASE_URL`,
`MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN`, `MONOTONE_CALIBRATE_LLM_MODEL`;
timeout/retries и insecure-HTTP opt-in заданы контрактом 06. Ambient
`OPENAI_*`, LangSmith tracing, tools и provider-specific extensions запрещены.

Advisor не выбирает новую функцию. Для full-data и каждого outer train он
может заменить только bounded start slots уже зарегистрированных
family/pair/direction/cell; число starts/evaluations и certificates неизменно.
Все exact slot IDs, vector shapes/bounds, anchors, replaceable ordinals и
fallback vectors берутся только из frozen
[`start-slot-policy-v1.json`](../acceptance/start-slot-policy-v1.json), чей hash
входит в manifest и identity.
Outer-test rows отсутствуют. Strict schema/bounds failure возвращает исходный
deterministic start. Все ответы/fallback states замораживаются до solver,
ledger hash входит в `analysis_id`, а access token/raw URL нигде не
сериализуются. Это позволяет улучшать numerical basin discovery без доверия к
формуле или утверждению LLM.

## 4. Exact grouped validation и uncertainty

Для `K` folds canonical `x`-groups распределяются exact algorithm из
[`split-policy-v1.json`](../acceptance/split-policy-v1.json). Этот machine
policy фиксирует binary preimage каждого counter-SHA-256, digest-to-order,
greedy row-load tie-break, последнюю неполную stratum, canonical membership
ledger, `training_scope_id` для LLM slots и `split_id`, а также executable
assignment/hash golden.

Это exact algorithm, а не предложение implementation. Изменение order,
tie-break, seed derivation либо hash требует version bump. Test `y` не входит
ни в одну операцию split/scaling/search.

Paired OOF group bootstrap:

- ровно 200 resamples по `G` canonical `x`-groups с replacement;
- resample только повторно агрегирует уже frozen paired outer-OOF squared-loss
  contributions всех repetitions; fitting и selection не запускаются;
- occurrence сохраняет multiplicity каждой выбранной group;
- seed и каждый draw выводятся exact counter-based SHA-256 algorithm из
  контракта 03 §8; `bootstrap-resamples.csv.gz` хранит canonical compact JSON
  всех ненулевых `[x_group_id,multiplicity]`, сумма которых равна `G`;
- P2 fallback уже находится в OOF ledger и остаётся в denominator; полный
  fallback имеет uplift `0`;
- `MSE_P1=0` даёт `null/unavailable + ZERO_P1_MSE`; минимум 160 defined
  relative-effect resamples нужен для interval;
- interval является conditional stability summary design composition, а не
  full-selection confidence interval.

Leave-one-`x`-group sensitivity переоценивает только параметры исходных
сертифицированных full-data P1/P2 structures и P2 boundary profile. Registry
selection, validation, bootstrap и recommendation не повторяются; family,
direction, OOF quality и decision deltas не заявляются.

## 5. Numeric и statistical pass bands

Все числа находятся в manifest `numeric-acceptance-v1` и
`simulation-operating-characteristics-v1`; безымянных tolerance в normative
тексте больше нет.

Ключевые frozen engineering значения:

| Проверка | V1 |
|---|---:|
| `LOW_X_RESOLUTION` | `max ulp(x)/span(x) > 10⁻⁶` |
| Collapse по predictions/derivative | оба `≤10⁻⁶` в scaled units |
| Condition warning / weak | `>10⁸` / `>10¹²` или rank deficiency |
| Practical selection tie | relative pooled MSE `≤1%` |
| Join | `10⁻¹⁰·max(1,|μ_scaled|)` |
| Same-platform semantic replay | exact canonical fields и model JSON |
| Cross-platform prediction | max `10⁻⁷` scaled-`y` |
| Cross-platform normalized `c` | max `10⁻⁶` |
| Cross-platform RMSE/MAE relative | max `10⁻⁶` |
| Cross-platform `R²`/uplift absolute | max `10⁻⁶` |

Threshold fixtures обязаны сохранить одну и ту же decision side; tolerance не
создаёт «серую зону» вокруг `0.60`, uplift либо stability gate.

Каждая operating-characteristic cell имеет 200 untouched replicates. P1 на
null/weak truth выбирается минимум в 90%; false P2 не превышает 5%; strong P2
выбирается минимум в 80%, а нижняя Wilson-95% граница не меньше 75%; верхняя
Wilson-95% граница ложного clear-P2 на no-uplift не больше 10%. Для
идентифицируемой P2 median/p90 breakpoint error не превышает 5%/10% span.
Residual/influence FPR/power bands также записаны в manifest и считаются по
всем заранее перечисленным regimes, а не по выбранному после прогона subset.

## 6. Acceptance matrix

| ID | Scope | Главный pass condition |
|---|---|---|
| `DATA-CONTRACT-001` | data/parser | все инварианты 01 и exact-limit/+1 parser oracles |
| `MODEL-CONTRACT-002` | registry/fitting | все core P1 и ordered P2 pairs, certificates, collapse, forbidden AST |
| `STAT-CALIBRATION-003` | validation/diagnostics | untouched operating characteristics, fallback, paired OOF bootstrap, conditional refit LGO |
| `CLI-E2E-004` | requested demo | test CSV → ready function/report → predict → verify |
| `OPS-NET-005` | offline runtime | ноль DNS/IPv4/IPv6 attempts после install |
| `INPUT-SNAPSHOT-006` | TOCTOU/input | один bounded regular snapshot; symlink/special/mutation rejected |
| `ADMISSION-007` | resources | boundary passes, +1/overflow pre-solver reject, actual≤forecast |
| `PUBLICATION-008` | atomicity | race/fault leaves absent or fully verified immutable final |
| `CHECKPOINT-009` | resume | lock/corruption/environment rules; resumed semantic result exact |
| `FAILURE-EXIT-010` | terminal state | exits `0/2/3/4/5/6/70/130`, stdout и conditional bundle exact |
| `VERIFY-011` | integrity | bounded read-only fail-closed mutation/path/schema/gzip checks |
| `PREDICT-012` | ready function | role/hash/certificate/domain/c-boundary evaluator conformance |
| `REPORT-SEC-013` | HTML/SVG/a11y | CSP/XSS, runtime network, reflow, non-color and assistive checks |
| `PRIVACY-014` | sensitive data | allowlisted provenance/logs, private modes, contained links |
| `IDENTITY-015` | hashes | retained canonical payload goldens and anti-alias mutations |
| `REPLAY-016` | determinism | clean/resumed/operational perturbations preserve semantic projection |
| `XPLAT-017` | support matrix | macOS/Linux structural exactness and pinned numeric tolerances |
| `CSVW-018` | exports | complete local typing/units/null/key/identifier round trip |
| `BENCHMARK-019` | measurement | full outer-validation/bootstrap/refit-sensitivity completes; actual≤forecast; wall/RSS retained without SLA |
| `LLM-ADVISOR-020` | optional LLM seam | ChatOpenAI/env/mock endpoint, no leakage, strict starts, secret/network isolation, frozen fallback replay |

Каждая manifest row содержит requirement pointers, fixture IDs, executor,
oracle, tolerance-policy, будущий evidence path и `NOT_RUN` status. Новый gate
нельзя принять устным утверждением или тестом более узкого seam.

## 7. Runtime security and operations

### Parser и immutable input

`10 MiB` считается по raw snapshot bytes вместе с BOM; `10 000` — logical
data records после header, физически пустые линии не считаются, quoted newline
остаётся одной record; `256` — точное число header fields и каждой record;
`64 KiB` — UTF-8 bytes после CSV unquoting до trim. Ровно limit разрешён,
limit+1 даёт `INPUT_LIMIT_EXCEEDED` до solver без усечения.

Adapter принимает только regular non-symlink file. Directory, FIFO, socket,
device и symlink отклоняются. `fstat` identity/size/mtime до и после bounded
read должны совпасть; hash и parser читают один retained byte array.

### Paths, publication и locks

Manifest path grammar, no-follow rules, NFC/case-fold collision protection и
native atomic no-replace primitives заданы в manifest. Ни один symlink в
output parent, staging, workdir либо final tree не допускается. Если
dirfd-anchored `renameat2(...,RENAME_NOREPLACE)`/
`renameatx_np(...,RENAME_EXCL)` недоступен, публикация fail-closed;
path-oriented и overwrite-capable fallback запрещены.

Lock содержит hostname, PID и OS process-start token. Recovery разрешён только
явным флагом на том же host при отсутствии живого процесса с теми же PID/token;
возраст файла сам по себе не доказывает stale. Неустановимый remote owner
остаётся locked.

### Bounded verifier

Verifier использует limits manifest: не более 2,048 artifacts и 8 GiB total,
bounded JSON/schema/model/manifest files, depth/member/item limits, максимум
512 MiB decompressed на gzip, 2 GiB суммарно и expansion ratio 1000. Duplicate
JSON keys, non-finite values, remote `$ref`, unknown versions, extra/missing
file, symlink и path collision отвергаются. До/после verify ни bytes, ни paths,
types, modes или timestamps не меняются.

### Failure bundle boundary

Failure bundle обязателен только после проверки no-follow output parent,
отсутствующего final leaf, возможности создать private staging и наличия
bounded input snapshot либо детерминированного infrastructure state. Минимум и
conditional exports перечислены в manifest. До этой trust boundary exit `3`
может честно не иметь final; failure renderer/publication повышает outcome до
`6/70`, а не оставляет partial report.

### Browser and network

В default advisor-off режиме `run`, `predict`, `verify` после
`uv sync --frozen` исполняются с `--frozen --no-sync` под OS-level deny/trace.
Pass — ноль попыток DNS, AF_INET/AF_INET6. Advisor-enabled `run` отдельно
проверяется hostile local mock: разрешён только configured origin во время
prefit phase, redirects/telemetry/LangSmith запрещены; `predict`/`verify`
всегда zero-network. `file://report.html` проверяется в Chromium 138+ и
Safari/WebKit 17.4+; точная версия сохраняется. Canary server не должен
получить запросов. Runtime проверяет narrow viewport, 400% reflow,
keyboard/focus, screen reader, grayscale/CVD/forced colors и browser print без
заявления tagged-PDF accessibility.

## 8. Production bundle и CSVW

Единственная exact success-tree перечислена в manifest. В отличие от
report-only prototype она добавляет usable model, root schema copies,
compressed OOF, P2 outer-fit and bootstrap ledgers, influence, full provenance,
atomic `manifest.json + COMPLETE`.

`oof-appearances.csv.gz` одновременно является appearance-level predictive
diagnostic export; отдельный неоговорённый `diagnostic-appearances.csv` не
создаётся. `p2-stability-outer-fits.csv.gz` и
`bootstrap-resamples.csv.gz` являются обязательным источником §7 report
statistics и содержат aggregation-only resample outcomes.
`influence-groups.csv` содержит selected-structure refit sensitivity summary.
`trace/llm-advisor.jsonl.gz` присутствует iff advisor включён; свободный ответ,
token и raw endpoint туда не попадают.

`manifest.json` валиден по `manifest.schema.json`, использует поле `artifacts`,
не перечисляет себя/`COMPLETE` и сериализуется canonical compact sorted-key
UTF-8 JSON с одним LF. `COMPLETE` валиден по
`manifest.schema.json#/$defs/complete_marker` и имеет те же canonical byte
rules; его `manifest_sha256` считается по exact manifest bytes.

[CSVW contract v1](../acceptance/csvw-contract-v1.json) закрывает order,
datatype, nullable state, units, key, enum/meaning и identifier encoding для
каждого spreadsheet artifact, включая predictions. Каждая sidecar является
локальной; фиксированный CSVW context token никогда не загружается из сети.

## 9. Goldens и reproducibility

Byte-golden fields: source schemas/policies/registry, canonical hash payloads,
same-environment model JSON и deterministic fixture CSV. Semantic goldens:
status, decision, warnings, metrics, formula AST, intervals, plot coordinates,
DOM source pointers and artifact relationships.

Не являются golden: `report_id`, `generated_at`, elapsed/RSS, absolute paths,
filesystem metadata и gzip header. Их исключение проверяется, а не достигается
игнорированием произвольной diff. Cross-platform exact fields и numeric fields
разделены manifest policy; `model_instance_hash` не является cross-platform
pass condition, но обязан быть exact внутри каждой platform/environment.

## 10. Existing evidence и `NOT RUN`

Уже доказано:

- исследовательская traceability и контракты 01–07;
- fitting seam `PROFILE_CELLS`: exploratory 11/11 gates и два аудита;
- report contract: 15 bundles, 1,021 checks, 0 failures, 560-file frozen tree,
  external schema/XML checks и два независимых final GO.

Не доказано до implementation:

- confirmatory full registry/pair recovery и operating characteristics;
- production model/report schemas и exact bundle publication;
- usable `recommended-model.json`, `predict`, `verify`;
- checkpoint/resume/admission/process races;
- runtime browser/network/screen-reader/forced-colors;
- macOS/Linux replay и full resource benchmark.
- optional LangChain/OpenAI-compatible advisor seam, mock-provider network,
  secret redaction и frozen-ledger replay.

Эти строки остаются `NOT RUN`, а не inheritance от prototype PASS.

## 11. Definition of implementation acceptance

Implementation handoff принимается только когда:

1. clean locked install проходит на обеих reference platforms;
2. отдельный final results aggregate содержит те же 20 gate IDs с retained
   `PASS`, без skips/xfail для mandatory branch и без изменения frozen design
   manifest/oracle после просмотра результата;
3. `examples/demo.csv` через public CLI создаёт verified bundle,
   `TWO_RECOMMENDED`, готовую функцию и отчёт, затем `predict` и `verify`
   проходят offline;
4. все synthetic, metamorphic, hostile и empirical fixture hashes совпадают;
5. два reviewer под разными out-of-band Ed25519 trust roots проверяют
   statistical/model contract и security/operations contract на одном
   source/lock/evidence digest; custodian independence подтверждается внешней
   acceptance-процедурой;
6. документация не называет demonstrator production-ready до выполнения всех
   пунктов выше.

## 12. Явные non-claims

За пределами v1 остаются Windows, offline installation до `uv sync`, latency
SLA, parallel jobs, Web/API, bundle encryption/redaction/authenticity signature, power-loss fsync
guarantee, cross-version resume, tagged PDF, extrapolation, XLSX/TSV/stdin/URL,
weights/uncertainties, несколько `y` и третий сегмент. Эти ограничения не
скрывают обязательный MVP: один локальный CSV должен завершать полный
production pipeline и выдавать проверяемую функцию и отчёт.

Также не заявляются качество/доступность/подлинность внешнего LLM provider,
bit-identical fresh responses, LLM-generated executable formulas/AST/families
или автоматическое расширение registry. Надёжная offline функция остаётся
обязательной при advisor=`off` или полном advisor fallback.
