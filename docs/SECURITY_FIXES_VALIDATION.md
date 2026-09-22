# Проверка исправлений независимого аудита — 2026-09-22

Исходный ZIP SHA-256: `1b4ef080d4b1a7b74ef60783cfed94c9ed3d8bca8f5640cc4d1e712e4c716e0d`. Старый архив не изменён.

## Изменённые файлы

- `ACCEPTANCE.md`
- `CHANGELOG.md`
- `DEMO_SCRIPT.md`
- `LIVE_E2E.md`
- `README.md`
- `TEST_REPORT.md`
- `browser_agent/browser.py`
- `browser_agent/browser_webdriver.py`
- `browser_agent/extraction.py`
- `browser_agent/observer.js`
- `browser_agent/runtime.py`
- `tests/test_browser_regressions.py`
- `tests/test_webdriver_live.py`

Добавлен этот файл `docs/SECURITY_FIXES_VALIDATION.md`. Архитектура и зависимости не изменены; существующие tests сохранены, regression cases дописаны в два тестовых модуля.

## Воспроизведение до исправлений

Четыре успешных воспроизведения трёх дефектов (два пути upload). Здесь passed означает наличие дефекта.

```text
REPRO type_text: page read file outside root with uploads disabled; zero approvals
.REPRO fill_form: page read file outside root with uploads disabled; zero approvals
.REPRO Enter: form submitted after action destination changed during approval
.REPRO shadow focus: Enter submits native form without confirmation or verification
.
4 passed, 2 deselected in 2.11s
```

## Полный suite после исправлений

Команда: `CHROMEDRIVER_EXECUTABLE_PATH=/path/to/chromedriver BROWSER_EXECUTABLE_PATH=/path/to/headless_shell python -m pytest -q -rs`, из корня проекта.

```text
........................................................................ [ 36%]
........................................................................ [ 72%]
.....................................................s                   [100%]
=========================== short test summary info ============================
SKIPPED [1] tests/test_wikipedia_live.py:16: Opt-in external Wikipedia visible-UI test
197 passed, 1 skipped in 37.78s
```

## Browser CLI

Команда: `BROWSER_EXECUTABLE_PATH=/path/to/headless_shell python main.py --headless --check-browser`.

```text
AI Browser Agent
================
[PASS] Browser observed: 0 elements; 1 tabs.
[PASS] Browser closed.
```

## LLM CLI без credentials

`python main.py --check-llm` с закрытым stdin, exit 2: провайдер не проверен. Исторический live PASS описан отдельно в LIVE_E2E.md.

```text
AI Browser Agent
================
[ERROR] Invalid configuration, missing key, or missing task. Check .env.example.
```

## Wikipedia opt-in browser smoke

`RUN_LIVE_WIKIPEDIA=1 BROWSER_EXECUTABLE_PATH=/path/to/headless_shell python -m pytest -q -rs tests/test_wikipedia_live.py`.

```text
s                                                                        [100%]
=========================== short test summary info ============================
SKIPPED [1] tests/test_wikipedia_live.py:22: Environment network prevents Wikipedia access; no bypass attempted
1 skipped in 0.80s
```

## Остальные проверки

`compileall` — PASS; `pip check` — No broken requirements found. Версии Python 3.12.14, Playwright 1.63.0, Chromium/ChromeDriver 140.0.7339.16.

Release scanner проверяет финальные source/documentation файлы. При упаковке отдельно проверены ZIP CRC, отсутствие .env/keys/cookies/profiles/auth state/caches, symlinks и traversal paths, а также побайтное совпадение каждого архивного файла с проверенным исходником. Найденные credential-like значения в старых unit tests — искусственные test fixtures. Новые тесты читают только собственный временный файл с синтетическим маркером. Нестандартные неизвестные секреты сканер не гарантирует распознать.
