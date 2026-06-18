const els = {
  modeBadge: document.getElementById("modeBadge"),
  pageUrl: document.getElementById("pageUrl"),
  detectBtn: document.getElementById("detectBtn"),
  prevBtn: document.getElementById("prevBtn"),
  nextCandBtn: document.getElementById("nextCandBtn"),
  pickBtn: document.getElementById("pickBtn"),
  tableInfo: document.getElementById("tableInfo"),
  strategySelect: document.getElementById("strategySelect"),
  speedSelect: document.getElementById("speedSelect"),
  locateBtn: document.getElementById("locateBtn"),
  paginationInfo: document.getElementById("paginationInfo"),
  maxPages: document.getElementById("maxPages"),
  runBtn: document.getElementById("runBtn"),
  stopBtn: document.getElementById("stopBtn"),
  exportBtn: document.getElementById("exportBtn"),
  progressText: document.getElementById("progressText"),
  eventLog: document.getElementById("eventLog"),
  savedInfo: document.getElementById("savedInfo"),
  clearBtn: document.getElementById("clearBtn"),
};

let tabContext = { tabId: null, url: "", hostname: "" };
let stopRequested = false;
let lastRows = [];
let lastHeaders = null;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function logEvent(line) {
  els.eventLog.textContent += `${line}\n`;
  els.eventLog.scrollTop = els.eventLog.scrollHeight;
}
function setProgress(text) {
  els.progressText.textContent = text;
}

// Human-like inter-page delay with random jitter so pagination doesn't look robotic.
function pageDelayMs() {
  const base = { fast: 1200, normal: 3000, slow: 6000 }[els.speedSelect.value] ?? 3000;
  return Math.round(base + base * 0.6 * Math.random());
}

async function refreshTabContext() {
  const ctx = await chrome.runtime.sendMessage({ type: "getTabContext" });
  if (ctx && !ctx.error) {
    tabContext = ctx;
    els.pageUrl.textContent = ctx.url || "—";
  }
  return tabContext;
}

async function tabAction(action, args = {}) {
  if (!tabContext.tabId) await refreshTabContext();
  const result = await chrome.runtime.sendMessage({
    type: "tabScrapeAction",
    tabId: tabContext.tabId,
    action,
    args,
  });
  if (result && result.error) throw new Error(result.error);
  return result;
}

// ---------- table picker ----------
function showTableInfo(info) {
  if (!info) {
    els.tableInfo.textContent = "No repeating table found on this page.";
    els.tableInfo.className = "muted warn";
    return;
  }
  els.tableInfo.className = "muted ok";
  els.tableInfo.textContent = `Candidate ${info.index + 1}/${info.total} · ${info.rowCount} rows × ${info.columnCount} cols · "${info.sample}"`;
}

async function detectTable() {
  try {
    await refreshTabContext();
    showTableInfo(await tabAction("highlight", { index: 0 }));
  } catch (e) {
    logEvent(`Detect failed: ${e.message}`);
  }
}
async function cycleTable(step) {
  try {
    showTableInfo(await tabAction("cycleTable", { step }));
  } catch (e) {
    logEvent(`Cycle failed: ${e.message}`);
  }
}
async function pickTable() {
  els.tableInfo.className = "muted warn";
  els.tableInfo.textContent = "Click the correct table in the page…";
  try {
    const info = await tabAction("pickTable");
    showTableInfo(info);
    logEvent(`Table locked: ${info.selector}`);
    updateSaved();
  } catch (e) {
    logEvent(`Pick failed: ${e.message}`);
  }
}
async function locateNext() {
  els.paginationInfo.className = "muted warn";
  els.paginationInfo.textContent = "Click the Next / pagination control in the page…";
  try {
    const res = await tabAction("locateNext");
    els.paginationInfo.className = "muted ok";
    els.paginationInfo.textContent = `Next button saved: ${res.selector}`;
    updateSaved();
  } catch (e) {
    logEvent(`Locate failed: ${e.message}`);
  }
}

