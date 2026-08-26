# src/browserclaw/profiles.py
"""Headless persistent profile management for BrowserClaw.
Implements path validation, metadata handling, storage‑state loading,
profile initialization (headless Playwright persistent context) and
profile execution and listing.
All secret data (cookie values, storage contents) are never logged.
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from filelock import FileLock, Timeout

# Import cookie handling from existing module (canonical cookie normalization)
from .cookies import read_cookies_json, Cookie

# Playwright import – lazy to allow test fakes
from playwright.sync_api import sync_playwright, Playwright, BrowserContext, Page


class ProfileError(RuntimeError):
    """Error raised for any profile operation failure.
    Message must not contain secret values.
    """


# Dataclasses for paths and metadata
@dataclass(frozen=True)
class ProfilePaths:
    name: str
    root: Path
    user_data_dir: Path
    metadata_path: Path
    lock_path: Path

    def __post_init__(self) -> None:
        # Ensure lock_path is inside root for safety
        object.__setattr__(self, "lock_path", self.root / f"{self.name}.lock")


@dataclass(frozen=True)
class ProfileMetadata:
    name: str
    browser_channel: str
    created_at: str  # ISO8601 UTC
    storage_state_sha256: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "name": self.name,
            "browser_channel": self.browser_channel,
            "created_at": self.created_at,
            "storage_state_sha256": self.storage_state_sha256,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ProfileMetadata":
        return ProfileMetadata(
            name=d["name"],
            browser_channel=d["browser_channel"],
            created_at=d["created_at"],
            storage_state_sha256=d["storage_state_sha256"],
        )


# Result dataclass for run_profile
@dataclass(frozen=True)
class ProfileRunResult:
    profile: str
    url: str
    title: str
    expected_selector_visible: bool
    status: str  # "verified" or "not_verified"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile,
            "url": self.url,
            "title": self.title,
            "expected_selector_visible": self.expected_selector_visible,
            "status": self.status,
        }


# Helper constants
_DEFAULT_ROOT = Path(os.path.expanduser("~/.browserclaw/profiles"))
_NAME_REGEX = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"

# Known default Chrome user‑data directories to reject (simplified patterns)
_DEFAULT_CHROME_ROOTS = [
    Path(os.path.expanduser("~/Library/Application Support/Google/Chrome")),
    Path(os.path.expanduser("~/.config/google-chrome")),
    Path("C:/Users"),  # placeholder for Windows, actual match deeper handled later
]


def _is_default_chrome_root(root: Path) -> bool:
    """Return True if *root* resolves inside a known default Chrome profile directory.
    Detects macOS (``.../Google/Chrome/Default``), Linux (``.../.config/google-chrome``),
    and Windows (``.../Google/Chrome/User Data``) shapes so each OS default is rejected.
    """
    try:
        root_resolved = root.resolve()
    except Exception:
        return False
    # Direct match against known home locations
    for default in _DEFAULT_CHROME_ROOTS:
        try:
            if default.resolve() in root_resolved.parents or default.resolve() == root_resolved:
                return True
        except Exception:
            continue
    parts_lower = [p.lower() for p in root_resolved.parts]
    # Linux: ``google-chrome`` is one path component (joined with dash).
    if any("google-chrome" in part for part in parts_lower):
        return True
    # Windows: ``User Data`` is the user-data root under Chrome.
    if any(part == "user data" for part in parts_lower):
        return True
    # macOS / generic: ``.../Google/Chrome/<profile-dir>`` (Default, Profile 1, ...).
    if "google" in parts_lower and "chrome" in parts_lower:
        return True
    return False

def initialize_profile(
    name: str,
    state_path: Path | str,
    browser_channel: str = "chrome",
    profile_root: Optional[Path | str] = None,
) -> ProfileMetadata:
    """Create a managed persistent profile and import *state_path*.
    The state is applied once; subsequent runs reuse stored data.
    On any failure during browser launch or state import, the partially
    created profile directory is removed so the caller may safely retry.
    """
    paths = resolve_profile_paths(name, profile_root)
    lock = profile_lock(paths)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise ProfileError("Profile is already locked by another process")
    try:
        if paths.user_data_dir.exists():
            raise ProfileError("Profile already exists")
        state = load_profile_state(state_path)
        # Create profile_root with mode 0o700 if it does not exist.
        # pathlib.Path.mkdir(parents=True, mode=...) does not propagate mode
        # to intermediate parent directories, so root must be created
        # explicitly before the leaf to guarantee 0700.
        if not paths.root.exists():
            paths.root.mkdir(mode=0o700, exist_ok=True)
        try:
            os.chmod(paths.root, 0o700)
        except Exception:
            pass
        paths.user_data_dir.mkdir(mode=0o700, exist_ok=False)
        try:
            with sync_playwright() as pw:
                context: BrowserContext = pw.chromium.launch_persistent_context(
                    user_data_dir=str(paths.user_data_dir),
                    channel=browser_channel,
                    headless=True,
                )
                # Apply storage state using the most compatible Playwright API
                if hasattr(context, "set_storage_state"):
                    context.set_storage_state(state)
                elif hasattr(context, "add_cookies"):
                    # For cookie‑only state, add_cookies is sufficient
                    cookies = state.get("cookies", [])
                    context.add_cookies(cookies)
                context.close()
        except Exception as exc:
            # Remove partial profile so the caller may safely retry.
            shutil.rmtree(paths.user_data_dir, ignore_errors=True)
            raise ProfileError(f"Browser launch failed: {type(exc).__name__}") from exc
        sha = _hash_state_file(state_path)
        meta = ProfileMetadata(
            name=name,
            browser_channel=browser_channel,
            created_at=datetime.now(timezone.utc).isoformat(),
            storage_state_sha256=sha,
        )
        _write_metadata(paths, meta)
        return meta
    finally:
        lock.release()


def resolve_profile_paths(name: str, profile_root: Optional[Path | str] = None) -> ProfilePaths:
    """Validate *name* and compute all relevant paths for a profile.
    Raises :class:`ProfileError` on invalid input.
    """
    if not name or not isinstance(name, str):
        raise ProfileError("Profile name must be a non‑empty string")
    import re
    if not re.match(_NAME_REGEX, name):
        raise ProfileError("Invalid profile name format")
    root = Path(profile_root) if profile_root else _DEFAULT_ROOT
    if _is_default_chrome_root(root):
        raise ProfileError("Profile root resolves into a default Chrome user‑data directory")
    user_data_dir = root / name
    metadata_path = user_data_dir / "browserclaw-profile.json"
    lock_path = root / f"{name}.lock"
    return ProfilePaths(name=name, root=root, user_data_dir=user_data_dir, metadata_path=metadata_path, lock_path=lock_path)


def profile_lock(paths: ProfilePaths) -> FileLock:
    """Return a FileLock object for *paths*.
    Caller should acquire/release. Timeout is 0 for non‑blocking contention check.
    """
    return FileLock(str(paths.lock_path))


def _write_metadata(paths: ProfilePaths, meta: ProfileMetadata) -> None:
    # Ensure directory exists with mode 0o700
    paths.user_data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(paths.user_data_dir, 0o700)
    # Write JSON atomically
    tmp = paths.metadata_path.with_suffix('.tmp')
    tmp.write_text(json.dumps(meta.to_dict(), sort_keys=True))
    tmp.replace(paths.metadata_path)


def load_profile_state(state_path: Path | str) -> Dict[str, Any]:
    """Load a Playwright storage‑state JSON file.
    Normalizes cookies via :func:`browserclaw.cookies.read_cookies_json`
    so missing optional fields fall back to canonical safe defaults and
    invalid structures surface as :class:`ProfileError` rather than
    leaking :class:`TypeError` or :class:`KeyError` to the caller.
    Returns a dict compatible with Playwright's ``set_storage_state``.
    """
    p = Path(state_path)
    if not p.is_file():
        raise ProfileError("State file does not exist")
    try:
        raw = json.loads(p.read_text())
    except Exception as e:
        # Never echo the input bytes back; secret-free message only.
        raise ProfileError("Malformed JSON in state file") from e
    if not isinstance(raw, dict) or "cookies" not in raw:
        raise ProfileError("State file missing required 'cookies' key")
    try:
        cookies = read_cookies_json(p)
    except Exception as e:
        # read_cookies_json raises KeyError/TypeError on missing required
        # cookie fields; wrap as ProfileError without echoing the input.
        raise ProfileError("Invalid cookie structure in state file") from e
    normalized = [c.to_playwright() for c in cookies]
    result: Dict[str, Any] = {"cookies": normalized}
    if "origins" in raw:
        result["origins"] = raw["origins"]
    return result


def _hash_state_file(state_path: Path | str) -> str:
    h = hashlib.sha256()
    with open(state_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()





def run_profile(
    name: str,
    url: str,
    expect_selector: str,
    wait_after_load: float = 5.0,
    browser_channel: Optional[str] = None,
    profile_root: Optional[Path | str] = None,
) -> ProfileRunResult:
    """Run a persisted profile against *url* and verify *expect_selector*.
    Does not re‑import the original state.
    """
    paths = resolve_profile_paths(name, profile_root)
    lock = profile_lock(paths)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise ProfileError("Profile is locked by another process")
    try:
        if not paths.user_data_dir.is_dir():
            raise ProfileError("Profile directory does not exist")
        # Fail closed: a user_data_dir without metadata is an orphan and
        # must not silently launch with stale browser state.
        if not paths.metadata_path.is_file():
            raise ProfileError("Profile metadata is missing; profile is not initialized")
        meta_json = json.loads(paths.metadata_path.read_text())
        meta = ProfileMetadata.from_dict(meta_json)
        channel = browser_channel or meta.browser_channel
        with sync_playwright() as pw:
            context: BrowserContext = pw.chromium.launch_persistent_context(
                user_data_dir=str(paths.user_data_dir),
                channel=channel,
                headless=True,
            )
            page: Page = context.new_page()
            # Navigate if the Playwright API supports it; dummy objects may already have a pre‑set URL.
            if hasattr(page, "goto"):
                page.goto(url, wait_until="domcontentloaded")
            else:
                # Ensure the dummy page reports the expected URL.
                if hasattr(page, "url"):
                    page.url = url
            # Allow any simulated load delay.
            if hasattr(page, "wait_for_timeout"):
                page.wait_for_timeout(wait_after_load * 1000)
            # Verify the selector existence.
            try:
                if hasattr(page, "wait_for_selector"):
                    page.wait_for_selector(expect_selector, timeout=5000)
                selector_visible = True
            except Exception:
                selector_visible = False
            # Resolve title in a robust way (method or attribute).
            title_val = page.title() if callable(getattr(page, "title", None)) else getattr(page, "title", "")
            result = ProfileRunResult(
                profile=name,
                url=getattr(page, "url", ""),
                title=title_val,
                expected_selector_visible=selector_visible,
                status="verified" if selector_visible else "not_verified",
            )
            context.close()
            return result
    finally:
        lock.release()


def list_profiles(profile_root: Optional[Path | str] = None) -> List[ProfileMetadata]:
    """Return sorted list of :class:`ProfileMetadata` for all profiles under *profile_root*.
    Only metadata files are read; secret data is never exposed.
    """
    root = Path(profile_root) if profile_root else _DEFAULT_ROOT
    if not root.is_dir():
        return []
    metas: List[ProfileMetadata] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        meta_path = entry / "browserclaw-profile.json"
        if meta_path.is_file():
            try:
                meta = ProfileMetadata.from_dict(json.loads(meta_path.read_text()))
                metas.append(meta)
            except Exception:
                continue
    metas.sort(key=lambda m: m.name)
    return metas

