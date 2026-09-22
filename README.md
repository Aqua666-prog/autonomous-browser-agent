# General-purpose AI Browser Agent

Продолжение существующего проекта из `browser-agent-current-for-fix.zip`. Python 3.11+, проверено на Python 3.12. Агент получает задачу естественным языком и выполняет цикл `observe → decide → act → observe`. Решение на каждом шаге принимает LLM через tool calling. Код не содержит сценариев, селекторов или ответов под конкретные сайты.

Текущая ревизия сохраняет security-fix baseline и добавляет официальный `GigaChat` provider без изменения архитектуры Runtime/browser backends. Адаптер автоматически получает и обновляет access token, поддерживает GigaChat custom function calling, нормализует tool names/arguments и повторяет временные сетевые сбои. До provider-патча security baseline был независимо проверен как **197 passed, 1 skipped, 0 failed**. Текущая Termux/GigaChat ревизия проверена полным regression-прогоном: **147 passed, 19 skipped, 0 failed**. Также подтверждены `--check-browser`, `--check-llm` и реальные автономные browser E2E. Подробности — в `TEST_REPORT.md`.

**Текущие live PASS:** в Android/Termux с GigaChat-3-Ultra и видимым Chromium через Termux:X11 автономный Wikipedia E2E завершён за **7 шагов** с ответом **1137**; отдельно synthetic shop E2E завершён за **14 шагов**: агент добавил 2 синих блокнота, проверил итог **24 EUR**, перешёл к оформлению и остановился перед оплатой. Исторические результаты предыдущих ревизий отделены в `LIVE_E2E.md`.

## Быстрый запуск на Windows — PowerShell

