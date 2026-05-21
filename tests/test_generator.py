from __future__ import annotations

import json
import tempfile
from pathlib import Path

from browserclaw.generator import (
    generate_bundle,
    render_mcp_tools,
    render_python_client,
    _format_url,
    _python_method_name,
    _python_arg_name,
    _extract_path_params,
)
from browserclaw.har import infer_endpoint_catalog
from browserclaw.models import EndpointSignature, EndpointCatalog

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


def test_format_url_named_placeholders_become_positional() -> None:
    """Named {id} placeholders must be renumbered to {0} for .format(*args)."""
    url_template = "https://example.com/updates/{id}/reactions"
    path_params = _extract_path_params(url_template)
    result = _format_url(url_template, path_params)
    # Should use positional {0}, not named {id}
    assert '"https://example.com/updates/{0}/reactions".format(' in result
    assert "id" in result  # arg name still passed
    # The generated code must actually work when executed
    # Use a local variable named 'id' with a string value
    id = "123"  # noqa: A001
    url = eval(result)
    assert url == "https://example.com/updates/123/reactions"


def test_format_url_duplicate_placeholders() -> None:
    """Duplicate {id} placeholders should each get a unique positional index."""
    url_template = "https://example.com/{id}/items/{id}"
    path_params = _extract_path_params(url_template)
    result = _format_url(url_template, path_params)
    assert "{0}" in result
    assert "{1}" in result
    assert "{id}" not in result


def test_python_method_name_sanitizes_dots() -> None:
    """Method names with dots (e.g. 'menugrid.asp') must become valid identifiers."""
    assert _python_method_name("menugrid.asp") == "menugrid_asp"
    assert _python_method_name("ajax_d3Content.ashx") == "ajax_d3Content_ashx"


def test_python_method_name_sanitizes_dashes() -> None:
    assert _python_method_name("get-user") == "get_user"


def test_python_method_name_digit_prefix() -> None:
    """Names starting with a digit must get a _ prefix."""
    assert _python_method_name("1get") == "_1get"


def test_python_arg_name_dotted_keys() -> None:
    """Query/body keys like 'cb.gsLastRender' must become valid arg names."""
    assert _python_arg_name("cb.gsLastRender") == "cb_gsLastRender"
    assert _python_arg_name("action-type") == "action_type"
    assert _python_arg_name("2count") == "_2count"


def test_generated_client_uses_httpx() -> None:
    """Generated client must import httpx, not requests."""
    catalog = infer_endpoint_catalog(FIXTURE, site="linkedin")
    rendered = render_python_client(catalog)
    assert "import httpx" in rendered
    assert "import requests" not in rendered
    assert "httpx.Client" in rendered
    assert "requests.Session" not in rendered


def test_generated_client_form_encoded_uses_data() -> None:
    """Form-encoded endpoints must emit data=, not json=."""
    ep = EndpointSignature(
        name="create_order",
        method="POST",
        url_template="https://example.com/getorderinfo.asp",
        host="example.com",
        query_keys=[],
        request_body_keys=["orderId", "action"],
        request_content_type="form",
        description="Form POST",
    )
    catalog = EndpointCatalog(site="example", source_har="test.har", notes=[], endpoints=[ep])
    rendered = render_python_client(catalog)
    assert "data=payload or None" in rendered
    assert "json=payload" not in rendered


def test_generated_client_json_endpoint_uses_json() -> None:
    """JSON endpoints must still emit json=."""
    ep = EndpointSignature(
        name="create_reaction",
        method="POST",
        url_template="https://example.com/reactions",
        host="example.com",
        query_keys=[],
        request_body_keys=["type"],
        request_content_type="json",
        description="JSON POST",
    )
    catalog = EndpointCatalog(site="example", source_har="test.har", notes=[], endpoints=[ep])
    rendered = render_python_client(catalog)
    assert "json=payload or None" in rendered
