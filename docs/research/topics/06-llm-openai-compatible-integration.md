# RQ6 — LLM-SR selector через OpenAI-compatible API

**Исходная дата:** 2026-07-16.
**Актуализация LLM-SR:** 2026-07-22, Europe/Moscow.
**Актуализация training summary:** 2026-07-28, Europe/Moscow.
**Область:** `langchain-openai` / `ChatOpenAI`, внешний
OpenAI-compatible Chat Completions endpoint и локальный typed fitting runtime.

## Источники

- [LLM-SR: Scientific Equation Discovery via Programming with Large Language Models](https://arxiv.org/abs/2404.18400);
- [официальная реализация LLM-SR](https://github.com/deep-symbolic-mathematics/LLM-SR),
  изученный commit `41c212312df6c16d936c9cb395356a62774c47e3`;
- [LangChain: ChatOpenAI integration](https://docs.langchain.com/oss/python/integrations/chat/openai);
- [OpenAI Chat API reference](https://developers.openai.com/api/reference/resources/chat).

Подробное сопоставление статьи с кодом проекта находится в
[`../../llm-sr-algorithm.md`](../../llm-sr-algorithm.md).

## Решение

LLM является недоверенным генератором **дискретных typed skeletons**, а не
источником готовой формулы или численных коэффициентов. Она может назвать
только:

- одно семейство из `registry-v1` для P1;
- одну ordered-пару семейств из `registry-v1` для P2.

Коэффициенты, направление, breakpoint, непрерывное сопряжение, quantization,
fitness, сертификаты, validation и рекомендация вычисляются локально. Этот
seam сохраняет ключевое разделение LLM-SR «program skeleton + numerical
optimizer», не исполняя generated Python из внешнего ответа.

При выключенном selector-е или любом полном fallback приложение перебирает
весь реестр и остаётся локальным детерминированным инструментом.

## 1. Конфигурация

Приложение читает только собственный префикс
`MONOTONE_CALIBRATE_LLM_*`; ambient `OPENAI_API_KEY` и `OPENAI_BASE_URL` не
подменяют настройки.

```dotenv
MONOTONE_CALIBRATE_LLM_ENABLED=false
MONOTONE_CALIBRATE_LLM_MODEL=my-model-id
MONOTONE_CALIBRATE_LLM_BASE_URL=https://provider.example/v1
MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN=your-access-token
MONOTONE_CALIBRATE_LLM_TIMEOUT_SECONDS=20
MONOTONE_CALIBRATE_LLM_MAX_RETRIES=1
MONOTONE_CALIBRATE_LLM_SEARCH_ITERATIONS=4
MONOTONE_CALIBRATE_LLM_ALLOW_INSECURE_HTTP=false
```

| Переменная | Текущий контракт |
|---|---|
| `MONOTONE_CALIBRATE_LLM_ENABLED` | exact `true|false`, default `false` |
| `MONOTONE_CALIBRATE_LLM_MODEL` | обязательный непустой model/deployment ID при enabled |
| `MONOTONE_CALIBRATE_LLM_BASE_URL` | абсолютный URL без credentials/query/fragment; HTTPS либо loopback HTTP |
| `MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN` | обязательный raw credential без префикса `Bearer ` |
| `MONOTONE_CALIBRATE_LLM_TIMEOUT_SECONDS` | integer `1..120`, default `20` |
| `MONOTONE_CALIBRATE_LLM_MAX_RETRIES` | integer `0..3`, default `1` |
| `MONOTONE_CALIBRATE_LLM_SEARCH_ITERATIONS` | integer `1..64`, default `4` |
| `MONOTONE_CALIBRATE_LLM_ALLOW_INSECURE_HTTP` | разрешает non-loopback HTTP как явный opt-in |

CLI `--llm-symbolic-search` force-enables selector и поэтому также требует три
обязательных provider-поля. `--llm-start-advisor` — только compatibility alias
того же LLM-SR режима. Если данные находятся в `.env`, `--no-dotenv` указывать
нельзя.

Неполная/невалидная конфигурация не вызывает provider call. Run продолжается
на полном реестре со статусом `CONFIG_INVALID` и typed `LLM_CONFIG_*` warning.

## 2. Минимальный endpoint contract

Клиент создаётся явно:

```python
ChatOpenAI(
    model=config.model,
    base_url=config.base_url,
    api_key=config.access_token,
    temperature=0.8,
    timeout=config.timeout_seconds,
    max_retries=config.max_retries,
    streaming=False,
    stream_usage=False,
    disable_streaming=True,
    use_responses_api=False,
)
```

Используется только обычный non-streaming message invocation. Tools,
function-calling, server-side structured output, files, conversation state и
provider-specific `extra_body` не требуются. Ответ должен предоставлять
строковый `message.content`.

LangChain прямо указывает, что `ChatOpenAI` ориентирован на официальную OpenAI
API specification и не обязан сохранять нестандартные response fields сторонних
провайдеров. Проект намеренно использует только минимальное текстовое
пересечение протоколов; compatibility конкретного endpoint должна проверяться
отдельно, а provider-specific reasoning/tool fields игнорируются.

Входной `base_url` валидируется локально, но текущий код не устанавливает
собственный HTTP transport с redirect allowlist. Поэтому формальная гарантия
«ни одного redirect за пределы configured origin» пока не доказана и остаётся
за пределами текущего security claim.

Приложение не настраивает LangSmith callbacks, однако также не отклоняет
ambient `LANGSMITH_TRACING`/`LANGCHAIN_TRACING_V2`. Оператор обязан оставить их
выключенными. Явный fail-closed tracing guard — будущий hardening, а не
свойство текущей реализации.

## 3. Что отправляется модели

На каждой итерации prompt содержит:

- generic problem specification для одной конечной координаты `x` и цели `y`;
- агрегированный training summary максимум из 200 deterministic `x`-bins;
- список разрешённых registry families и их program skeletons;
- локальную evaluation/certificate policy;
- до двух scored experience examples из выбранного island.

`row_id`, невалидные raw fields, пути, access token и provider URL в prompt не
входят. Но summary содержит числовые диапазоны и агрегаты исходной кривой;
поэтому включение LLM является явным раскрытием данных и маркируется
`LLM_TRAINING_SUMMARY_DISCLOSED`.

## 4. Закрытый output contract

Текущая schema:
[`../../specification/llm-sr-hypotheses.schema.json`](../../specification/llm-sr-hypotheses.schema.json).

```json
{
  "schema_version": "llm-sr-hypotheses-v1",
  "hypotheses": [
    {"structure": "P1", "family_ids": ["logistic_v1"]},
    {
      "structure": "P2",
      "family_ids": ["poly1_v1", "poly2_v1"]
    }
  ]
}
```

Parser отклоняет весь ответ при любом из условий:

- prose/Markdown вместо одного JSON object;
- duplicate object key, `NaN`/`Infinity`, лишнее или пропущенное поле;
- пустой batch, batch больше request maximum;
- unknown family, неверная длина `family_ids`, duplicate hypothesis;
- P2 `constant_v1/constant_v1`;
- `code`, `formula`, coefficient или иной внешний payload.

JSON Schema документирует форму, но runtime parser дополнительно обеспечивает
duplicate-key, non-finite и canonical-ID проверки. Ни `eval`, ни dynamic
import, ни generated code в активном пути нет.

## 5. Search и local evaluation

Адаптация использует параметры статьи:

- `10` islands;
- `4` skeletons на prompt;
- generation temperature `0.8`;
- `2` in-context experiences;
- fitness `-MSE`;
- Boltzmann cluster sampling с `T₀=0.1`, period `10 000`;
- предпочтение короткого skeleton внутри score cluster.

Каждая typed гипотеза отдельно fit-ится существующим solver-ом. В buffer
попадает только кандидат с конечным score после quantization и certificate.
Все валидные score-clusters сохраняются для diversity; best score используется
для ранжирования/reset islands. Numerical evaluation кэшируется по canonical
hypothesis ID.

Product default — четыре provider calls, а не paper-scale 2.5K iterations.
Слабая половина islands перезапускается по детерминированному iteration period,
если configured budget до него доходит.

## 6. Validation boundary

Selector один раз использует full-data summary и full-data fitness, после чего
portfolio замораживается. В каждом grouped outer fold заново выполняются
локальный fit параметров, breakpoint и выбор лучшего кандидата только внутри
этого portfolio.

Следствие: outer-test `y` не участвует в fold fit, но full-data `y` уже повлиял
на состав portfolio. Поэтому OOF является **conditional validation**, а не
untouched оценкой всей discovery procedure. Успешный поиск всегда получает
`LLM_SR_PORTFOLIO_CONDITIONAL_VALIDATION`.

Полностью nested LLM-SR потребовал бы отдельного provider search в каждом
outer-training scope и оставлен будущей явно бюджетируемой политикой.

## 7. Fallback и provenance

Полный deterministic registry используется при:

- disabled или invalid configuration;
- timeout/transport/provider failure;
- malformed/untrusted response;
- отсутствии хотя бы одной новой валидной гипотезы;
- неожиданной ошибке search stage.

Fallback атомарный: случайный частичный portfolio не подменяет полный реестр.
Provider exception text не попадает в report.

Исторически совместимое поле `report.json.llm_advisor` хранит только status,
версии, counts, booleans и SHA-256 hashes. Access token, raw endpoint, prompts,
responses и provider diagnostics не сериализуются.

## 8. Non-claims

Текущая интеграция не заявляет:

- качество, доступность или подлинность внешнего provider-а;
- bit-identical fresh LLM responses;
- произвольную symbolic grammar за пределами `registry-v1`;
- независимую validation full-data discovery;
- redirect/telemetry isolation уровня formal security gate;
- подписанное cross-platform release acceptance.

Исторический numerical start-advisor удалён из application flow. Его схема,
live-run и frozen acceptance v1 сохранены только как архив; граница описана в
[`../../acceptance/README.md`](../../acceptance/README.md).
