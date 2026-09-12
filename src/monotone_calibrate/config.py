"""Configuration for optional OpenAI-compatible or GigaChat formula search.

Only the explicitly documented ``MONOTONE_CALIBRATE_LLM_*`` names are read.
The mathematical pipeline can therefore construct the default configuration
without consulting any OpenAI or LangChain ambient aliases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import ip_address
import os
from os import PathLike
from typing import Mapping
from urllib.parse import urlsplit

from dotenv import dotenv_values


_PREFIX = "MONOTONE_CALIBRATE_LLM_"
GIGACHAT_BASE_URL = "https://api.giga.chat/v1"
GIGACHAT_AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"


class LLMConfigError(ValueError):
    """A typed, secret-free configuration error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class LLMConfig:
    """Resolved settings for the typed symbolic-search adapter."""

    enabled: bool = False
    model: str | None = None
    base_url: str | None = None
    access_token: str | None = field(default=None, repr=False)
    timeout_seconds: int = 20
    max_retries: int = 1
    search_iterations: int = 4
    allow_insecure_http: bool = False
    max_output_tokens: int = 4096
    reasoning_effort: str | None = None
    provider: str = "openai"
    gigachat_credentials: str | None = field(default=None, repr=False)
    gigachat_access_token: str | None = field(default=None, repr=False)
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_auth_url: str = GIGACHAT_AUTH_URL
    gigachat_verify_ssl_certs: bool = True
    gigachat_ca_bundle_file: str | None = None

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, str | None],
        *,
        force_enable: bool = False,
    ) -> LLMConfig:
        """Resolve only the documented names from an environment-like map."""

        configured_enabled = _exact_bool(
            values.get(f"{_PREFIX}ENABLED"),
            name=f"{_PREFIX}ENABLED",
            default=False,
        )
        enabled = force_enable or configured_enabled
        provider = _optional_text(values.get(f"{_PREFIX}PROVIDER")) or "openai"
        if provider not in {"openai", "gigachat"}:
            raise LLMConfigError(
                "LLM_CONFIG_PROVIDER", "provider must be openai or gigachat"
            )
        allow_insecure = _exact_bool(
            values.get(f"{_PREFIX}ALLOW_INSECURE_HTTP"),
            name=f"{_PREFIX}ALLOW_INSECURE_HTTP",
            default=False,
        )
        timeout = _bounded_int(
            values.get(f"{_PREFIX}TIMEOUT_SECONDS"),
            name=f"{_PREFIX}TIMEOUT_SECONDS",
            default=20,
            minimum=1,
            maximum=120,
        )
        retries = _bounded_int(
            values.get(f"{_PREFIX}MAX_RETRIES"),
            name=f"{_PREFIX}MAX_RETRIES",
            default=1,
            minimum=0,
            maximum=3,
        )
        search_iterations = _bounded_int(
            values.get(f"{_PREFIX}SEARCH_ITERATIONS"),
            name=f"{_PREFIX}SEARCH_ITERATIONS",
            default=4,
            minimum=1,
            maximum=64,
        )
        max_output_tokens = _bounded_int(
            values.get(f"{_PREFIX}MAX_OUTPUT_TOKENS"),
            name=f"{_PREFIX}MAX_OUTPUT_TOKENS",
            default=4096,
            minimum=512,
            maximum=32768,
        )
        reasoning_effort = _optional_text(values.get(f"{_PREFIX}REASONING_EFFORT"))
        if reasoning_effort not in {
            None,
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            raise LLMConfigError("LLM_CONFIG_REASONING", "unsupported reasoning effort")

        model = _optional_text(values.get(f"{_PREFIX}MODEL"))
        base_url = _optional_text(values.get(f"{_PREFIX}BASE_URL"))
        token = values.get(f"{_PREFIX}ACCESS_TOKEN")
        if token is not None and not token.strip():
            token = None
        credentials = gigachat_token = ca_bundle = None
        scope = "GIGACHAT_API_PERS"
        auth_url = GIGACHAT_AUTH_URL
        verify_ssl = True
        if provider == "gigachat":
            # Separate credentials prevent accidentally forwarding an OpenRouter key.
            token = None
            credentials = _optional_text(values.get(f"{_PREFIX}GIGACHAT_CREDENTIALS"))
            gigachat_token = _optional_text(
                values.get(f"{_PREFIX}GIGACHAT_ACCESS_TOKEN")
            )
            if credentials and gigachat_token:
                raise LLMConfigError(
                    "LLM_CONFIG_AUTH",
                    "choose GigaChat credentials or access token, not both",
                )
            base_url = (
                _optional_text(values.get(f"{_PREFIX}GIGACHAT_BASE_URL"))
                or GIGACHAT_BASE_URL
            )
            auth_url = (
                _optional_text(values.get(f"{_PREFIX}GIGACHAT_AUTH_URL"))
                or GIGACHAT_AUTH_URL
            )
            scope = _optional_text(values.get(f"{_PREFIX}GIGACHAT_SCOPE")) or scope
            if scope not in {
                "GIGACHAT_API_PERS",
                "GIGACHAT_API_B2B",
                "GIGACHAT_API_CORP",
            }:
                raise LLMConfigError("LLM_CONFIG_SCOPE", "unsupported GigaChat scope")
            verify_ssl = _exact_bool(
                values.get(f"{_PREFIX}GIGACHAT_VERIFY_SSL_CERTS"),
                name=f"{_PREFIX}GIGACHAT_VERIFY_SSL_CERTS",
                default=True,
            )
            ca_bundle = _optional_text(values.get(f"{_PREFIX}GIGACHAT_CA_BUNDLE_FILE"))
            if ca_bundle and not verify_ssl:
                raise LLMConfigError(
                    "LLM_CONFIG_TLS", "CA bundle requires TLS verification"
                )
            _validate_base_url(auth_url, allow_insecure_http=allow_insecure)
        if base_url is not None:
            _validate_base_url(base_url, allow_insecure_http=allow_insecure)

        if enabled:
            missing = tuple(
                name
                for name, value in (
                    (f"{_PREFIX}MODEL", model),
                    (f"{_PREFIX}BASE_URL", base_url),
                    (
                        (
                            f"{_PREFIX}GIGACHAT_CREDENTIALS or {_PREFIX}GIGACHAT_ACCESS_TOKEN"
                        )
                        if provider == "gigachat"
                        else f"{_PREFIX}ACCESS_TOKEN",
                        (credentials or gigachat_token)
                        if provider == "gigachat"
                        else token,
                    ),
                )
                if value is None
            )
            if missing:
                raise LLMConfigError(
                    "LLM_CONFIG_MISSING",
                    "enabled LLM symbolic search requires: " + ", ".join(missing),
                )

        return cls(
            enabled=enabled,
            model=model,
            base_url=base_url,
            access_token=token,
            timeout_seconds=timeout,
            max_retries=retries,
            search_iterations=search_iterations,
            allow_insecure_http=allow_insecure,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            provider=provider,
            gigachat_credentials=credentials,
            gigachat_access_token=gigachat_token,
            gigachat_scope=scope,
            gigachat_auth_url=auth_url,
            gigachat_verify_ssl_certs=verify_ssl,
            gigachat_ca_bundle_file=ca_bundle,
        )

    @classmethod
    def load(
        cls,
        dotenv_path: str | PathLike[str] | None = ".env",
        *,
        environ: Mapping[str, str] | None = None,
        force_enable: bool = False,
    ) -> LLMConfig:
        """Load a local dotenv file, then overlay the process environment.

        Unknown and ambient OpenAI/LangChain variables are discarded before
        resolution. Passing ``environ`` makes this method deterministic in
        tests and embedded callers.
        """

        merged: dict[str, str | None] = {}
        if dotenv_path is not None:
            merged.update(
                (key, value)
                for key, value in dotenv_values(dotenv_path, interpolate=False).items()
                if key.startswith(_PREFIX)
            )
        source = os.environ if environ is None else environ
        merged.update(
            (key, value) for key, value in source.items() if key.startswith(_PREFIX)
        )
        return cls.from_mapping(merged, force_enable=force_enable)


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _validate_base_url(value: str, *, allow_insecure_http: bool) -> None:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        # Accessing port also validates malformed/out-of-range ports.
        parsed.port
    except ValueError as exc:
        raise LLMConfigError("LLM_CONFIG_BASE_URL", "LLM base URL is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise LLMConfigError(
            "LLM_CONFIG_BASE_URL",
            "LLM base URL must be an absolute HTTP(S) endpoint without credentials, query, or fragment",
        )
    if parsed.scheme == "http" and not (allow_insecure_http or _is_loopback_host(host)):
        raise LLMConfigError(
            "LLM_CONFIG_INSECURE_HTTP",
            "non-loopback HTTP requires MONOTONE_CALIBRATE_LLM_ALLOW_INSECURE_HTTP=true",
        )


def _is_loopback_host(host: str) -> bool:
    normalized = host.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _exact_bool(value: str | None, *, name: str, default: bool) -> bool:
    if value is None or value == "":
        return default
    if value == "true":
        return True
    if value == "false":
        return False
    raise LLMConfigError("LLM_CONFIG_BOOLEAN", f"{name} must be exactly true or false")


def _bounded_int(
    value: str | None,
    *,
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise LLMConfigError(
            "LLM_CONFIG_INTEGER", f"{name} must be an integer"
        ) from exc
    if str(parsed) != value and value != f"+{parsed}":
        raise LLMConfigError("LLM_CONFIG_INTEGER", f"{name} must be an integer")
    if not minimum <= parsed <= maximum:
        raise LLMConfigError(
            "LLM_CONFIG_RANGE",
            f"{name} must be in the inclusive range {minimum}..{maximum}",
        )
    return parsed
