# GigaChat provider — 2026-09-22

- Исправлена ложная классификация обычного GET search submit как consequential action: Runtime распознаёт `type=search` в той же GET-форме без ослабления POST и обычных GET-submit.
- Добавлены regression cases для GET search submit, POST search submit и GET submit без matching search input.
- Полный текущий suite: **145 passed, 18 skipped, 0 failed**.
- `--check-browser` и реальный GigaChat `--check-llm` — PASS.
- Автономный GigaChat-3-Ultra → WebDriver → Wikipedia E2E — LIVE PASS, ответ 1137, COMPLETE за 11 шагов.

- Добавлен отдельный официальный GigaChat REST provider: OAuth Authorization key → cached access token → automatic refresh.
- По умолчанию для GigaChat используется `GigaChat-3-Ultra` и `https://api.giga.chat/v1/`; scope/endpoint/TLS CA настраиваются через env.
- OpenAI-style registry tools конвертируются в GigaChat `functions`; runtime names с `_` получают deterministic letters-only aliases и маппятся обратно перед Pydantic validation.
- Поддержаны object/string `function_call.arguments`, один protocol-repair turn, refresh после chat 401 и bounded retries для timeout/transport/408/429/5xx.
- Добавлены `LLM_MAX_RETRIES`, `LLM_RETRY_BACKOFF`, `GIGACHAT_CA_BUNDLE`, `GIGACHAT_VERIFY_SSL` и Termux one-shell инструкции.
- Добавлены 8 provider/config regression tests. Текущая среда без ChromeDriver/live network: 145 passed, 18 skipped, 0 failed в сегментированных прогонах; security baseline до этого provider-патча: 197 passed, 1 skipped, 0 failed в полностью настроенном окружении.

---

# Исправления независимого аудита — 2026-09-22

- Runtime запрещает text/batch ввод в file inputs; WebDriver дополнительно проверяет тип перед clear/send keys.
- Подтверждённый Enter сравнивает адрес/метод формы и дополнительные поля свежего наблюдения.
- Focus определяется до leaf во вложенном открытом Shadow DOM, включая find/collection serializers.
- Добавлены 16 real-browser regression cases. Текущий полный результат: **197 passed, 1 skipped, 0 failed**.
- Исторический Wikipedia live PASS отделён от текущего независимо воспроизведённого GigaChat/Termux live PASS.
- Архитектура и существующие тестовые assertions сохранены.

---

История предыдущих ревизий; старые результаты ниже не описывают текущий submission.

# Runtime hardening — 2026-09-22

- Page-local search budget: две попытки на semantic state, bounded refinement, structured recovery; synonyms, volatile refs и replanning не обходят ограничение.
- Обычные focus/search/menu/navigation interactions не получают strict verification из-за одного `expected_outcome`; consequential barriers сохранены.
- Рискованный `Submit application` вне нативной формы теперь распознаётся; повторный verify без pending не расходует весь лимит шагов.
- Timeout после dispatch оставляет pending и uncertain action trace. Stale preflight не создаёт ложную транзакцию.
- Ref identity учитывает форму, destination и autocomplete; WebDriver привязывает refs к окну. Tab indices сверяются с последним наблюдением.
- Поиск предпочитает actionable controls вместо одноимённых landmarks. Labels больше не включают весь список select options.
- Structured records сохраняют явные поля таблиц/dl/microdata, headings, status и time. Две последние выборки удерживаются в bounded context.
- Form snapshots показывают обычные значения, required/readonly/expanded; batch повторно проверяет каждый target.
- Upload resolver усилил проверку путей; confirmation показывает basename без пути, обычные logs скрывают оба; verification проверяет нужное имя файла.
- Local WebDriver transport не использует ambient proxy. Несколько новых окон не выбираются по предполагаемому порядку.
- Добавлены runtime, browser, security и real WebDriver regression tests, synthetic acceptance stand и opt-in Wikipedia smoke.
- Обновлены README, TEST_REPORT, LIVE_E2E и приёмочные критерии. Точные результаты — в TEST_REPORT; live LLM не выдаётся за проверенный.

---

Ниже — исторический changelog входного проекта; прежние числа тестов не относятся к этой ревизии.

# Changelog

## Текущая ревизия — 2026-09-22

Продолжена именно загруженная Work-версия; архитектура Runtime + provider + Playwright/WebDriver не переписывалась. После изучения актуальных open-source browser-agent решений добавлены только универсальные механизмы, без site-specific workflows.

