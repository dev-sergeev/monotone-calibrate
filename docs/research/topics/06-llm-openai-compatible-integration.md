# RQ6 — опциональный LLM start-advisor через OpenAI-compatible API

**Дата исполнения и отсечения:** 2026-07-16, Europe/Moscow  
**Область:** Python `langchain-openai` / `ChatOpenAI`, настраиваемый OpenAI-compatible Chat Completions endpoint  
**Источники:** только официальная документация и исходный код LangChain/OpenAI; вторичные обзоры не использованы.

## Решение

LLM не должна входить в доверенное вычислительное ядро и не нужна для корректности метода. Её допустимая роль — **необязательный start-advisor**, который заменяет только заранее помеченные replaceable slots, сохраняя обязательные anchor starts и общий численный budget. Семейства функций, численная оптимизация, сертификаты монотонности, `R²`, uplift и итоговая рекомендация остаются локальными и проверяемыми. Таймаут, HTTP/API-ошибка, невалидный JSON или нарушение схемы дают warning и возврат исходного deterministic start, но не останавливают калибровку.

Это соответствует области применимости `ChatOpenAI`: LangChain разрешает custom `base_url` для **basic chat functionality**, но предупреждает, что класс ориентирован на официальную схему OpenAI и не сохраняет нестандартные поля сторонних провайдеров ([LangChain: Chat model integrations](https://docs.langchain.com/oss/python/integrations/chat)).

## 1. Точный контракт переменных окружения

Проект использует собственный префикс и явно передаёт значения в `ChatOpenAI`; это исключает неявное смешение с ambient `OPENAI_*` другого приложения.

```dotenv
MONOTONE_CALIBRATE_LLM_ENABLED=false
MONOTONE_CALIBRATE_LLM_MODEL=
MONOTONE_CALIBRATE_LLM_BASE_URL=
MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN=
MONOTONE_CALIBRATE_LLM_TIMEOUT_SECONDS=20
MONOTONE_CALIBRATE_LLM_MAX_RETRIES=1
MONOTONE_CALIBRATE_LLM_ALLOW_INSECURE_HTTP=false
```

| Переменная | Контракт |
|---|---|
| `MONOTONE_CALIBRATE_LLM_ENABLED` | Только `true` или `false`, default `false`; CLI `--llm-start-advisor` также включает режим. |
| `MONOTONE_CALIBRATE_LLM_MODEL` | Непустая строка, обязательна при `true`; всегда передаётся как `model`, без библиотечного default. |
| `MONOTONE_CALIBRATE_LLM_BASE_URL` | Непустой абсолютный API root, обычно `https://host/v1`, а не полный `/chat/completions`; обязателен при `true`. Запрещены userinfo, query и fragment. Разрешён `https`; `http` — literal loopback либо отдельный explicit insecure opt-in. |
| `MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN` | Непустой raw bearer credential, обязательный при `true`; префикс `Bearer ` пользователь **не** добавляет. |
| `MONOTONE_CALIBRATE_LLM_TIMEOUT_SECONDS` | Целое число `1..120`, default `20`. |
| `MONOTONE_CALIBRATE_LLM_MAX_RETRIES` | Целое число `0..3`, default `1`; это один повтор после первой попытки. |
| `MONOTONE_CALIBRATE_LLM_ALLOW_INSECURE_HTTP` | Только `true|false`, default `false`; разрешает non-loopback HTTP как явный операторский риск. |

Если enabled-конфигурация неполна или невалидна, приложение не должно подставлять `https://api.openai.com/v1` или случайный model default: оно не выполняет сетевой вызов, пишет `LLM_ADVISOR_CONFIG_INVALID` без секрета и продолжает с детерминированными стартами.

`ChatOpenAI` действительно принимает `model`, `api_key`, `base_url`, `timeout` и `max_retries`; `api_key` внутри модели имеет тип `SecretStr`, а `base_url` можно передать явно ([документация интеграции](https://docs.langchain.com/oss/python/integrations/chat/openai), [зафиксированный исходник LangChain](https://github.com/langchain-ai/langchain/blob/98216c0c1d7d2dc13e3ebeac36329853a5cb52a0/libs/partners/openai/langchain_openai/chat_models/base.py#L640-L751)). OpenAI API принимает API key или access token как Bearer credential, а официальный Python SDK сам строит `Authorization: Bearer <api_key>` ([OpenAI API authentication](https://developers.openai.com/api/reference/overview#authentication), [исходник SDK](https://github.com/openai/openai-python/blob/f16fbbd2bd25dc1ff150b5f78dbd15ff6bab6d91/src/openai/_client.py#L497-L513)). Поэтому в `MONOTONE_CALIBRATE_LLM_ACCESS_TOKEN` хранится токен без префикса.

Рекомендуемое явное создание клиента:

```python
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

llm = ChatOpenAI(
    model=config.model,
    base_url=config.base_url,
    api_key=SecretStr(config.access_token),
    timeout=config.timeout_seconds,
    max_retries=config.max_retries,
    use_responses_api=False,
    streaming=False,
    stream_usage=False,
)
```

Параметры нельзя делать runtime-configurable из CSV, HTTP payload или ответа модели. В частности, LangChain предупреждает, что unrestricted runtime configuration может менять `api_key` и `base_url` и перенаправлять запросы на другой сервис ([security note для `init_chat_model`](https://reference.langchain.com/python/langchain/chat_models/base/init_chat_model)).

## 2. Минимальная совместимость endpoint

Версия v1 опирается только на базовый Chat Completions-контракт:

- SDK отправляет `POST <base_url>/chat/completions` с `model` и `messages`;
- credential передаётся HTTP Bearer authentication;
- ответ содержит хотя бы `choices[0].message.content`;
- вызов не требует Responses API, streaming usage, tools/function calling, provider-specific reasoning fields или server-side storage.

Официальная OpenAI reference определяет `POST /chat/completions` и ответ с `choices[].message` ([Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)). `use_responses_api=False`, `streaming=False` и `stream_usage=False` задаются явно, чтобы LangChain не включил возможность, которую совместимый сервер мог не реализовать. Если нужны нестандартные поля провайдера, следует использовать его отдельную LangChain-интеграцию, а не расширять доверенную поверхность `ChatOpenAI`.

## 3. Structured output: только локальная гарантия

`ChatOpenAI.with_structured_output` умеет три разных протокола: `json_schema`, `function_calling` и `json_mode` ([LangChain reference](https://reference.langchain.com/python/langchain-openai/chat_models/base/ChatOpenAI/with_structured_output)). Они требуют разных server capabilities. OpenAI Structured Outputs обеспечивает соответствие поддерживаемой JSON Schema, тогда как JSON mode гарантирует лишь валидный JSON, но не конкретную схему ([OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs#structured-outputs-vs-json-mode)). Сторонний endpoint, заявляющий только Chat Completions compatibility, не обязан реализовать ни одну из этих надстроек.

Поэтому baseline v1:

1. вызывает обычный `llm.invoke(messages)` без `response_format` и tools;
2. просит вернуть один JSON object без Markdown;
3. ограничивает размер принятого текста;
4. разбирает только `json.loads`, никогда `eval`, `ast.literal_eval` или исполняемую формулу;
5. валидирует результат локальной Pydantic/JSON Schema моделью в strict mode с `extra="forbid"`;
6. при любой ошибке отбрасывает весь ответ без repair-loop и включает fallback.

Если позднее конкретный провайдер будет аттестован на native structured output, режим должен включаться отдельной versioned capability policy и всегда указывать `method="json_schema"` явно. Автоматически угадывать поддержку или зависеть от меняющегося library default нельзя.

## 4. Allowlisted schema советов

Ответ модели содержит только данные для старта, например:

```json
{
  "schema_version": "llm-start-advice-v1",
  "suggestions": [
    {
      "slot_id": "<schema-known-replaceable-slot-id>",
      "parameter_vector": [0.1, 0.5]
    }
  ]
}
```

Локальная проверка обязана обеспечить:

- не более числа объявленных replaceable slots в fit scope;
- каждый `slot_id` существует в predeclared scope request и встречается один раз;
- точную длину `parameter_vector` по registry slot schema, только конечные числа и только registry bounds;
- отсутствие дополнительных полей, свободного Python-кода, произвольных функций, выражений и файловых/сетевых инструкций.

LLM не может добавлять новое семейство, повышать степень полинома выше 3, менять budget, отбрасывать anchor starts или сертифицировать найденную модель. Принятый vector заменяет только соответствующий deterministic replaceable slot; после этого работает тот же локальный fitter и те же сертификаты.

## 5. Validation boundary и воспроизводимость

Главный инвариант: **LLM не видит ни одной строки, held out для метрики, на которую её совет может повлиять**.

- В outer fold ей доступны только outer-training rows или их детерминированное нормализованное представление; row IDs и заведомо лишние поля не отправляются.
- В v1 inner fit scopes отсутствуют: registry/budgets/thresholds заранее
  заморожены, а полный selector переоценивается на каждом outer train.
- Полный final refit после завершения оценки может использовать все принятые строки, но не меняет уже зафиксированную OOF-оценку.

Для replay сохраняются точные **принятые и нормализованные** numeric suggestions, version/hash prompt и schema, training-scope hash, model string, package versions, status и rejection codes. Access token, полный raw prompt/response и текст исключения в bundle не попадают; вместо raw ответа достаточно SHA-256. Повторное воспроизведение использует сохранённые старты без нового LLM-вызова.

Качество LLM не предполагается заранее. Её польза доказывается отдельной ablation-проверкой `deterministic starts` против `anchor + LLM-substituted slots` при строго одинаковом числе starts/evaluations. До такого результата LLM остаётся экспериментальным средством basin discovery, а не основанием доверять модели.

## 6. Таймауты, retries и fallback

Underlying OpenAI Python SDK по умолчанию ждёт до 10 минут и повторяет ряд connection/408/409/429/5xx ошибок два раза; оба значения настраиваются ([официальный SDK: retries](https://github.com/openai/openai-python/blob/f16fbbd2bd25dc1ff150b5f78dbd15ff6bab6d91/README.md#retries), [timeouts](https://github.com/openai/openai-python/blob/f16fbbd2bd25dc1ff150b5f78dbd15ff6bab6d91/README.md#timeouts)). Для fit loop это слишком долго, поэтому фиксируются `20 s` и один retry. Дополнительный внешний retry-layer не добавляется.

Нормальные fallback-события:

- timeout, DNS/TLS/connection failure;
- HTTP authentication, rate-limit или server error;
- отсутствующий/неподдерживаемый model;
- пустой или слишком большой content;
- invalid JSON, schema/cross-field violation, NaN/Infinity, unknown family;
- отказ модели.

Во всех случаях: sanitized warning `LLM_ADVISOR_UNAVAILABLE`, `accepted_suggestions=0`, запуск обязательных deterministic starts и продолжение анализа. Parsing/schema failures не ретраятся: transport retry не должен превращаться в скрытый prompt-repair agent.

## 7. Секреты и сетевой контур

OpenAI рекомендует не коммитить ключ, не отдавать его browser/mobile client и хранить в environment variable или secret manager ([API key safety](https://help.openai.com/en/articles/5112595-best-practices-for-api-key-safety)). Практический контракт проекта:

- `.env.example` содержит только пустые placeholders; рабочий `.env` игнорируется VCS и не публикуется;
- token не выводится в stdout/stderr, report, manifest, cache key, telemetry и exception text;
- `base_url` задаётся только доверенным оператором при старте; ввод из CSV не может перенаправить credential;
- URL с embedded credentials запрещён; production требует TLS, кроме явно локального loopback;
- LangSmith tracing и verbose OpenAI logging по умолчанию выключены, потому что prompt содержит данные fit scope;
- в публичной provenance вместо полного URL сохраняются capability-policy ID и hash нормализованного URL; секрет никогда не хешируется и не сохраняется;
- при подозрении на раскрытие token ротируется вне приложения.

Итог: такая интеграция удобна для пользователя с любым базово OpenAI-compatible сервером, но не меняет математический trust boundary проекта. Доступность или качество LLM влияет только на replaceable starts и всегда наблюдаемо в отчёте.
