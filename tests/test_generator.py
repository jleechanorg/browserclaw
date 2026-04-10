from __future__ import annotations

import json
from pathlib import Path

from browserclaw.generator import generate_bundle, render_mcp_tools, render_python_client
from browserclaw.har import infer_endpoint_catalog


FIXTURE = Path(__file__).parent / "fixtures" / "sample.har"


def test_render_python_client_contains_inferred_methods() -> None:
    catalog = infer_endpoint_catalog(FIXTURE, site="linkedin")
    rendered = render_python_client(catalog)
    assert "def get_api_graphql" in rendered
    assert "def create_updates_reactions" in rendered


def test_render_mcp_tools_emits_schema() -> None:
    catalog = infer_endpoint_catalog(FIXTURE, site="linkedin")
    payload = render_mcp_tools(catalog)
    assert payload["site"] == "linkedin"
    assert len(payload["tools"]) == 2


def test_generate_bundle_writes_outputs(tmp_path: Path) -> None:
    catalog = infer_endpoint_catalog(FIXTURE, site="linkedin")
    bundle = generate_bundle(catalog, tmp_path)
    assert bundle["client"].exists()
    assert json.loads(bundle["catalog"].read_text())["site"] == "linkedin"