- `find_in_page`: поиск по полному набору интерактивных элементов последнего semantic observation, включая элементы за пределами обычных первых 180; найденные refs остаются пригодными для следующего решения. Идея адаптирована из `browser_find` Playwright MCP.
- `extract_page`: read-only semantic sections/headings/forms/links для быстрой ориентации на незнакомой странице без site-specific selectors.
- `extract_collection`: generic extraction повторяющихся `article`/list/table/ARIA-row элементов для почты, карточек товаров, вакансий и других коллекций. Вложенные controls получают snapshot-scoped refs и могут быть использованы на следующем decision.
- Для современных SPA без semantic roles добавлен structural fallback: повторяющиеся sibling-card группы выделяются по DOM-структуре, а не по CSS-классам/текстам конкретного сайта.
- `manage_tabs`: list/select/new/close в Playwright и WebDriver. Закрытие вкладки всегда требует human confirmation; fingerprint теперь учитывает tab state, поэтому close/select можно проверять как реальный state transition.
- `fill_form`: пакетное заполнение нескольких стабильных text/select полей; значения не выводятся в action log. Для динамически зависимых полей модель по-прежнему должна использовать отдельные действия.
- `upload_file`: загрузка только относительного файла из оператором заданного `BROWSER_UPLOAD_ROOT`. Абсолютные/`..` пути запрещены, лимит 25 MiB, действие всегда требует human confirmation, имя выбранного файла наблюдаемо для post-action verification.
- Recovery: в model context добавлен явный `last_action_error`; повторный `verify_action` без pending mutation теперь возвращает специфичный `NoPendingVerification`, а не общий `PolicyError`.
- Context compaction теперь сохраняет более ёмкие structured extraction results и при переполнении recent history приоритетно оставляет самые свежие tool results.
- Prompt обновлён: `verify_action` вызывается только при непустом `pending_verification`; find/fill/upload описаны как generic tools, page content остаётся untrusted.
- Добавлены Playwright/WebDriver/unit/safety tests для поиска за пределами snapshot, form batch fill, root-scoped upload, redacted logs, human approval и snapshot-ref reuse.
- Текущая проверка в контейнере: исторический результат предыдущей ревизии (не актуальный suite); единственный skip — CDP continuity test из-за browser policy окружения (`ERR_BLOCKED_BY_ADMINISTRATOR` даже для intercepted localhost). Новые real-Chromium tests покрывают structured extraction и управление вкладками; production `--check-browser` PASS.

Design references: Microsoft Playwright MCP (`browser_find`, `browser_fill_form`, `browser_file_upload`), browser-use (явный error/history state), Skyvern (generic form/validation patterns). Код не копирует их workflows и не добавляет сценарии под Gmail/HH/магазины.

## Текущая ревизия — 2026-09-21 UTC

Продолжен загруженный проект; исходная архитектура Runtime + provider + Playwright/WebDriver сохранена.

- Введены optional expected_outcome и generic verify_action. Pending verification создаётся до важной мутации, сохраняется после timeout; complete и повторная важная мутация блокируются до проверки.
- Evidence должна находиться в текущем snapshot/актуальном read_page. Для achieved нужны изменившееся состояние и новая evidence; dispatch success не трактуется как outcome success.
- Native form semantics, Enter/Space, submit controls участвуют в confirmation; focus проверяется повторно после approval. Изменение expected_outcome не сбрасывает denial.
- Обновлены инструкции untrusted data для страниц, derived facts и verification. Нет tools для shell/filesystem/arbitrary JS. Добавлен runtime handoff для распознанного challenge и password/OTP typing.
- Memory: bounded payload/results/trace, keyed facts с provenance; первоначальная цель и ответы сохранены. Переполнение facts/answers обрабатывается явно.
- Общий observer показывает form semantics, numeric/select values, validation errors и truncation. Generic switch_tab работает в обоих backend, существующий popup/closed-tab recovery сохранён.
- CLI получил --check-browser без LLM/key; CDP detach проверен на реальном Chromium с synthetic session.
- Добавлены 36 regression/integration cases: итог 99 вместо исходных 63 в desktop-окружении. Исходные 50 Termux core/WebDriver cases сохранены.
- Добавлен локальный синтетический acceptance site, инструкции Windows/CDP/Termux и честное разделение automated/live/blocked/unverified.
- requirements дополнены тремя фактически установленными transitive pins; requirements-test выделяет tests для WebDriver-only установки.
- Source-only release builder с allowlist/secret scan; .gitignore расширен. В release нет .env, cookies, profiles, auth state и caches.

Live LLM E2E и реальные аккаунты новой версии не проверены. Исторические отчёты входного архива сохранены в docs/history и не обозначены как новые PASS.
