# Termux / Android — сохранённый WebDriver backend

Playwright не требуется для этого пути. Используются совместимые `chromedriver` и Chromium `headless_shell`, уже настроенные на устройстве. Входная Work-версия этой ветки уже запускалась пользователем на Android: `--check-browser` PASS, `--check-llm` PASS, 80 passed / 2 skipped и Wikipedia visible-UI E2E PASS за 12 шагов. Текущий дополнительный patch после этого baseline на устройстве ещё не перепроверен; WebDriver protocol tests новых tools прошли в Linux.

Из корня распакованного проекта:

```bash
python -m pip install -r requirements-webdriver.txt
python -m pip install -r requirements-test.txt
cp .env.example .env
```

Настройте `.env`:

```dotenv
BROWSER_BACKEND=webdriver
WEBDRIVER_URL=http://127.0.0.1:9515
WEBDRIVER_BINARY=/data/data/com.termux/files/usr/bin/headless_shell
HEADLESS=true
BROWSER_LOCALE=ru-RU
MAX_AGENT_STEPS=100
CONFIRMATION_MODE=risky
```

Для своего локального router при его наличии:

```dotenv
LLM_PROVIDER=openai_compatible
LLM_MODEL=free-best
LLM_BASE_URL=http://127.0.0.1:8787/v1/
LLM_API_KEY_REQUIRED=false
MODEL_TIMEOUT=180
```

`free-best` — пример имени маршрута, не встроенный provider. При необходимости авторизации задайте свой ключ локально; не публикуйте `.env`. В одном терминале оставьте router, в другом:

```bash
chromedriver --port=9515
```

В третьем, из папки проекта:

```bash
curl -s http://127.0.0.1:9515/status
python main.py --check-browser
python main.py --check-llm
python -m pytest -q
python main.py --task "Открой https://ru.wikipedia.org/. Через видимое поле поиска найди Элеонору Аквитанскую. Укажи год, когда она стала королевой Франции, и URL источника."
```

Если Playwright не установлен, два файла browser integration tests будут пропущены при сборе. Это не PASS браузера; core/WebDriver suite должно выполняться. Не устанавливайте Playwright ради Android, если используете WebDriver.

Сохранены native click/clear/send-keys, W3C key actions, options, scrolling, history, read chunks, stale refs и popup follow. WebDriver теперь также поддерживает `find_in_page`, `fill_form` и root-scoped `upload_file`; `switch_tab`, validation, pending verification/confirmation/memory общие с desktop backend.


Для теста загрузки файла (не обязательно для Wikipedia) добавьте директорию, содержимое которой агенту разрешено прикреплять:

```dotenv
BROWSER_UPLOAD_ROOT=/data/data/com.termux/files/home/storage/downloads/BrowserAgentUploads
```

`upload_file` не принимает абсолютные пути от модели и всегда запрашивает подтверждение.

При таймауте LLM проверьте доступность router/model. MODEL_TIMEOUT допускает 1–300 секунд. При ошибке WebDriver проверьте совместимость версий driver/browser. Для CAPTCHA/OTP/login требуется видимый браузер и человек; headless shell не является способом обхода. Для полноценной authenticated acceptance используйте desktop Chrome CDP.
