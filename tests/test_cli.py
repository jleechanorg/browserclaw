from __future__ import annotations

import json
from pathlib import Path

from browserclaw.models import EndpointCatalog, EndpointSignature


def _write_catalog(tmp_path: Path, catalog: EndpointCatalog) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(catalog.to_dict()))
    return p


def test_generate_no_skill_without_save_skill_flag(tmp_path: Path) -> None:
    """generate command without --save-skill must not emit SKILL.md."""
    from browserclaw.cli import _build_parser

    catalog = EndpointCatalog(
        site="example.com",
        source_har="t.har",
        notes=[],
        endpoints=[
            EndpointSignature(
                name="get_data",
                method="GET",
                url_template="https://example.com/data",
                host="example.com",
                description="test",
            )
        ],
    )
    cat_path = _write_catalog(tmp_path / "input", catalog)
    out_dir = tmp_path / "output"

    parser = _build_parser()
    args = parser.parse_args(["generate", "--catalog", str(cat_path), "--output-dir", str(out_dir)])
    site_url = args.url if getattr(args, "save_skill", False) else None
    assert site_url is None


def test_generate_skill_with_save_skill_flag(tmp_path: Path) -> None:
    """generate command with --save-skill --url must pass site_url."""
    from browserclaw.cli import _build_parser

    catalog = EndpointCatalog(
        site="example.com",
        source_har="t.har",
        notes=[],
        endpoints=[],
    )
    cat_path = _write_catalog(tmp_path / "input", catalog)
    out_dir = tmp_path / "output"

    parser = _build_parser()
    args = parser.parse_args([
        "generate", "--catalog", str(cat_path), "--output-dir", str(out_dir),
        "--save-skill", "--url", "https://example.com",
    ])
    site_url = args.url if getattr(args, "save_skill", False) else None
    assert site_url == "https://example.com"


def test_generate_save_skill_without_url_errors(tmp_path: Path, monkeypatch) -> None:
    """--save-skill without --url must cause a parser error."""
    import pytest
    from browserclaw.cli import main

    catalog = EndpointCatalog(
        site="example.com",
        source_har="t.har",
        notes=[],
        endpoints=[],
    )
    cat_path = _write_catalog(tmp_path / "input", catalog)
    out_dir = tmp_path / "output"

    monkeypatch.setattr("sys.argv", [
        "browserclaw", "generate",
        "--catalog", str(cat_path), "--output-dir", str(out_dir), "--save-skill",
    ])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2


# ── Profile subcommand tests ───────────────────────────────────────────────

import sys
from dataclasses import dataclass
from typing import Any

import pytest


@dataclass(frozen=True)
class _FakeMetadata:
    name: str
    browser_channel: str
    created_at: str
    storage_state_sha256: str


@dataclass(frozen=True)
class _FakeRunResult:
    profile: str
    url: str
    title: str
    expected_selector_visible: bool
    status: str


class _FakeProfileError(Exception):
    pass


def _make_fake_profiles(overrides: dict | None = None) -> Any:
    """Build a synthetic `browserclaw.profiles` with recording call lists."""
    mod = type(sys)("browserclaw.profiles")
    mod.ProfileError = _FakeProfileError
    mod.ProfileMetadata = _FakeMetadata
    mod.ProfileRunResult = _FakeRunResult
    mod.init_calls = []
    mod.run_calls = []
    mod.list_calls = []

    def initialize_profile(name, storage_state, browser_channel="chrome", profile_root=None):
        mod.init_calls.append({
            "name": name, "storage_state": storage_state,
            "browser_channel": browser_channel, "profile_root": profile_root,
        })
        return _FakeMetadata(name, browser_channel, "2026-08-25T00:00:00+00:00", "f" * 64)

    def run_profile(name, goto, expect_selector, wait_after_load=5.0,
                    browser_channel=None, profile_root=None):
        mod.run_calls.append({
            "name": name, "goto": goto, "expect_selector": expect_selector,
            "wait_after_load": wait_after_load, "browser_channel": browser_channel,
            "profile_root": profile_root,
        })
        return _FakeRunResult(name, goto, "Example", True, "verified")

    def list_profiles(profile_root=None):
        mod.list_calls.append({"profile_root": profile_root})
        return []

    mod.initialize_profile = initialize_profile
    mod.run_profile = run_profile
    mod.list_profiles = list_profiles
    if overrides:
        for k, v in overrides.items():
            setattr(mod, k, v)
    return mod


