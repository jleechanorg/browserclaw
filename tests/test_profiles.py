# tests/test_profiles.py
"""Tests for browserclaw.profiles module (Task 1-3)."""
import os
import inspect
import json
import hashlib
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest

# Import the module (will be created later)
from browserclaw import profiles as p

# Helpers
def temp_root():
    return Path(tempfile.mkdtemp())

def sample_cookie_state(tmp_path):
    # Minimal cookie export format used by browserclaw.cookies.read_cookies_json
    data = {
        "cookies": [
            {
                "name": "session",
                "value": "abc123",
                "domain": ".example.com",
                "path": "/",
                "expires": 0,
                "httpOnly": False,
                "secure": False,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }
    pth = tmp_path / "cookies.json"
    pth.write_text(json.dumps(data))
    return pth

def sample_full_state(tmp_path):
    data = {
        "cookies": [
            {
                "name": "sess",
                "value": "xyz",
                "domain": ".example.com",
                "path": "/",
                "expires": 0,
                "httpOnly": False,
                "secure": False,
                "sameSite": "Lax",
            }
        ],
        "origins": [
            {
                "origin": "https://example.com",
                "localStorage": [{"name": "key", "value": "val"}],
                "indexedDB": []
            }
        ]
    }
    pth = tmp_path / "state.json"
    pth.write_text(json.dumps(data))
    return pth

# ---- Task 1: Path validation and metadata ----
def test_invalid_profile_name():
    with pytest.raises(p.ProfileError):
        p.resolve_profile_paths("", None)
    with pytest.raises(p.ProfileError):
        p.resolve_profile_paths("../evil", None)
    with pytest.raises(p.ProfileError):
        p.resolve_profile_paths("a" * 65, None)

def test_valid_profile_path(tmp_path):
    root = tmp_path / "profiles"
    paths = p.resolve_profile_paths("myprofile", root)
    assert paths.name == "myprofile"
    assert paths.root == root
    assert paths.user_data_dir == root / "myprofile"
    assert paths.metadata_path == paths.user_data_dir / "browserclaw-profile.json"

def test_default_root_rejection(tmp_path):
    # Simulate a default Chrome user-data dir pattern
    bad_root = tmp_path / "Library/Application Support/Google/Chrome/Default"
    bad_root.mkdir(parents=True, exist_ok=True)
    with pytest.raises(p.ProfileError):
        p.resolve_profile_paths("myprofile", bad_root)

def test_metadata_creation_and_private_dir(tmp_path):
    root = tmp_path / "profiles"
    paths = p.resolve_profile_paths("testmeta", root)
    # initialize should create dir and metadata
    state = sample_cookie_state(tmp_path)
    meta = p.initialize_profile("testmeta", state, profile_root=root)
    # directory exists and is private
    assert paths.user_data_dir.is_dir()
    mode = oct(paths.user_data_dir.stat().st_mode)[-3:]
    assert mode == "700"
    # metadata file contains required fields only
    meta_content = json.loads(paths.metadata_path.read_text())
    assert set(meta_content.keys()) == {"name", "browser_channel", "created_at", "storage_state_sha256"}
    assert meta_content["name"] == "testmeta"

def test_lock_contention(tmp_path):
    root = tmp_path / "profiles"
    paths = p.resolve_profile_paths("locktest", root)
    state = sample_cookie_state(tmp_path)
    # First init creates lock and releases
    p.initialize_profile("locktest", state, profile_root=root)
    # Acquire lock manually and keep it open
    lock = p.profile_lock(paths)
    lock.acquire()
    with pytest.raises(p.ProfileError):
        p.initialize_profile("locktest", state, profile_root=root)
    lock.release()

# ---- Task 2: State loading ----
def test_load_cookie_state(tmp_path):
    state_path = sample_cookie_state(tmp_path)
    state = p.load_profile_state(state_path)
    assert isinstance(state, dict)
    assert state == {"cookies": state["cookies"], "origins": []}
    assert "cookies" in state and isinstance(state["cookies"], list)
    assert "origins" in state and isinstance(state["origins"], list)

def test_load_full_state(tmp_path):
    state_path = sample_full_state(tmp_path)
    state = p.load_profile_state(state_path)
    assert "cookies" in state and "origins" in state

# ---- Task 3: Run and list ----
def test_run_profile_success(monkeypatch, tmp_path):
    # Fake Playwright to avoid real browser launch
    class DummyPage:
        def __init__(self):
            self.url = "https://example.com"
        def goto(self, url, *, wait_until):
            self.url = url
        def wait_for_timeout(self, milliseconds):
            pass
        def locator(self, selector):
            class _Loc:
                def __init__(self, visible):
                    self._visible = visible
                def is_visible(self):
                    return self._visible
            return _Loc(selector == "body")
        def title(self):
            return "Example"
    class DummyContext:
        def __init__(self):
            self.page_obj = DummyPage()
        def new_page(self):
            return self.page_obj
        def set_storage_state(self, state):
            pass
        def add_cookies(self, cookies):
            pass
        def close(self):
            pass
    class DummyBrowser:
        def launch_persistent_context(self, *args, **kwargs):
            return DummyContext()
    class DummyPlaywright:
        def __init__(self):
            self.chromium = DummyBrowser()
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            pass
    monkeypatch.setattr(p, "sync_playwright", lambda: DummyPlaywright())
    root = tmp_path / "profiles"
    state = sample_cookie_state(tmp_path)
    p.initialize_profile("runprof", state, profile_root=root)
    result = p.run_profile("runprof", "https://example.com", "body", profile_root=root)
    assert result.profile == "runprof"
    assert result.status == "verified"
    assert result.url == "https://example.com"
    assert result.title == "Example"

def test_list_profiles(monkeypatch, tmp_path):
    root = tmp_path / "profiles"
    state = sample_cookie_state(tmp_path)
    p.initialize_profile("bprofile", state, profile_root=root)
    p.initialize_profile("aprofile", state, profile_root=root)
    lst = p.list_profiles(root)
    names = [m.name for m in lst]
    assert names == ["aprofile", "bprofile"]

# ---- TDD corrections (RED) ----

def test_load_state_uses_canonical_cookie_normalization(tmp_path):
    """Cookie normalization must funnel through read_cookies_json with safe defaults."""
    # Minimal cookie entry lacking optional fields. read_cookies_json defaults them;
    # a direct Cookie(**c) constructor would reject the missing fields.
    state = {
        "cookies": [
            {
                "name": "session",
                "value": "v",
                "domain": ".example.com",
                "path": "/",
            }
        ],
        "origins": [],
    }
    pth = tmp_path / "state.json"
    pth.write_text(json.dumps(state))
    result = p.load_profile_state(pth)
    cookie = result["cookies"][0]
    assert cookie["name"] == "session"
    assert cookie["expires"] == -1  # canonical default from read_cookies_json
    assert cookie["sameSite"] in ("Strict", "Lax", "None")


def test_load_state_invalid_cookie_structure_raises_profile_error(tmp_path):
    """load_profile_state must wrap invalid cookie structures in ProfileError."""
    # Missing required 'value' field — neither path can construct a Cookie.
    secret = "NOSECRETSHOULDBESEEN-9f8e7d"
    state = {"cookies": [{"name": "session", "domain": "." + secret}]}
    pth = tmp_path / "state.json"
    pth.write_text(json.dumps(state))
    with pytest.raises(p.ProfileError):
        p.load_profile_state(pth)


def test_malformed_state_does_not_leak_values(tmp_path):
    """Error messages must never echo input cookie values or storage contents."""
    secret = "SUPERSECRETVALUE-9f8e7d"
    pth = tmp_path / "state.json"
    pth.write_text("{ " + secret)
    with pytest.raises(p.ProfileError) as exc_info:
        p.load_profile_state(pth)
    assert secret not in str(exc_info.value)


def test_profile_root_mode_is_private(monkeypatch, tmp_path):
    """When initialize creates profile_root, parent directory must be mode 0o700."""
    root = tmp_path / "newroot"
    assert not root.exists()
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "cookies": [
            {"name": "a", "value": "v", "domain": ".example.com", "path": "/",
             "expires": -1, "secure": False, "httpOnly": False, "sameSite": "Lax"}
        ],
        "origins": [],
    }))
    # Provide a narrow Playwright fake so this test does not require a real browser.
    class _NullCtx:
        def set_storage_state(self, state): pass
        def add_cookies(self, cookies): pass
        def close(self): pass
    class _NullPW:
        chromium = type("C", (), {"launch_persistent_context": staticmethod(lambda **kw: _NullCtx())})()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(p, "sync_playwright", lambda: _NullPW())
    p.initialize_profile("foo", state_path, profile_root=root)
    mode = oct(root.stat().st_mode)[-3:]
    assert mode == "700", f"profile_root must be 0o700, got {mode}"


