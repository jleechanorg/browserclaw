from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Iterable

from playwright.async_api import BrowserContext, Page, async_playwright

from .models import (
    WebSocketCaptureResult,
    WebSocketConnection,
    WebSocketFrame,
    parse_firestore_message,
)


async def _run_step(page: Page, step) -> None:
    from .models import BrowserStep
    step = BrowserStep(**step) if isinstance(step, dict) else step
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
    elif step.action == "eval":
        await page.evaluate(step.value or "")
    else:
        raise ValueError(f"Unsupported browser step: {step.action}")


def load_steps(path: str | Path) -> list:
    payload = json.loads(Path(path).read_text())
    return payload


async def _capture(
    url: str,
    output: Path,
    *,
    browser_channel: str,
    headless: bool,
    storage_state: str | None,
    manual: bool,
    wait_after_load: float,
    steps: Iterable | None,
    extra_headers: dict[str, str] | None = None,
) -> Path:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel=browser_channel, headless=headless)
        context_options: dict = {
            "record_har_path": str(output),
            "record_har_mode": "full",
            "record_har_content": "embed",
        }
        if storage_state:
            context_options["storage_state"] = storage_state
        if extra_headers:
            context_options["extra_http_headers"] = extra_headers
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
    steps: Iterable | None = None,
    extra_headers: dict[str, str] | None = None,
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
            extra_headers=extra_headers,
        )
    )


# ─── WebSocket Capture ─────────────────────────────────────────────────────────


class _WsCaptureSession:
    """CDP WebSocket event collector for Playwright 1.x.

    Playwright 1.x uses requestId (not connectionId) as the WS event key.
    Event shapes differ from CDP spec:
      - webSocketCreated: {requestId, url, initiator}
      - webSocketFrameSent/Received: {requestId, timestamp, response: {opcode, payloadData, payloadLength}}
      - webSocketHandshakeResponseReceived: {requestId, timestamp, response: {headers, status}}
      - webSocketDestroyed: {requestId, timestamp, reason}
    """

    def __init__(self, cdp):
        self.cdp = cdp
        self.connections: dict[str, WebSocketConnection] = {}
        self._call_count = 0
        self._firestore_calls: list = []

    async def enable(self):
        await self.cdp.send("Network.enable")
        self.cdp.on("Network.webSocketCreated", self._on_created)
        self.cdp.on("Network.webSocketFrameSent", self._on_frame_sent)
        self.cdp.on("Network.webSocketFrameReceived", self._on_frame_received)
        self.cdp.on("Network.webSocketHandshakeResponseReceived", self._on_handshake)
        self.cdp.on("Network.webSocketDestroyed", self._on_destroyed)

    def _on_created(self, event):
        # Playwright 1.x: requestId + url (no nested request.url)
        rid = event["requestId"]
        ws_url = event.get("url", "")
        initiator = event.get("initiator", {})
        req_headers = {}
        if isinstance(initiator, dict):
            # Stack trace may contain URL info but not full headers
            pass
        self.connections[rid] = WebSocketConnection(
            connection_id=rid,
            url=ws_url,
            created_at=time.time(),
            request_headers=req_headers,
        )

    def _on_frame_sent(self, event):
        rid = event["requestId"]
        conn = self.connections.get(rid)
        if not conn:
            return
        response = event.get("response", {})
        payload_data = response.get("payloadData", "")
        opcode = response.get("opcode", 0)
        payload, is_bin = _decode_ws_payload(payload_data)
        frame = WebSocketFrame(
            timestamp=event.get("timestamp", time.time()),
            connection_id=rid,
            direction="sent",
            opcode=opcode,
            payload=payload,
            size=response.get("payloadLength", len(payload_data) if payload_data else 0),
            is_binary=is_bin,
        )
        conn.frames.append(frame)
        self._maybe_parse_firestore(conn, frame)

    def _on_frame_received(self, event):
        rid = event["requestId"]
        conn = self.connections.get(rid)
        if not conn:
            return
        response = event.get("response", {})
        payload_data = response.get("payloadData", "")
        opcode = response.get("opcode", 0)
        payload, is_bin = _decode_ws_payload(payload_data)
        frame = WebSocketFrame(
            timestamp=event.get("timestamp", time.time()),
            connection_id=rid,
            direction="received",
            opcode=opcode,
            payload=payload,
            size=response.get("payloadLength", len(payload_data) if payload_data else 0),
            is_binary=is_bin,
        )
        conn.frames.append(frame)
        self._maybe_parse_firestore(conn, frame)

    def _on_handshake(self, event):
        rid = event["requestId"]
        conn = self.connections.get(rid)
        if conn:
            response = event.get("response", {})
            headers = response.get("headers", {})
            conn.response_headers = dict(headers)

    def _on_destroyed(self, event):
        rid = event["requestId"]
        conn = self.connections.get(rid)
        if conn:
            conn.closed_at = event.get("timestamp", time.time())

    def _maybe_parse_firestore(self, conn: WebSocketConnection, frame: WebSocketFrame):
        """If this is a Firestore connection, parse the frame for RPC calls."""
        if not conn.is_firestore:
            return
        if frame.is_binary or not frame.payload.strip():
            return
        calls = parse_firestore_message(frame.payload)
        for call in calls:
            call.call_id = self._call_count
            self._call_count += 1
            self._firestore_calls.append(call)

    def result(self) -> WebSocketCaptureResult:
        from .models import WebSocketCaptureResult as R
        return R(
            connections=list(self.connections.values()),
            firestore_calls=self._firestore_calls,
            notes=[
                f"Captured {len(self.connections)} WebSocket connections",
                f"Total frames: {sum(len(c.frames) for c in self.connections.values())}",
                f"Firestore RPC calls parsed: {len(self._firestore_calls)}",
            ],
        )


