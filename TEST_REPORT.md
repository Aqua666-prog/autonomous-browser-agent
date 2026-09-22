# TEST_REPORT — runtime hardening, 2026-09-22

## Фактический итог

**181 passed / 1 skipped / 0 failed**, 29.79 s.
Исходный архив без изменений в том же доступном браузерном окружении: **132 passed**, 18.72 s.
Ни один исходный test case не удалён. Добавлено 49 проходящих случаев и один opt-in внешний тест.

Окружение: Linux x86_64, Python 3.12.14; Chromium headless shell 140.0.7339.16; совместимый ChromeDriver 140.0.7339.16. Использованы зависимости существующего requirements.txt, включая Playwright 1.63.0. Его штатная загрузка браузера вернула повреждённый архив; Chromium установлен отдельно через bootstrap Playwright 1.55.0 и передан через BROWSER_EXECUTABLE_PATH. Версия Playwright в проекте не понижалась, bootstrap и бинарники не входят в ZIP.

Команда полного прогона (пути замените своими):

```bash
CHROMEDRIVER_EXECUTABLE_PATH=/path/to/chromedriver \
BROWSER_EXECUTABLE_PATH=/path/to/headless_shell python -m pytest -q
```

Без CHROMEDRIVER_EXECUTABLE_PATH два real WebDriver теста будут skipped, а не проверены. При отсутствии browser binary browser tests завершаются ошибкой, а не маскируются skip.

| Проверка | Фактический результат |
|---|---|
| Исходный core/safety/WebDriver mock baseline | 106 passed |
| Исходный полный baseline после установки браузера | 132 passed |
| Итоговый полный suite | 181 passed, 1 skipped, 0 failed |
| Playwright: наблюдение, find, extraction, формы, upload, вкладки | PASS, настоящий Chromium |
| CDP: подключение, synthetic localStorage continuity, disconnect без закрытия | PASS, настоящий Chromium process |
| WebDriver: find → ref → type, значения полей, collection, upload, tabs, stale target | PASS, настоящий ChromeDriver, два test case |
| Runtime mail: 10 сообщений, чтение/кандидат/confirmation/delete/verify | PASS, scripted provider + настоящий браузер |
| Runtime shop: variant/qty/24 EUR/checkout, запрет оплаты | PASS, scripted provider + настоящий браузер |
| Runtime jobs: три вакансии/три письма/три confirmation/три receipt | PASS, scripted provider + настоящий браузер |
| Production CLI `main.py --headless --check-browser` | PASS |
| Wikipedia visible UI external browser smoke | Попытка выполнена; сеть вернула ERR_EMPTY_RESPONSE до загрузки главной |
| Live LLM / `--check-llm` | НЕ ЗАПУСКАЛОСЬ: нет настроенного ключа провайдера |
| Windows/CDP и реальные authenticated accounts | НЕ ПРОВЕРЕНЫ |
| compileall | PASS |
| Ruff F821/F822/F823 | PASS; это targeted static checks, не полный style/security audit |
| Release secret scan | PASS по правилам scripts/release.py; нестандартные секреты не гарантированно распознаются |

## Исправления, подтверждённые тестами

1. **Поисковый synonym loop.** Общий budget на semantic fingerprint; два find максимум. Success требует действия/inspection перед повтором либо ограниченного refinement_reason. Zero-match synonyms также расходуют budget. Ref generation, расширение snapshot и planning не увеличивают его. Новое состояние даёт новый budget. Последовательное игнорирование recovery останавливает run.
2. **Focus verification.** `expected_outcome` сам по себе больше не создаёт pending для search/focus/menu/navigation. Проверены настоящий focus-only click, no-DOM interaction и guard для consequential controls.
3. **Business safety.** Send/delete/payment/application controls и native submit защищены confirmation/verification. Кнопка Submit application вне формы теперь тоже распознаётся. Timeout хранит uncertainty и action trace; duplicate verify и no-pending loop ограничены.
4. **Refs/approval race.** Find/collection refs работают на следующем decision без observe. Несвязанный DOM churn допустим; relabel/detach, смена окна и form destination инвалидируют target. Смена action формы во время подтверждения блокирует dispatch.
5. **Forms.** Обычные значения видимы; password/OTP values скрыты. Native checkbox/radio/select, required, readonly, validation, простое datalist autocomplete проверены. Dynamic replacement останавливает batch; продолжение использует fresh refs. Select label больше не включает option text.
6. **Extraction.** Таблица из 10 писем, карточки товаров и вакансий получают только наблюдаемые fields/times и actionable controls. Parent wrappers с несколькими records отсекаются. Две recent collection-выборки не исчезают при обычном вытеснении history.
7. **Upload.** Schema и resolver запрещают absolute/traversal/control-character paths; symlink escape и размер проверяются. Confirmation всегда обязателен. Имя нужного файла проверяется в текущем UI.
8. **Tabs.** Старый index не закрывает другую вкладку после изменения списка. WebDriver refs привязаны к окну. Несколько новых окон не трактуются как упорядоченная последовательность popup. Loopback ChromeDriver не отправляется в ambient proxy.
9. **Injection.** Шесть размещений hostile text: heading, button, mail body, product, job и semantic region. Runtime сохраняет confirmation denial, отвергает shell и path traversal. Это проверка барьеров при враждебных decisions, а не доказательство устойчивости конкретной LLM.

Один исходный upload privacy test уточнён: теперь он требует basename в интерактивном confirmation, поскольку человек должен знать, какой файл разрешает. Одновременно он проверяет отсутствие каталогов в confirmation и отсутствие имени/пути в обычных logs. Тест не удалён и не ослаблен до простого PASS.

## Промежуточные ошибки и ограничения результатов

Первый полный запуск без установленного Chromium: 106 passed, 2 failed, 24 setup errors (отсутствующий executable). После установки браузера исходный suite целиком прошёл. Новые тесты выявляли и исправляли runtime/label/proxy/approval проблемы; итог выше относится к последнему коду, а не к промежуточным результатам.

Scripted providers задают решения в test code и не импортируются production runtime. Они доказывают работу browser/runtime-контрактов и safety gates, но не автономную классификацию спама, подбор вакансий или устойчивость модели к смысловой инъекции. Полный live acceptance с вашим provider остаётся обязательным перед сдачей. Порядок ручной проверки — в ACCEPTANCE.md.
