# Bright Data Web Unlocker

**Status in Super Saiyan Browser:** `evaluating` (live adapter — REST unlock)  
**Docs:** https://docs.brightdata.com/api-reference/rest-api/unlocker/unlock-website  
**Signup:** https://brightdata.com/

## Verified in Super Saiyan Browser

- Adapter: `POST https://api.brightdata.com/request` with unlocker zone → markdown artifact.
- Env: `BRIGHTDATA_API_KEY` (zone names auto-discovered via `super-browser brightdata-discover`).
- Flags: `supports_anti_bot`, `supports_unlocked_http`, `supports_captcha`.

## Use alone when

- Read-only anti-bot URL fetch is enough.
- You need markdown/HTML without browser interaction.
- Cost-sensitive unlock before Browser Use or Hyperbrowser.

## Escalation rank

**1** — alongside rank-1 browser providers for anti-bot read tasks.

## Last verified

2026-06-17 — adapter contract tests; live tests user-gated.
