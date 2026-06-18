window.__superBrowserTabScraper = (() => {
  const engine = () => window.__superBrowserIdsEngine;

  // Bonus columns derived from the whole row text (emails aren't in most grids; phones/LinkedIn
  // often hide inside a description cell). The positional cells remain the real columns.
  function deriveExtra(rawText, cells) {
    const blob = [rawText, ...(cells || [])].join("\n");
    const extra = {};
    const email = blob.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i);
    if (email) extra.email = email[0];
    const phone = blob.match(/(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}/);
    if (phone) extra.phone = phone[0];
    const li = blob.match(/https?:\/\/[^\s,]*linkedin\.com\/[^\s,]+/i);
    if (li) extra.linkedin = li[0];
    return extra;
  }
  function enrich(rows) {
    return rows.map((row) => ({
      source: row.source,
      raw_text: row.raw_text,
      cells: row.cells || [],
      extra: deriveExtra(row.raw_text || "", row.cells || []),
    }));
  }

  return {
    pageInfo() {
      return { url: location.href, title: document.title || "", hostname: location.hostname };
    },

    tableSignature() {
      return engine()?.tableSignature() || location.href;
    },

    candidates() {
      return engine()?.candidates || [];
    },

    detectBlock() {
      return engine()?.detectBlock() || null;
    },

    extractRows() {
      const eng = engine();
      if (!eng) return { rows: [], headers: null };
      eng.findTables();
      const { rows, headers } = eng.getTableData(window.__superBrowserSelectedTable?.selector);
      return { rows: enrich(rows), headers };
    },

    async waitForRows({ timeoutMs = 10000 } = {}) {
      const eng = engine();
      const start = Date.now();
      let last = { rows: [], headers: null };
      while (Date.now() - start < timeoutMs) {
        eng?.findTables();
        const res = this.extractRows();
        last = res;
        // Require a real multi-column table (≥2 cells) before declaring ready, so a 1-column
        // filter sidebar that's briefly the biggest block during a page load isn't accepted.
        const cols = res.rows[0]?.cells?.length || 0;
        if (res.rows.length > 0 && cols >= 2) {
          return {
            rows: res.rows,
            headers: res.headers,
            waitedMs: Date.now() - start,
            candidateCount: eng?.candidates?.length || 0,
            selector: eng?.selectedSelector,
            ready: true,
          };
        }
        await new Promise((r) => setTimeout(r, 500));
      }
      return {
        rows: last.rows,
        headers: last.headers,
        waitedMs: Date.now() - start,
        candidateCount: eng?.candidates?.length || 0,
        selector: eng?.selectedSelector,
        ready: false,
        timedOut: true,
      };
    },

    detectLoginWall() {
      const res = this.extractRows();
      if (res.rows.length >= 3) return null;
      const body = ((document.body && document.body.innerText) || "").toLowerCase();
      if (body.includes("captcha") || body.includes("are you human")) return "captcha";
      if (document.querySelector("input[type='password']") && res.rows.length === 0) return "login_wall";
      return null;
    },

    async waitForTableChange({ previousSignature, timeoutMs = 10000 } = {}) {
      const start = Date.now();
      while (Date.now() - start < timeoutMs) {
        if (this.tableSignature() !== previousSignature) {
          return { changed: true, waitedMs: Date.now() - start };
        }
        await new Promise((r) => setTimeout(r, 400));
      }
      return { changed: false, waitedMs: Date.now() - start, timedOut: true };
    },

    async advancePage({ mode = "auto" } = {}) {
      const eng = engine();
      if (!eng) return "end";
      if (mode === "infinite_scroll") {
        return eng.scrollTableDown(window.__superBrowserSelectedTable?.selector);
      }
      return eng.clickNext(null, mode);
    },

    // ---- picker / locate passthroughs ----
    highlight({ index } = {}) {
      return engine()?.highlight(index);
    },
    cycleTable({ step = 1 } = {}) {
      return engine()?.cycleTable(step);
    },
    confirmTable() {
      return engine()?.confirmTable();
    },
    pickTable() {
      return engine()?.pickByClick();
    },
    locateNext() {
      return engine()?.locateNext();
    },
    stopPick() {
      engine()?.stopPick();
      return { stopped: true };
    },
    savedSelectors() {
      return engine()?.savedSelectors();
    },
    clearSaved() {
      return engine()?.clearSaved();
    },
  };
})();
