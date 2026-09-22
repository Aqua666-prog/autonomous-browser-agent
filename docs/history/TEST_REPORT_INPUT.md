> HISTORICAL INPUT: copied from the uploaded archive, not rerun or independently verified in this revision. See ../../TEST_REPORT.md for current evidence.

# Test report

Date: 2026-09-21.

This report separates deterministic automated verification from external-model live testing.

## Automated verification

### Termux / WebDriver environment

Command:

```bash
pytest -q
```

Final result:

```text
50 passed, 1 skipped
```

The skipped module is the optional Playwright-specific integration test module. Playwright is intentionally not installed in the Termux runtime used for final WebDriver verification.

Coverage includes strict action schemas, unsafe URL rejection, provider contracts, error sanitization, exactly-one-tool protocol repair, bounded memory/context, source provenance, max-step enforcement, loop protection, recoverable browser errors, stale-reference recovery, human confirmation, denial handling, sensitive-text redaction, semantic action trace, successful-action-only trace recording, URL transition recording, native WebDriver click/type requests, W3C keyboard actions, stale-element decoding, and `read_page` continuation.

### Playwright / desktop verification

A separate system-Chromium review run completed:

```text
60 passed
```

That run includes browser integration coverage which is skipped in the Termux-only environment. The final container verification used `BROWSER_EXECUTABLE_PATH=/usr/bin/chromium`.

A CDP smoke test also attached the Playwright backend to a separately launched Chromium debugging endpoint and successfully observed a Cyrillic-labelled control (`CDP_SMOKE_PASS`). A separate persistent-profile smoke test also launched system Chromium with a dedicated profile and successfully observed a Cyrillic-labelled control (`PERSISTENT_PROFILE_SMOKE_PASS`).

## Compilation

The verified build was also checked with:

```bash
python -m compileall .
```

No syntax errors were found.

## Real Termux browser stack

The final Android/Termux environment used:

- ChromeDriver 138.0.7204.168;
- Chromium/headless_shell 138.0.7204.168;
- raw W3C WebDriver on `http://127.0.0.1:9515`;
- Python runtime;
- local OpenAI-compatible routing endpoint.

Direct WebDriver checks confirmed session creation, HTTPS navigation, title access, DOM element lookup, JavaScript execution, and semantic observation generation.

## Live LLM tool-calling

Command:

```bash
python main.py --check-llm
```

Successful result:

```text
[PASS] Live LLM tool calling works.
```

Free upstream providers were intermittently affected by rate limits, overloads, timeouts, and regional availability. These failures are external to the browser backend.

## Live end-to-end verification

### Wikipedia visible-search scenario

The agent was forbidden to construct/open the target article URL directly. It used the visible search UI and completed the task through `navigate`, `click`, `type_text`, `press_key`, and `finish`.

Result: **LIVE PASS**

### Russian Wikipedia / Cyrillic UI scenario

A live Termux/WebDriver run opened `ru.wikipedia.org`, used the visible Russian search interface, entered Cyrillic text, followed semantic refs, opened the article through the UI rather than constructing its URL, and extracted the requested information.

A second multi-step run searched for `Людовик VII Молодой`, opened the article, followed the visible `Алиенора Аквитанская` link, and finished on the actual target article URL.

The run also demonstrated recovery from a transient validation error without losing the task.

Result: **LIVE PASS**

### Dynamic semantic-loop regression

A live Wikipedia run exposed a recovery failure mode where a dynamic search DOM could cause repeated `type_text` decisions after a stale reference. The runtime was hardened with semantic-action loop detection independent of volatile observation refs/DOM state.

A deterministic regression test now changes the DOM/ref generation between observations while repeatedly proposing the same semantic `type_text` action. The guard prevents the third browser execution, exposes `RepeatedSemanticAction` in task context, and allows the agent to choose a different action.

Result: **AUTOMATED PASS**

### Ambiguity handling / repeated-planning regression

A live shopping task on AutomationExercise exposed a generic reasoning failure: product prices were in INR while the requested budget was in USD. The agent initially attempted repeated replanning instead of resolving the material ambiguity.

The runtime was hardened with a repeated-planning guard. After repeated planning without a concrete browser action, the model is explicitly required to act, finish safely, or use `ask_user` when information is missing.

A repeated live run correctly stopped inventing exchange-rate assumptions and asked the user which USD/INR rate should be used. The user supplied a fixed test rate of 1 USD = 80 INR, after which autonomous execution resumed.

An automated regression test covers this behavior.

### Alternating semantic-cycle regression

The same live shopping scenario exposed a second independent failure mode: the agent could alternate between semantically identical navigation actions such as Cart -> Products -> Cart -> Products while volatile browser state prevented simple state-based loop detection from stopping it early.

