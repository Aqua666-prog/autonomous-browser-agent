import asyncio
import json
import ssl
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

try:
    from openai import AsyncOpenAI
except ImportError:  # Keep browser/core tests importable without provider extras.
    AsyncOpenAI = None


@dataclass
class Decision:
    name: str
    arguments: str


class ProviderError(Exception):
    """Safe, user-facing LLM provider failure with no secret response data."""


class MalformedDecision(Exception):
    pass


class LLMProvider(Protocol):
    async def decide(self, messages: list[dict], tools: list[dict]) -> Decision: ...
    async def close(self): ...


def _provider_error(exc: Exception, provider_name: str) -> ProviderError:
    """Map SDK/network errors to safe diagnostics without exposing body/headers."""
    status = getattr(exc, 'status_code', None)
    body = getattr(exc, 'body', None)
    code = str(body.get('code')) if isinstance(body, dict) and body.get('code') is not None else None
    kind = type(exc).__name__
    suffix = f' (HTTP {status})' if status else ''

    if provider_name == 'Z.AI' and code == '1113':
        return ProviderError('Z.AI quota unavailable (HTTP 429, code 1113): no balance or resource package.')
    if status == 401 or kind == 'AuthenticationError':
        return ProviderError(f'{provider_name} authentication rejected{suffix}: check the API key.')
    if status == 403 or kind == 'PermissionDeniedError':
        return ProviderError(f'{provider_name} access denied{suffix}: check model/plan permissions.')
    if status == 404 or kind == 'NotFoundError':
        return ProviderError(f'{provider_name} endpoint or model not found{suffix}: check base URL and model name.')
    if status == 429 or kind == 'RateLimitError':
        return ProviderError(f'{provider_name} quota/rate limit reached{suffix}: check balance, package, and limits.')
    if status == 400 or kind == 'BadRequestError':
        return ProviderError(f'{provider_name} rejected the request{suffix}: check model and tool-calling compatibility.')
    if kind in {'APIConnectionError', 'ConnectError'}:
        return ProviderError(f'{provider_name} connection failed: check network and base URL.')
    if kind in {'APITimeoutError', 'TimeoutError'} or isinstance(exc, TimeoutError):
        return ProviderError(f'{provider_name} request timed out.')
    return ProviderError(f'{provider_name} request failed{suffix}; check network, credentials, model access, and quota.')


class OpenAICompatibleProvider:
    """Generic provider for OpenAI-compatible Chat Completions + tool calling APIs."""

    def __init__(self, settings, client=None):
        self.settings = settings
        self.provider_name = settings.provider_name
        if client is not None:
            self.client = client
        else:
            if AsyncOpenAI is None:
                raise RuntimeError(
                    "The 'openai' package is not installed. Install requirements-core.txt."
                )
            self.client = AsyncOpenAI(
                api_key=settings.require_key(),
                base_url=settings.base_url,
                timeout=settings.timeout,
                max_retries=settings.llm_max_retries,
            )

    def request_options(self) -> dict:
        return {}

    async def decide(self, messages, tools):
        async def request(decision_messages, tool_choice="auto"):
            try:
                async with asyncio.timeout(self.settings.timeout + 5):
                    return await self.client.chat.completions.create(
                        model=self.settings.model,
                        messages=decision_messages,
                        tools=tools,
                        tool_choice=tool_choice,
                        temperature=0.2,
                        max_tokens=1800,
                        **self.request_options(),
                    )
            except Exception as exc:
                raise _provider_error(exc, self.provider_name) from None

        def extract(response):
            if not response.choices:
                raise MalformedDecision("Provider returned no choices.")

            message = response.choices[0].message
            calls = message.tool_calls

            if not calls:
                content = message.content
                state = (
                    "nonempty"
                    if isinstance(content, str) and content.strip()
                    else "empty"
                )
                raise MalformedDecision(
                    f"Provider returned no tool call (content={state})."
                )

            if len(calls) != 1:
                names = [
                    getattr(getattr(call, "function", None), "name", "?")
                    for call in calls[:5]
                ]
                raise MalformedDecision(
                    f"Provider returned {len(calls)} tool calls "
                    f"instead of one; names={names}."
                )

            call = calls[0]
            name = getattr(call.function, "name", None)
            arguments = getattr(call.function, "arguments", None)

            if not isinstance(name, str) or not name:
                raise MalformedDecision(
                    "Tool call has no valid function name."
                )

            if not isinstance(arguments, str):
                raise MalformedDecision(
                    f"Tool call {name!r} has non-string arguments."
                )

            return Decision(name, arguments)

        response = await request(messages)

        try:
            return extract(response)
        except MalformedDecision as first_error:
            # One protocol-repair attempt. No browser action has happened yet,
            # so the current observation and element refs are still valid.
            repair_messages = list(messages)
            repair_messages.append({
                "role": "user",
                "content": (
                    "PROTOCOL CORRECTION: Your previous response violated "
                    "the agent protocol. Return exactly ONE tool call now. "
                    "Do not answer with ordinary text and do not call "
                    "multiple tools. Use finish if the task is complete."
                ),
            })

            repaired = await request(repair_messages, tool_choice="required")

            try:
                return extract(repaired)
            except MalformedDecision as second_error:
                raise MalformedDecision(
                    f"Protocol repair failed: first={first_error}; "
                    f"retry={second_error}"
                ) from None

    async def close(self):
        close = getattr(self.client, 'close', None)
        if close is not None:
            await close()


