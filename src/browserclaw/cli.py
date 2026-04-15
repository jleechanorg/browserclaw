from __future__ import annotations

import argparse
import json
from pathlib import Path

from .capture import capture_har, capture_ws, load_steps
from .generator import generate_bundle, generate_ws_bundle
from .har import infer_endpoint_catalog
from .llm import enrich_catalog, plan_steps
from .models import EndpointCatalog


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="browserclaw")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── HAR capture ────────────────────────────────────────────────────────────
    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("--url", required=True)
    capture_parser.add_argument("--output", required=True)
    capture_parser.add_argument("--browser-channel", default="chromium")
    capture_parser.add_argument("--storage-state")
    capture_parser.add_argument(
        "--extra-headers",
        nargs="*",
        help="Extra HTTP headers as KEY=VALUE pairs (e.g., Authorization=Bearer xxx)",
    )
    capture_parser.add_argument("--headless", action="store_true")
    capture_parser.add_argument("--manual", action="store_true")
    capture_parser.add_argument("--wait-after-load", type=float, default=15.0)
    capture_parser.add_argument("--steps")
    capture_parser.add_argument("--goal")
    capture_parser.add_argument("--provider", choices=["anthropic", "openai", "gemini"])
    capture_parser.add_argument("--model")

    # ── WebSocket capture ──────────────────────────────────────────────────────
    ws_parser = subparsers.add_parser("capture-ws", help="Capture WebSocket frames via CDP")
    ws_parser.add_argument("--url", required=True)
    ws_parser.add_argument("--output", required=True)
    ws_parser.add_argument("--browser-channel", default="chromium")
    ws_parser.add_argument("--storage-state")
    ws_parser.add_argument(
        "--extra-headers",
        nargs="*",
        help="Extra HTTP headers as KEY=VALUE pairs",
    )
    ws_parser.add_argument("--headless", action="store_true")
    ws_parser.add_argument("--manual", action="store_true")
    ws_parser.add_argument("--wait-after-load", type=float, default=15.0)
    ws_parser.add_argument("--steps")
    ws_parser.add_argument("--goal")
    ws_parser.add_argument("--provider", choices=["anthropic", "openai", "gemini"])
    ws_parser.add_argument("--model")

    # ── Infer from HAR ─────────────────────────────────────────────────────────
    infer_parser = subparsers.add_parser("infer")
    infer_parser.add_argument("--har", required=True)
    infer_parser.add_argument("--output", required=True)
    infer_parser.add_argument("--site")
    infer_parser.add_argument("--goal")
    infer_parser.add_argument("--provider", choices=["anthropic", "openai", "gemini"])
    infer_parser.add_argument("--model")

    # ── Generate from catalog ──────────────────────────────────────────────────
    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--catalog", required=True)
    generate_parser.add_argument("--output-dir", required=True)

    # ── Generate from WebSocket capture ───────────────────────────────────────
    generate_ws_parser = subparsers.add_parser("generate-ws", help="Generate WebSocket replay scripts from capture")
    generate_ws_parser.add_argument("--ws-capture", required=True, help="Path to WebSocket JSON capture file")
    generate_ws_parser.add_argument("--output-dir", required=True)

    # ── Reverse (HAR + infer + generate) ──────────────────────────────────────
    reverse_parser = subparsers.add_parser("reverse")
    reverse_parser.add_argument("--url", required=True)
    reverse_parser.add_argument("--output-dir", required=True)
    reverse_parser.add_argument("--browser-channel", default="chromium")
    reverse_parser.add_argument("--storage-state")
    reverse_parser.add_argument(
        "--extra-headers",
        nargs="*",
        help="Extra HTTP headers as KEY=VALUE pairs (e.g., Authorization=Bearer xxx)",
    )
    reverse_parser.add_argument("--headless", action="store_true")
    reverse_parser.add_argument("--manual", action="store_true")
    reverse_parser.add_argument("--wait-after-load", type=float, default=15.0)
    reverse_parser.add_argument("--steps")
    reverse_parser.add_argument("--goal")
    reverse_parser.add_argument("--provider", choices=["anthropic", "openai", "gemini"])
    reverse_parser.add_argument("--model")
    reverse_parser.add_argument("--site")
    reverse_parser.add_argument("--capture-ws", action="store_true", help="Also capture WebSocket frames")
    reverse_parser.add_argument("--ws-output-dir", default=None, help="Output dir for WS capture (default: <output-dir>/ws)")

    return parser


