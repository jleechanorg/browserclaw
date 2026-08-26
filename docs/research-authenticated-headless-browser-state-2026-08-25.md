# Authenticated headless Chrome state for browserclaw

Date: 2026-08-25

## Question

How should browserclaw improve authenticated Playwright automation so a
headless real-Chrome fallback preserves more of a browser session than the
current cookie-only injection, particularly when a site presents a Cloudflare
challenge, without stealth, CAPTCHA bypass, provider APIs, or mutation of the
user's live Chrome profile?

## Recommendation

Add two explicit, non-evasive authentication modes, in this order:

1. **Browserclaw-managed persistent profile (preferred).** Create a dedicated,
   non-default Chrome user-data directory owned by browserclaw. Bootstrap it in
   headed real Chrome so the user can sign in and, when necessary, complete a
   challenge normally. Reuse that same directory through Playwright
   `launch_persistent_context(..., channel="chrome", headless=True)` on later
   runs. Never point this mode at Chrome's default `User Data` directory.
2. **Partition-aware cookie hydration (incremental fallback).** Preserve the
   cookie's partition key when decrypting Chrome's cookie database and pass it
   through Playwright `add_cookies`. Keep this mode explicitly weaker than a
   persistent profile because it still lacks other browser-managed state and
   continuity.

Keep the existing CDP attachment route as the strongest operational fallback
when the selected site rejects a headless run. It controls an already-running,
user-authorized browser session rather than attempting to defeat a challenge.
It is not the default headless path.