class ZAIProvider(OpenAICompatibleProvider):
    """Z.AI adapter. Adds Z.AI-specific thinking control while keeping the generic contract."""

    def request_options(self) -> dict:
        return {'extra_body': {'thinking': {'type': 'disabled'}}}


def _gigachat_alias(name: str) -> str:
    """GigaChat function names are documented as Latin letters only."""
    alias = ''.join(ch for ch in name if ch.isascii() and ch.isalpha())
    if not alias:
        raise ValueError(f'GigaChat cannot encode tool name: {name!r}')
    return alias


def _gigachat_functions(tools: list[dict]) -> tuple[list[dict], dict[str, str]]:
    converted = []
    reverse = {}
    for tool in tools:
        function = tool.get('function') if isinstance(tool, dict) else None
        if not isinstance(function, dict):
            raise ValueError('Invalid tool schema for GigaChat')
        original = function.get('name')
        if not isinstance(original, str) or not original:
            raise ValueError('Tool schema has no function name')
        alias = _gigachat_alias(original)
        if alias in reverse and reverse[alias] != original:
            raise ValueError(f'GigaChat tool alias collision: {original!r}')
        reverse[alias] = original
        converted.append({
            'name': alias,
            'description': function.get('description', ''),
            'parameters': function.get('parameters', {'type': 'object', 'properties': {}}),
        })
    return converted, reverse


def _gigachat_http_error(status: int, phase: str) -> ProviderError:
    suffix = f' (HTTP {status})'
    if status == 400:
        return ProviderError(f'GigaChat rejected the {phase} request{suffix}: check model/function schema and configuration.')
    if status == 401:
        return ProviderError(f'GigaChat authentication rejected{suffix}: check the Authorization key.')
    if status == 403:
        return ProviderError(f'GigaChat access denied{suffix}: check model/plan permissions.')
    if status == 404:
        return ProviderError(f'GigaChat endpoint or model not found{suffix}: check URL and model name.')
    if status == 422:
        return ProviderError(f'GigaChat rejected request parameters{suffix}: check function schemas and model compatibility.')
    if status == 429:
        return ProviderError(f'GigaChat quota/rate limit reached{suffix}: check limits and retry later.')
    if status >= 500:
        return ProviderError(f'GigaChat service unavailable{suffix}: retry later.')
    return ProviderError(f'GigaChat {phase} request failed{suffix}.')


