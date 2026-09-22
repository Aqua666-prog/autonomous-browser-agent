import getpass
import os
import warnings
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, field_validator


DEFAULT_ZAI_BASE_URL = 'https://api.z.ai/api/paas/v4/'
DEFAULT_GIGACHAT_BASE_URL = 'https://api.giga.chat/v1/'
DEFAULT_GIGACHAT_OAUTH_URL = 'https://ngw.devices.sberbank.ru:9443/api/v2/oauth'


def _validated_https_url(value: str, label: str, allow_local_http=False) -> str:
    p = urlsplit(value)
    if not p.hostname or p.username or p.password:
        raise ValueError(f'{label} must have a host and must not contain credentials')
    local_hosts = {'localhost', '127.0.0.1', '::1'}
    if p.scheme != 'https' and not (allow_local_http and p.scheme == 'http' and p.hostname in local_hosts):
        suffix = '; HTTP is allowed only for localhost' if allow_local_http else ''
        raise ValueError(f'{label} must use HTTPS{suffix}')
    return value.rstrip('/') + '/'


class Settings(BaseModel):
    provider: Literal['zai', 'openai_compatible', 'gigachat'] = 'zai'
    model: str = 'glm-4.6'
    base_url: str = DEFAULT_ZAI_BASE_URL
    api_key: SecretStr = SecretStr('')
    api_key_required: bool = True
    gigachat_auth_key: SecretStr = SecretStr('')
    gigachat_scope: Literal['GIGACHAT_API_PERS', 'GIGACHAT_API_B2B', 'GIGACHAT_API_CORP'] = 'GIGACHAT_API_PERS'
    gigachat_oauth_url: str = DEFAULT_GIGACHAT_OAUTH_URL
    gigachat_verify_ssl: bool = True
    gigachat_ca_bundle: str | None = None
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_retry_backoff: float = Field(default=1.0, ge=0, le=30)
    max_steps: int = Field(default=30, ge=1, le=200)
    observation_budget: int = Field(default=28000, ge=2000, le=100000)
    headless: bool = False
    timeout: float = Field(default=60, ge=1, le=300)
    confirmation_mode: Literal['risky', 'all', 'none'] = 'risky'
    browser_executable_path: str | None = None
    browser_backend: Literal['playwright', 'webdriver', 'cdp'] = 'playwright'
    browser_profile_dir: str | None = None
    browser_storage_state: str | None = None
    browser_cdp_url: str = 'http://127.0.0.1:9222'
    browser_locale: str = 'ru-RU'
    webdriver_url: str = 'http://127.0.0.1:9515'
    webdriver_binary: str = '/data/data/com.termux/files/usr/bin/headless_shell'
    browser_upload_root: str | None = None

    @field_validator('base_url')
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        return _validated_https_url(value, 'LLM base URL', allow_local_http=True)

    @field_validator('gigachat_oauth_url')
    @classmethod
    def _validate_gigachat_oauth_url(cls, value: str) -> str:
        return _validated_https_url(value, 'GigaChat OAuth URL').rstrip('/')

    @classmethod
    def load(cls, env_file: str | Path = '.env'):
        load_dotenv(env_file, override=False)

        provider = os.getenv('LLM_PROVIDER', 'zai')
        if provider == 'gigachat':
            default_model = 'GigaChat-3-Ultra'
            base_url = os.getenv('LLM_BASE_URL') or DEFAULT_GIGACHAT_BASE_URL
            api_key = os.getenv('LLM_API_KEY', '')
        else:
            default_model = 'glm-4.6'
            # New generic names take precedence. ZAI_* stays supported for backward compatibility.
            base_url = os.getenv('LLM_BASE_URL') or os.getenv('ZAI_BASE_URL') or DEFAULT_ZAI_BASE_URL
            api_key = os.getenv('LLM_API_KEY')
            if api_key is None:
                api_key = os.getenv('ZAI_API_KEY', '')

        values = {
            'provider': provider,
            'model': os.getenv('LLM_MODEL', default_model),
            'base_url': base_url,
            'api_key': api_key,
            'gigachat_auth_key': os.getenv('GIGACHAT_AUTH_KEY', ''),
        }
        optional = {
            'api_key_required': 'LLM_API_KEY_REQUIRED',
            'gigachat_scope': 'GIGACHAT_SCOPE',
            'gigachat_oauth_url': 'GIGACHAT_OAUTH_URL',
            'gigachat_verify_ssl': 'GIGACHAT_VERIFY_SSL',
            'gigachat_ca_bundle': 'GIGACHAT_CA_BUNDLE',
            'llm_max_retries': 'LLM_MAX_RETRIES',
            'llm_retry_backoff': 'LLM_RETRY_BACKOFF',
            'max_steps': 'MAX_AGENT_STEPS',
            'observation_budget': 'OBSERVATION_BUDGET',
            'headless': 'HEADLESS',
            'timeout': 'MODEL_TIMEOUT',
            'confirmation_mode': 'CONFIRMATION_MODE',
            'browser_executable_path': 'BROWSER_EXECUTABLE_PATH',
            'browser_backend': 'BROWSER_BACKEND',
            'browser_profile_dir': 'BROWSER_PROFILE_DIR',
            'browser_storage_state': 'BROWSER_STORAGE_STATE',
            'browser_cdp_url': 'BROWSER_CDP_URL',
            'browser_locale': 'BROWSER_LOCALE',
            'webdriver_url': 'WEBDRIVER_URL',
            'webdriver_binary': 'WEBDRIVER_BINARY',
            'browser_upload_root': 'BROWSER_UPLOAD_ROOT',
        }
        values.update({k: os.environ[v] for k, v in optional.items() if v in os.environ})
        return cls(**values)

    @property
    def provider_name(self) -> str:
        if self.provider == 'zai':
            return 'Z.AI'
        if self.provider == 'gigachat':
            return 'GigaChat'
        return 'OpenAI-compatible'

    def require_key(self, prompt=None):
        if self.provider == 'gigachat':
            key = self.gigachat_auth_key.get_secret_value().strip()
            if key:
                return key
            label = 'GigaChat Authorization key'
            env_hint = 'GIGACHAT_AUTH_KEY'
        else:
            key = self.api_key.get_secret_value().strip()
            if key:
                return key
            if not self.api_key_required and self.provider == 'openai_compatible':
                # The OpenAI SDK requires a non-empty client key even when a local server ignores auth.
                return 'local-no-key-required'
            label = f'{self.provider_name} API key'
            env_hint = 'LLM_API_KEY (or ZAI_API_KEY)'

        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                key = (prompt or getpass.getpass)(f'{label} не найден. Введите ключ: ')
        except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
            raise ValueError(f'Ключ не введён; задайте {env_hint} локально.') from None
        if not key.strip():
            raise ValueError('API key пуст.')
        if self.provider == 'gigachat':
            self.gigachat_auth_key = SecretStr(key.strip())
            return self.gigachat_auth_key.get_secret_value()
        self.api_key = SecretStr(key.strip())
        return self.api_key.get_secret_value()
