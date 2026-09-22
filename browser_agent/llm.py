import asyncio
from dataclasses import dataclass
from typing import Protocol

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
                max_retries=1,
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


def create_provider(settings, client=None) -> LLMProvider:
    if settings.provider == 'zai':
        return ZAIProvider(settings, client=client)
    if settings.provider == 'openai_compatible':
        return OpenAICompatibleProvider(settings, client=client)
    raise ValueError(f'Unsupported LLM provider: {settings.provider}')
