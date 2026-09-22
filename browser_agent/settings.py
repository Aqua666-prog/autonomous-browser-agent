import getpass
import os
import warnings
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, field_validator


DEFAULT_ZAI_BASE_URL = 'https://api.z.ai/api/paas/v4/'


class Settings(BaseModel):
    provider: Literal['zai', 'openai_compatible'] = 'zai'
    model: str = 'glm-4.6'
    base_url: str = DEFAULT_ZAI_BASE_URL
    api_key: SecretStr = SecretStr('')
    api_key_required: bool = True
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
        p = urlsplit(value)
        if not p.hostname or p.username or p.password:
            raise ValueError('LLM base URL must have a host and must not contain credentials')
        local_hosts = {'localhost', '127.0.0.1', '::1'}
        if p.scheme != 'https' and not (p.scheme == 'http' and p.hostname in local_hosts):
            raise ValueError('LLM base URL must use HTTPS; HTTP is allowed only for localhost')
        return value.rstrip('/') + '/'

    @classmethod
    def load(cls, env_file: str | Path = '.env'):
        load_dotenv(env_file, override=False)

        provider = os.getenv('LLM_PROVIDER', 'zai')
        # New generic names take precedence. ZAI_* stays supported for backward compatibility.
        base_url = os.getenv('LLM_BASE_URL') or os.getenv('ZAI_BASE_URL') or DEFAULT_ZAI_BASE_URL
        api_key = os.getenv('LLM_API_KEY')
        if api_key is None:
            api_key = os.getenv('ZAI_API_KEY', '')

        values = {
            'provider': provider,
            'model': os.getenv('LLM_MODEL', 'glm-4.6'),
            'base_url': base_url,
            'api_key': api_key,
        }
        optional = {
            'api_key_required': 'LLM_API_KEY_REQUIRED',
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
        return 'Z.AI' if self.provider == 'zai' else 'OpenAI-compatible'

    def require_key(self, prompt=None):
        key = self.api_key.get_secret_value().strip()
        if key:
            return key
        if not self.api_key_required and self.provider == 'openai_compatible':
            # The OpenAI SDK requires a non-empty client key even when a local server ignores auth.
            return 'local-no-key-required'
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                key = (prompt or getpass.getpass)(f'{self.provider_name} API key не найден. Введите API key: ')
        except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
            raise ValueError('API key не введён; задайте LLM_API_KEY (или ZAI_API_KEY) локально.') from None
        if not key.strip():
            raise ValueError('API key пуст.')
        self.api_key = SecretStr(key.strip())
        return self.api_key.get_secret_value()