Установите Python 3.12, распакуйте ZIP и откройте терминал в папке `browser-agent-ru-final`.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.example .env
notepad .env
```

В `.env` укажите provider/model и локальные credentials. Поддерживаются официальный GigaChat REST API, Z.AI и OpenAI-compatible tool-calling endpoints. `.env.example` теперь по умолчанию показывает `GigaChat-3-Ultra`: задайте `GIGACHAT_AUTH_KEY`, а access token агент получит и обновит сам. На системах, где не доверен сертификатный корень GigaChat, используйте `GIGACHAT_CA_BUNDLE`; подробности — `docs/GIGACHAT.md`. Секреты храните только локально и не включайте `.env` в submission. Для локального сервера без аутентификации остаётся `LLM_PROVIDER=openai_compatible`, его URL/модель и `LLM_API_KEY_REQUIRED=false`.

```powershell
.\.venv\Scripts\python.exe main.py --check-browser --headless
.\.venv\Scripts\python.exe main.py --check-llm
.\.venv\Scripts\python.exe main.py --task "Открой https://ru.wikipedia.org/. Через видимое поле поиска найди Элеонору Аквитанскую. В каком году она стала королевой Франции? Укажи URL источника."
.\.venv\Scripts\python.exe -m pytest -q
```

`--check-browser` не требует LLM-ключа, наблюдает браузер без навигации. `--check-llm` проверяет реальный вызов tool у настроенного provider. Exit code: 0 — complete/PASS; 2 — blocked/ошибка. `Ctrl+C` останавливает выполнение. `HEADLESS=false` — видимый браузер для ручного входа и handoff. `.env.example` задаёт 100 шагов; допустимый `MAX_AGENT_STEPS` — 1–200, внутренний default без env — 30.

Linux/macOS: `python3 -m venv .venv`, затем `.venv/bin/python -m pip install -r requirements.txt`, `.venv/bin/python -m playwright install chromium`. На минимальном Linux могут понадобиться системные библиотеки браузера: `python -m playwright install --with-deps chromium`.


### GigaChat quick check

```dotenv
LLM_PROVIDER=gigachat
LLM_MODEL=GigaChat-3-Ultra
LLM_BASE_URL=https://api.giga.chat/v1/
GIGACHAT_AUTH_KEY=<ваш Authorization key>
GIGACHAT_SCOPE=GIGACHAT_API_PERS
MODEL_TIMEOUT=180
```

`python main.py --check-llm` выполняет настоящий function-calling smoke. GigaChat access token действует ограниченное время; provider кэширует его и автоматически обновляет. Runtime tool names с `_` преобразуются только на границе provider в документированные letters-only aliases и после ответа отображаются обратно. См. `docs/GIGACHAT.md`.

## Реальный Chrome и уже авторизованная сессия — CDP

Chrome должен быть запущен с отладочным портом и **отдельным постоянным профилем**. Обычный уже открытый Chrome без CDP подключить нельзя. Начиная с Chrome 136, отладка стандартного каталога пользовательских данных ограничена: [официальное объяснение Chrome](https://developer.chrome.com/blog/remote-debugging-port).

PowerShell, первое окно:

```powershell
$chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
if (!(Test-Path $chrome)) { $chrome = "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe" }
if (!(Test-Path $chrome)) { $chrome = "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe" }
if (!(Test-Path $chrome)) { throw "Chrome не найден" }
$profile = Join-Path $env:LOCALAPPDATA "BrowserAgent\ChromeProfile"
& $chrome --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 "--user-data-dir=$profile"
```

В открытом Chrome **войдите вручную** в нужный сайт. При следующих запусках с тем же `$profile` сессия сохраняется, пока сайт её не завершит. Пароли/cookies не копируются в проект. Не запускайте второй Chrome с этим же профилем одновременно. Отладочный порт предназначен только для локального подключения.

Второе окно PowerShell, из папки проекта:

```powershell
$env:BROWSER_BACKEND = "cdp"
$env:BROWSER_CDP_URL = "http://127.0.0.1:9222"
$env:CONFIRMATION_MODE = "all"
$env:MAX_AGENT_STEPS = "100"
# Optional for resume/file attachment; upload_file can read only this folder:
# $env:BROWSER_UPLOAD_ROOT = "$env:USERPROFILE\Documents\BrowserAgentUploads"
Invoke-RestMethod http://127.0.0.1:9222/json/version
.\.venv\Scripts\python.exe main.py --check-browser
.\.venv\Scripts\python.exe main.py --task "На уже открытом сайте почты прочитай последние 10 писем. Сначала сообщи кандидатов в спам и причины. Перед перемещением каждого письма запроси подтверждение. Проверь результат и сообщи точное число обработанных писем."
```

После контролируемой приёмки `CONFIRMATION_MODE=risky` оставляет обычный просмотр автономным. `all` запрашивает подтверждение для кликов, ввода, клавиш и навигации; полезен на незнакомых авторизованных сайтах. `none` сохраняется для одноразовых тестовых стендов, не для реальных транзакций. CDP disconnect не закрывает Chrome. Synthetic CDP continuity в текущем прогоне прошёл: подключение, сохранение тестовой localStorage-сессии и отключение без закрытия Chromium. Реальные аккаунты и Windows GUI здесь не проверены.

## Реализованные patterns

- Planning/replanning: короткий план до шести шагов, защита от повторного планирования.
- Semantic observation: роли, подписи, состояние controls, варианты select, количество, validation messages, вкладки; текущие refs вместо CSS/XPath от модели.
- Tool use: navigate/click/type/select/key/scroll/history/wait/read_page, `find_in_page`, `extract_page`, `extract_collection`, `fill_form`, root-scoped `upload_file`, а также `manage_tabs` (list/select/new/close; старый `switch_tab` сохранён для совместимости). Только зарегистрированные инструменты с Pydantic validation, без shell/eval/произвольного JS для модели.
- Memory: исходная задача, пользовательские уточнения, keyed facts с URL источника, план, последние результаты и trace.
- Recovery: stale refs, явный `last_action_error`, ошибки браузера/навигации, динамический DOM, popup/закрытие вкладки, validation errors, ожидание содержимого, повторные действия и A–B–A–B циклы.
- Human-in-the-loop: ask_user, ручной login/OTP/CAPTCHA, подтверждение рискованных действий.
- Post-action verification: `expected_outcome`, pending mutation, `verify_action` и наблюдаемое доказательство результата.


## Research / design references

Сохранены архитектурные ориентиры, описанные в исходном проекте; новый внешнеисследовательский обзор в этой ревизии не проводился. Из Microsoft Playwright MCP адаптированы идеи дешёвого поиска по semantic snapshot (`browser_find`), отдельных generic form/file tools и явного управления вкладками; из browser-use — явный error/history state для recovery; из Skyvern — разделение form actions, extraction и validation. В текущей реализации `extract_page` и `extract_collection` детерминированно компактят незнакомые страницы в semantic sections и повторяющиеся mail/product/job/list/table records; LLM не получает инструмент произвольного JavaScript. Эти проекты используются как архитектурные ориентиры, а не как site-specific workflow source. Production-код по-прежнему не содержит Gmail/HH/магазинных маршрутов или заранее заданных последовательностей.

- https://github.com/microsoft/playwright-mcp
- https://github.com/browser-use/browser-use
- https://github.com/Skyvern-AI/skyvern

## Как работает проверка результата

Успех browser tool означает только отсутствие ошибки вызова. Runtime различает обычное взаимодействие, значимое обратимое изменение и consequential action. Само наличие `expected_outcome` больше не делает focus, search textbox, menu или navigation click бизнес-транзакцией. Распознанные рискованные controls, нативная отправка формы, upload и закрытие вкладки сохраняют confirmation policy. Для consequential actions и распознанных обратимых изменений (например, количество или добавление в корзину) runtime устанавливает pending verification **до dispatch**. Это защищает и от повторной отправки после неоднозначного таймаута.

Модель должна исследовать новое состояние и вызвать `verify_action` с точной цитатой из текущего наблюдения/актуального `read_page`, ожидаемым смыслом результата и статусом `achieved`, `not_achieved` либо `uncertain`. Для `achieved` требуются изменение состояния и новое доказательство, отсутствовавшее до действия. До проверки нельзя завершить задачу как complete или выполнить следующую распознанную важную мутацию. `not_achieved` фиксирует неудачу и разрешает исправление; `uncertain` сохраняет барьер. DOM change сам по себе не считается бизнес-успехом.

**Предел гарантии:** runtime проверяет происхождение цитаты и наличие изменения, но соответствие доказательства намерению оценивает LLM. Он не может универсально доказать бизнес-смысл любого сайта. Непонятные custom controls могут не распознаться эвристикой; режим `all` и проверка видимого результата важны при приёмке. `expected_outcome` — пояснение намерения и дополнительный сигнал для некоторых обратимых изменений, а не универсальный переключатель проверки. Статус complete после подтверждённой неудачи требует смысловой проверки моделью, а не универсального формального доказательства всей задачи.

## Prompt injection и ограничения

Страницы, labels, URL, сохранённые факты и evidence считаются untrusted data. Они не меняют system policy и не разрешают действия. Подтверждения выполняются runtime; отказ нельзя сбросить переформулировкой `expected_outcome`. Нативная отправка формы, включая Enter, требует подтверждения (семантически обозначенный поиск может быть автономным). Password и OTP поля требуют ручного ввода в браузере. Распознанный human challenge блокирует активные browser actions до участия человека.

Это **защита в несколько слоёв, не доказанная невосприимчивость к prompt injection**. Страница может обманывать текстом и семантикой; для приватных сценариев используйте `all` и проверяйте видимый интерфейс перед разрешением. Провайдер LLM получает наблюдаемый текст страницы — учитывайте это при работе с личной почтой/резюме. API key не помещается в модельный контекст; значения вводимых полей не выводятся в action log. Не включайте реальные приватные логи в демонстрацию.

Нативные JavaScript confirm/prompt автоматически отклоняются; отдельного accept-dialog tool нет.

Ограничения: iframe controls не поддержаны как полноценные отдельные интерактивные контексты; canvas-only интерфейсы, downloads, сложные drag-and-drop и гарантированное обнаружение всех CAPTCHA не реализованы. File upload поддержан только через `BROWSER_UPLOAD_ROOT` и всегда требует подтверждения. Memory живёт в одном запуске, browser profile — между запусками. После перезапуска агент не возобновляет автоматически незавершённую транзакцию.

## Ограничения контекста

Task до 12 000 символов, observation до 28 000, latest read до 12 000, последние 8 tool results до 3 000 каждый / 16 000 в payload, trace до 200 операций / 24 000 в payload (приоритет свежим). До 24 facts по 1 000 символов с bounded provenance; существующий key обновляет факт. Переполнение facts сообщает ошибку и требует compact/update, не молча удаляет выбранные позиции. Ответы пользователя не вытесняются: до 4 000 на ответ и 16 000 суммарно с вопросами; превышение останавливает работу. Две последние collection-выборки дополнительно сохраняются с source URL (до 10 000 символов каждая), даже после вытеснения из recent history; их старые refs не продлеваются и требуют текущего наблюдения. Общий JSON payload ограничен 200 000 символов. Это символьные лимиты, не обещание вместиться в контекст любой модели. Проверен запуск на 108 шагах с сохранением плана, первых ответов и 24 candidates.

## Локальная демонстрация и сдача

```powershell
.\.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1 --directory tests/fixtures
```

Откройте `http://127.0.0.1:8765/` для исходного стенда или `http://127.0.0.1:8765/acceptance.html` для нового стенда с десятью письмами, корзиной и тремя вакансиями. Используйте задачи из `DEMO_SCRIPT.md` и `ACCEPTANCE.md`. Стенд содержит синтетические письма, магазин и вакансии. Его JavaScript — код тестового сайта, не workflow агента; production runtime его не импортирует. Исторический shop-прогон действительно завершался HTTP 413 и отдельно сохранён как исторический результат. На текущей GigaChat/Termux ревизии synthetic shop E2E независимо повторён успешно: **LIVE PASS, COMPLETE за 14 шагов**, 2 синих блокнота, итог 24 EUR, переход к оформлению без оплаты.