def _decode_ws_payload(data: str) -> tuple[str, bool]:
    """Decode WebSocket payload data. Returns (text, is_binary)."""
    if not data:
        return "", False
    try:
        return data, False
    except Exception:
        return "", True


async def _capture_ws(
    url: str,
    output: Path,
    *,
    browser_channel: str,
    headless: bool,
    storage_state: str | None,
    manual: bool,
    wait_after_load: float,
    steps: Iterable | None,
    extra_headers: dict[str, str] | None = None,
) -> Path:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel=browser_channel, headless=headless)
        context_options: dict = {}
        if storage_state:
            context_options["storage_state"] = storage_state
        if extra_headers:
            context_options["extra_http_headers"] = extra_headers
        context: BrowserContext = await browser.new_context(**context_options)
        page = await context.new_page()
        cdp = await context.new_cdp_session(page)

        session = _WsCaptureSession(cdp)
        await session.enable()

        await page.goto(url, wait_until="domcontentloaded")

        if steps:
            for step in steps:
                await _run_step(page, step)

        if manual:
            await asyncio.to_thread(
                input,
                "Interact with the page, then press Enter here to finish WebSocket capture: ",
            )
        else:
            await page.wait_for_timeout(wait_after_load * 1000)

        await context.close()
        await browser.close()

        result = session.result()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({
            "connections": [c.to_dict() for c in result.connections],
            "firestore_calls": [c.to_dict() for c in result.firestore_calls],
            "notes": result.notes,
        }, indent=2))
        return output


def capture_ws(
    url: str,
    output: str | Path,
    *,
    browser_channel: str = "chromium",
    headless: bool = False,
    storage_state: str | None = None,
    manual: bool = True,
    wait_after_load: float = 15.0,
    steps: Iterable | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Path:
    """Capture WebSocket frames via CDP and save as JSON.

    Returns Path to the JSON output file containing:
    - connections: list of WebSocketConnection summaries
    - firestore_calls: parsed Firestore RPC calls (if Firestore WS detected)
    - notes: capture session summary
    """
    output_path = Path(output)
    return asyncio.run(
        _capture_ws(
            url,
            output_path,
            browser_channel=browser_channel,
            headless=headless,
            storage_state=storage_state,
            manual=manual,
            wait_after_load=wait_after_load,
            steps=steps,
            extra_headers=extra_headers,
        )
    )
