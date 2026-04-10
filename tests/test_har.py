from __future__ import annotations

from pathlib import Path

from browserclaw.har import generalize_path, infer_endpoint_catalog


FIXTURE = Path(__file__).parent / "fixtures" / "sample.har"


def test_generalize_path_replaces_numeric_segments() -> None:
    assert generalize_path("/voyager/api/feed/updates/1234567890/reactions") == (
        "/voyager/api/feed/updates/{id}/reactions"
    )


def test_infer_endpoint_catalog_builds_endpoints() -> None:
    catalog = infer_endpoint_catalog(FIXTURE, site="linkedin")
    assert catalog.site == "linkedin"
    assert len(catalog.endpoints) == 2
    assert any(endpoint.name == "get_api_graphql" for endpoint in catalog.endpoints)
    reaction_endpoint = next(
        endpoint for endpoint in catalog.endpoints if endpoint.method == "POST"
    )
    assert reaction_endpoint.request_body_keys == ["reactionType"]
    assert reaction_endpoint.url_template.endswith("/voyager/api/feed/updates/{id}/reactions")