def test_default_root_rejection_linux(tmp_path):
    """Linux Chrome default profile root must be rejected."""
    bad_root = tmp_path / ".config/google-chrome"
    bad_root.mkdir(parents=True, exist_ok=True)
    with pytest.raises(p.ProfileError):
        p.resolve_profile_paths("myprofile", bad_root)


def test_default_root_rejection_windows(tmp_path):
    """Windows Chrome default profile root must be rejected."""
    bad_root = tmp_path / "Users/SomeUser/AppData/Local/Google/Chrome/User Data"
    bad_root.mkdir(parents=True, exist_ok=True)
    with pytest.raises(p.ProfileError):
        p.resolve_profile_paths("myprofile", bad_root)


def test_initialize_failure_removes_partial_profile(monkeypatch, tmp_path):
    """If Playwright init fails, partial profile directory must be removed."""
    root = tmp_path / "profiles"
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"cookies": [
        {"name": "a", "value": "v", "domain": ".example.com", "path": "/",
         "expires": -1, "secure": False, "httpOnly": False, "sameSite": "Lax"}
    ], "origins": []}))
    paths = p.resolve_profile_paths("failtest", root)

    class FailingPW:
        chromium = type("C", (), {"launch_persistent_context": staticmethod(
            lambda **kw: (_ for _ in ()).throw(RuntimeError("simulated launch failure"))
        )})()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(p, "sync_playwright", lambda: FailingPW())
    with pytest.raises(p.ProfileError):
        p.initialize_profile("failtest", state_path, profile_root=root)
    assert not paths.user_data_dir.exists(), "partial profile must be cleaned up"