This is a per-site capability, not a promise that headless automation will pass
every Cloudflare policy. Cloudflare says clearance is tied to the visitor and
device and is continuously reassessed from client-side session behavior; a
copied cookie alone therefore cannot establish equivalent trust in a new
browser context. This conclusion is an inference from Cloudflare's documented
model, not a claim about ChatGPT's or Perplexity's private configuration.
([Cloudflare clearance](https://developers.cloudflare.com/cloudflare-challenges/concepts/clearance/),
[Cloudflare JavaScript Detections](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/javascript-detections/))

## Current browserclaw gap

The current `cookies inject` implementation launches a new browser, creates a
new non-persistent context, calls `add_cookies`, and navigates. It therefore
starts with only the fields represented by browserclaw's `Cookie` dataclass.
([`src/browserclaw/cli.py`](../src/browserclaw/cli.py),
[`src/browserclaw/cookies.py`](../src/browserclaw/cookies.py))

The exported JSON hard-codes `"origins": []`, so it contains no local-storage
entries. The SQL projection also omits Chromium's `top_frame_site_key` and
`has_cross_site_ancestor` fields, and the dataclass has no partition-key field.
That means the current path cannot faithfully recreate a partitioned cookie.
([`src/browserclaw/cookies.py`](../src/browserclaw/cookies.py))

That omission matters independently of Cloudflare: Chromium defines a
partitioned cookie as carrying a partition key, and Playwright exposes
`partitionKey`/`partition_key` when adding and reading cookies. Playwright added
that field in version 1.54, while browserclaw currently declares only
`playwright>=1.51.0`; an implementation must either raise the minimum to 1.54 or
feature-detect and fail closed when partitioned cookies are present.
([Chromium cookie partition key](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/net/cookies/cookie_partition_key.h),
[Playwright `add_cookies`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-add-cookies),
[Playwright 1.54 release notes](https://playwright.dev/python/docs/release-notes#version-154),
[`pyproject.toml`](../pyproject.toml))

The README statement that Playwright storage state captures only HTTP cookies
is now inaccurate. Current Playwright storage state includes cookies and local
storage, can include IndexedDB with `indexed_db=True`, and supports virtual
WebAuthn credentials in newer releases. Playwright separately documents that
session storage requires an explicit application-level save/restore snippet.
([Playwright `storage_state`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-storage-state),
[Playwright authentication guide](https://playwright.dev/python/docs/auth#session-storage),
[`README.md`](../README.md))

Storage state is still not a full browser-profile snapshot. Its documented
schema covers cookies, origins/local storage, optional IndexedDB, and optional
virtual WebAuthn credentials; it does not claim to serialize service workers,
cache storage, browser preferences, or arbitrary profile files. Chromium's user
data directory, by contrast, is the container for profile data and local state.
([Playwright `storage_state`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-storage-state),
[Chromium user-data directory](https://chromium.googlesource.com/chromium/src.git/+/HEAD/docs/user_data_dir.md))

## Why a dedicated persistent profile is the correct primary enhancement

Playwright's persistent context is specifically the API for using browser
storage from a user-data directory, including cookies and local storage. It also
states that browsers do not permit two instances to share one user-data
directory. Browserclaw should therefore take an exclusive lock for a named
managed profile and reject concurrent use rather than risk corruption.
([Playwright `launch_persistent_context`](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context))

Both Playwright and Chrome reject the tempting design of automating the user's
default profile. Playwright warns that using Chrome's main `User Data` directory
may make pages fail or the browser exit. Since Chrome 136, remote-debugging
switches are ignored for the default directory and require a non-standard
`--user-data-dir`; Chrome recommends custom directories to isolate automation
from real profiles. A browserclaw-owned profile follows both contracts and
avoids unsafe mutation of the daily-driver profile.
([Playwright `launch_persistent_context`](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context),
[Chrome remote-debugging security change](https://developer.chrome.com/blog/remote-debugging-port))

Use the branded `chrome` channel for this mode. Modern Chrome headless uses the
same Chrome codebase as headed Chrome, and Playwright supports installed stable
Chrome through the `chrome` channel. This improves fidelity without pretending
that headed and headless sessions have identical bot-management outcomes.
([Chrome headless architecture](https://developer.chrome.com/blog/tools-from-chrome-for-frictionless-testing),
[Playwright browser channels](https://playwright.dev/docs/browsers#google-chrome--microsoft-edge))

Do not copy the current inject command's custom flags into the new mode without
a demonstrated need. Playwright warns that custom browser arguments can break
its functionality. In particular, a general authenticated-browser command
should not disable Chrome's sandbox by default.
([Playwright browser launch arguments](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch))

## Proposed interface and invariants

The names are illustrative; the behavioral boundaries are normative for an
implementation proposal.

```text
browserclaw profile init web-advice --browser-channel chrome
browserclaw profile run web-advice --headless --goto https://chatgpt.com/
browserclaw cookies inject ... --preserve-partition-keys
```

`profile init` should:

- allocate a browserclaw-owned non-default user-data directory with user-only
  permissions;
- launch headed real Chrome with Playwright's persistent-context API;
- require manual login/challenge completion and never script a CAPTCHA;
- verify a site-specific authenticated indicator before reporting the profile
  ready; and
- record only metadata in logs, never cookie values or storage contents.

`profile run` should:

- acquire an exclusive per-profile lock;
- launch the same channel and user-data directory headless;
- refuse paths resolving to known default Chrome profile roots;
- use ordinary Playwright DOM actions only, with no stealth patches or browser
  fingerprint overrides;
- qualify each target independently using three observations: authenticated UI,
  writable composer, and a distinct real response;
- classify a Cloudflare challenge as `headless_rejected`, close cleanly, and
  fall back to headed CDP/extension/Aside rather than looping or bypassing; and
- update the managed profile only through Chrome itself, never by rewriting its
  SQLite databases.

The existing CDP helper already demonstrates attachment to an existing context
at `localhost:9222`. Preserve that route as an explicit fallback, but expose its
lower-fidelity tradeoff: Playwright documents CDP attachment as Chromium-only
and lower fidelity than its native connection protocol.
([`src/browserclaw/capture.py`](../src/browserclaw/capture.py),
[Playwright `connect_over_cdp`](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp))

## Partition-aware cookie increment

For the smaller cookie-mode enhancement:

1. Extend the cookie SQL projection and model with Chromium's
   `top_frame_site_key` and `has_cross_site_ancestor` data.
2. Map a non-empty serialized top-frame site to Playwright's partition-key
   field. Do not silently downgrade a partitioned cookie to an unpartitioned
   cookie.
3. Require Playwright 1.54 or newer for that mapping, or emit a hard diagnostic
   and skip the affected cookie on older versions.
4. After injection, read cookies back through Playwright and compare names,
   domains, paths, and partition keys without logging values.
5. Retain the current host-only/domain boundary behavior.

Cloudflare documents `cf_clearance` as a Secure, SameSite=None, Partitioned
cookie and says a partitioned cookie is keyed to the top-level site when issued
in a third-party context. This makes silent loss of the partition key a concrete
fidelity defect even though fixing it cannot guarantee challenge passage.
([Cloudflare cookie reference](https://developers.cloudflare.com/fundamentals/reference/policies-compliances/cloudflare-cookies/),
[Cloudflare CHIPS behavior](https://developers.cloudflare.com/waf/troubleshooting/samesite-cookie-interaction/),
[Playwright `add_cookies`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-add-cookies))

## Acceptance tests

An implementation should be considered successful only if all of these pass:

1. A fixture with unpartitioned and partitioned cookies round-trips without
   values appearing in test output.
2. A managed profile bootstrapped headed can be reopened headless with the same
   cookie, local-storage, IndexedDB, and service-worker registration markers.
3. The tool rejects Chrome's default user-data path and concurrent access to a
   managed profile.
4. A clean profile is not reported authenticated merely because prompt text is
   visible; the response assertion must target a distinct response container.
5. When a challenge page remains, the result is a bounded failure with a headed
   fallback recommendation, not retries, stealth, or CAPTCHA automation.
6. The current manual-auth/no-evasion repository contract remains true.
   ([`CLAUDE.md`](../CLAUDE.md))

## Decision

Implement the browserclaw-managed persistent Chrome profile first because it
preserves substantially more state through Chrome's supported storage model and
does not touch the live default profile. Implement partition-aware cookie
hydration as a useful, testable improvement to the existing command, but do not
market it as sufficient for Cloudflare-protected sites. Keep per-site probes and
the headed CDP/extension/Aside fallback because Cloudflare's own model makes
clearance continuously device- and behavior-dependent.
