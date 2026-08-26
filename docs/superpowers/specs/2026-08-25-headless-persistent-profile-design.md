# Fully headless persistent Chrome profiles

Date: 2026-08-25

## Goal

Add a fully headless browserclaw mode that preserves authenticated browser state
across runs more faithfully than the current cookie-only injection command. The
feature should improve the chance that authorized ChatGPT and Perplexity web
sessions remain usable, while remaining honest when Cloudflare rejects a
headless browser.

This is state-preserving browser automation. It is not stealth, CAPTCHA
automation, an authentication bypass, or a promise that every vendor will
accept headless Chrome.

## Research basis

The supporting primary-source review is recorded in
[`docs/research-authenticated-headless-browser-state-2026-08-25.md`](../../research-authenticated-headless-browser-state-2026-08-25.md).

The important findings are:

- browserclaw currently restores cookies into a fresh, non-persistent context
  and discards storage-state origins;
- Playwright persistent contexts retain browser-managed state in a dedicated
  user-data directory;
- current Playwright storage state can restore cookies, local storage, and
  IndexedDB;
- Chrome and Playwright prohibit automating the user's default Chrome profile;
  and
- Cloudflare evaluates headless/browser/session signals continuously, so
  additional state fidelity cannot guarantee challenge passage.

## Scope

### Included

- Named browserclaw-managed persistent profiles.
- Fully headless profile initialization and execution.
- Initial seeding from a browserclaw cookie export or full Playwright storage
  state.
- Preservation of storage-state origins, including local storage and IndexedDB
  data when present in the source file.
- Safe profile naming, private filesystem permissions, default-profile path
  rejection, and exclusive profile locking.
- Caller-supplied selector verification for authenticated UI.
- Structured diagnostics that never print cookie values or page-history text.
- Live ChatGPT and Perplexity probes using temporary managed profiles.

### Excluded

- Headed login or challenge completion.
- Copying, symlinking, or directly opening Chrome's default user-data directory.
- Stealth patches, fingerprint overrides, CAPTCHA solvers, retry loops, or
  provider APIs.
- A hard-coded semantic classifier for Cloudflare or vendor authentication.
- A guarantee that ChatGPT or Perplexity accepts headless automation.
- Partition-key extraction from Chromium's cookie database. That change
  overlaps active cookie work and can follow independently without blocking the
  persistent-profile feature.

## User interface

### Initialize a profile

```bash
browserclaw profile init web-advice-chatgpt \
  --storage-state /secure/path/chatgpt-state.json \
  --browser-channel chrome
```

`profile init` always launches Chrome headlessly. It creates the named managed
profile, imports the supplied state exactly once, flushes it through Chrome,
and exits. It fails if the profile already exists.

The input may be:

- a browserclaw `cookies decrypt` JSON file, which seeds cookies only; or
- a Playwright storage-state file, which may additionally seed origin local
  storage and IndexedDB.

### Run a profile

```bash
browserclaw profile run web-advice-chatgpt \
  --goto https://chatgpt.com/ \
  --expect-selector 'textarea, [contenteditable="true"]' \
  --wait-after-load 8
```

`profile run` always launches the same persistent profile headlessly. It does
not reimport the source state, because doing so could overwrite fresher
clearance or session data accumulated by Chrome.

The command prints bounded JSON diagnostics:

```json
{
  "profile": "web-advice-chatgpt",
  "url": "https://chatgpt.com/",
  "title": "ChatGPT",
  "expected_selector_visible": true,
  "status": "verified"
}
```

When the caller's selector is absent, status is `not_verified` and the command
exits nonzero. Browserclaw does not infer why the selector is absent; the caller
may inspect the title, URL, or an explicitly requested screenshot.

### Inspect profiles

```bash
browserclaw profile list
```

Listing returns profile names and non-secret metadata only. It never enumerates
cookies, local-storage keys, history, or page text.

## Storage and lifecycle

The default root is `~/.browserclaw/profiles/`. Tests and isolated callers may
override it with `--profile-root`. A profile name must match
`[A-Za-z0-9][A-Za-z0-9._-]{0,63}` and is never interpreted as a path.

Each profile directory contains Chrome's user-data files plus a small
`browserclaw-profile.json` metadata file. The metadata records the profile name,
browser channel, creation time, and a SHA-256 digest of the imported state file;
it contains no cookie values, storage contents, or source file path.

