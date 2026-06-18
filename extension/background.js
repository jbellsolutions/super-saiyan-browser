chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});

// Ensure the engine + scraper are present in the page. Probes first so it survives SPA route
// changes and full reloads without re-injecting on every call.
async function ensureTabScraper(tabId) {
  const probe = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => !!(window.__superBrowserIdsEngine && window.__superBrowserTabScraper),
  });
  if (probe[0]?.result) return;
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["ids-engine.js", "tab-scraper.js"],
  });
}

async function runTabAction(tabId, action, args = {}) {
  await ensureTabScraper(tabId);
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: async (scrapeAction, scrapeArgs) => {
      const scraper = window.__superBrowserTabScraper;
      if (!scraper || typeof scraper[scrapeAction] !== "function") {
        throw new Error(`Unknown tab scrape action: ${scrapeAction}`);
      }
      return await scraper[scrapeAction](scrapeArgs || {});
    },
    args: [action, args],
  });
  return results[0]?.result;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "getTabContext") {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs[0];
      if (!tab?.id) {
        sendResponse({ error: "No active tab" });
        return;
      }
      sendResponse({
        tabId: tab.id,
        url: tab.url || "",
        title: tab.title || "",
        hostname: tab.url ? new URL(tab.url).hostname : "",
      });
    });
    return true;
  }

  if (message.type === "syncCookies") {
    const query = message.url ? { url: message.url } : { domain: message.domain };
    chrome.cookies.getAll(query, (cookies) => {
      sendResponse({
        domain: message.domain,
        cookies: cookies.map((c) => ({
          name: c.name,
          value: c.value,
          domain: c.domain,
          path: c.path,
          secure: c.secure,
          httpOnly: c.httpOnly,
          sameSite: c.sameSite,
          expirationDate: c.expirationDate,
        })),
      });
    });
    return true;
  }

  if (message.type === "tabScrapeAction") {
    const { tabId, action, args } = message;
    runTabAction(tabId, action, args || {})
      .then((result) => sendResponse(result))
      .catch((error) => sendResponse({ error: error.message }));
    return true;
  }
});
