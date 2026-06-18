# ListKit In-Tab Scrape — Handoff (Super Saiyan Browser v0.7.1)

**Public repo:** [github.com/jbellsolutions/super-saiyan-browser](https://github.com/jbellsolutions/super-saiyan-browser)

**Status:** Engine rewritten against live `next.listkit.io` People DOM + fixture PASS. User should verify in their Chrome via side panel (Detect table → Run scrape → Export CSV).

---

## Architecture

In-tab scrape only — **no Railway/API required** for the Chrome extension.

```
Logged-in Chrome tab
  → ids-engine.js + tab-scraper.js (injected by background.js)
  → Side panel: Detect table → Pagination → Run scrape
  → Client-side CSV export (Blob download)
```

Optional cloud API path lives in the private `super-browser` repo (`POST /list/ingest`).

---

## What was wrong (v0.6.x)

1. **Pagination** — ListKit pager uses numbered `<button>`s in a flex row, not `nav.pagination`. Active page is background color only (`bg-[#EBECF0]`), no `aria-current`. Old `clickNext` never advanced → all rows from page 1.
2. **Engine divergence** — Early port added homogeneity/aside filters IDS does not use; tuned on a synthetic fixture, not live DOM.
3. **Dead IDS bridge** — Invalid cross-extension messaging; always fell back to diverged port.
4. **Load-time drift** — Empty grid during transition → filter sidebar selected instead of people grid.

---

## What v0.7.x fixed

| File | Role |
|------|------|
| `extension/ids-engine.js` | Faithful `area × children²` scoring; ≥2-column preference; numbered pager + next/load-more/scroll/`?page=`; visual picker; `locateNext`; per-host saved selectors |
| `extension/tab-scraper.js` | Delegates to engine; strategy plumbing |
| `extension/background.js` | Probe-then-inject (SPA-safe); no dead IDS bridge |
| `extension/sidepanel/*` | 3-step UI: Table / Pagination / Run; local CSV; Stop |
| `extension/test-fixtures/listkit-b2b.html` | Sidebar decoy + Tailwind grid + numbered pager |

---

## Verification

```bash
npm i -D playwright && npx playwright install chromium
node extension/scripts/test-tab-scraper.mjs
# PASS — grid over sidebar, rich columns, pagination advances
```

Live ListKit (logged in, People view): grid ranks #1; ~25 rows × 10–13 fields; pages 1→2→3→4 advance with distinct people. Emails may be credit-gated (not in free grid).

---

## Run it

1. `chrome://extensions` → Load unpacked → `extension/` in this repo (or Desktop copy below).
2. Reload after updates.
3. Open ListKit **People** results (logged in).
4. Side panel → **Detect table** (cycle ◀ ▶ or **Pick manually** if wrong block) → **Run scrape** → **Export CSV**.
5. If pagination stuck: **Locate next button** once on the real pager control.

**Desktop copy:** `/Users/home/Desktop/super-browser-extension`

---

## For the next agent

1. Read `extension/ids-engine.js` (`findTables`, `clickNext`, `highlight`, `locateNext`).
2. Compare side-panel log vs fixture test if user still sees wrong data.
3. ListKit-specific: People grid, not B2B company sidebar alone.
4. IDS reference: `~/Library/Application Support/Google/Chrome/Default/Extensions/ofaokhiedipichpaobibbnahnkdoiiah/1.4.4_0/src/onload.js`
5. Private Railway ingest path: `super-browser` branch `feature/chrome-ui-extension` + [handoff there](https://github.com/jbellsolutions/super-browser/blob/feature/chrome-ui-extension/docs/listkit-scrape-handoff.md).

---

## Out of scope

- Skool (user uses Apify)
- Paid ListKit email unlock in scrape (preview fields only)