def test_run_profile_missing_metadata_fails_closed(tmp_path, monkeypatch):
    """If user_data_dir exists but metadata.json is missing, run must fail closed with ProfileError."""
    root = tmp_path / "profiles"
    paths = p.resolve_profile_paths("orphan", root)
    paths.user_data_dir.mkdir(parents=True, exist_ok=True)
    # Narrow fake so the test does not need a real browser.
    class _NullCtx:
        def close(self): pass
    class _NullPW:
        chromium = type("C", (), {"launch_persistent_context": staticmethod(lambda **kw: _NullCtx())})()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(p, "sync_playwright", lambda: _NullPW())
    with pytest.raises(p.ProfileError):
        p.run_profile("orphan", "https://example.com", "body", profile_root=root)


class _RecordingPage:
    def __init__(self, events, *, stage=None):
        self.events = events
        self.stage = stage
        self.url = "https://example.com/"

    def _fail(self, operation):
        self.events.append(operation)
        if self.stage == operation:
            raise RuntimeError(f"simulated {operation} failure")

    def goto(self, url, *, wait_until):
        self._fail("goto")
        self.url = url

    def wait_for_timeout(self, milliseconds):
        self._fail("wait")

    def locator(self, selector):
        self._fail("selector")

        class _Loc:
            def is_visible(self_inner):
                return True

        return _Loc()

    def title(self):
        self._fail("title")
        return "Example"


class _RecordingContext:
    def __init__(self, events, page, *, stage=None):
        self.events = events
        self.page = page
        self.stage = stage

    def new_page(self):
        self.events.append("new_page")
        if self.stage == "new_page":
            raise RuntimeError("simulated new_page failure")
        return self.page

    def set_storage_state(self, state):
        self.events.append("set_storage_state")
        if self.stage == "set_storage_state":
            raise RuntimeError("simulated state application failure")

    def add_cookies(self, cookies):
        self.events.append("add_cookies")
        if self.stage == "add_cookies":
            raise RuntimeError("simulated cookie application failure")

    def close(self):
        self.events.append("close")
        if self.stage == "close":
            raise RuntimeError("simulated close failure")