`ACCEPTANCE.md` содержит финальные критерии. `RU_SITES.md` — авторизованную приёмку. `TERMUX.md` — сохранённый WebDriver путь. `scripts/release.py` создаёт исходный ZIP по allowlist и проверяет известные форматы secrets; scanner не заменяет ручную проверку нестандартных токенов.


## Runtime-контракт поиска и refs

На один неизменный semantic fingerprint разрешены максимум два `find_in_page` независимо от формулировки. После результата с actionable controls сначала используйте ref или выполните содержательное чтение/действие. Один уточняющий поиск допустим после нового inspection либо с `refinement_reason` (12–300 символов); это не увеличивает общий бюджет. Повтор того же запроса отклоняется. После двух zero-match запросов дальнейшие синонимы блокируются. Четыре игнорирования recovery без конкретного продвижения завершают run безопасно. Хранятся бюджеты последних 16 состояний; ref generation и focus не входят в fingerprint, реальное изменение страницы даёт новое состояние. Непрерывно меняющийся текст может давать новые fingerprints; дополнительно работают semantic/cycle guards и общий лимит шагов.

`find_in_page` и `extract_collection` не вызывают промежуточный observe: новые refs добавляются в текущую generation и доступны следующему decision. Расширение списка refs само по себе не сбрасывает search budget. После ошибки нужен fresh observation. Перед ref-действием и после confirmation проверяются identity элемента, форма/её action, autocomplete и принадлежность вкладке. Подтверждённый `press_key` дополнительно сверяет form_action, form_method, form_role, autocomplete и href с новым наблюдением; изменение останавливает dispatch. Observer и find/collection определяют focused leaf через вложенные открытые shadow roots. Изменение несвязанного DOM не инвалидирует стабильный control. Возврат ref не гарантирует, что элемент не изменится асинхронно: в этом случае возвращается `StaleRef` и структурированная причина recovery.

