# GigaChat provider

The project contains a native REST adapter for the official GigaChat API. It does not route GigaChat through the OpenAI-compatible adapter because GigaChat custom functions use the `functions` / `function_call` protocol.

## Configuration

```dotenv
LLM_PROVIDER=gigachat
LLM_MODEL=GigaChat-3-Ultra
LLM_BASE_URL=https://api.giga.chat/v1/
GIGACHAT_AUTH_KEY=
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_OAUTH_URL=https://ngw.devices.sberbank.ru:9443/api/v2/oauth
GIGACHAT_VERIFY_SSL=true
# GIGACHAT_CA_BUNDLE=/path/to/chain_pem.txt
LLM_MAX_RETRIES=2
LLM_RETRY_BACKOFF=1
MODEL_TIMEOUT=180
```

`GIGACHAT_AUTH_KEY` is the Authorization key from the GigaChat API project. The adapter exchanges it for an access token, caches the token, and refreshes it before expiry or once after a chat HTTP 401. The access token is never written to `.env` or logs.

## TLS on Termux/Linux

GigaChat uses a certificate chain that may not exist in the default CA store. Preferred configuration is to install the required root chain and set `GIGACHAT_CA_BUNDLE` to its PEM bundle when the system store still cannot validate the endpoint.

For a temporary local diagnostic only, `GIGACHAT_VERIFY_SSL=false` reproduces curl `-k`. The committed example keeps verification enabled.

## Function calling compatibility

The browser runtime exposes OpenAI-style tool schemas internally. The GigaChat adapter converts them to GigaChat `functions`. GigaChat documentation restricts function names to Latin letters, while the runtime uses names such as `read_page` and `update_plan`, so the adapter sends deterministic letters-only aliases (`readpage`, `updateplan`) and maps returned calls back to the original runtime names before Pydantic validation.

GigaChat may return `function_call.arguments` as an object or a JSON string; both forms are normalized to the runtime's JSON-string `Decision` contract.

The runtime does not replay assistant function-call history between LLM decisions. Each turn sends a fresh system message plus the complete current bounded browser/context state. Therefore `functions_state_id` is not required in the current architecture. If provider conversation history is added later, GigaChat assistant `functions_state_id` and matching `role=function` results must be preserved together.

## Reliability

Transient transport failures and HTTP 408/429/5xx statuses are retried with bounded exponential backoff. `LLM_MAX_RETRIES` is limited to 0–5. Provider errors are sanitized so response bodies, Authorization keys, and access tokens are not printed.

Run:

```bash
python main.py --check-llm
```

A successful result is:

```text
[PASS] Live LLM tool calling works.
```