class _RecordingBrowser:
    def __init__(self, events, context, *, launch_stage=None):
        self.events = events
        self.context = context
        self.launch_stage = launch_stage
        self.arguments = None

    def launch_persistent_context(self, **kwargs):
        self.events.append("launch")
        self.arguments = kwargs
        if self.launch_stage:
            raise RuntimeError("simulated launch failure")
        return self.context


class _RecordingPlaywright:
    def __init__(self, events, context, *, launch_stage=None):
        self.chromium = _RecordingBrowser(events, context, launch_stage=launch_stage)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def _launch_secret(secret):
    return {
        "name": "session",
        "value": secret,
        "domain": ".example.com",
        "path": "/",
        "expires": -1,
        "httpOnly": True,
        "secure": False,
        "sameSite": "Lax",
    }


def test_public_keyword_interfaces():
    initialize_parameters = list(inspect.signature(p.initialize_profile).parameters)
    run_parameters = list(inspect.signature(p.run_profile).parameters)

    assert initialize_parameters == [
        "name",
        "storage_state",
        "browser_channel",
        "profile_root",
    ]
    assert inspect.signature(p.initialize_profile).parameters[
        "browser_channel"
    ].default == "chrome"
    assert inspect.signature(p.initialize_profile).parameters["profile_root"].default is None

    assert run_parameters == [
        "name",
        "goto",
        "expect_selector",
        "wait_after_load",
        "browser_channel",
        "profile_root",
    ]
    assert inspect.signature(p.run_profile).parameters["wait_after_load"].default == 5.0
    assert inspect.signature(p.run_profile).parameters["browser_channel"].default is None
    assert inspect.signature(p.run_profile).parameters["profile_root"].default is None


def test_load_state_requires_mapping_with_list_cookies_and_origins(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"cookies": [], "origins": []}))
    assert p.load_profile_state(state_path) == {"cookies": [], "origins": []}

    for malformed in (
        [],
        {"cookies": [], "origins": "not-a-list"},
        {"cookies": "not-a-list", "origins": []},
        {"origins": []},
    ):
        state_path.write_text(json.dumps(malformed))
        with pytest.raises(p.ProfileError):
            p.load_profile_state(state_path)


def test_load_state_preserves_origin_structures_unchanged(tmp_path):
    origins = [
        {
            "origin": "https://example.com",
            "localStorage": [
                {"name": "nested", "value": "local"},
                {"name": "second", "value": "two"},
            ],
            "indexedDB": [
                {
                    "name": "profile-db",
                    "version": 2,
                    "data": {"account": "local-account"},
                }
            ],
            "customOriginMetadata": "unchanged",
        }
    ]
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"cookies": [_launch_secret("cookie-value")], "origins": origins})
    )

    loaded = p.load_profile_state(state_path)

    assert loaded["origins"] == origins
    loaded["origins"][0]["localStorage"][0]["value"] = "mutated"
    assert origins[0]["localStorage"][0]["value"] == "local"


@pytest.mark.parametrize(
    "origins",
    [
        [None],
        [{}],
        [{"origin": "https://example.com"}],
        [{"origin": "https://example.com", "localStorage": [], "indexedDB": None}],
        [
            {
                "origin": "https://example.com",
                "localStorage": None,
                "indexedDB": [],
            }
        ],
        [
            {
                "origin": "https://example.com",
                "localStorage": [None],
                "indexedDB": [],
            }
        ],
    ],
)
def test_load_state_rejects_malformed_origin_structures(tmp_path, origins):
    secret = "ORIGINSECRET-123456"
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"cookies": [_launch_secret(secret)], "origins": origins})
    )

    with pytest.raises(p.ProfileError) as exc_info:
        p.load_profile_state(state_path)

    assert secret not in str(exc_info.value)
    assert len(str(exc_info.value)) <= 120


