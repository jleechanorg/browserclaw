from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .capture import (
    capture_har,
    capture_ws,
    capture_evidence,
    capture_responses_superpower,
    load_steps,
)
from .generator import _slug_from_url, generate_bundle, generate_ws_bundle
from .har import build_catalog_from_responses, infer_endpoint_catalog
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
    generate_parser.add_argument("--save-skill", action="store_true", help="Also generate SKILL.md")
    generate_parser.add_argument("--skill-name", help="Skill name override (default: auto from URL)")

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
    reverse_parser.add_argument("--save-skill", action="store_true", help="Also generate SKILL.md")

    # ── Learn (capture + infer + generate + skill) ──────────────────────────────
    learn_parser = subparsers.add_parser("learn", help="Capture, infer, generate client code AND save a SKILL.md")
    learn_parser.add_argument("--url", required=True)
    learn_parser.add_argument("--output-dir", required=True)
    learn_parser.add_argument("--browser-channel", default="chromium")
    learn_parser.add_argument("--storage-state")
    learn_parser.add_argument(
        "--extra-headers",
        nargs="*",
        help="Extra HTTP headers as KEY=VALUE pairs",
    )
    learn_parser.add_argument("--headless", action="store_true")
    learn_parser.add_argument("--manual", action="store_true")
    learn_parser.add_argument("--wait-after-load", type=float, default=15.0)
    learn_parser.add_argument("--steps")
    learn_parser.add_argument("--goal")
    learn_parser.add_argument("--provider", choices=["anthropic", "openai", "gemini"])
    learn_parser.add_argument("--model")
    learn_parser.add_argument("--site")
    learn_parser.add_argument("--skill-name", help="Skill name (default: auto from URL)")
    learn_parser.add_argument(
        "--deploy-skill",
        action="store_true",
        help="Also copy generated SKILL.md to ~/.claude/skills/<slug>/SKILL.md",
    )
    learn_parser.add_argument(
        "--capture-evidence",
        action="store_true",
        help="Also capture screenshot and console logs to evidence/ subdirectory",
    )

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
        if path.exists():
            print(path)
        else:
            print(f"# No HAR captured (superpower chrome detected at localhost:9222)")
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
        bundle = generate_bundle(catalog, args.output_dir, site_url=getattr(args, 'site', None) or catalog.site)
        print(json.dumps({key: str(value) for key, value in bundle.items()}, indent=2))
        return

    if args.command == "generate-ws":
        bundle = generate_ws_bundle(args.ws_capture, args.output_dir)
        print(json.dumps({key: str(value) for key, value in bundle.items()}, indent=2))
        return

    if args.command == "learn":
        output_dir = Path(args.output_dir)
        har_path = output_dir / "capture.har"
        catalog_path = output_dir / "catalog.json"
        steps = _resolve_steps(args)
        manual = False if args.headless else (args.manual or steps is None)

        # HAR capture
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

        # Superpower chrome response capture (fills in response shapes HAR missed)
        response_shapes: dict[str, dict] = {}
        try:
            response_shapes = capture_responses_superpower(args.url)
        except Exception:
            pass

        # Build catalog — from HAR if available, otherwise from superpower chrome responses
        if har_path.exists():
            catalog = infer_endpoint_catalog(har_path, site=args.site)
        elif response_shapes:
            site = args.site or args.url.split("//")[1].split("/")[0]
            catalog = build_catalog_from_responses(site, response_shapes)
        else:
            # Empty catalog
            catalog = EndpointCatalog(
                site=args.site or args.url,
                source_har=None,
                notes=["No HAR and no superpower chrome responses captured"],
                endpoints=[],
            )
        if args.provider and args.model:
            catalog = enrich_catalog(catalog, args.provider, args.model, goal=args.goal)
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(json.dumps(catalog.to_dict(), indent=2) + "\n")
        bundle = generate_bundle(catalog, output_dir, site_url=args.url, response_shapes=response_shapes)

        # Evidence capture (screenshot + console)
        if getattr(args, "capture_evidence", False):
            try:
                evidence_dir = capture_evidence(
                    args.url,
                    output_dir,
                    browser_channel=args.browser_channel,
                    headless=args.headless,
                )
                bundle["evidence"] = str(evidence_dir)
            except Exception:
                pass

        # Deploy skill to ~/.claude/skills/<slug>/SKILL.md AND browserclaw repo skills/ dir
        if getattr(args, "deploy_skill", False):
            skill_path = bundle.get("skill")
            if skill_path:
                slug = _slug_from_url(args.url)

                # 1. User's local skills dir (for immediate agent use)
                skills_home = Path.home() / ".claude" / "skills" / slug
                skills_home.mkdir(parents=True, exist_ok=True)
                dest_home = skills_home / "SKILL.md"
                shutil.copy2(skill_path, dest_home)
                bundle["deployed_skill"] = str(dest_home)

                # 2. browserclaw repo skills/ dir (for version control)
                repo_skills = Path(__file__).parent.parent.parent / "skills" / slug
                repo_skills.mkdir(parents=True, exist_ok=True)
                dest_repo = repo_skills / "SKILL.md"
                shutil.copy2(skill_path, dest_repo)
                bundle["repo_skill"] = str(dest_repo)

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
        
        # Build catalog — from HAR if available, otherwise from superpower chrome responses
        if har_path.exists():
            catalog = infer_endpoint_catalog(har_path, site=args.site)
        else:
            # Try superpower chrome response capture as fallback
            response_shapes: dict[str, dict] = {}
            try:
                response_shapes = capture_responses_superpower(args.url)
            except Exception:
                pass
            
            if response_shapes:
                site = args.site or args.url.split("//")[1].split("/")[0]
                catalog = build_catalog_from_responses(site, response_shapes)
            else:
                # Empty catalog
                catalog = EndpointCatalog(
                    site=args.site or args.url,
                    source_har=None,
                    notes=["No HAR and no superpower chrome responses captured"],
                    endpoints=[],
                )
        
        if args.provider and args.model:
            catalog = enrich_catalog(catalog, args.provider, args.model, goal=args.goal)
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(json.dumps(catalog.to_dict(), indent=2) + "\n")
        bundle = generate_bundle(catalog, output_dir, site_url=args.url if getattr(args, 'save_skill', False) else None)

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