// ---------- dedupe (on full row text — stable) ----------
function rowFingerprint(rawText) {
  const s = rawText.toLowerCase().replace(/\s+/g, " ").trim();
  let hash = 0;
  for (let i = 0; i < s.length; i += 1) {
    hash = (hash << 5) - hash + s.charCodeAt(i);
    hash |= 0;
  }
  return String(hash);
}
function buildPageRows(domRows, pageIndex, pageUrl, fingerprints) {
  const pageRows = [];
  for (const raw of domRows) {
    const rawText = String(raw.raw_text || "").trim();
    if (!rawText) continue;
    const fp = rowFingerprint(rawText);
    const duplicate = fingerprints.has(fp);
    fingerprints.add(fp);
    pageRows.push({
      page_index: pageIndex,
      raw_text: rawText,
      cells: raw.cells || [],
      extra: raw.extra || {},
      source_url: pageUrl,
      duplicate,
    });
  }
  return pageRows;
}

// ---------- scrape loop ----------
async function runScrape() {
  await refreshTabContext();
  if (!tabContext.tabId || !tabContext.url?.startsWith("http")) {
    logEvent("Open the list page you want to scrape in the active tab first.");
    return;
  }
  stopRequested = false;
  lastRows = [];
  lastHeaders = null;
  els.runBtn.disabled = true;
  els.stopBtn.disabled = false;
  els.exportBtn.hidden = true;
  els.eventLog.textContent = "";

  const maxPages = Number(els.maxPages.value || 50);
  const strategy = els.strategySelect.value;
  const allRows = [];
  const fingerprints = new Set();
  let stopReason = null;
  let status = "complete";

  logEvent(`Scraping in your logged-in tab · strategy: ${strategy} · speed: ${els.speedSelect.value}`);

  try {
    for (let pageIndex = 1; pageIndex <= maxPages; pageIndex += 1) {
      if (stopRequested) {
        stopReason = "stopped";
        break;
      }

      const block = await tabAction("detectBlock");
      if (block) {
        status = "blocked";
        stopReason = `anti_bot:${block}`;
        logEvent(`⚠ Possible bot challenge detected (${block}). Paused. Solve it in the tab, switch Speed to Slow, then re-run.`);
        break;
      }
      const loginSignal = await tabAction("detectLoginWall");
      if (loginSignal) {
        status = "blocked";
        stopReason = loginSignal;
        logEvent(`Stopped: ${loginSignal}`);
        break;
      }

      const wait = await tabAction("waitForRows", { timeoutMs: pageIndex === 1 ? 9000 : 6000 });
      const domRows = wait.rows || [];

      if (pageIndex === 1) {
        if (wait.headers && wait.headers.length) lastHeaders = wait.headers;
        if (!domRows.length) {
          status = "blocked";
          stopReason = "no_table";
          logEvent(`No table detected (waited ${(wait.waitedMs / 1000).toFixed(1)}s, ${wait.candidateCount} candidates).`);
          logEvent("Try 'Detect table' or 'Pick manually', then Run again.");
          break;
        }
        const cols = domRows[0].cells.length;
        logEvent(`Table ready: ${domRows.length} rows × ${cols} columns${lastHeaders ? " (headers detected)" : ""}.`);
      }

      const pageInfo = await tabAction("pageInfo");
      const pageRows = buildPageRows(domRows, pageIndex, pageInfo.url, fingerprints);
      allRows.push(...pageRows);
      const newRows = pageRows.filter((r) => !r.duplicate).length;
      const uniqueTotal = allRows.filter((r) => !r.duplicate).length;
      logEvent(`Page ${pageIndex}: ${pageRows.length} rows (${newRows} new) · ${uniqueTotal} unique total`);
      setProgress(`Running · page ${pageIndex} · ${uniqueTotal} unique rows`);

      if (pageIndex > 1 && newRows === 0) {
        stopReason = "no_new_rows";
        break;
      }
      if (pageIndex >= maxPages) {
        stopReason = "max_pages";
        break;
      }

      // Human-like dwell before turning the page (jittered), then advance.
      await sleep(pageDelayMs());
      if (stopRequested) {
        stopReason = "stopped";
        break;
      }

      const beforeSig = await tabAction("tableSignature");
      const advanced = await tabAction("advancePage", { mode: strategy });
      logEvent(`Pagination: ${advanced}`);
      if (advanced === "disabled") {
        stopReason = "disabled_next";
        break;
      }
      if (advanced === "end") {
        stopReason = "end_of_list";
        break;
      }
      if (advanced !== "advanced") {
        stopReason = "no_next_control";
        break;
      }

      await sleep(400);
      const change = await tabAction("waitForTableChange", { previousSignature: beforeSig, timeoutMs: 9000 });
      if (!change.changed) {
        logEvent("Table content did not change after advancing — treating as end of list.");
        stopReason = "no_change";
        break;
      }
    }
  } catch (e) {
    logEvent(`Run error: ${e.message}`);
    status = "error";
  }

  lastRows = allRows;
  const uniqueRows = allRows.filter((r) => !r.duplicate);
  setProgress(`${status} · ${uniqueRows.length} unique rows · stop: ${stopReason || "—"}`);
  logEvent(`Done. ${uniqueRows.length} unique rows across ${new Set(allRows.map((r) => r.page_index)).size} page(s).`);
  if (uniqueRows.length) els.exportBtn.hidden = false;
  els.runBtn.disabled = false;
  els.stopBtn.disabled = true;
}