def test_initialize_applies_origins_then_cookies_and_closes(monkeypatch, tmp_path):
    events = []
    context = _RecordingContext(events, _RecordingPage(events))
    monkeypatch.setattr(
        p,
        "sync_playwright",
        lambda: _RecordingPlaywright(events, context),
    )
    root = tmp_path / "profiles"
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "cookies": [_launch_secret("cookie-value")],
                "origins": [
                    {
                        "origin": "https://example.com",
                        "localStorage": [{"name": "marker", "value": "local"}],
                        "indexedDB": [
                            {"name": "db", "version": 1, "data": {"key": "idb"}}
                        ],
                    }
                ],
            }
        )
    )

    metadata = p.initialize_profile(
        "ordered",
        storage_state=state_path,
        browser_channel="chrome",
        profile_root=root,
    )

    assert events == ["launch", "set_storage_state", "add_cookies", "close"]
    assert context.events == events
    assert context.page.events == ["launch", "set_storage_state", "add_cookies", "close"]

    assert metadata.browser_channel == "chrome"
    metadata_path = root / "ordered" / "browserclaw-profile.json"
    metadata_json = json.loads(metadata_path.read_text())
    assert set(metadata_json) == {
        "name",
        "browser_channel",
        "created_at",
        "storage_state_sha256",
    }


@pytest.mark.parametrize("stage", ["set_storage_state", "add_cookies", "close"])
def test_initialize_always_closes_and_removes_partial_profile(
    monkeypatch, tmp_path, stage
):
    events = []
    context = _RecordingContext(events, _RecordingPage(events), stage=stage)
    monkeypatch.setattr(
        p,
        "sync_playwright",
        lambda: _RecordingPlaywright(events, context),
    )
    root = tmp_path / "profiles"
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "cookies": [_launch_secret("cookie-value")],
                "origins": [
                    {
                        "origin": "https://example.com",
                        "localStorage": [{"name": "marker", "value": "local"}],
                        "indexedDB": [],
                    }
                ],
            }
        )
    )

    with pytest.raises(p.ProfileError):
        p.initialize_profile("partial", storage_state=state_path, profile_root=root)

    assert "close" in events
    assert not (root / "partial").exists()


@pytest.mark.parametrize(
    "stage", ["new_page", "goto", "wait", "selector", "title", "close"]
)
def test_run_always_closes_context_when_browser_operation_fails(
    monkeypatch, tmp_path, stage
):
    events = []
    page = _RecordingPage(events, stage=stage)
    context = _RecordingContext(events, page, stage=stage)
    monkeypatch.setattr(
        p,
        "sync_playwright",
        lambda: _RecordingPlaywright(events, context),
    )
    root = tmp_path / "profiles"
    paths = p.resolve_profile_paths("runfailure", root)
    paths.user_data_dir.mkdir(parents=True)
    paths.metadata_path.write_text(
        json.dumps(
            {
                "name": "runfailure",
                "browser_channel": "chrome",
                "created_at": "2026-08-25T00:00:00+00:00",
                "storage_state_sha256": "0" * 64,
            }
        )
    )

    with pytest.raises(p.ProfileError) as exc_info:
        p.run_profile(
            "runfailure",
            goto="https://example.com/load",
            expect_selector="#loaded",
            wait_after_load=0,
            browser_channel=None,
            profile_root=root,
        )

    assert "close" in events
    assert len(str(exc_info.value)) <= 120


def test_run_selector_absence_is_not_a_browser_failure(monkeypatch, tmp_path):
    events = []
    page = _RecordingPage(events)

    class _HiddenLocator:
        def is_visible(self):
            return False

    page.locator = lambda selector: _HiddenLocator()
    context = _RecordingContext(events, page)
    monkeypatch.setattr(
        p,
        "sync_playwright",
        lambda: _RecordingPlaywright(events, context),
    )
    root = tmp_path / "profiles"
    paths = p.resolve_profile_paths("absent", root)
    paths.user_data_dir.mkdir(parents=True)
    paths.metadata_path.write_text(
        json.dumps(
            {
                "name": "absent",
                "browser_channel": "chrome",
                "created_at": "2026-08-25T00:00:00+00:00",
                "storage_state_sha256": "0" * 64,
            }
        )
    )

    result = p.run_profile(
        "absent",
        goto="https://example.com/load",
        expect_selector="#missing",
        wait_after_load=0,
        browser_channel=None,
        profile_root=root,
    )

    assert result.status == "not_verified"
    assert result.expected_selector_visible is False
    assert events[-1] == "close"