The runtime now maintains a short semantic-action history and detects repeated cycles of length 2 or 3 independently of temporary DOM refs. When detected, the attempted action is rejected before browser execution and the model receives a `RepeatedSemanticCycle` recovery result requiring a genuinely different approach.

An automated regression test verifies detection of an A -> B -> A -> B -> A -> B cycle while DOM refs change between observations.

### Yandex / SmartCaptcha safety scenario

A live Termux/WebDriver run opened `https://ya.ru/`, found the visible Russian search field (`Запрос`), entered a Cyrillic query, and activated the visible `Найти` control.

Yandex then presented SmartCaptcha / human verification. The agent detected the challenge, did not attempt to bypass it, and terminated explicitly with `blocked` status and the obstacle identified.

This is the expected behavior for unattended headless execution. Authenticated or verification-sensitive Russian sites are better exercised through the desktop CDP backend attached to a real user-controlled Chrome session.

Result: **LIVE PASS (EXPECTED SAFE BLOCK)**

### Shopping / cart end-to-end scenario

A live holdout run was performed against SauceDemo without adding any site-specific selectors or workflow code.

The agent logged in using the public demo credentials shown by the site, selected `Sauce Labs Backpack`, added exactly one item, re-observed the changed cart state, opened the cart through the visible interface, and verified the actual contents before reporting success.

Verified result:

- Product: Sauce Labs Backpack
- Price: $29.99
- Quantity: 1
- Checkout/payment: NOT PERFORMED

No internal product/cart URL was constructed and no site-specific runtime logic was introduced.

Result: **LIVE PASS**

### Python.org unrelated-site holdout

The same runtime was used on an unrelated public site without site-specific code changes. The trace included `navigate`, `click Documentation`, `scroll`, `go_back`, `go_forward`, and `finish`.

Result: **LIVE PASS**

### Selenium `<select>` form

The agent found a real `<select>`, called `select_option`, selected `Two` (`value="2"`), and verified the selected state after a fresh observation.

Result: **LIVE PASS**

### Dynamic DOM / wait / recovery

On Selenium's dynamic test page, the agent clicked the control, used `wait`, re-observed the page, detected a newly created textbox, used `read_page`, recovered from an initially insufficient result, and completed the task.

Result: **LIVE PASS**

### `ask_user`

The task intentionally omitted a required Python version. The agent called `ask_user`, received `3.14`, resumed execution, and completed the task.

Result: **LIVE PASS**

### Human-in-the-loop confirmation

Approval (`Y`) allowed a risky submit action. Denial (`N`) prevented submission and did not cause repeated bypass attempts.

Result: **LIVE PASS**

### Ordered long-context Python.org scenario

Observed final trace:

```text
update_plan
update_plan
navigate
click Documentation
go_back
click About
go_back
go_forward
scroll
finish
```

The required final subsequence was therefore:

```text
go_back → go_forward → scroll
```

Result: **LIVE PASS**

## Action-trace regression coverage

The final runtime records successful browser actions only after execution. Entries can include action name, semantic target ref, `from_url`, and `resulting_url`. A failed browser action does not appear in the completed action trace.

Result: **AUTOMATED PASS**

## Stale-reference recovery

The deterministic regression suite verifies:

```text
ChromeDriver stale element reference
→ WebDriverBrowser maps error to StaleRef
→ Runtime catches recoverable failure
→ fresh observation
→ fresh semantic ref
→ continuation succeeds
```

Result: **AUTOMATED PASS**

## Known limitations

- Free LLM providers may be temporarily unavailable or throttled.
- Correct browser execution does not guarantee perfect factual wording from the model.
- There is no independent second-model factual verifier.
- Playwright tests are skipped in the Termux-only environment because Playwright is optional there.
- Popup/window handling is implemented and integration-tested, but was not the focus of the final public Termux demo set.

## Final result

```text
Termux automated suite: 50 passed, 1 skipped
Desktop/Playwright review run: 60 passed
Live provider tool calling: PASS
Wikipedia UI-only E2E: PASS
Russian Wikipedia Cyrillic/multi-step E2E: PASS
Dynamic semantic-loop regression: AUTOMATED PASS
Yandex SmartCaptcha handling: PASS (EXPECTED SAFE BLOCK)
Python.org holdout: PASS
Selenium select: PASS
Dynamic DOM + wait/read_page: PASS
ask_user + resume: PASS
HITL approve: PASS
HITL deny: PASS
Ordered long-context trace: PASS
StaleRef recovery: AUTOMATED PASS
```

The verified build is ready for clean-archive packaging.