New directories use user-only permissions. Browserclaw refuses roots that
resolve to or inside known default Chrome user-data directories on macOS,
Linux, or Windows.

An exclusive cross-platform file lock covers initialization and every run.
Concurrent access fails with a bounded diagnostic instead of risking profile
corruption. Chrome remains the sole writer of its profile databases.

## Components and data flow

### CLI parser and dispatch

`src/browserclaw/cli.py` adds the `profile init`, `profile run`, and `profile
list` command surface and delegates profile behavior to one focused module.

### Profile lifecycle module

`src/browserclaw/profiles.py` owns:

- name and root validation;
- default Chrome profile rejection;
- private directory and metadata creation;
- locking;
- storage-state normalization;
- persistent-context initialization and execution; and
- structured result objects.

This warrants a new module because profile lifecycle, locking, filesystem
safety, and persistent Playwright operation are a separate responsibility from
cookie decryption and the already-large CLI dispatcher.

### State import

Initialization reads the JSON once. Existing cookie parsing remains the
canonical cookie normalization path. Origin state passes through only when it
matches Playwright's documented storage-state structure.

The persistent context starts with the branded `chrome` channel and no custom
fingerprint or sandbox-disabling arguments. Browserclaw applies origin state and
normalized cookies, then closes the context so Chrome persists them.

### Execution

Run acquires the profile lock, launches `launch_persistent_context` headlessly,
navigates to the requested URL, waits for the bounded caller-selected interval,
checks the caller-supplied selector, emits diagnostics, and closes cleanly.

## Error handling

The feature fails closed for:

- invalid or path-like profile names;
- roots resolving into a default Chrome user-data directory;
- missing, malformed, or unsupported storage state;
- an existing profile during initialization;
- a missing profile during execution;
- concurrent profile access;
- Playwright/Chrome launch or navigation failure; and
- absence of the caller-provided verification selector.

Errors never include cookie values or serialized storage content. There is no
automatic retry after a Cloudflare or authentication failure.

## Dependency decision

Raise the Playwright minimum to `1.59`, which supports applying current storage
state, including IndexedDB, to an existing context. Use the established
`filelock` package for cross-platform exclusive profile locking rather than
inventing platform-specific lock code.

No other runtime dependency is added.

## Testing

### Unit tests

- Parser coverage for all profile subcommands.
- Valid and invalid profile names.
- Default-profile path rejection on macOS, Linux, and Windows path shapes.
- Private directory and metadata creation.
- Existing/missing profile failures.
- Lock contention failure.
- Cookie-only state normalization.
- Full origin/local-storage/IndexedDB state preservation.
- Secret-free diagnostics and metadata.
- Selector success and failure using a local deterministic page.

Every production behavior is introduced through a failing test before its
implementation.

### Integration tests

- Initialize a temporary persistent profile from a fixture state.
- Run it twice and prove a cookie plus local-storage and IndexedDB markers
  survive the process boundary.
- Prove a failed selector exits nonzero without dumping page text.
- Run the complete repository suite with `PYTHONPATH=src pytest -q`.

### Live authorized probes

Use temporary browserclaw-managed profiles seeded from the operator's decrypted
Chrome cookies. Probe ChatGPT and Perplexity twice each with real Chrome
headless. Record only cookie counts, URL, title, selector result, timing, and
challenge status inferred by the reviewing agent—not cookie values or history.

Success means an authenticated composer selector is present on a repeated
headless run. Continued Cloudflare pages are an honest negative result, not a
reason to add stealth or bypass logic.

## Acceptance criteria

1. The new commands operate entirely headlessly.
2. Managed state survives separate Chrome processes and is not overwritten on
   subsequent runs.
3. Browserclaw never opens or mutates the user's default Chrome profile.
4. Profile files and metadata are private and contain no logged secrets.
5. Concurrent use and invalid paths fail closed.
6. Verification depends on a caller-supplied DOM selector, not keyword policy.
7. Existing cookie, capture, infer, generate, reverse, and learn commands remain
   compatible.
8. All automated tests pass.
9. Live ChatGPT and Perplexity results are reported accurately even if the
   outcome remains `not_verified`.

## PR strategy

Create one draft PR against `jleechanorg/browserclaw` from
`feat/headless-persistent-profile`. Keep the existing unrelated cookie fix out
of the branch. The PR remains draft until the repository's required review and
verification gates are satisfied.