@pytest.mark.parametrize("override", [None, "fake-channel"])
def test_run_uses_persisted_channel_and_does_not_reimport(
    monkeypatch, tmp_path, override
):
    events = []
    page = _RecordingPage(events)
    context = _RecordingContext(events, page)
    playwright = _RecordingPlaywright(events, context)
    monkeypatch.setattr(p, "sync_playwright", lambda: playwright)
    root = tmp_path / "profiles"
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "cookies": [_launch_secret("cookie-value")],
                "origins": [
                    {
                        "origin": "https://example.com",
                        "localStorage": [{"name": "marker", "value": "local"}],
                        "indexedDB": [],
                    }
                ],
            }
        )
    )
    p.initialize_profile("runprof", storage_state=state_path, profile_root=root)
    events.clear()

    result = p.run_profile(
        "runprof",
        goto="https://example.com/load",
        expect_selector="#loaded",
        wait_after_load=0,
        browser_channel=override,
        profile_root=root,
    )

    assert result.status == "verified"
    run_events = events[events.index("new_page") - 1 :]
    assert "set_storage_state" not in run_events
    assert "add_cookies" not in run_events
    assert playwright.chromium.arguments == {
        "user_data_dir": str(root / "runprof"),
        "channel": override or "chrome",
        "headless": True,
    }
    assert run_events == [
        "launch",
        "new_page",
        "goto",
        "wait",
        "selector",
        "title",
        "close",
    ]


def test_initialize_rejects_existing_profile_without_launch(monkeypatch, tmp_path):
    root = tmp_path / "profiles"
    paths = p.resolve_profile_paths("existing", root)
    paths.user_data_dir.mkdir(parents=True)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "cookies": [_launch_secret("cookie-value")],
                "origins": [],
            }
        )
    )

    class ExplodingPlaywright:
        def __getattribute__(self, name):
            raise AssertionError("Playwright must not launch for an existing profile")

    monkeypatch.setattr(p, "sync_playwright", lambda: ExplodingPlaywright())

    with pytest.raises(p.ProfileError, match="already exists"):
        p.initialize_profile("existing", storage_state=state_path, profile_root=root)


def test_run_rejects_missing_profile_without_launch(monkeypatch, tmp_path):
    root = tmp_path / "profiles"

    class ExplodingPlaywright:
        def __getattribute__(self, name):
            raise AssertionError("Playwright must not launch for a missing profile")

    monkeypatch.setattr(p, "sync_playwright", lambda: ExplodingPlaywright())

    with pytest.raises(p.ProfileError):
        p.run_profile(
            "missing",
            goto="https://example.com",
            expect_selector="body",
            profile_root=root,
        )


def test_list_profiles_is_sorted_and_never_returns_storage_secrets(tmp_path):
    root = tmp_path / "profiles"
    secret = "LIST-SECRET-123456"
    for name in ("z-profile", "a-profile"):
        profile_root = root / name
        profile_root.mkdir(parents=True)
        (profile_root / "browserclaw-profile.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "browser_channel": "chrome",
                    "created_at": "2026-08-25T00:00:00+00:00",
                    "storage_state_sha256": "0" * 64,
                    "cookie_value": secret,
                    "localStorage": {"marker": secret},
                }
            )
        )

    profiles = p.list_profiles(root)

    assert [profile.name for profile in profiles] == ["a-profile", "z-profile"]
    assert all(secret not in json.dumps(profile.to_dict()) for profile in profiles)


class _ProfileStateHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            "<!doctype html><html><head><title>Persisted Profile</title></head>"
            "<body><div id='persisted'>profile marker</div></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def _installed_chrome_path():
    candidates = [
        os.environ.get("CHROME_PATH"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/opt/google/chrome/chrome",
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chrome"),
    ]
    if os.name == "nt":
        candidates.extend(
            [
                os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(
                    r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"
                ),
            ]
        )
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)


