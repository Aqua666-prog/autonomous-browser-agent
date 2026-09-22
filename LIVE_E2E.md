# LIVE_E2E — проверка текущей ревизии, 2026-09-22

## Что действительно запущено

- Настоящий Chromium/Playwright: browser integration и synthetic mail/shop/jobs, включая подтверждения и проверку результата — PASS.
- Настоящий ChromeDriver: формы, поиск с refs, extraction, upload, tabs и stale recovery — PASS.
- Настоящий CDP: synthetic session continuity и отключение без закрытия браузера — PASS.
- `main.py --headless --check-browser` — PASS.
- Полный suite: **181 passed, 1 skipped, 0 failed**.

Эти сценарии используют scripted providers или прямые browser primitives. Они **не являются live LLM E2E**.

## Wikipedia — фактическая попытка

Запущено:

```bash
RUN_LIVE_WIKIPEDIA=1 BROWSER_EXECUTABLE_PATH=/path/to/headless_shell \
python -m pytest -q -rs -s tests/test_wikipedia_live.py
```

Браузер начал с единственного заданного URL `https://ru.wikipedia.org/`.
Первый запрос завершился `net::ERR_EMPTY_RESPONSE`. Поиск, статья, год и source URL в этом окружении **не получены**. Защита не обходилась; proxy/anti-bot bypass для внешнего сайта не выполнялся. Результат opt-in теста: **1 skipped — Environment network prevents Wikipedia access**. В обычном suite этот тест skipped по opt-in настройке.

Сам тест содержит только видимый поиск: наблюдаемый input → ввод → Enter → наблюдаемый result link при необходимости. URL статьи не конструируется. После сетевого допуска он проверяет браузерный путь; самостоятельный выбор действий LLM проверяется отдельно следующей командой.

```text
python main.py --task "Открой только главную страницу https://ru.wikipedia.org/. Через видимое поле поиска найди статью «Элеонора Аквитанская». Не составляй URL статьи самостоятельно. На открытой через поиск странице выясни, в каком году Элеонора стала королевой Франции, и ответь годом с фактическим URL страницы-источника."
```

## Не проверено и не считается PASS

- Позднее автономный Wikipedia E2E был повторён с реальным OpenAI-compatible LLM и успешно завершён: поиск выполнялся через видимый UI, runtime восстановился после stale ref, результат — 1137 с фактическим URL статьи.
- Реальный LLM tool calling отдельно прошёл provider health check.
- Synthetic shop LLM acceptance подтвердил реальные browser actions и verification, но полный сценарий не считается PASS: прогон был прерван внешней ошибкой provider HTTP 413.
- Реальные авторизованные пользовательские аккаунты не проверялись.
- Текущий патч на Android/Termux и Windows GUI.
- Реальные почтовый аккаунт, магазин, сайт вакансий, OAuth и реальная загрузка резюме.
- Поведение конкретного сайта при CAPTCHA/2FA. Проверены локальные handoff/safety contracts, а не все anti-bot системы.

Предыдущие Termux результаты пользователя и исторические документы не заменяют повторный прогон текущего ZIP. Перед сдачей выполните ACCEPTANCE.md.
