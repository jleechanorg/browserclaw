import json
import os
import pytest
import shutil
from pathlib import Path
from browserclaw.models import EndpointCatalog, EndpointSignature
from browserclaw.cli import main, _build_parser
from browserclaw.generator import render_curl_replay, render_python_client

def test_learn_dry_run_redacts_har(tmp_path: Path, monkeypatch):
    # Mock capture_har to write a raw HAR with sensitive values
    def mock_capture_har(url, path, **kwargs):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({
            "log": {
                "entries": [
                    {
                        "request": {
                            "headers": [
                                {"name": "Authorization", "value": "Bearer xoxb-secret-token"},
                                {"name": "Cookie", "value": "d=xoxd-secret-cookie; other=123"},
                                {"name": "Api-Key", "value": "secret-api-key"}
                            ]
                        }
                    }
                ]
            }
        }))
    monkeypatch.setattr("browserclaw.cli.capture_har", mock_capture_har)
    # Mock enrich_catalog and capture_responses_superpower to avoid real network/chrome calls
    monkeypatch.setattr("browserclaw.cli.enrich_catalog", lambda cat, *a, **kw: cat)
    monkeypatch.setattr("browserclaw.cli.capture_responses_superpower", lambda *a, **kw: {})

    output_dir = tmp_path / "output"
    monkeypatch.setattr("sys.argv", [
        "browserclaw", "learn",
        "--url", "https://slack.com",
        "--output-dir", str(output_dir),
        "--dry-run",
        "--headless",
    ])
    main()

    # The raw HAR capture.har should have been overwritten and redacted in place.
    har_file = output_dir / "capture.har"
    assert har_file.exists()
    har_content = json.loads(har_file.read_text())
    headers = har_content["log"]["entries"][0]["request"]["headers"]
    
    # Assert values are redacted
    auth_header = next(h for h in headers if h["name"] == "Authorization")
    cookie_header = next(h for h in headers if h["name"] == "Cookie")
    api_key_header = next(h for h in headers if h["name"] == "Api-Key")
    
    assert auth_header["value"] == "$MOCKSET_TOKENS_AUTHORIZATION"
    assert "xoxb-secret-token" not in auth_header["value"]
    assert "$MOCKSET_TOKENS_COOKIE_D" in cookie_header["value"]
    assert "xoxd-secret-cookie" not in cookie_header["value"]
    assert api_key_header["value"] == "$MOCKSET_TOKENS_API_KEY"
    assert "secret-api-key" not in api_key_header["value"]

    # Redacted temp file should be cleaned up / renamed
    assert not (output_dir / "capture.redacted.har").exists()


def test_mockset_deletes_raw_har(tmp_path: Path, monkeypatch):
    # Mock capture_har to write a raw HAR with sensitive values
    def mock_capture_har(url, path, **kwargs):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({
            "log": {
                "entries": [
                    {
                        "request": {
                            "headers": [
                                {"name": "Authorization", "value": "Bearer xoxb-secret-token"},
                                {"name": "Cookie", "value": "d=xoxd-secret-cookie"}
                            ]
                        }
                    }
                ]
            }
        }))
    monkeypatch.setattr("browserclaw.cli.capture_har", mock_capture_har)
    monkeypatch.setattr("browserclaw.cli.enrich_catalog", lambda cat, *a, **kw: cat)
    monkeypatch.setattr("browserclaw.cli.capture_responses_superpower", lambda *a, **kw: {})

    output_dir = tmp_path / "output"
    monkeypatch.setattr("sys.argv", [
        "browserclaw", "mockset",
        "--url", "https://slack.com",
        "--output-dir", str(output_dir),
        "--headless",
    ])
    main()

    # The raw HAR file 'capture.har' MUST NOT exist in the output directory
    assert not (output_dir / "capture.har").exists()
    # The redacted HAR 'capture.redacted.har' MUST exist
    assert (output_dir / "capture.redacted.har").exists()

    # The mockset.json must not reference raw_har_path (or it should be absent/None)
    mockset_file = output_dir / "mockset.json"
    assert mockset_file.exists()
    mockset_data = json.loads(mockset_file.read_text())
    assert "raw_har_path" not in mockset_data or mockset_data["raw_har_path"] is None or not Path(mockset_data["raw_har_path"]).exists()


def test_redact_api_key_in_curl_replay(tmp_path: Path):
    har_file = tmp_path / "test.har"
    har_file.write_text(json.dumps({
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "POST",
                        "url": "https://example.com/api",
                        "headers": [
                            {"name": "Api-Key", "value": "secret-api-key-123"},
                            {"name": "api_key", "value": "secret-api-key-456"}
                        ]
                    }
                }
            ]
        }
    }))

    curl_script = render_curl_replay(har_file, dry_run=True)
    assert "secret-api-key-123" not in curl_script
    assert "secret-api-key-456" not in curl_script
    assert "MOCKSET_TOKENS_API_KEY" in curl_script
    assert "MOCKSET_TOKENS_API_KEY" in curl_script


def test_dry_run_python_client_guard_and_tokens(monkeypatch):
    catalog = EndpointCatalog(
        site="slack",
        source_har="t.har",
        notes=[],
        endpoints=[
            EndpointSignature(
                name="get_data",
                method="GET",
                url_template="https://example.com/data",
                host="example.com",
                request_header_keys=["Authorization", "Cookie"],
            )
        ],
    )

    rendered = render_python_client(catalog, dry_run=True)
    
    # Check that MockSetTokenMissingError and load_mockset_tokens are present
    assert "class MockSetTokenMissingError" in rendered
    assert "def load_mockset_tokens" in rendered

    # Create namespace for executing generated python client
    namespace = {}
    exec(rendered, namespace)
    BrowserClawClient = namespace["BrowserClawClient"]
    MockSetTokenMissingError = namespace["MockSetTokenMissingError"]

    # Instantiating client without setting env var should raise MockSetTokenMissingError
    monkeypatch.delenv("MOCKSET_TOKENS", raising=False)
    # Also delete config file if it exists to ensure error raises
    config_dir = Path.home() / ".config" / "browserclaw"
    config_file = config_dir / "mockset-tokens.json"
    if config_file.exists():
        monkeypatch.setattr("os.path.exists", lambda path: False)

    with pytest.raises(MockSetTokenMissingError) as exc_info:
        BrowserClawClient()
    assert "MOCKSET_TOKENS not set" in str(exc_info.value)

    # Now run with environment variable set
    monkeypatch.setenv("MOCKSET_TOKENS", json.dumps({
        "AUTHORIZATION": "Bearer xoxb-test",
        "COOKIE_D": "xoxd-cookie-val"
    }))

    client = BrowserClawClient()
    assert client.client.headers["Authorization"] == "Bearer xoxb-test"
    assert client.client.cookies["d"] == "xoxd-cookie-val"
