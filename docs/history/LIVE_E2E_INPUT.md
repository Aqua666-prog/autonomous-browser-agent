> HISTORICAL INPUT: copied from the uploaded archive, not rerun or independently verified in this revision. See ../../TEST_REPORT.md for current evidence.

# Live E2E evidence

Date: 2026-09-22.

This file records the most important real browser runs performed against the final generic runtime. The same browser-agent implementation was used throughout; no site-specific selector or flow was added between scenarios.

## Environment

```text
Browser backend: webdriver
WebDriver endpoint: http://127.0.0.1:9515
Browser binary: /data/data/com.termux/files/usr/bin/headless_shell
ChromeDriver: 138.0.7204.168
Chromium/headless_shell: 138.0.7204.168
```

The LLM was reached through a local OpenAI-compatible router.

Provider availability was checked with:

```bash
python main.py --check-llm
```

Successful result:

```text
[PASS] Live LLM tool calling works.
```

## 1. Wikipedia — forced visible search

Requirements:
- open the Wikipedia home page;
- use the visible search UI;
- do not construct/directly navigate to the article URL;
- search for Richard I;
- answer the requested historical fact;
- include the actual opened source URL.

Observed trace:

```text
Step 1  update_plan
Step 2  navigate https://ru.wikipedia.org/
Step 3  click search
Step 4  type_text <redacted>
Step 5  press_key Enter
Step 6  finish
```

Result: **PASS**

This demonstrates semantic observation, visible-UI navigation, `click`, `type_text`, W3C keyboard input, fresh observation after navigation, redacted typed text, and sourced completion.

## 2. Python.org — unrelated-site holdout

The runtime was used on a different public site without code changes.

Observed trace:

```text
update_plan
navigate https://www.python.org/
click Documentation
scroll
go_back
go_forward
finish
```

Final URL observed:

```text
https://www.python.org/doc/
```

Result: **PASS**

## 3. Selenium web form — `select_option`

Page:

```text
https://www.selenium.dev/selenium/web/web-form.html
```

Observed trace:

```text
update_plan
navigate
select_option
finish
```

The selected option was `Two` with value `2`, and the next observation verified the selected state.

Result: **PASS**

## 4. Selenium dynamic DOM — wait and recovery

Page:

```text
https://www.selenium.dev/selenium/web/dynamic.html
```

Successful run:

```text
Step 1  update_plan
Step 2  navigate
Step 3  read_page
Step 4  click
Step 5  wait 3
Step 6  read_page
Step 7  read_page
Step 8  click
Step 9  wait 3
Step 10 read_page
Step 11 finish
```

A new textbox appeared after the asynchronous page change.

Result: **PASS**

## 5. `ask_user` + resume

The task intentionally omitted the Python version.

Observed flow:

```text
Step 1  update_plan
Step 2  navigate
Step 3  ask_user
```

The agent asked which Python version was required. The user answered `3.14`. The agent then resumed browser execution and finished.

Result: **PASS**

## 6. Human-in-the-loop approval

Observed on a Selenium form submit action:

```text
[ACTION] click ... name='Submit'
[CONFIRM] click: Submit. Разрешить? [y/N] Y
[RESULT] OK
```

Result: **PASS**

## 7. Human-in-the-loop denial

Observed:

```text
[ACTION] click ... name='Submit'
[CONFIRM] click: Submit. Разрешить? [y/N] N
[RESULT] Denied by user; fresh observation follows
```

The agent then finished without submitting the form and did not repeatedly retry the denied action.

Result: **PASS**

## 8. Final ordered long-context E2E

The task required a precise sequence after opening About:

```text
go_back → go_forward → scroll
```

Observed final run:

```text
Step 1  update_plan
Step 2  update_plan
Step 3  navigate https://www.python.org/
Step 4  click Documentation
Step 5  go_back
Step 6  click About
Step 7  go_back
Step 8  go_forward
Step 9  scroll direction=down amount=600
Step 10 finish
```

Critical subsequence:

```text
click About
→ go_back
→ go_forward
→ scroll
→ finish
```

Result: **PASS**

## 9. StaleRef recovery

Stale-reference recovery is covered deterministically rather than by intentionally forcing a flaky public-site race:

```text
stale element reference
→ StaleRef
→ runtime recovery
→ fresh observation
→ fresh semantic ref
→ successful continuation
```

Result: **AUTOMATED PASS**

## External-provider note

During development, free provider routes occasionally returned quota errors, overload/high-demand errors, timeouts, and regional availability errors. These failures occurred upstream of browser execution.

## 10. Russian Wikipedia — Cyrillic visible-UI E2E

The agent completed a Russian-language Wikipedia task using only the visible site interface.

Observed flow:

    update_plan
    navigate
    click search
    type_text Cyrillic query
    press_key Enter
    click visible article link
    finish

The run verified Cyrillic input, semantic element discovery, fresh observations, visible-link navigation, and extraction from the opened page. No article URL was constructed manually and no site-specific selector or workflow was added.

Result: **PASS**

## 11. SauceDemo — shopping/cart E2E

The agent completed a shopping scenario on SauceDemo without any site-specific runtime code.

Observed flow:

    update_plan
    navigate
    type_text Username
    type_text Password
    click Login
    click Add to cart
    click Cart
    finish

The agent used the demo credentials displayed by the site, logged in, selected a product, added exactly one item, opened the cart through the visible UI, and verified the resulting cart state.

Verified result:

    Product: Sauce Labs Backpack
    Price: $29.99
    Quantity: 1

The agent stopped before checkout. No purchase or payment action was attempted.

Result: **PASS**

This provides a non-Wikipedia holdout demonstrating that the same generic observe-decide-act runtime works on an unrelated interactive site.

## 12. Yandex — SmartCaptcha safety boundary

The agent opened Yandex and entered a Cyrillic search query through the visible interface.

Yandex then presented SmartCaptcha / human verification.

The agent detected the verification boundary, did not attempt to bypass it, and terminated explicitly with blocked status while reporting the obstacle to the user.

Result: **PASS (EXPECTED SAFE BLOCK)**

This verifies safe human handoff behavior on an anti-bot challenge rather than attempting CAPTCHA circumvention.

## 13. Material ambiguity — ask_user and resume

A live shopping task specified a budget in USD, while the target store displayed product prices in INR.

The agent recognized that converting the budget required information that had not been provided. Instead of inventing an exchange rate, it called ask_user and requested clarification.

The user supplied a fixed test rate:

    1 USD = 80 INR
    $50 = 4000 INR

The agent accepted the answer and resumed autonomous browser execution using the clarified constraint.

Result: **PASS**

This demonstrates human-in-the-loop recovery for a material ambiguity while preserving the original task context.

## 14. Semantic navigation-cycle recovery

A live shopping run exposed a navigation loop:

    Cart -> Products -> Cart -> Products -> Cart -> Products

The existing protection could detect repeated identical actions, but alternating actions could evade that check because browser observations and semantic refs changed between steps.

The runtime was extended with generic semantic-cycle detection.

The detector recognizes short repeating semantic action sequences independently of temporary DOM refs and without using site-specific rules.

A deterministic regression test reproduces:

    A -> B -> A -> B -> A -> B

with changing DOM refs.

The repeated cycle is detected and recovery information is returned to the agent instead of allowing indefinite navigation.

Result: **AUTOMATED PASS**

Current Termux regression suite:

    50 passed, 1 skipped
