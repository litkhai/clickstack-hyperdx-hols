// HyperDX screens for this lab, from the local ClickStack (bin/up.sh), in dark mode: the dashboard,
// denied requests, one request/response pair by TraceId, and the detail panel of a login response.
// Korean and English sets: the Korean set uses dashboard/vault-audit.ko.json and Korean column names.
//
//   PLAYWRIGHT=<path to the playwright package> node bin/capture.mjs [ko|en|all]
//   WINDOW_MIN=15   the time range shown, ending now (default 15 minutes)
// The dashboard is shown at 1-minute granularity: finer buckets than the 30 s scrape interval
// draw the HA gauges as a 1/0 zig-zag.
//
// Writes images/<lang>/NN-*.png at 2x (1440 px wide viewport) and, when `cwebp` is on PATH, a
// 1600 px WebP next to each. Logs in with the local test user from .run/hyperdx-user (never printed).
// Dark mode is HyperDX's own preference (localStorage hdx-user-preferences, colorMode), set before
// the first page loads; prefers-color-scheme is dark too. Nothing on the page is edited.
import { readFileSync, mkdirSync, existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const LAB = join(dirname(fileURLToPath(import.meta.url)), "..");
const UI = `http://localhost:${process.env.VAULTLAB_UI_PORT ?? 48080}`;
const CH = `http://localhost:${process.env.VAULTLAB_CH_HTTP_PORT ?? 48123}`;
const { chromium } = await import(process.env.PLAYWRIGHT ?? "playwright");
const user = JSON.parse(readFileSync(join(LAB, ".run/hyperdx-user"), "utf8"));
const which = process.argv[2] ?? "all";
const A = "LogAttributes";

const L = {
  ko: {
    dashboard: "Vault 감사와 HA",
    denied: `Timestamp, SeverityText AS \`심각도\`, ${A}['vault.auth.display_name'] AS \`주체\`, ${A}['vault.auth.role_name'] AS \`역할\`, ${A}['vault.request.operation'] AS \`작업\`, ${A}['vault.request.path'] AS \`경로\`, ${A}['vault.node'] AS \`노드\``,
    pair: `Timestamp, EventName AS \`이벤트\`, TraceId, ${A}['vault.request.path'] AS \`경로\`, ${A}['vault.node'] AS \`노드\`, ${A}['vault.issued.policies'] AS \`발급 정책\``,
  },
  en: {
    dashboard: "Vault audit and HA",
    denied: `Timestamp, SeverityText, ${A}['vault.auth.display_name'] AS who, ${A}['vault.auth.role_name'] AS role, ${A}['vault.request.operation'] AS op, ${A}['vault.request.path'] AS path, ${A}['vault.node'] AS node`,
    pair: `Timestamp, EventName, TraceId, ${A}['vault.request.path'] AS path, ${A}['vault.node'] AS node, ${A}['vault.issued.policies'] AS issued_policies`,
  },
};

async function sql(q) {
  const r = await fetch(`${CH}/?default_format=TSV`, { method: "POST", body: q, headers: { Authorization: "Basic " + Buffer.from("api:api").toString("base64") } });
  if (!r.ok) throw new Error(`ClickHouse ${r.status}: ${(await r.text()).slice(0, 200)}`);
  return (await r.text()).trim();
}

const to = Date.now();
const from = to - Number(process.env.WINDOW_MIN ?? 15) * 60 * 1000;
const traceId = await sql(`SELECT TraceId FROM vault_audit.otel_logs WHERE ${A}['vault.request.path'] = 'auth/approle/login' AND ${A}['vault.audit.type'] = 'response' ORDER BY Timestamp DESC LIMIT 1`);
if (!traceId) throw new Error("no AppRole login in vault_audit.otel_logs -- run bin/traffic.sh first");

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2, colorScheme: "dark", locale: "en-US" });
await ctx.addInitScript(() => {
  try {
    const k = "hdx-user-preferences";
    const cur = JSON.parse(localStorage.getItem(k) || "{}");
    localStorage.setItem(k, JSON.stringify({ ...cur, colorMode: "dark", timeFormat: "24h" }));
  } catch {}
});
const page = await ctx.newPage();
await page.goto(UI + "/login", { waitUntil: "networkidle" });
await page.fill('input[type="email"]', user.email);
await page.fill('input[type="password"]', user.password);
await page.keyboard.press("Enter");
await page.waitForTimeout(4000);   // login redirects to the image's FRONTEND_URL (port 8080); ignore it

const api = (p) => page.evaluate(async (p) => (await fetch(p)).json(), p);
await page.goto(UI + "/search", { waitUntil: "networkidle" });
const sources = await api("/api/sources");
const dashboards = await api("/api/dashboards");
const auditId = sources.find((s) => s.name === "Vault Audit")?.id;
if (!auditId) throw new Error("no Vault Audit source -- run bin/setup_hyperdx.py");

const search = (where, select) => "/search?" + new URLSearchParams({ source: auditId, where, whereLanguage: "sql", select, from, to, isLive: "false" });

async function shot(lang, name, path, { height = 1000, wait = 8000, click } = {}) {
  await page.setViewportSize({ width: 1440, height });
  await page.goto(UI + path, { waitUntil: "networkidle" });
  await page.waitForTimeout(wait);
  if (click) { await page.getByText(click, { exact: true }).first().click(); await page.waitForTimeout(3000); }
  await page.mouse.move(1439, height - 1);   // keep the pointer off the content
  const dir = join(LAB, "images", lang);
  mkdirSync(dir, { recursive: true });
  const png = join(dir, name + ".png");
  await page.screenshot({ path: png });
  let out = png;
  if (hasCwebp) { out = png.replace(/\.png$/, ".webp"); execFileSync("cwebp", ["-quiet", "-q", "82", "-resize", "1600", "0", png, "-o", out]); }
  console.log(out);
}
const hasCwebp = (() => { try { execFileSync("cwebp", ["-version"], { stdio: "ignore" }); return true; } catch { return false; } })();

// Collapse the side navigation once; HyperDX keeps the choice for the session.
await page.goto(UI + "/search", { waitUntil: "networkidle" });
const collapse = page.locator('[aria-label*="ollapse"], [title*="ollapse"]').first();
if (await collapse.count()) await collapse.click().catch(() => {});

for (const lang of which === "all" ? ["ko", "en"] : [which]) {
  const t = L[lang];
  const dash = dashboards.find((d) => d.name === t.dashboard);
  if (!dash) throw new Error(`no dashboard "${t.dashboard}" -- run bin/setup_hyperdx.py`);
  await shot(lang, "01-dashboard", `/dashboards/${dash.id}?from=${from}&to=${to}&granularity=${encodeURIComponent("1 minute")}`, { height: 860, wait: 10000 });
  await shot(lang, "02-denied-search", search(`${A}['vault.event.category'] = 'denied' AND ${A}['vault.audit.type'] = 'response'`, t.denied), { height: 720 });
  await shot(lang, "03-trace-pair", search(`TraceId = '${traceId}'`, t.pair), { height: 620 });
  await shot(lang, "04-issue-detail", search(`TraceId = '${traceId}' AND ${A}['vault.audit.type'] = 'response'`, t.pair), { height: 1000, click: "vault.audit.response" });
}
await browser.close();
