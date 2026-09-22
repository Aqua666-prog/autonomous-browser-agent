import argparse
import asyncio
import json
import logging

from browser_agent.settings import Settings
from browser_agent.llm import ProviderError, create_provider


SMOKE_TOOL = [{
    'type': 'function',
    'function': {
        'name': 'finish',
        'description': 'Finish the smoke test.',
        'parameters': {
            'type': 'object',
            'properties': {'answer': {'type': 'string'}},
            'required': ['answer'],
            'additionalProperties': False,
        },
    },
}]


async def check_llm(settings) -> int:
    provider = create_provider(settings)
    try:
        print(f'Provider: {settings.provider_name}')
        print(f'Model: {settings.model}')
        print(f'Base URL: {settings.base_url}')
        decision = await provider.decide([
            {'role': 'system', 'content': 'Call exactly one available tool.'},
            {'role': 'user', 'content': 'Call finish with answer exactly: LLM tool calling works'},
        ], SMOKE_TOOL)
        try:
            args = json.loads(decision.arguments)
        except json.JSONDecodeError:
            print('[FAIL] Provider returned malformed tool arguments.')
            return 2
        if decision.name == 'finish' and args.get('answer') == 'LLM tool calling works':
            print('[PASS] Live LLM tool calling works.')
            return 0
        print(f'[FAIL] Unexpected tool call: {decision.name}')
        return 2
    except ProviderError as exc:
        print(f'[LLM ERROR] {exc}')
        return 2
    finally:
        await provider.close()


async def main():
    parser = argparse.ArgumentParser(description='Autonomous AI Browser Agent')
    parser.add_argument('--task')
    parser.add_argument('--headless', action='store_true', default=None)
    parser.add_argument('--check-llm', action='store_true', help='Test provider/tool-calling without starting Playwright')
    parser.add_argument('--check-browser', action='store_true', help='Start/attach, observe and disconnect without an LLM or navigation')
    args = parser.parse_args()
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('openai').setLevel(logging.WARNING)
    print('AI Browser Agent\n================')

    provider = None
    try:
        settings = Settings.load()
        if args.headless is not None:
            settings.headless = args.headless

        if args.check_llm:
            return await check_llm(settings)

        # Browser backend is selected at runtime. This keeps Playwright optional
        # on Termux while preserving it as the default desktop backend.
        from browser_agent.runtime import Runtime

        if settings.browser_backend == 'webdriver':
            from browser_agent.browser_webdriver import WebDriverBrowser

            browser_factory = lambda: WebDriverBrowser(
                webdriver_url=settings.webdriver_url,
                binary=settings.webdriver_binary,
                headless=settings.headless,
                profile_dir=settings.browser_profile_dir,
                locale=settings.browser_locale,
                upload_root=settings.browser_upload_root,
            )
        else:
            try:
                from browser_agent.browser import Browser
            except ImportError as exc:
                if 'playwright' in str(exc).lower():
                    print('[ERROR] Playwright is not installed. Set BROWSER_BACKEND=webdriver or install full requirements.')
                    return 2
                raise

            browser_factory = lambda: Browser(
                settings.headless,
                settings.browser_executable_path,
                profile_dir=settings.browser_profile_dir,
                storage_state=settings.browser_storage_state,
                cdp_url=settings.browser_cdp_url if settings.browser_backend == 'cdp' else None,
                locale=settings.browser_locale,
                upload_root=settings.browser_upload_root,
            )

        if args.check_browser:
            async with browser_factory() as browser:
                observation = await browser.observe()
                print(f'[PASS] Browser observed: {len(observation.get("elements", []))} elements; {len(observation.get("tabs", []))} tabs.')
            print('[PASS] CDP disconnected; browser left running.' if settings.browser_backend == 'cdp' else '[PASS] Browser closed.')
            return 0

        settings.require_key()
        goal = args.task or input('Task:\n> ').strip()
        if not goal:
            raise ValueError('Task is empty')
        provider = create_provider(settings)
        async with browser_factory() as browser:
            result = await Runtime(
                browser,
                provider,
                settings.max_steps,
                confirmation_mode=settings.confirmation_mode,
                observation_budget=settings.observation_budget,
            ).run(goal)
            print(f'[{result.status.upper()}] {result.result}\nSteps: {result.steps}')
            return 0 if result.status == 'complete' else 2
    except ProviderError as exc:
        print(f'[LLM ERROR] {exc}')
        return 2
    except (EOFError, ValueError):
        print('[ERROR] Invalid configuration, missing key, or missing task. Check .env.example.')
        return 2
    except Exception as exc:
        print(f'[ERROR] {type(exc).__name__}: run the installation steps in README and check network/model access.')
        return 2
    finally:
        if provider:
            await provider.close()


if __name__ == '__main__':
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print('\n[STOP] Cancelled by user.')