## Structured extraction и формы

`extract_collection` возвращает исходный видимый `text`, `links`, `controls` с refs, `fields` и `times`. Поля извлекаются из headings, table headers, `dt/dd`, видимого microdata `itemprop`, подписей controls и `role=status`. Названия явно размеченных полей сохраняются; отсутствующие sender/salary/price/currency не выдумываются. Неоднозначные суммы остаются строками, валюты не конвертируются автоматически. Для неразмеченных карточек доступны исходный текст и controls, а не обещание универсального нормализованного объекта.

Observer показывает обычные значения input/textarea/select (до 4000 символов), required, readonly, checked, expanded и validation messages; password/OTP values скрыты. Checkbox/radio меняются через `click`, autocomplete — через ввод и выбор текущего наблюдаемого option. `fill_form` предназначен для text/select; при динамической замене следующего поля batch останавливается, ранее заполненные поля не откатываются. После fresh observe продолжайте с новыми refs. Кастомные autocomplete и multi-step формы требуют отдельных решений, а не фиксированного workflow.

Текстовые инструменты `type_text` и `fill_form` отклоняют файловые поля на уровне runtime; WebDriver повторяет запрет после resolve перед clear/send keys. Для прикрепления файла используется только `upload_file`.

Upload разрешён только под `BROWSER_UPLOAD_ROOT`: relative path, запрет traversal/absolute/control characters, symlink escape и лимит 25 MiB. В интерактивном confirmation показывается только имя файла, без каталогов; обычный action log скрывает путь и имя. Для успешной verification требуется наблюдаемое имя именно выбранного файла. Локальный процесс должен контролировать содержимое upload-root: защита от конкурентной подмены файла другим локальным процессом не заявляется.

## Дополнительные проверки

```powershell
# Необязательный внешний browser smoke, без LLM:
$env:RUN_LIVE_WIKIPEDIA="1"
.\.venv\Scripts\python.exe -m pytest -q -rs -s tests/test_wikipedia_live.py
Remove-Item Env:RUN_LIVE_WIKIPEDIA

# Реальный WebDriver parity при установленном совместимом ChromeDriver:
$env:CHROMEDRIVER_EXECUTABLE_PATH="C:\tools\chromedriver.exe"
$env:BROWSER_EXECUTABLE_PATH="C:\tools\chrome.exe"
.\.venv\Scripts\python.exe -m pytest -q tests/test_webdriver_live.py
```

Без переменных live WebDriver tests пропускаются с явной причиной. Wikipedia smoke не конструирует URL статьи, но использует управляемую последовательность в тесте; автономный LLM acceptance запускается отдельно через `main.py --task`. Production code не импортирует тестовые сценарии.
