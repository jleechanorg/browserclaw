from __future__ import annotations

import argparse
import json
from pathlib import Path

from .capture import capture_har, load_steps
from .generator import generate_bundle
from .har import infer_endpoint_catalog
from .llm import enrich_catalog, plan_steps
from .models import EndpointCatalog


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="browserclaw")
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("--url", required=True)
    capture_parser.add_argument("--output", required=True)
    capture_parser.add_argument("--browser-channel", default="chromium")
    capture_parser.add_argument("--storage-state")
    capture_parser.add_argument("--headless", action="store_true")
    capture_parser.add_argument("--manual", action="store_true")
    capture_parser.add_argument("--wait-after-load", type=float, default=15.0)
    capture_parser.add_argument("--steps")
    capture_parser.add_argument("--goal")
    capture_parser.add_argument("--provider", choices=["anthropic", "openai", "gemini"])
    capture_parser.add_argument("--model")

    infer_parser = subparsers.add_parser("infer")
    infer_parser.add_argument("--har", required=True)
    infer_parser.add_argument("--output", required=True)
    infer_parser.add_argument("--site")
    infer_parser.add_argument("--goal")
    infer_parser.add_argument("--provider", choices=["anthropic", "openai", "gemini"])
    infer_parser.add_argument("--model")

    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--catalog", required=True)
    generate_parser.add_argument("--output-dir", required=True)

    reverse_parser = subparsers.add_parser("reverse")
    reverse_parser.add_argument("--url", required=True)
    reverse_parser.add_argument("--output-dir", required=True)
    reverse_parser.add_argument("--browser-channel", default="chromium")
    reverse_parser.add_argument("--storage-state")
    reverse_parser.add_argument("--headless", action="store_true")
    reverse_parser.add_argument("--manual", action="store_true")
    reverse_parser.add_argument("--wait-after-load", type=float, default=15.0)
    reverse_parser.add_argument("--steps")
    reverse_parser.add_argument("--goal")
    reverse_parser.add_argument("--provider", choices=["anthropic", "openai", "gemini"])
    reverse_parser.add_argument("--model")
    reverse_parser.add_argument("--site")
    return parser


def _resolve_steps(args: argparse.Namespace):
    if args.steps:
        return load_steps(args.steps)
    if args.goal and args.provider and args.model:
        return plan_steps(args.url, args.goal, args.provider, args.model)
    return None


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "capture":
        steps = _resolve_steps(args)
        path = capture_har(
            args.url,
            args.output,
            browser_channel=args.browser_channel,
            headless=args.headless,
            storage_state=args.storage_state,
            manual=args.manual or steps is None,
            wait_after_load=args.wait_after_load,
            steps=steps,
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

    if args.command == "reverse":
        output_dir = Path(args.output_dir)
        har_path = output_dir / "capture.har"
        catalog_path = output_dir / "catalog.json"
        steps = _resolve_steps(args)
        capture_har(
            args.url,
            har_path,
            browser_channel=args.browser_channel,
            headless=args.headless,
            storage_state=args.storage_state,
            manual=args.manual or steps is None,
            wait_after_load=args.wait_after_load,
            steps=steps,
        )
        catalog = infer_endpoint_catalog(har_path, site=args.site)
        if args.provider and args.model:
            catalog = enrich_catalog(catalog, args.provider, args.model, goal=args.goal)
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        bundle = generate_bundle(catalog, output_dir)
        print(json.dumps({key: str(value) for key, value in bundle.items()}, indent=2))
        return

    parser.error(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    main()