// ---------- CSV export (client-side, positional columns + detected headers) ----------
function csvCell(value) {
  const s = value == null ? "" : String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
function toCSV(rows, headers) {
  const unique = rows.filter((r) => !r.duplicate);
  const maxCells = unique.reduce((m, r) => Math.max(m, r.cells.length), 0);
  const colNames = [];
  for (let i = 0; i < maxCells; i += 1) {
    const h = headers && headers[i] ? String(headers[i]).trim() : "";
    colNames.push(h || `Column ${i + 1}`);
  }
  const extraKeys = [];
  for (const r of unique) for (const k of Object.keys(r.extra || {})) if (!extraKeys.includes(k)) extraKeys.push(k);

  const header = [...colNames, ...extraKeys, "page", "source_url"];
  const lines = [header.map(csvCell).join(",")];
  for (const r of unique) {
    const cells = [];
    for (let i = 0; i < maxCells; i += 1) cells.push(r.cells[i] ?? "");
    for (const k of extraKeys) cells.push(r.extra?.[k] ?? "");
    cells.push(r.page_index, r.source_url ?? "");
    lines.push(cells.map(csvCell).join(","));
  }
  return lines.join("\r\n");
}
function exportCSV() {
  if (!lastRows.length) return;
  const csv = toCSV(lastRows, lastHeaders);
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const host = (tabContext.hostname || "list").replace(/[^a-z0-9]+/gi, "-");
  a.href = url;
  a.download = `${host}-scrape.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------- saved selectors ----------
async function updateSaved() {
  try {
    const saved = await tabAction("savedSelectors");
    const parts = [];
    if (saved?.table) parts.push("table ✓");
    if (saved?.next) parts.push("next ✓");
    els.savedInfo.textContent = parts.length ? `Saved for this site: ${parts.join(" · ")}` : "";
  } catch (_) {
    els.savedInfo.textContent = "";
  }
}
async function clearSaved() {
  try {
    await tabAction("clearSaved");
    await tabAction("clearHighlight").catch(() => {});
    els.savedInfo.textContent = "";
    els.paginationInfo.className = "muted";
    els.paginationInfo.textContent = "Cleared. Auto-detection will be used.";
    logEvent("Cleared saved table + next-button selectors for this site.");
  } catch (e) {
    logEvent(`Clear failed: ${e.message}`);
  }
}

// ---------- wire up ----------
els.detectBtn.addEventListener("click", detectTable);
els.prevBtn.addEventListener("click", () => cycleTable(-1));
els.nextCandBtn.addEventListener("click", () => cycleTable(1));
els.pickBtn.addEventListener("click", pickTable);
els.locateBtn.addEventListener("click", locateNext);
els.runBtn.addEventListener("click", runScrape);
els.stopBtn.addEventListener("click", () => {
  stopRequested = true;
  logEvent("Stop requested…");
});
els.exportBtn.addEventListener("click", exportCSV);
els.clearBtn.addEventListener("click", clearSaved);

refreshTabContext().then(updateSaved);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refreshTabContext().then(updateSaved);
});
