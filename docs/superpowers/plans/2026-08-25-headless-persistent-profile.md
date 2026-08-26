# Headless Persistent Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fully headless, browserclaw-managed persistent Chrome profiles that preserve imported Playwright state across separate runs and report selector-based verification honestly.

**Architecture:** A focused `browserclaw.profiles` module owns profile paths, metadata, locking, storage-state import, and Playwright persistent-context lifecycle. The existing CLI only parses arguments and renders structured results. Profiles live under a private browserclaw-owned root and never point at Chrome's default user-data directory.

**Tech Stack:** Python 3.11+, Playwright Python 1.59+, filelock, pytest, branded Chrome channel.

## Global Constraints

- All new profile initialization and execution is headless.
- Never copy, symlink, open, or mutate Chrome's default user-data directory.
- Never add stealth, fingerprint overrides, CAPTCHA automation, provider APIs, or semantic keyword routing.
- Profile names match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`.
- Default storage root is `~/.browserclaw/profiles`; callers may override it explicitly for isolation.
- Diagnostics and metadata never contain cookie values, local-storage contents, IndexedDB contents, page-history text, or the storage-state source path.
- Existing cookie parsing remains the canonical cookie-normalization path.
- Existing unrelated cookie work stays outside this PR diff by stacking on the active cookie-fix branch if that prerequisite remains unmerged.

---

### Task 1: Profile paths, metadata, and locking

**Files:**
- Create: `src/browserclaw/profiles.py`
- Create: `tests/test_profiles.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `ProfileError`, `ProfilePaths`, `ProfileMetadata`, `resolve_profile_paths(name: str, profile_root: str | Path | None) -> ProfilePaths`, `profile_lock(paths: ProfilePaths)`.
- Consumes: `filelock.FileLock` and standard-library path, permission, JSON, hash, and timestamp utilities.

- [ ] **Step 1: Write failing path and metadata tests**

Add table-driven tests proving valid names resolve below a temporary root; path-like, empty, overlong, and invalid names raise `ProfileError`; known macOS/Linux/Windows Chrome default roots are rejected; created roots use mode `0700`; and metadata JSON contains only `name`, `browser_channel`, `created_at`, and `storage_state_sha256`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_profiles.py`

Expected: collection/import failure because `browserclaw.profiles` does not exist.

- [ ] **Step 3: Implement validation and metadata minimally**

Implement immutable dataclasses for paths/metadata, deterministic name validation, known-default-root rejection, private directory creation, atomic metadata writing, and SHA-256 source-state hashing. Add `filelock>=3.15` to runtime dependencies.

- [ ] **Step 4: Add and verify lock contention behavior**

Add a test that holds the profile lock and proves a second zero-timeout acquisition raises a secret-free `ProfileError`; implement `profile_lock` as the single locking boundary.

- [ ] **Step 5: Run tests and commit**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_profiles.py tests/test_cli.py`

Commit: `feat: add managed profile lifecycle [codex][gpt-5.6]`

---

### Task 2: Normalize and import Playwright storage state

**Files:**
- Modify: `src/browserclaw/profiles.py`
- Modify: `tests/test_profiles.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `load_profile_state(path: str | Path) -> dict`, `initialize_profile(name: str, storage_state: str | Path, browser_channel: str = "chrome", profile_root: str | Path | None = None) -> ProfileMetadata`.
- Consumes: `browserclaw.cookies.read_cookies_json()` and each `Cookie.to_playwright()` result.

- [ ] **Step 1: Write failing state-normalization tests**

Use literal fixtures for a cookie-only browserclaw export and a full Playwright state containing origin local-storage and IndexedDB fields. Assert normalized cookie dictionaries preserve exact non-secret attributes, origin structures survive unchanged, malformed roots fail closed, and input values never appear in exception text.

- [ ] **Step 2: Verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_profiles.py -k 'state or initialize'`

Expected: failures because `load_profile_state` and `initialize_profile` are absent.

- [ ] **Step 3: Implement state loading**

Read JSON once, require a mapping with list-valued `cookies` and `origins`, normalize cookies through the existing cookie model, and pass documented origin fields through without logging content. Raise `ProfileError` for malformed state.

- [ ] **Step 4: Implement headless initialization**

Create the managed directory under lock, launch `playwright.chromium.launch_persistent_context(user_data_dir, channel=browser_channel, headless=True)` without custom evasion/sandbox flags, apply origin state with `context.set_storage_state({"cookies": [], "origins": origins})`, add normalized cookies, write metadata, and close in `finally`. Delete the incomplete managed profile when initialization fails before metadata is committed.

- [ ] **Step 5: Test with a controlled fake Playwright boundary and commit**

