from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Iterable

from playwright.async_api import BrowserContext, Page, async_playwright

from .models import BrowserStep


async def _run_step(page: Page, step: BrowserStep) -> None:
    if step.action == "goto":
        await page.goto(step.url or "", wait_until="networkidle")
    elif step.action == "click":
        await page.click(step.selector or "")
    elif step.action == "fill":
        await page.fill(step.selector or "", step.value or "")
    elif step.action == "press":
        await page.press(step.selector or "", step.value or "")
    elif step.action == "wait_for_timeout":
        await page.wait_for_timeout(float(step.milliseconds or 1000))
    elif step.action == "wait_for_url":
        await page.wait_for_url(step.value or "")
    else:
        raise ValueError(f"Unsupported browser step: {step.action}")


def load_steps(path: str | Path) -> list[BrowserStep]:
    payload = json.loads(Path(path).read_text())
    return [BrowserStep(**item) for item in payload]


async def _capture(
    url: str,
    output: Path,
    *,
    browser_channel: str,
    headless: bool,
    storage_state: str | None,
    manual: bool,
    wait_after_load: float,
    steps: Iterable[BrowserStep] | None,
) -> Path:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel=browser_channel, headless=headless)
        context_options = {
            "record_har_path": str(output),
            "record_har_mode": "full",
            "record_har_content": "embed",
        }
        if storage_state:
            context_options["storage_state"] = storage_state
        context: BrowserContext = await browser.new_context(**context_options)
        page = await context.new_page()
        cdp = await context.new_cdp_session(page)
        await cdp.send("Network.enable")
        await page.goto(url, wait_until="domcontentloaded")

        if steps:
            for step in steps:
                await _run_step(page, step)

        if manual:
            await asyncio.to_thread(
                input,
                "Interact with the page, then press Enter here to finish HAR capture: ",
            )
        else:
            await page.wait_for_timeout(wait_after_load * 1000)

        await context.close()
        await browser.close()
    return output


def capture_har(
    url: str,
    output: str | Path,
    *,
    browser_channel: str = "chromium",
    headless: bool = False,
    storage_state: str | None = None,
    manual: bool = True,
    wait_after_load: float = 15.0,
    steps: Iterable[BrowserStep] | None = None,
) -> Path:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return asyncio.run(
        _capture(
            url,
            output_path,
            browser_channel=browser_channel,
            headless=headless,
            storage_state=storage_state,
            manual=manual,
            wait_after_load=wait_after_load,
            steps=steps,
        )
    )