def _resolve_steps(args: argparse.Namespace):
    if args.steps:
        return load_steps(args.steps)
    if args.goal and args.provider and args.model:
        return plan_steps(args.url, args.goal, args.provider, args.model)
    return None


def _parse_extra_headers(raw: list[str] | None) -> dict[str, str] | None:
    if not raw:
        return None
    result = {}
    for item in raw:
        if "=" in item:
            key, value = item.split("=", 1)
            result[key.strip()] = value.strip()
    return result or None


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "capture":
        steps = _resolve_steps(args)
        # --headless is always non-interactive; otherwise fall back to manual flag
        manual = False if args.headless else (args.manual or steps is None)
        path = capture_har(
            args.url,
            args.output,
            browser_channel=args.browser_channel,
            headless=args.headless,
            storage_state=args.storage_state,
            manual=manual,
            wait_after_load=args.wait_after_load,
            steps=steps,
            extra_headers=_parse_extra_headers(args.extra_headers),
        )
        print(path)
        return

    if args.command == "capture-ws":
        steps = _resolve_steps(args)
        manual = False if args.headless else (args.manual or steps is None)
        path = capture_ws(
            args.url,
            args.output,
            browser_channel=args.browser_channel,
            headless=args.headless,
            storage_state=args.storage_state,
            manual=manual,
            wait_after_load=args.wait_after_load,
            steps=steps,
            extra_headers=_parse_extra_headers(args.extra_headers),
        )
        print(path)
        return

    if args.command == "infer":
        catalog = infer_endpoint_catalog(args.har, site=args.site)
        if args.provider and args.model:
            catalog = enrich_catalog(catalog, args.provider, args.model, goal=args.goal)
        Path(args.output).write_text(json.dumps(catalog.to_dict(), indent=2) + "\n")
        print(args.output)
        return

    if args.command == "generate":
        catalog = EndpointCatalog.from_dict(json.loads(Path(args.catalog).read_text()))
        bundle = generate_bundle(catalog, args.output_dir)
        print(json.dumps({key: str(value) for key, value in bundle.items()}, indent=2))
        return

    if args.command == "generate-ws":
        bundle = generate_ws_bundle(args.ws_capture, args.output_dir)
        print(json.dumps({key: str(value) for key, value in bundle.items()}, indent=2))
        return

    if args.command == "reverse":
        output_dir = Path(args.output_dir)
        har_path = output_dir / "capture.har"
        catalog_path = output_dir / "catalog.json"
        steps = _resolve_steps(args)
        manual = False if args.headless else (args.manual or steps is None)
        capture_har(
            args.url,
            har_path,
            browser_channel=args.browser_channel,
            headless=args.headless,
            storage_state=args.storage_state,
            manual=manual,
            wait_after_load=args.wait_after_load,
            steps=steps,
            extra_headers=_parse_extra_headers(args.extra_headers),
        )
        catalog = infer_endpoint_catalog(har_path, site=args.site)
        if args.provider and args.model:
            catalog = enrich_catalog(catalog, args.provider, args.model, goal=args.goal)
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(json.dumps(catalog.to_dict(), indent=2) + "\n")
        bundle = generate_bundle(catalog, output_dir)

        # Optional: also capture WebSocket frames
        if args.capture_ws:
            ws_output_dir = Path(args.ws_output_dir) if args.ws_output_dir else (output_dir / "ws")
            ws_path = ws_output_dir / "ws_capture.json"
            capture_ws(
                args.url,
                ws_path,
                browser_channel=args.browser_channel,
                headless=args.headless,
                storage_state=args.storage_state,
                manual=manual,
                wait_after_load=args.wait_after_load,
                steps=steps,
                extra_headers=_parse_extra_headers(args.extra_headers),
            )
            ws_bundle = generate_ws_bundle(ws_path, ws_output_dir)
            bundle.update({"ws": str(ws_path), "ws_replay": str(ws_bundle["replay"])})

        print(json.dumps({key: str(value) for key, value in bundle.items()}, indent=2))
        return

    parser.error(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    main()
