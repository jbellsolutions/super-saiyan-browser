window.__superBrowserTabScraper = (() => {
  const engine = () => window.__superBrowserIdsEngine;

  // Derive friendly contact columns from whatever the path-based walk captured.
  function inferContactFields(fields, rawText) {
    const out = { ...fields };
    const blob = [rawText, ...Object.values(fields)].join("\n");
    const email = blob.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i);
    if (email && !out.email) out.email = email[0];
    const phone = blob.match(/(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}/);
    if (phone && !out.phone) out.phone = phone[0];
    for (const value of Object.values(fields)) {
      if (typeof value !== "string") continue;
      if (/linkedin\.com/i.test(value) && !out.profile_url) out.profile_url = value;
      else if (/^https?:\/\//i.test(value) && !out.website) out.website = value;
    }
    return out;
  }

  function enrich(rows) {
    return rows.map((row) => ({ ...row, fields: inferContactFields(row.fields || {}, row.raw_text || "") }));
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

    extractRows() {
      const eng = engine();
      if (!eng) return [];
      eng.findTables();
      const rows = eng.getTableData(window.__superBrowserSelectedTable?.selector);
      return enrich(rows);
    },

    async waitForRows({ timeoutMs = 10000 } = {}) {
      const eng = engine();
      const start = Date.now();
      let last = [];
      while (Date.now() - start < timeoutMs) {
        eng?.findTables();
        const rows = this.extractRows();
        last = rows;
        // Require a real multi-column table before declaring ready, so a 1-column filter
        // sidebar that's briefly the biggest block during a page load isn't accepted.
        const cols = Object.keys(rows[0]?.fields || {}).length;
        if (rows.length > 0 && cols >= 2) {
          return {
            rows,
            waitedMs: Date.now() - start,
            candidateCount: eng?.candidates?.length || 0,
            selector: eng?.selectedSelector,
            ready: true,
          };
        }
        await new Promise((r) => setTimeout(r, 500));
      }
      return {
        rows: last,
        waitedMs: Date.now() - start,
        candidateCount: eng?.candidates?.length || 0,
        selector: eng?.selectedSelector,
        ready: false,
        timedOut: true,
      };
    },

    detectLoginWall() {
      const rows = this.extractRows();
      if (rows.length >= 3) return null;
      const body = ((document.body && document.body.innerText) || "").toLowerCase();
      if (body.includes("captcha") || body.includes("are you human")) return "captcha";
      if (document.querySelector("input[type='password']") && rows.length === 0) return "login_wall";
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

    // ---- picker / locate passthroughs (return Promises where interactive) ----
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