Use a narrow fake only for the external browser launch boundary. Assert real filesystem state, normalized input, call ordering, headless/channel arguments, cleanup, and metadata. Do not assert merely that a mock exists.

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_profiles.py`

Commit: `feat: initialize headless persistent profiles [codex][gpt-5.6]`

---

### Task 3: Run and list persistent profiles

**Files:**
- Modify: `src/browserclaw/profiles.py`
- Modify: `tests/test_profiles.py`

**Interfaces:**
- Produces: `ProfileRunResult`, `run_profile(name: str, goto: str, expect_selector: str, wait_after_load: float = 5.0, browser_channel: str | None = None, profile_root: str | Path | None = None) -> ProfileRunResult`, `list_profiles(profile_root: str | Path | None = None) -> list[ProfileMetadata]`.
- Consumes: metadata written by Task 2 and the shared lock from Task 1.

- [ ] **Step 1: Write failing run/list tests**

Prove missing profiles fail, persisted metadata determines the channel, selector-visible returns status `verified`, selector-absent returns `not_verified`, no state is reimported on run, and listing is sorted and contains only metadata fields.

- [ ] **Step 2: Verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_profiles.py -k 'run or list'`

Expected: failures because the interfaces are absent.

- [ ] **Step 3: Implement run and list minimally**

Launch the existing user-data directory with a headless persistent context, navigate with `wait_until="domcontentloaded"`, wait the requested bounded interval, evaluate only selector visibility, capture URL/title, and close cleanly. Return a dataclass; do not print body text or classify page semantics.

- [ ] **Step 4: Verify GREEN and process-boundary persistence**

Add an integration test using installed Chromium/Chrome and a local HTTP page. Initialize a temporary profile, set cookie/local-storage/IndexedDB markers through browser APIs, close, reopen via `run_profile`, and prove the markers survive in a separate browser process.

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_profiles.py`

- [ ] **Step 5: Commit**

Commit: `feat: run and verify persistent profiles [codex][gpt-5.6]`

---

### Task 4: CLI and documentation

**Files:**
- Modify: `src/browserclaw/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `README.md`
- Modify: `SKILL.md`

**Interfaces:**
- Consumes: `initialize_profile`, `run_profile`, and `list_profiles` from Tasks 2-3.
- Produces: `browserclaw profile init`, `browserclaw profile run`, and `browserclaw profile list` command contracts.

- [ ] **Step 1: Write failing parser and dispatch tests**

Assert exact parsing for each command, JSON output for success and `not_verified`, nonzero exit on failed selector verification, `--profile-root` isolation, and secret-free errors. Exercise real parser/dispatch code while faking only the external browser boundary.

- [ ] **Step 2: Verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_cli.py -k profile`

Expected: parser rejection because `profile` is not registered.

- [ ] **Step 3: Implement parser and dispatch**

Add the nested subcommands and structured JSON rendering. Keep the CLI dispatcher thin; all policy and filesystem behavior remains in `profiles.py`.

- [ ] **Step 4: Update current documentation**

Document the commands, fully-headless limitation, sensitive-state handling, and accurate Playwright storage-state capabilities. Remove the outdated README claim that storage state contains only HTTP cookies.

- [ ] **Step 5: Run the full suite and commit**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q -p no:cacheprovider`

Commit: `feat: expose headless profile commands [codex][gpt-5.6]`

---

### Task 5: Live probes, evidence, independent review, and PR update

**Files:**
- Create: `evidence/headless-persistent-profile/<timestamp>/manifest.json`
- Create: `evidence/headless-persistent-profile/<timestamp>/commands.log`
- Create: `evidence/headless-persistent-profile/<timestamp>/results.json`
- Create: `evidence/headless-persistent-profile/<timestamp>/README.md`
- Modify: PR #17 description

**Interfaces:**
- Consumes: installed branch CLI and the operator's local Chrome cookie database.
- Produces: secret-free raw evidence and an independent `/er` verdict bound to the exact PR head SHA.

- [ ] **Step 1: Install the branch in its isolated virtual environment**

Run: `.venv/bin/python -m pip install -e '.[dev]'`

- [ ] **Step 2: Generate ephemeral cookie state without logging values**

Decrypt the active Chrome cookie database to a mode-`0600` temporary file. Record only per-domain cookie counts. Seed separate temporary managed profiles for ChatGPT and Perplexity.

- [ ] **Step 3: Run each vendor twice**

Use real Chrome headless and vendor-specific composer selectors. Record command, timing, exit code, final URL, title, selector result, and profile-reuse result. Do not record page body, screenshot, cookie values, history, or local-storage contents.

- [ ] **Step 4: Sanitize and package evidence**

Delete plaintext state and temporary profiles after extracting non-secret results. Create SHA-256 checksums and a manifest binding evidence to `git rev-parse HEAD`. Verify repository evidence contains no secrets with gitleaks.

- [ ] **Step 5: Run independent `/er`**

Resolve the canonical `/er` command and skills, run the dedup check, then dispatch an independent `gpt-5.6-terra` evidence reviewer at medium reasoning against the evidence directory and exact PR head. Require a claim-by-claim verdict and evidence-standards compliance result.

- [ ] **Step 6: Fix every confirmed blocker and rerun affected proof**

Do not weaken evidence claims. If `/er` finds a product defect, return to the relevant TDD task, add the failing test, fix it, rerun automated and live evidence, and obtain a fresh verdict at the new SHA.

- [ ] **Step 7: Commit, push once, and refresh PR**

Run the full suite, `git diff --check`, staged gitleaks, branch tracking, remote-head equality, and PR checks. Update the draft PR with exact results and limitations.

Commit: `test: add persistent profile evidence [codex][gpt-5.6]`
