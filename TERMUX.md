# Termux / Android — WebDriver + GigaChat

На Android проект использует сохранённый raw WebDriver backend. Playwright для этого пути не нужен.

## Установка

```bash
python -m pip install -r requirements-webdriver.txt
python -m pip install -r requirements-test.txt
cp .env.example .env
```

Минимальная конфигурация для официального GigaChat API:

```dotenv
LLM_PROVIDER=gigachat
LLM_MODEL=GigaChat-3-Ultra
LLM_BASE_URL=https://api.giga.chat/v1/
GIGACHAT_AUTH_KEY=
GIGACHAT_SCOPE=GIGACHAT_API_PERS
MODEL_TIMEOUT=180
LLM_MAX_RETRIES=2
LLM_RETRY_BACKOFF=1

BROWSER_BACKEND=webdriver
WEBDRIVER_URL=http://127.0.0.1:9515
WEBDRIVER_BINARY=/data/data/com.termux/files/usr/bin/headless_shell
HEADLESS=true
BROWSER_LOCALE=ru-RU
MAX_AGENT_STEPS=100
CONFIRMATION_MODE=risky
```

Вставьте свой Authorization key только в локальный `.env`. Если вы изменили `.env` в уже открытой оболочке, перечитайте его перед запуском:

```bash
set -a
source .env
set +a
```

## Сертификат GigaChat

Если `curl`/Python сообщает `self-signed certificate in certificate chain`, установите требуемый корневой сертификат и задайте PEM bundle:

```dotenv
GIGACHAT_VERIFY_SSL=true
GIGACHAT_CA_BUNDLE=/absolute/path/to/chain_pem.txt
```

Для краткой локальной диагностики можно временно использовать:

```dotenv
GIGACHAT_VERIFY_SSL=false
```

Это эквивалентно диагностическому `curl -k`; в публикуемом `.env.example` проверка TLS включена.

## Один Termux-сеанс

Проект подключается к уже слушающему WebDriver endpoint и сам ChromeDriver не запускает. В одной оболочке его можно явно поднять фоном, проверить, запустить агент и затем остановить:

```bash
chromedriver --port=9515 > "$TMPDIR/chromedriver.log" 2>&1 &
CHROMEDRIVER_PID=$!
sleep 2
curl -s http://127.0.0.1:9515/status

python main.py --check-browser
python main.py --check-llm
python main.py --task "Открой https://example.com. Определи заголовок страницы и основной текст. В конце кратко сообщи, что находится на странице."

kill "$CHROMEDRIVER_PID" 2>/dev/null || true
wait "$CHROMEDRIVER_PID" 2>/dev/null || true
```

Для диагностики ChromeDriver:

```bash
cat "$TMPDIR/chromedriver.log"
```

## Альтернативный local OpenAI-compatible router

```dotenv
LLM_PROVIDER=openai_compatible
LLM_MODEL=free-best
LLM_BASE_URL=http://127.0.0.1:8787/v1/
LLM_API_KEY_REQUIRED=false
MODEL_TIMEOUT=180
```

`free-best` — только пример имени внешнего маршрута, не встроенная модель проекта.

Если Playwright не установлен, Playwright-specific browser integration tests пропускаются; это не считается PASS браузера. Core/WebDriver tests должны выполняться отдельно. Native click/clear/send-keys, W3C key actions, options, scrolling, history, read chunks, stale refs, popup follow, `find_in_page`, `fill_form`, root-scoped `upload_file` и generic tabs поддерживаются WebDriver backend.

Для file upload задайте отдельную разрешённую директорию:

```dotenv
BROWSER_UPLOAD_ROOT=/data/data/com.termux/files/home/storage/downloads/BrowserAgentUploads
```

`upload_file` не принимает абсолютные пути от модели и всегда требует human confirmation. CAPTCHA/OTP/login выполняет человек в видимом браузере; headless shell не используется для обхода проверок.
