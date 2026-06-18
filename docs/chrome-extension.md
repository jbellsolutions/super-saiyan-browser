# Chrome extension — in-tab list scraping

Super Saiyan Browser scrapes paginated lists **in your logged-in Chrome tab** and exports CSV locally. No API server, Railway account, or token is required.

## Install (load unpacked)

1. Clone this repo (or download and unzip).
2. Open `chrome://extensions`.
3. Enable **Developer mode**.
4. Click **Load unpacked** and select the `extension/` folder in this repo.
5. After code updates, click **Reload** on the Super Saiyan Browser card.

## Scrape a list

1. Log into the target site (directory, community members page, Facebook group, etc.) and open the **results** view.
2. Click the Super Saiyan Browser icon to open the side panel.
3. **1 · Table** — **Detect table**. The grid is outlined in blue. Use **◀ / ▶** to cycle candidates or **Pick manually** if needed. Your choice is saved per site.
4. **2 · Pagination** — leave **Strategy: Auto**, or **Locate next button** and click the control once if Auto misses it.
5. **3 · Run** — set **Max pages**, click **Run scrape**. Watch the log for `Page N: X rows (Y new)`.
6. **Export CSV** when finished.

## Works well on

| Site type | Notes |
| --- | --- |
| Directory / search results | Tables and card grids |
| Skool communities | Member lists |
| Facebook groups | Infinite scroll and paginated members |
| Generic SaaS tables | Numbered pagers, next buttons, load-more |

## Pagination (Auto order)

1. Saved next selector (from **Locate next button**)
2. Numbered pager (`1 2 3 …`)
3. Next / › / » buttons
4. Load more / show more
5. Infinite scroll
6. `?page=` URL increment

Stops when the list ends, next is disabled, no new deduped rows appear, or content does not change after advancing.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Wrong block selected | Cycle candidates or **Pick manually** |
| Stuck on page 1 | **Locate next button** and click the real control |
| Login wall in log | Log in on the tab first, then re-run |
| Empty export | Run **Detect table** before **Run scrape** |

## Advanced: cloud API (optional)

The client extension does not need a backend. Self-hosted cloud sync is advanced and optional — deploy your own API and keep credentials in your host env vars, never in git.
