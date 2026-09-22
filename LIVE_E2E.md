# LIVE_E2E — исторические live-результаты и текущая проверка, 2026-09-22

## Исторически зафиксированный Wikipedia live PASS

В переданных документах до текущего security-патча зафиксирован успешный автономный прогон с реальной OpenAI-compatible LLM: агент использовал видимый поиск Wikipedia, восстановился после stale ref и ответил **1137**, указав фактический URL статьи. Реальный LLM tool calling отдельно прошёл provider health check.

**Статус: исторический live PASS.** Результат сохранён и не отнесён к неуспешным или непроверенным историческим запускам. При этом он не является независимым повторным прогоном текущего исправленного ZIP. Отдельного подробного лога позднего запуска с model/version и хешем submission в переданном архиве нет; основание записи — ранее переданная документация. Более ранние Termux результаты находятся в docs/history.

Исторический shop LLM acceptance дошёл до browser actions и verification, затем остановился с HTTP 413. Полный live shop не считается PASS; причина 413 без исходного payload и лимитов модели не установлена.

## Независимая проверка исправленной ревизии без API credentials

- Полный существующий suite и новые regressions: **197 passed, 1 skipped, 0 failed**.
- Настоящие Chromium/Playwright, ChromeDriver и CDP — PASS.
- Synthetic mail/shop/jobs — PASS, решения задают scripted providers.
- Новые 16 regression cases закрывают upload bypass, Enter approval race и nested Shadow DOM focus на реальных browser backend.
- `main.py --headless --check-browser` — PASS (Chromium headless, 1 вкладка, штатное закрытие).
- `main.py --check-llm` — контролируемая ошибка отсутствующей конфигурации/ключа; реальный API не проверен.

Browser primitives и scripted providers не являются live LLM E2E и не заменяют исторический автономный запуск.

## Текущая внешняя Wikipedia browser-проверка

```bash
RUN_LIVE_WIKIPEDIA=1 BROWSER_EXECUTABLE_PATH=/path/to/headless_shell \
python -m pytest -q -rs tests/test_wikipedia_live.py
```

Результат текущего запуска: **1 skipped — Environment network prevents Wikipedia access**. Сетевые ограничения не обходились. Это тест браузера без LLM; в обычном полном suite он выключен opt-in настройкой. В текущем окружении статья, год и source URL этим тестом не получены.

Для нового автономного live-прогона с собственным настроенным provider:

```text
python main.py --task "Открой только главную страницу https://ru.wikipedia.org/. Через видимое поле поиска найди статью «Элеонора Аквитанская». Не составляй URL статьи самостоятельно. На открытой через поиск странице выясни, в каком году Элеонора стала королевой Франции, и ответь годом с фактическим URL страницы-источника."
```

## Ещё не проверено на исправленной версии

- Новый автономный LLM E2E текущего ZIP: в окружении нет настроенных API credentials.
- Текущий patch на Android/Termux и Windows GUI.
- Реальные авторизованные почта, магазин, вакансии, OAuth и реальная загрузка резюме.
- Поведение конкретных сайтов при CAPTCHA/2FA: проверены локальные handoff contracts, не все anti-bot системы.

Отсутствие API-ключа/квоты/модели или сетевого доступа не записывается как ошибка программы. Перед демонстрацией выполните ACCEPTANCE.md и фиксируйте новый live-результат отдельно от исторического.
