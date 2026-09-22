# TEST_REPORT — security fixes + GigaChat provider, 2026-09-22

## Фактический итог текущей ревизии

Security-fix baseline до добавления provider был проверен в полностью настроенном окружении как **197 passed, 1 skipped, 0 failed**. Текущий GigaChat patch добавляет 8 config/provider regression tests и не меняет Runtime/browser action policy.

Текущая ревизия проверена в Termux полным запуском `python -m pytest -q`: **145 passed, 18 skipped, 0 failed** за 26.45 s. В этом же окружении доступны и проверены ChromeDriver/WebDriver и Chromium headless shell. Skipped-тесты остаются opt-in/environment-dependent проверками и не считаются PASS.

Окружение: Linux x86_64, Python 3.12.14, Playwright 1.63.0, Chromium headless shell 140.0.7339.16 и ChromeDriver 140.0.7339.16. Использовано существующее Python-окружение с версиями из requirements.txt. Чистая установка зависимостей с нуля не проверялась. Штатный загрузчик Playwright ранее получал повреждённый browser ZIP; Chromium установлен отдельно штатным bootstrap Playwright 1.55.0. Production Playwright не понижался; bootstrap, браузер и driver не входят в submission.

```bash
CHROMEDRIVER_EXECUTABLE_PATH=/path/to/chromedriver \
BROWSER_EXECUTABLE_PATH=/path/to/headless_shell python -m pytest -q -rs
```

Без driver/binary env vars 14 real WebDriver cases пропускаются, а не считаются проверенными. Единственный skip в указанном полном прогоне — opt-in Wikipedia smoke. При отсутствующем Chromium Playwright tests сообщают ошибку запуска, не PASS.

## Три исправления и их воспроизведение

До правок повторены исходные независимые reproducers: **4 воспроизведения, 2 нерелевантных случая deselected**. Четыре случая соответствуют трём дефектам: upload через type_text и fill_form, изменение form action при Enter approval, отправка Enter внутри Shadow DOM. PASS в том baseline означал успешное воспроизведение дефекта.

1. **Upload bypass.** Runtime отклоняет type_text/fill_form для input[type=file]. WebDriver дополнительно отклоняет такой target перед clear/send keys после resolve с проверкой текущего типа. Разрешённый путь — upload_file с upload-root, confirmation и verification. Восемь новых real ChromeDriver cases проверяют обе команды через runtime и непосредственно backend, с включённым и отключённым upload-root. Ни файл, ни его содержимое не становятся доступны странице.
2. **Enter approval race.** Повторная проверка после confirmation сравнивает form_action, form_method, form_role, autocomplete и href в дополнение к прежним полям. Четыре cases проверяют смену action/method на Playwright и WebDriver: submit не происходит, pending/trace не создаются.
3. **Shadow DOM focus.** Observer и сериализация find/collection следуют по shadowRoot.activeElement до focused leaf. Четыре cases используют два вложенных открытых shadow roots на обоих backend. После отказа Enter не отправляет форму; после разрешения submit происходит и требует свежего verify_action. Отдельно внутри этих cases проверен focused flag в find_in_page.

Новые cases добавлены в `tests/test_browser_regressions.py` (4) и `tests/test_webdriver_live.py` (12). Это scripted decisions + реальный браузер, не реальные LLM решения.

## Что независимо проверено в текущем окружении

| Проверка | Результат |
|---|---|
| Security baseline до GigaChat patch | 197 passed, 1 skipped, 0 failed в ранее полностью настроенном окружении |
| Текущая GigaChat/Termux ревизия | `python -m pytest -q`: 145 passed, 18 skipped, 0 failed |
| GigaChat config/provider regressions | 8 новых tests; OAuth cache/refresh, aliases, retry, repair, sanitization — PASS |
| Playwright: DOM, поиск, формы, extraction, upload, вкладки | PASS |
| ChromeDriver: обычный разрешённый upload, формы, поиск, tabs, stale refs | PASS |
| Новые upload / approval race / nested Shadow DOM regressions | 16 passed в полном suite |
| CDP: подключение, synthetic localStorage continuity, отключение без закрытия браузера | PASS |
| Synthetic mail/shop/jobs и verification | PASS, scripted providers |
| CLI `main.py --headless --check-browser` | PASS (Chromium headless, 1 вкладка, штатное закрытие) |
| CLI `main.py --check-llm` | PASS с реальным GigaChat API; автоматическое получение нового OAuth access token после перезапуска Termux также проверено |
| Автономный Wikipedia E2E | LIVE PASS: GigaChat-3-Ultra → Runtime → WebDriver → ru.wikipedia.org; ответ 1137, COMPLETE за 11 шагов |
| compileall | PASS |
| Secret/repository scan | PASS: release scanner и проверка состава submission; реальных секретов не обнаружено |

## Исторически зафиксированный live-результат

До этого патча в переданных README/LIVE_E2E зафиксированы успешные реальный OpenAI-compatible provider health check и автономный Wikipedia visible-UI E2E: поиск, stale-ref recovery, ответ **1137** с фактическим URL статьи. Этот исторический live PASS сохранён. Он не является новым независимым прогоном исправленного submission. Подробного отдельного лога того позднего запуска с привязкой к хешу архива в исходном ZIP нет.

Ранее live shop дошёл до browser actions/verification, затем остановился с HTTP 413; полный live shop PASS не заявляется. Без лимитов модели и исходного запроса нельзя установить, был ли 413 вызван лимитом провайдера или слишком большим payload. Исторические Termux прогоны сохранены в docs/history и не подменяют текущие результаты.

## Границы проверки

Без настроенных API credentials в контейнере текущий автономный LLM E2E новой сборки не подтверждён. Недоступность модели/сети не считается дефектом программы. Windows GUI, текущий Android/Termux, реальные авторизованные аккаунты, OAuth и полный набор CAPTCHA/2FA не проверены. Существующие unit/integration tests подтверждают контракты, а не качество классификации спама или выбора вакансий реальной моделью.

Native JavaScript confirm/prompt по-прежнему отклоняются автоматически; инструмента accept-dialog нет. После not_achieved общий finish complete остаётся решением LLM. Эти ограничения предыдущего аудита не входят в три запрошенных исправления. Символьные лимиты контекста не гарантируют совместимость с любой моделью. Общая невосприимчивость к prompt injection и произвольным изменениям JavaScript страницы не заявляется. Ruff/CVE/SCA scan в этом прогоне не выполнялись.
