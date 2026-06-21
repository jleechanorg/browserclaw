# /browserclaw

Reverse-engineer browser APIs from an interactive browsing session.

## Usage

```bash
/browserclaw https://www.linkedin.com/feed/
/browserclaw inspect LinkedIn messaging APIs
/browserclaw learn https://example.com --output-dir ./learned
```

## Commands

| Command | Description |
|---------|-------------|
| `learn` | Capture + infer + generate + save SKILL.md (full pipeline, recommended) |
| `reverse` | Capture + infer + generate |
| `capture` | HAR capture only |
| `capture-ws` | WebSocket frame capture |
| `infer` | Parse HAR → Endpoint Catalog |
| `generate` | Generate client code from catalog |
| `generate-ws` | Generate WebSocket replay scripts |
| `mockset` | `learn` in dry-run mode + write `mockset.json` for token-free replay |

## Dry-run / mockset mode (token-safe reverse engineering)

Use this when you want to capture HTTP traffic from an authenticated site
**without ever writing tokens back to disk**. Two ways:

```bash
# Inline dry-run (uses learn pipeline, dry-run client + curl_replay.sh)
browserclaw learn --url https://api.slack.com/apps/<app_id> \
  --output-dir ./out --manual --dry-run

# Full mockset (writes mockset.json — replay contract for later sessions)
browserclaw mockset --url https://api.slack.com/apps/<app_id> \
  --output-dir ./out --manual
```

In dry-run mode:
- `generated_client.py` includes a `MockSetTokenMissingError` guard. The client
  reads tokens from `$MOCKSET_TOKENS` (JSON) or
  `~/.config/browserclaw/mockset-tokens.json` at call time.
- `curl_replay.sh` references `$MOCKSET_TOKENS_AUTHORIZATION` and
  `$MOCKSET_TOKENS_COOKIE_*` shell variables. The script `exit 2`s if any
  required token is unset.
- Tokens are **never embedded** in any generated file.

**Why this exists**: secrets-from-`.env` is a recurring footgun (see the May
2026 prod Slack invalid_auth incident in
`~/roadmap/nextsteps-2026-05-11-hermes-slack-env-chain.md`). The mockset
mode is the default-safe way to reverse-engineer any authenticated API.

## Behavior

1. Resolve the target URL or site from the argument.
2. Ask whether the user wants manual capture or scripted capture.
3. Run `browserclaw learn` with an output directory under `generated/<site>/`.
4. Summarize the emitted:
   - `capture.har`
   - `catalog.json`
   - `generated_client.py`
   - `mcp_tools.json`
   - `SKILL.md` ← the saved skill for this site
5. Do not claim auth bypass, CAPTCHA bypass, or stealth support.

## Examples

```bash
# Learn a site and save its skill (recommended)
browserclaw learn --url https://www.linkedin.com/feed/ --output-dir generated/linkedin --manual

# With LLM enrichment
browserclaw learn --url https://app.example.com --output-dir generated/example --goal "Open invoices and capture list/detail APIs" --provider anthropic --model claude-sonnet-4-20250514

# Reverse (no skill output)
browserclaw reverse --url https://www.linkedin.com/feed/ --output-dir generated/linkedin --manual

# Generate skill from existing catalog
browserclaw generate --catalog /tmp/catalog.json --output-dir ./out --save-skill
```