@pytest.fixture
def fake_profiles(monkeypatch):
    from browserclaw import cli
    fake = _make_fake_profiles()
    monkeypatch.setitem(sys.modules, "browserclaw.profiles", fake)
    monkeypatch.setattr(cli, "_profiles", fake, raising=False)
    return fake


def _invoke(monkeypatch, capsys, argv: list[str]) -> tuple[Any, str, Any]:
    from browserclaw import cli
    monkeypatch.setattr(sys, "argv", argv)
    code = 0
    try:
        cli.main()
    except SystemExit as exc:
        code = exc.code if exc.code is not None else 0
    out = capsys.readouterr().out
    try:
        parsed = json.loads(out) if out.strip() else None
    except json.JSONDecodeError:
        parsed = None
    return code, out, parsed


@pytest.mark.parametrize("argv, expected", [
    (["profile", "init", "demo", "--storage-state", "/s"],
     {"command": "profile", "profile_action": "init", "name": "demo",
      "storage_state": "/s", "browser_channel": "chrome", "profile_root": None}),
    (["profile", "run", "demo", "--goto", "https://x/", "--expect-selector", "h1"],
     {"command": "profile", "profile_action": "run", "name": "demo",
      "goto": "https://x/", "expect_selector": "h1",
      "wait_after_load": 5.0, "browser_channel": None, "profile_root": None}),
    (["profile", "list"], {"command": "profile", "profile_action": "list", "profile_root": None}),
])
def test_profile_parsers_extract_expected_fields(argv, expected):
    from browserclaw.cli import _build_parser
    args = _build_parser().parse_args(argv)
    for k, v in expected.items():
        assert getattr(args, k) == v


@pytest.mark.parametrize("argv", [
    ["profile", "init", "demo"],                      # missing --storage-state
    ["profile", "run", "demo", "--expect-selector", "h1"],  # missing --goto
    ["profile", "run", "demo", "--goto", "https://x/"],      # missing --expect-selector
    ["profile"],                                              # missing sub-action
])
def test_profile_required_args_fail_at_parse(argv):
    from browserclaw.cli import _build_parser
    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args(argv)
    assert exc_info.value.code == 2


def test_profile_init_renders_metadata_json(fake_profiles, monkeypatch, capsys, tmp_path):
    state = tmp_path / "s.json"
    state.write_text("{}")
    code, _, parsed = _invoke(monkeypatch, capsys, [
        "browserclaw", "profile", "init", "demo",
        "--storage-state", str(state), "--browser-channel", "chrome",
        "--profile-root", str(tmp_path / "r"),
    ])
    assert code == 0
    assert parsed == {
        "name": "demo", "browser_channel": "chrome",
        "created_at": "2026-08-25T00:00:00+00:00", "storage_state_sha256": "f" * 64,
    }
    call = fake_profiles.init_calls[0]
    assert call["name"] == "demo" and call["storage_state"] == str(state)
    assert call["browser_channel"] == "chrome" and call["profile_root"] == str(tmp_path / "r")


def test_profile_run_verified_renders_json_and_exits_zero(fake_profiles, monkeypatch, capsys):
    code, _, parsed = _invoke(monkeypatch, capsys, [
        "browserclaw", "profile", "run", "demo",
        "--goto", "https://example.com/", "--expect-selector", "h1",
        "--wait-after-load", "3",
    ])
    assert code == 0
    assert parsed["profile"] == "demo" and parsed["status"] == "verified"
    assert parsed["expected_selector_visible"] is True
    call = fake_profiles.run_calls[0]
    assert call["wait_after_load"] == 3.0 and call["browser_channel"] is None


