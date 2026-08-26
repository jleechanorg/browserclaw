# tests/test_profiles.py
"""Tests for browserclaw.profiles module (Task 1-3)."""
import os
import json
import hashlib
import tempfile
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
        ]
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
    assert "cookies" in state and isinstance(state["cookies"], list)
    assert "origins" not in state

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
            self.title = "Example"
        def wait_for_load_state(self, state="domcontentloaded"):
            pass
        def wait_for_selector(self, selector, timeout=5000):
            if selector == "body":
                return True
            raise Exception("selector not found")
    class DummyContext:
        def __init__(self):
            self.page_obj = DummyPage()
        def new_page(self):
            return self.page_obj
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
        ]
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


def test_profile_root_mode_is_private(tmp_path):
    """When initialize creates profile_root, parent directory must be mode 0o700."""
    root = tmp_path / "newroot"
    assert not root.exists()
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "cookies": [
            {"name": "a", "value": "v", "domain": ".example.com", "path": "/",
             "expires": -1, "secure": False, "httpOnly": False, "sameSite": "Lax"}
        ]
    }))
    # Provide a narrow Playwright fake so this test does not require a real browser.
    class _NullCtx:
        def close(self): pass
    class _NullPW:
        chromium = type("C", (), {"launch_persistent_context": staticmethod(lambda **kw: _NullCtx())})()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    p.sync_playwright = lambda: _NullPW()
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
    ]}))
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
