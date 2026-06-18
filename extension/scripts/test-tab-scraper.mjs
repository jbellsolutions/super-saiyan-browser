/**
 * Regression smoke test for the in-tab engine against a real-structure ListKit fixture.
 * Run: node extension/scripts/test-tab-scraper.mjs
 * Requires a real browser for layout (offsetWidth/Height): npm i -D playwright && npx playwright install chromium
 *
 * The live-site verification (driving next.listkit.io directly) is the primary oracle;
 * this fixture guards against regressions in: grid-vs-sidebar selection, rich extraction,
 * and numbered-pager advance.
 */
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const idsEngineJs = readFileSync(join(__dirname, "../ids-engine.js"), "utf8");
const scraperJs = readFileSync(join(__dirname, "../tab-scraper.js"), "utf8");
const html = readFileSync(join(__dirname, "../test-fixtures/listkit-b2b.html"), "utf8");

function fail(msg) {
  console.error(`FAIL: ${msg}`);
  process.exitCode = 1;
}

async function main() {
  let playwright;
  try {
    playwright = await import("playwright");
  } catch {
    console.error("Install playwright: npm i -D playwright && npx playwright install chromium");
    process.exit(1);
  }

  const server = createServer((_req, res) => {
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end(html);
  });
  await new Promise((resolve) => server.listen(0, resolve));
  const url = `http://127.0.0.1:${server.address().port}/`;

  const browser = await playwright.chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.goto(url);
  await page.evaluate(idsEngineJs + "\n" + scraperJs);

  const p1 = await page.evaluate(() => window.__superBrowserTabScraper.waitForRows({ timeoutMs: 3000 }));
  const cols1 = (p1.rows?.[0]?.cells || []).length;
  const firstText1 = (p1.rows?.[0]?.raw_text || "").toLowerCase();
  const equalCells = new Set((p1.rows || []).map((r) => r.cells.length)).size === 1;
  console.log(`Page 1: ${p1.rows?.length} rows × ${cols1} cols · headers=${p1.headers ? p1.headers.length : 0} · equalCells=${equalCells} · first="${firstText1.slice(0, 30)}"`);

  // 1. Grid picked over the 1-column filter sidebar (sidebar rows are "Funding", "Industry"...)
  if ((p1.rows || []).length < 3 || cols1 < 4) fail("expected the multi-column people grid, not the sidebar");
  // 1b. Positional cells must be consistent (stable columns) and headers detected
  if (!equalCells) fail("rows have inconsistent cell counts — columns would drift");
  if (!p1.headers || p1.headers.length < cols1 - 1) fail("header row not detected");
  if (firstText1.includes("funding") || firstText1.includes("industry")) fail("picked the filter sidebar, not the grid");
  if (!firstText1.includes("ali khan")) fail("page-1 grid content not extracted");

  // 2. Numbered pagination advances and content changes
  const sig1 = await page.evaluate(() => window.__superBrowserTabScraper.tableSignature());
  const advanced = await page.evaluate(() => window.__superBrowserTabScraper.advancePage({ mode: "auto" }));
  console.log(`advancePage(auto): ${advanced}`);
  if (advanced !== "advanced") fail("numbered pager did not advance");
  await page.waitForTimeout(400);

  const p2 = await page.evaluate(() => window.__superBrowserTabScraper.waitForRows({ timeoutMs: 3000 }));
  const sig2 = await page.evaluate(() => window.__superBrowserTabScraper.tableSignature());
  const firstText2 = (p2.rows?.[0]?.raw_text || "").toLowerCase();
  console.log(`Page 2: ${p2.rows?.length} rows · first="${firstText2.slice(0, 30)}" · sigChanged=${sig1 !== sig2}`);
  if ((p2.rows || []).length < 3) fail("page-2 extraction empty");
  if (sig1 === sig2 || firstText2 === firstText1) fail("page-2 content identical to page-1");
  if (!firstText2.includes("alex kim")) fail("page-2 grid content not extracted");

  await browser.close();
  server.close();

  if (process.exitCode) console.error("\nSMOKE TEST FAILED");
  else console.log("\nPASS — grid selected over sidebar, extraction rich, numbered pagination advances");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