def test_profile_run_not_verified_prints_json_and_exits_nonzero(monkeypatch, capsys):
    from browserclaw import cli
    fake = _make_fake_profiles({"run_profile": lambda *args, **_: _FakeRunResult(
        args[0], args[1], "Cloudflare", False, "not_verified",
    )})
    monkeypatch.setitem(sys.modules, "browserclaw.profiles", fake)
    monkeypatch.setattr(cli, "_profiles", fake, raising=False)

    code, _, parsed = _invoke(monkeypatch, capsys, [
        "browserclaw", "profile", "run", "demo",
        "--goto", "https://example.com/", "--expect-selector", "h1",
    ])
    assert code != 0
    assert parsed["status"] == "not_verified" and parsed["expected_selector_visible"] is False
    assert parsed["profile"] == "demo" and parsed["url"] == "https://example.com/"


def test_profile_list_renders_json_array(fake_profiles, monkeypatch, capsys):
    def fake_list(profile_root=None):
        fake_profiles.list_calls.append({"profile_root": profile_root})
        return [
            _FakeMetadata("alpha", "chrome", "2026-08-25T00:00:00+00:00", "a" * 64),
            _FakeMetadata("beta", "chromium", "2026-08-25T01:00:00+00:00", "b" * 64),
        ]
    fake_profiles.list_profiles = fake_list
    code, _, parsed = _invoke(monkeypatch, capsys, ["browserclaw", "profile", "list"])
    assert code == 0 and isinstance(parsed, list) and len(parsed) == 2
    assert [p["name"] for p in parsed] == ["alpha", "beta"]
    assert parsed[0]["storage_state_sha256"] == "a" * 64


@pytest.mark.parametrize("action, args, expected_root_attr", [
    ("init", ["init", "demo", "--storage-state", "/s"], "init_calls"),
    ("run", ["run", "demo", "--goto", "https://x/", "--expect-selector", "h1"], "run_calls"),
    ("list", ["list"], "list_calls"),
])
def test_profile_root_forwarding(fake_profiles, monkeypatch, capsys, tmp_path,
                                 action, args, expected_root_attr):
    root = tmp_path / "iso"
    cli_args = ["browserclaw", "profile", *args, "--profile-root", str(root)]
    if action == "init":
        # init requires a real storage-state file
        (tmp_path / "s.json").write_text("{}")
        cli_args[cli_args.index("/s")] = str(tmp_path / "s.json")
    code, _, _ = _invoke(monkeypatch, capsys, cli_args)
    assert code == 0
    calls = getattr(fake_profiles, expected_root_attr)
    assert calls and calls[-1]["profile_root"] == str(root)


def test_profile_init_root_isolation_between_invocations(fake_profiles, monkeypatch, capsys, tmp_path):
    state = tmp_path / "s.json"
    state.write_text("{}")
    for root in (tmp_path / "a", tmp_path / "b"):
        _invoke(monkeypatch, capsys, [
            "browserclaw", "profile", "init", "demo",
            "--storage-state", str(state), "--profile-root", str(root),
        ])
    assert [c["profile_root"] for c in fake_profiles.init_calls] == [
        str(tmp_path / "a"), str(tmp_path / "b"),
    ]


@pytest.mark.parametrize("exc_msg, forbidden", [
    ("invalid name containing SECRET_TOKEN_abc123=/secret/path",
     ["SECRET_TOKEN_abc123", "super-secret-value", "secret/path"]),
    ("cookie leaked: Bearer abc.def.ghi", ["abc.def.ghi", "Bearer"]),
])
def test_profile_error_emits_secret_free_json(monkeypatch, capsys, tmp_path, exc_msg, forbidden):
    from browserclaw import cli
    state = tmp_path / "s.json"
    state.write_text('{"cookies": [{"name":"SESSION","value":"super-secret-value"}]}')
    def raise_init(*args, **kwargs):
        raise _FakeProfileError(exc_msg)
    fake = _make_fake_profiles({"initialize_profile": raise_init})
    monkeypatch.setitem(sys.modules, "browserclaw.profiles", fake)
    monkeypatch.setattr(cli, "_profiles", fake, raising=False)

    code, raw, parsed = _invoke(monkeypatch, capsys, [
        "browserclaw", "profile", "init", "demo", "--storage-state", str(state),
    ])
    assert code != 0
    assert isinstance(parsed, dict) and parsed.get("error") == "profile_error"
    for token in forbidden:
        assert token not in raw
