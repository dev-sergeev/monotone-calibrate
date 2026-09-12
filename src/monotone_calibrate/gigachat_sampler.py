"""GigaChat transport for the shared, locally validated formula grammar."""

from __future__ import annotations

import json
import math
import ssl

import httpx
from gigachat.exceptions import LengthFinishReasonError, ResponseError
from langchain_core.callbacks import CallbackManager
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_gigachat import GigaChat
from langsmith import tracing_context

from .config import GIGACHAT_BASE_URL, LLMConfig
from .expressions import FormulaHypothesis
from .formula_search import FormulaProviderError, SYSTEM_PROMPT, parse_formulas


class GigaChatFormulaSampler:
    """Use LangChain only for generation; coefficients are always fitted locally."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        # GigaChat reports tokens, not monetary cost. Do not invent a zero cost.
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self.http_attempts = 0
        self.rejections: list[str] = []

    def _model(self) -> GigaChat:
        config = self.config
        # The SDK drops None before reading GIGACHAT_* environment aliases.
        # Empty optional strings explicitly suppress ambient credentials/paths.
        context = None
        if config.gigachat_verify_ssl_certs:
            context = ssl.create_default_context(cafile=config.gigachat_ca_bundle_file)
        return GigaChat(
            model=config.model,
            base_url=config.base_url or GIGACHAT_BASE_URL,
            auth_url=config.gigachat_auth_url,
            credentials=config.gigachat_credentials or "",
            access_token=config.gigachat_access_token or "",
            scope=config.gigachat_scope,
            user="",
            password="",
            cert_file="",
            key_file="",
            key_file_password="",
            ca_bundle_file=config.gigachat_ca_bundle_file or "",
            ssl_context=context,
            verify_ssl_certs=config.gigachat_verify_ssl_certs,
            timeout=config.timeout_seconds,
            # Retries are counted and bounded in this adapter, not in two layers.
            max_retries=0,
            max_connections=1,
            flags=[],
            temperature=0.8,
            max_tokens=config.max_output_tokens,
            reasoning_effort=config.reasoning_effort,
            streaming=False,
            cache=False,
            verbose=False,
            callbacks=[],
        )

    def sample(self, request: dict) -> tuple[FormulaHypothesis, ...]:
        self.rejections = []
        config = self.config
        if (
            not config.enabled
            or not config.model
            or not (config.gigachat_credentials or config.gigachat_access_token)
        ):
            raise FormulaProviderError("LLM_CONFIG_MISSING")
        model = None
        try:
            model = self._model()
            messages = [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(request, allow_nan=False)),
            ]
            for attempt in range(config.max_retries + 1):
                self.http_attempts += 1
                try:
                    # Never enable LangSmith exports through ambient tracing flags.
                    with tracing_context(enabled=False):
                        response = model.invoke(
                            messages, config={"callbacks": CallbackManager([])}
                        )
                    break
                except ResponseError as error:
                    if (
                        error.status_code in {429, 500, 502, 503, 504}
                        and attempt < config.max_retries
                    ):
                        continue
                    raise FormulaProviderError(
                        f"LLM_HTTP_{error.status_code}"
                    ) from None
                except httpx.HTTPError:
                    if attempt == config.max_retries:
                        raise FormulaProviderError("LLM_TRANSPORT_FAILURE") from None
            usage = response.usage_metadata or {}
            for key, source in (
                ("prompt_tokens", "input_tokens"),
                ("completion_tokens", "output_tokens"),
            ):
                value = usage.get(source, 0)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    and value >= 0
                ):
                    self.usage[key] += value
            if response.response_metadata.get("finish_reason") == "length":
                raise FormulaProviderError("LLM_OUTPUT_TRUNCATED")
            content = response.content
            if isinstance(content, str) and len(content) > 65536:
                raise FormulaProviderError("LLM_RESPONSE_SIZE")
            return parse_formulas(content, rejections=self.rejections)
        except FormulaProviderError:
            raise
        except LengthFinishReasonError:
            raise FormulaProviderError("LLM_OUTPUT_TRUNCATED") from None
        except ValueError as error:
            if str(error) in {
                "FORMULA_ALREADY_IN_REGISTRY",
                "POLYNOMIAL_DEGREE_LIMIT",
                "TRANSCENDENTAL_NESTING",
                "FORMULA_COMPLEXITY",
                "EXPRESSION_COMPLEXITY",
            }:
                raise FormulaProviderError("LLM_" + str(error)) from None
            raise FormulaProviderError("LLM_INVALID_FORMULA_RESPONSE") from None
        except Exception:
            # SDK errors may embed request bodies, headers or credentials.
            raise FormulaProviderError("LLM_PROVIDER_FAILURE") from None
        finally:
            # langchain-gigachat 0.5 exposes no close method of its own.
            if model is not None and "_client" in model.__dict__:
                model._client.close()
