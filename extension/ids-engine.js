/**
 * Super Saiyan Browser table engine.
 * Faithful to Instant Data Scraper's core (findTables = area × children², path-based
 * getTableData), plus robust pagination (numbered pagers, generic next, load-more, infinite
 * scroll), a visual table picker, and one-click "locate next button" capture.
 *
 * Diverging additions the previous port made (homogeneity hard-filter, aside ×0.05 penalty,
 * nav-only next detection) are removed — they suppressed real React/Tailwind grids and missed
 * pagers that live outside <nav>. Verified against live next.listkit.io People results.
 */
window.__superBrowserIdsEngine = (() => {
  const SKIP = new Set(["script", "img", "meta", "style", "svg", "path", "noscript", "link"]);
  const STYLE_ID = "__super-browser-style";

  let tables = [];
  let tableIndex = 0;
  let pickCleanup = null;

  // ---------- helpers ----------
  function cssEscape(value) {
    return String(value).replace(/[!"#$%&'()*+,./:;<=>?@[\\\]^`{|}~]/g, "\\$&").trim();
  }
  function classNames(el) {
    const raw = typeof el.className === "string" ? el.className : el.getAttribute("class") || "";
    return raw.trim().split(/\s+/).filter(Boolean);
  }
  function nodeText(el) {
    return (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
  }
  function directText(el) {
    let out = "";
    for (const node of el.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) out += node.textContent;
    }
    return out.replace(/\s+/g, " ").trim();
  }
  function isVisible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return false;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") return false;
    return el.offsetParent !== null || style.position === "fixed";
  }
  function hostKey(name) {
    return `${name}:${location.hostname}`;
  }

  // ---------- table detection (faithful IDS) ----------
  function findRepeatingChildren(parent) {
    const children = Array.from(parent.children).filter((child) => {
      if (SKIP.has(child.nodeName.toLowerCase())) return false;
      return nodeText(child).length > 0;
    });

    const comboCount = {};
    const classCount = {};
    for (const child of children) {
      const combo = classNames(child).sort().join(" ");
      comboCount[combo] = (comboCount[combo] || 0) + 1;
      for (const cls of classNames(child)) classCount[cls] = (classCount[cls] || 0) + 1;
    }

    const threshold = children.length / 2 - 2;
    let goodCombos = Object.keys(comboCount).filter((c) => comboCount[c] >= threshold);
    if (!goodCombos.length) {
      goodCombos = Object.keys(classCount).filter((cls) => classCount[cls] >= threshold);
    }

    if (!goodCombos.length || (goodCombos.length === 1 && goodCombos[0] === "")) {
      return { children, goodClasses: [] };
    }

    const filtered = children.filter((child) =>
      goodCombos.some((combo) => {
        const tokens = combo.split(" ").filter(Boolean);
        if (!tokens.length) return true;
        return tokens.every((token) => child.classList.contains(token));
      }),
    );
    return { children: filtered.length ? filtered : children, goodClasses: goodCombos };
  }

  function buildSelector(el) {
    const parts = [];
    let node = el;
    while (node && node !== document.documentElement && node.tagName !== "BODY") {
      let part = node.tagName.toLowerCase();
      if (node.id && node.id.trim() && !/\d/.test(node.id)) {
        part += `#${cssEscape(node.id)}`;
        parts.unshift(part);
        break;
      }
      const classes = classNames(node).filter((c) => !c.startsWith("__super-browser"));
      if (classes.length) part += classes.map((c) => `.${cssEscape(c)}`).join("");
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(">");
  }

  function resolveSelector(selector) {
    if (!selector) return null;
    let current = selector;
    while (current) {
      try {
        const el = document.querySelector(current);
        if (el) return el;
      } catch (_) {
        return null;
      }
      const parts = current.split(">");
      if (parts.length <= 1) return null;
      parts.shift();
      current = parts.join(">");
    }
    return null;
  }

  function homogeneityScore(children) {
    if (!children.length) return 0;
    const patterns = {};
    for (const child of children) {
      const key = `${child.tagName.toLowerCase()}:${classNames(child).sort().join(".")}`;
      patterns[key] = (patterns[key] || 0) + 1;
    }
    return Math.max(...Object.values(patterns)) / children.length;
  }

  function findTables() {
    const bodyArea = document.body.clientWidth * document.body.clientHeight;
    const minArea = bodyArea * 0.02; // IDS uses 2% of body
    const found = [];

    for (const el of document.body.querySelectorAll("*")) {
      if (el.closest(`#${STYLE_ID}`)) continue;
      const area = el.offsetWidth * el.offsetHeight;
      if (Number.isNaN(area) || area < minArea) continue;
      const { children, goodClasses } = findRepeatingChildren(el);
      if (children.length < 3) continue;
      // Faithful IDS score: bigger area and more repeating rows win. No homogeneity/aside bias.
      const score = area * children.length * children.length;
      found.push({
        table: el,
        children,
        goodClasses,
        score,
        homogeneity: homogeneityScore(children),
        selector: buildSelector(el),
      });
    }

    tables = found.sort((a, b) => b.score - a.score).slice(0, 5);

    // A previously chosen table for this host wins if it still resolves.
    const saved = localStorage.getItem(hostKey("sbTableSelector"));
    const savedIdx = saved ? tables.findIndex((t) => t.selector === saved) : -1;
    if (savedIdx >= 0) {
      tableIndex = savedIdx;
    } else {
      // Otherwise prefer the highest-scoring candidate that looks like a real data
      // table (≥2 columns), so a 1-column filter sidebar / nav list isn't chosen while
      // the main grid is momentarily empty during a page load.
      const dataIdx = tables.findIndex(
        (t) => t.children[0] && Object.keys(extractRowObject(t.children[0])).length >= 2,
      );
      tableIndex = dataIdx >= 0 ? dataIdx : 0;
    }
    return tables;
  }

  function currentTable() {
    if (!tables.length) findTables();
    return tables[tableIndex] || null;
  }

  // ---------- extraction (faithful IDS path-based) ----------
  function extractRowObject(rowEl) {
    const fields = {};
    function setField(value, path, suffix) {
      if (!value) return;
      const base = path + (suffix ? ` ${suffix}` : "");
      let key = base;
      let count = 0;
      for (const existing of Object.keys(fields)) if (existing.startsWith(base)) count += 1;
      if (count > 0) key = `${base} ${count + 1}`;
      fields[key] = value;
    }
    function walk(node, path) {
      if (!node || node.nodeType !== 1) return;
      if (SKIP.has(node.nodeName.toLowerCase())) return;
      const segment = `/${node.nodeName.toLowerCase()}${classNames(node)
        .map((c) => `.${cssEscape(c)}`)
        .join("")}`;
      const nextPath = path + segment;
      setField(directText(node), nextPath);
      if (node.href) setField(node.href, nextPath, "href");
      if (node.src) setField(node.src, nextPath, "src");
      for (const child of node.children) walk(child, nextPath);
    }
    walk(rowEl, "");
    return fields;
  }

  function getTableData(selector) {
    let tableEl = null;
    let children = [];
    if (selector) {
      tableEl = resolveSelector(selector);
      if (tableEl) children = findRepeatingChildren(tableEl).children;
    } else {
      const current = currentTable();
      if (current) {
        tableEl = current.table;
        children = current.children;
        // Only pin a genuine multi-column table. A 1-column filter sidebar / nav list seen
        // while the main grid is still loading must not get locked in for the rest of the run.
        const cols = children[0] ? Object.keys(extractRowObject(children[0])).length : 0;
        if (cols >= 2) window.__superBrowserSelectedTable = { selector: current.selector, tableIndex };
      }
    }
    if (!tableEl || !children.length) return [];

    const rows = [];
    for (const child of children) {
      const fields = extractRowObject(child);
      if (!Object.keys(fields).length) continue;
      rows.push({ source: "ids_engine", raw_text: nodeText(child), fields });
    }
    return rows;
  }

  function tableSignature() {
    findTables(); // re-scan: SPA grids swap children in place, cached rows go stale
    const current = currentTable();
    if (!current) return location.href;
    const sample = current.children
      .slice(0, 6)
      .map((c) => nodeText(c).slice(0, 120))
      .join("|");
    return `${location.href}::${current.selector}::${current.children.length}::${sample}`;
  }

  // ---------- click helper ----------
  function dispatchMouseClick(el) {
    const rect = el.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    for (const type of ["mousedown", "mouseup", "click"]) {
      el.dispatchEvent(
        new MouseEvent(type, { view: window, bubbles: true, cancelable: true, clientX: x, clientY: y }),
      );
    }
  }
  function isDisabled(el) {
    return (
      el.disabled ||
      el.getAttribute("aria-disabled") === "true" ||
      getComputedStyle(el).pointerEvents === "none" ||
      parseFloat(getComputedStyle(el).opacity || "1") < 0.4
    );
  }

  // ---------- pagination strategies ----------
  function clickables() {
    return Array.from(document.querySelectorAll('button, a, [role="button"], li[role="button"]'));
  }

  // 1) numbered pager: find a parent holding ≥2 sibling integer buttons, detect the active one,
  //    click active+1. Handles ListKit (active marked only by background color).
  function numberedPagerAdvance() {
    const intEls = clickables().filter((b) => /^\d{1,4}$/.test(nodeText(b)) && isVisible(b));
    if (intEls.length < 2) return null;

    const groups = new Map();
    for (const el of intEls) {
      const p = el.parentElement;
      if (!p) continue;
      if (!groups.has(p)) groups.set(p, []);
      groups.get(p).push(el);
    }
    let pager = null;
    for (const [, btns] of groups) {
      if (btns.length < 2) continue;
      if (!pager || btns.length > pager.length) pager = btns;
    }
    if (!pager) return null;
    pager.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);

    const active = detectActivePage(pager);
    if (!active) return null;
    const n = parseInt(nodeText(active), 10);
    const next = pager.find((b) => parseInt(nodeText(b), 10) === n + 1 && !isDisabled(b));
    if (next) {
      next.scrollIntoView({ block: "center", inline: "center" });
      dispatchMouseClick(next);
      return "advanced";
    }
    return null; // no higher page visible — let caller try arrow / declare end
  }

  function detectActivePage(btns) {
    // aria-current
    let a = btns.find((b) => b.getAttribute("aria-current"));
    if (a) return a;
    // class hint
    a = btns.find((b) => /(^|[\s_-])(active|selected|current)([\s_-]|$)/i.test(b.className || ""));
    if (a) return a;
    // odd-one-out background color (ListKit: bg-[#EBECF0] on the active page only)
    const bgs = btns.map((b) => getComputedStyle(b).backgroundColor);
    const counts = {};
    bgs.forEach((c) => (counts[c] = (counts[c] || 0) + 1));
    let idx = bgs.findIndex(
      (c) => counts[c] === 1 && c !== "rgba(0, 0, 0, 0)" && c !== "transparent",
    );
    if (idx >= 0) return btns[idx];
    // odd-one-out font weight
    const fws = btns.map((b) => getComputedStyle(b).fontWeight);
    const fwc = {};
    fws.forEach((c) => (fwc[c] = (fwc[c] || 0) + 1));
    idx = fws.findIndex((c) => fwc[c] === 1 && parseInt(c, 10) >= 600);
    if (idx >= 0) return btns[idx];
    return null;
  }

  // 2) generic next control anywhere (text/aria), not just inside <nav>
  function genericNextAdvance() {
    const NEXT = /(^|\s)(next|older|more results|→|›|»|forward)(\s|$)/i;
    const candidates = clickables().filter((b) => {
      if (!isVisible(b)) return false;
      const label = `${b.getAttribute("aria-label") || ""} ${b.getAttribute("title") || ""} ${nodeText(b)}`.trim();
      const cls = b.className || "";
      return NEXT.test(label) || /\bnext\b/i.test(cls) || /pagination-next|next-page|page-next/i.test(cls);
    });
    for (const btn of candidates) {
      if (isDisabled(btn)) return "disabled";
      btn.scrollIntoView({ block: "center" });
      dispatchMouseClick(btn);
      return "advanced";
    }
    return null;
  }

  // 3) load-more button
  function loadMoreAdvance() {
    const MORE = /(load|show|view|see)\s+more|more results|show more/i;
    const btn = clickables().find((b) => isVisible(b) && MORE.test(nodeText(b)) && !isDisabled(b));
    if (btn) {
      btn.scrollIntoView({ block: "center" });
      dispatchMouseClick(btn);
      return "advanced";
    }
    return null;
  }

  // 4) ?page= URL param increment
  function pageParamAdvance() {
    const url = new URL(location.href);
    for (const key of ["page", "p", "pg", "pageNumber", "offset"]) {
      if (url.searchParams.has(key)) {
        const val = parseInt(url.searchParams.get(key), 10);
        if (!Number.isNaN(val)) {
          url.searchParams.set(key, String(key === "offset" ? val + 25 : val + 1));
          location.href = url.toString();
          return "advanced";
        }
      }
    }
    return null;
  }

  function clickNext(selector, strategy = "auto") {
    const saved = selector || localStorage.getItem(hostKey("nextSelector"));
    if (saved) {
      const el = resolveSelector(saved);
      if (el && isVisible(el)) {
        if (isDisabled(el)) return "disabled";
        el.scrollIntoView({ block: "center", inline: "center" });
        dispatchMouseClick(el);
        return "advanced";
      }
    }

    if (strategy === "infinite_scroll") return "end"; // handled via scrollTableDown
    if (strategy === "load_more") return loadMoreAdvance() || "end";

    const order =
      strategy === "next_button"
        ? [genericNextAdvance, numberedPagerAdvance]
        : [numberedPagerAdvance, genericNextAdvance, loadMoreAdvance, pageParamAdvance];

    let sawDisabled = false;
    for (const fn of order) {
      const result = fn();
      if (result === "advanced") return "advanced";
      if (result === "disabled") sawDisabled = true;
    }
    return sawDisabled ? "disabled" : "end";
  }

  // ---------- infinite scroll ----------
  async function scrollTableDown(selector) {
    let scrollEl = selector ? resolveSelector(selector) : null;
    if (!scrollEl) scrollEl = currentTable()?.table || null;
    while (scrollEl && scrollEl.scrollHeight <= scrollEl.clientHeight + 5) {
      scrollEl = scrollEl.parentElement;
      if (scrollEl === document.body || scrollEl === document.documentElement) break;
    }
    const target = scrollEl && scrollEl !== document.body ? scrollEl : document.scrollingElement;
    if (!target) return "end";

    const beforeCount = (currentTable()?.children || []).length;
    const beforeTop = target.scrollTop;
    target.scrollTop += Math.max(400, target.clientHeight * 0.9);
    window.scrollTo(0, document.body.scrollHeight);
    await new Promise((r) => setTimeout(r, 1200));
    findTables();
    const afterCount = (currentTable()?.children || []).length;
    if (afterCount > beforeCount || target.scrollTop !== beforeTop) return "advanced";
    return "end";
  }

  // ---------- visual picker + highlight ----------
  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      .__super-browser-table{outline:3px solid #2563eb !important;outline-offset:-1px;background:rgba(37,99,235,0.04) !important;}
      .__super-browser-row{outline:1px dashed rgba(37,99,235,0.6) !important;}
      .__super-browser-hover{outline:2px solid #f59e0b !important;cursor:crosshair !important;}
      .__super-browser-next{outline:3px solid #16a34a !important;outline-offset:2px;}
    `;
    (document.head || document.documentElement).appendChild(style);
  }
  function clearHighlight() {
    document
      .querySelectorAll(".__super-browser-table, .__super-browser-row, .__super-browser-hover, .__super-browser-next")
      .forEach((el) =>
        el.classList.remove(
          "__super-browser-table",
          "__super-browser-row",
          "__super-browser-hover",
          "__super-browser-next",
        ),
      );
  }
  function highlight(index) {
    ensureStyles();
    clearHighlight();
    if (!tables.length) findTables();
    if (index != null) tableIndex = ((index % tables.length) + tables.length) % tables.length;
    const current = tables[tableIndex];
    if (!current) return null;
    current.table.classList.add("__super-browser-table");
    current.children.forEach((c) => c.classList.add("__super-browser-row"));
    current.table.scrollIntoView({ block: "center", behavior: "smooth" });
    window.__superBrowserSelectedTable = { selector: current.selector, tableIndex };
    return tableInfo(current);
  }
  function tableInfo(t) {
    return {
      selector: t.selector,
      rowCount: t.children.length,
      columnCount: Object.keys(extractRowObject(t.children[0] || document.createElement("div"))).length,
      homogeneity: Number(t.homogeneity.toFixed(2)),
      sample: nodeText(t.children[0] || t.table).slice(0, 120),
      index: tableIndex,
      total: tables.length,
    };
  }
  function cycleTable(step) {
    if (!tables.length) findTables();
    return highlight(tableIndex + (step || 1));
  }
  function confirmTable() {
    const current = tables[tableIndex];
    if (!current) return null;
    localStorage.setItem(hostKey("sbTableSelector"), current.selector);
    clearHighlight();
    return { selector: current.selector };
  }

  function stopPick() {
    if (pickCleanup) {
      pickCleanup();
      pickCleanup = null;
    }
  }

  // one-shot: user clicks the correct table block; we walk up to a repeating-children container
  function pickByClick() {
    ensureStyles();
    stopPick();
    return new Promise((resolve) => {
      function findContainer(el) {
        let node = el;
        for (let i = 0; node && i < 12; i += 1) {
          const rep = findRepeatingChildren(node);
          if (rep.children.length >= 3) return node;
          node = node.parentElement;
        }
        return el;
      }
      const onMove = (e) => {
        document.querySelectorAll(".__super-browser-hover").forEach((el) => el.classList.remove("__super-browser-hover"));
        const c = findContainer(e.target);
        if (c) c.classList.add("__super-browser-hover");
      };
      const onClick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        const container = findContainer(e.target);
        const selector = buildSelector(container);
        localStorage.setItem(hostKey("sbTableSelector"), selector);
        cleanup();
        findTables();
        let idx = tables.findIndex((t) => t.selector === selector);
        if (idx < 0) {
          const kids = findRepeatingChildren(container).children;
          tables.unshift({
            table: container,
            children: kids,
            goodClasses: [],
            score: 0,
            homogeneity: homogeneityScore(kids),
            selector,
          });
          idx = 0;
        }
        tableIndex = idx;
        highlight(tableIndex);
        resolve({ selector, ...tableInfo(currentTable()) });
      };
      function cleanup() {
        document.removeEventListener("mousemove", onMove, true);
        document.removeEventListener("click", onClick, true);
        document.querySelectorAll(".__super-browser-hover").forEach((el) => el.classList.remove("__super-browser-hover"));
        pickCleanup = null;
      }
      document.addEventListener("mousemove", onMove, true);
      document.addEventListener("click", onClick, true);
      pickCleanup = cleanup;
    });
  }

  // one-shot: user clicks the real next/pagination control; persist its selector for reuse
  function locateNext() {
    ensureStyles();
    stopPick();
    return new Promise((resolve) => {
      const onMove = (e) => {
        document.querySelectorAll(".__super-browser-hover").forEach((el) => el.classList.remove("__super-browser-hover"));
        const t = e.target.closest("button, a, [role='button']") || e.target;
        if (t) t.classList.add("__super-browser-hover");
      };
      const onClick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        const target = e.target.closest("button, a, [role='button']") || e.target;
        const selector = buildSelector(target);
        localStorage.setItem(hostKey("nextSelector"), selector);
        cleanup();
        target.classList.add("__super-browser-next");
        setTimeout(() => target.classList.remove("__super-browser-next"), 1500);
        resolve({ selector });
      };
      function cleanup() {
        document.removeEventListener("mousemove", onMove, true);
        document.removeEventListener("click", onClick, true);
        document.querySelectorAll(".__super-browser-hover").forEach((el) => el.classList.remove("__super-browser-hover"));
        pickCleanup = null;
      }
      document.addEventListener("mousemove", onMove, true);
      document.addEventListener("click", onClick, true);
      pickCleanup = cleanup;
    });
  }

  function savedSelectors() {
    return {
      table: localStorage.getItem(hostKey("sbTableSelector")),
      next: localStorage.getItem(hostKey("nextSelector")),
    };
  }
  function clearSaved() {
    localStorage.removeItem(hostKey("sbTableSelector"));
    localStorage.removeItem(hostKey("nextSelector"));
    return savedSelectors();
  }

  return {
    findTables,
    getTableData,
    tableSignature,
    clickNext,
    scrollTableDown,
    highlight,
    cycleTable,
    confirmTable,
    pickByClick,
    locateNext,
    stopPick,
    clearHighlight,
    savedSelectors,
    clearSaved,
    get candidates() {
      if (!tables.length) findTables();
      return tables.map((t, i) => ({ ...tableInfo(t), index: i }));
    },
    get selectedSelector() {
      return currentTable()?.selector || null;
    },
  };
})();