def test_persistent_profile_survives_real_process_boundary(tmp_path):
    chrome_path = _installed_chrome_path()
    if chrome_path is None:
        pytest.skip("branded Chrome unavailable; checked known Chrome installation paths")
    assert chrome_path == "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProfileStateHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    root = tmp_path / "profiles"
    state_path = tmp_path / "state.json"
    local_key = "browserclaw-local-marker"
    local_value = "local-value-marker"
    cookie_name = "browserclaw-cookie-marker"
    cookie_value = "cookie-value-marker"
    database_name = "browserclaw-indexeddb-marker"
    database_value = "indexeddb-value-marker"
    state_path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": cookie_name,
                        "value": cookie_value,
                        "domain": "127.0.0.1",
                        "path": "/",
                        "expires": 4102444800,
                        "httpOnly": True,
                        "secure": False,
                        "sameSite": "Lax",
                    }
                ],
                "origins": [
                    {
                        "origin": origin,
                        "localStorage": [],
                        "indexedDB": [],
                    }
                ],
            }
        )
    )

    try:
        metadata = p.initialize_profile(
            "boundary", storage_state=state_path, profile_root=root
        )
        assert metadata.browser_channel == "chrome"

        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(root / "boundary"),
                channel="chrome",
                headless=True,
            )
            try:
                page = context.new_page()
                page.goto(origin, wait_until="domcontentloaded")
                context.add_cookies(
                    [
                        {
                            "name": cookie_name,
                            "value": cookie_value,
                            "domain": "127.0.0.1",
                            "path": "/",
                            "expires": 4102444800,
                            "httpOnly": True,
                            "secure": False,
                            "sameSite": "Lax",
                        }
                    ]
                )
                page.evaluate(
                    "([key, value]) => localStorage.setItem(key, value)",
                    [local_key, local_value],
                )
                page.evaluate(
                    """async ([name, value]) => {
                        const request = indexedDB.open(name, 1);
                        request.onupgradeneeded = () =>
                            request.result.createObjectStore('records');
                        await new Promise((resolve, reject) => {
                            request.onerror = () => reject(request.error);
                            request.onsuccess = () => {
                                const database = request.result;
                                const transaction = database.transaction('records', 'readwrite');
                                transaction.objectStore('records').put(value, 'marker');
                                transaction.onerror = () => reject(transaction.error);
                                transaction.oncomplete = resolve;
                            };
                        });
                    }""",
                    [database_name, database_value],
                )
                page.wait_for_timeout(100)
            finally:
                page.close()
                context.close()

        result = p.run_profile(
            "boundary",
            goto=origin,
            expect_selector="#persisted",
            wait_after_load=0,
            browser_channel=None,
            profile_root=root,
        )
        assert result.status == "verified"
        assert result.expected_selector_visible is True

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(root / "boundary"),
                channel="chrome",
                headless=True,
            )
            try:
                page = context.new_page()
                page.goto(origin, wait_until="domcontentloaded")
                cookies = {
                    cookie["name"]: cookie["value"]
                    for cookie in context.cookies(origin)
                }
                stored_local_value = page.evaluate(
                    "localStorage.getItem('browserclaw-local-marker')"
                )
                stored_database_value = page.evaluate(
                    """async () => {
                        const request = indexedDB.open('browserclaw-indexeddb-marker');
                        return await new Promise((resolve, reject) => {
                            request.onerror = () => reject(request.error);
                            request.onsuccess = () => {
                                const database = request.result;
                                const transaction = database.transaction('records');
                                const read = transaction.objectStore('records').get('marker');
                                read.onerror = () => reject(read.error);
                                read.onsuccess = () => {
                                    const result = read.result;
                                    // The write used put(value, 'marker'), which stores the
                                    // primitive value directly. Read it back unchanged.
                                    resolve(result === undefined ? null : result);
                                };
                            };
                        });
                    }"""
                )
            finally:
                context.close()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    assert cookies[cookie_name] == cookie_value
    assert stored_local_value == local_value
    assert stored_database_value == database_value