class GigaChatProvider:
    """Official GigaChat REST adapter with OAuth token refresh and custom functions.

    Runtime decisions are intentionally stateless between model calls: every turn
    sends the current browser/context snapshot, so GigaChat functions_state_id is
    not carried forward unless assistant/function history is also carried forward.
    """

    RETRY_STATUSES = {408, 429, 500, 502, 503, 504}

    def __init__(self, settings, client=None):
        self.settings = settings
        self.provider_name = 'GigaChat'
        self.auth_key = settings.require_key()
        self._access_token = None
        self._expires_at = 0.0
        self._owns_client = client is None
        if client is not None:
            self.client = client
        else:
            verify = self._ssl_verify()
            self.client = httpx.AsyncClient(
                timeout=settings.timeout,
                verify=verify,
                follow_redirects=True,
            )

    def _ssl_verify(self):
        if not self.settings.gigachat_verify_ssl:
            return False
        if self.settings.gigachat_ca_bundle:
            path = Path(self.settings.gigachat_ca_bundle).expanduser()
            if not path.is_file():
                raise ValueError('GIGACHAT_CA_BUNDLE does not point to a readable file.')
            return ssl.create_default_context(cafile=str(path))
        return True

    async def _sleep_retry(self, attempt: int):
        delay = min(self.settings.llm_retry_backoff * (2 ** attempt), 8.0)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _post(self, url: str, **kwargs):
        last_kind = None
        for attempt in range(self.settings.llm_max_retries + 1):
            try:
                async with asyncio.timeout(self.settings.timeout + 5):
                    response = await self.client.post(url, **kwargs)
            except (httpx.TimeoutException, TimeoutError):
                last_kind = 'timeout'
                if attempt < self.settings.llm_max_retries:
                    await self._sleep_retry(attempt)
                    continue
                raise ProviderError('GigaChat request timed out after retries.') from None
            except httpx.TransportError:
                last_kind = 'connection'
                if attempt < self.settings.llm_max_retries:
                    await self._sleep_retry(attempt)
                    continue
                raise ProviderError(
                    'GigaChat connection failed after retries. Check network, API URL, and the configured CA certificate.'
                ) from None
            except Exception:
                raise ProviderError('GigaChat request failed before a valid response was received.') from None

            if response.status_code in self.RETRY_STATUSES and attempt < self.settings.llm_max_retries:
                await self._sleep_retry(attempt)
                continue
            return response
        raise ProviderError(f'GigaChat {last_kind or "request"} failed.')

    @staticmethod
    def _json(response, phase: str) -> dict:
        try:
            data = response.json()
        except Exception:
            raise ProviderError(f'GigaChat {phase} returned invalid JSON (HTTP {response.status_code}).') from None
        if not isinstance(data, dict):
            raise ProviderError(f'GigaChat {phase} returned an invalid JSON object.')
        return data

    async def _token(self, force=False) -> str:
        now = time.time()
        if not force and self._access_token and now < self._expires_at - 60:
            return self._access_token

        response = await self._post(
            self.settings.gigachat_oauth_url,
            headers={
                'Authorization': f'Basic {self.auth_key}',
                'RqUID': str(uuid.uuid4()),
                'Accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            data={'scope': self.settings.gigachat_scope},
        )
        if response.status_code != 200:
            raise _gigachat_http_error(response.status_code, 'OAuth')
        data = self._json(response, 'OAuth')
        token = data.get('access_token')
        if not isinstance(token, str) or not token:
            raise ProviderError('GigaChat OAuth response did not contain an access token.')
        expires = data.get('expires_at')
        try:
            expires = float(expires)
        except (TypeError, ValueError):
            expires = now + 1740
        if expires > 10_000_000_000:  # Be tolerant of millisecond timestamps.
            expires /= 1000
        if expires <= now:
            expires = now + 1740
        self._access_token = token
        self._expires_at = expires
        return token

    async def _chat(self, messages, functions, function_call='auto') -> dict:
        payload = {
            'model': self.settings.model,
            'messages': messages,
            'functions': functions,
            'function_call': function_call,
            'temperature': 0,
            'max_tokens': 1800,
        }
        for auth_attempt in range(2):
            token = await self._token(force=auth_attempt > 0)
            response = await self._post(
                f'{self.settings.base_url}chat/completions',
                headers={
                    'Authorization': f'Bearer {token}',
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                },
                json=payload,
            )
            if response.status_code == 401 and auth_attempt == 0:
                self._access_token = None
                self._expires_at = 0
                continue
            if response.status_code != 200:
                raise _gigachat_http_error(response.status_code, 'chat')
            return self._json(response, 'chat')
        raise ProviderError('GigaChat authentication failed after token refresh.')

    @staticmethod
    def _extract(data: dict, reverse: dict[str, str]) -> Decision:
        choices = data.get('choices')
        if not isinstance(choices, list) or not choices:
            raise MalformedDecision('GigaChat returned no choices.')
        first = choices[0]
        message = first.get('message') if isinstance(first, dict) else None
        if not isinstance(message, dict):
            raise MalformedDecision('GigaChat returned no assistant message.')
        call = message.get('function_call')
        if not isinstance(call, dict):
            content = message.get('content')
            state = 'nonempty' if isinstance(content, str) and content.strip() else 'empty'
            raise MalformedDecision(f'GigaChat returned no function call (content={state}).')
        alias = call.get('name')
        if not isinstance(alias, str) or alias not in reverse:
            raise MalformedDecision('GigaChat returned an unknown function name.')
        arguments = call.get('arguments')
        if isinstance(arguments, (dict, list)):
            arguments = json.dumps(arguments, ensure_ascii=False, separators=(',', ':'))
        if not isinstance(arguments, str):
            raise MalformedDecision(f'GigaChat function {reverse[alias]!r} has invalid arguments.')
        return Decision(reverse[alias], arguments)

    async def decide(self, messages, tools):
        functions, reverse = _gigachat_functions(tools)
        response = await self._chat(messages, functions, function_call='auto')
        try:
            return self._extract(response, reverse)
        except MalformedDecision as first_error:
            # GigaChat has auto/none/specific-name modes, but no generic
            # "required any function" mode. A single correction turn keeps the
            # current browser refs valid while asking auto mode again.
            repair_messages = list(messages)
            repair_messages.append({
                'role': 'user',
                'content': (
                    'PROTOCOL CORRECTION: Return exactly ONE function call now. '
                    'Do not answer with ordinary text. Use the finish function '
                    'if the task is complete.'
                ),
            })
            repaired = await self._chat(repair_messages, functions, function_call='auto')
            try:
                return self._extract(repaired, reverse)
            except MalformedDecision as second_error:
                raise MalformedDecision(
                    f'Protocol repair failed: first={first_error}; retry={second_error}'
                ) from None

    async def close(self):
        if not self._owns_client:
            return
        close = getattr(self.client, 'aclose', None)
        if close is not None:
            await close()


def create_provider(settings, client=None) -> LLMProvider:
    if settings.provider == 'zai':
        return ZAIProvider(settings, client=client)
    if settings.provider == 'openai_compatible':
        return OpenAICompatibleProvider(settings, client=client)
    if settings.provider == 'gigachat':
        return GigaChatProvider(settings, client=client)
    raise ValueError(f'Unsupported LLM provider: {settings.provider}')
