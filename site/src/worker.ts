// pii.newmean.ai Worker. Static pages come from the assets binding; the Worker only runs for:
// - /: the demo (public/demo/), served at the root with a <base> so its relative paths still resolve to /demo/;
// - /mobile-bench: redirect to the trailing-slash URL so the page's relative paths resolve (query kept);
// - /mobile-bench/models/*: model files; while the Worker secret BENCH_KEY is set, only with that share code (?k=);
// - /api/bench: phone-bench results from the page, stored in D1. No IP or other request metadata is stored.
// - /api/demo: the demo's speed numbers (model size, engine, ms, tokens, where the model came from, coarse device class, sample
//   or own text; never the text) and which marked links or buttons were used, one Analytics Engine data point per run, model
//   load or click, plus the country Cloudflare reports and whether the browser is marked as the team's. No IP is stored.

interface Env {
  ASSETS: Fetcher;
  DB: D1Database;
  BENCH_KEY?: string;
  DEMO?: AnalyticsEngineDataset;
}

const MAX_BODY = 64 * 1024;

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });

// open when no BENCH_KEY is set (after the release); gated by the share code while it is set
const keyOk = (env: Env, k: string | null) => !env.BENCH_KEY || k === env.BENCH_KEY;

async function saveRun(request: Request, env: Env) {
  if (request.method !== "POST") return json(405, { error: "method" });
  if (!keyOk(env, request.headers.get("x-bench-key"))) return json(403, { error: "key" });
  const text = await request.text();
  if (text.length > MAX_BODY) return json(413, { error: "size" });
  let run: Record<string, any>;
  try {
    run = JSON.parse(text);
  } catch {
    return json(400, { error: "json" });
  }
  const str = (v: unknown, n: number) => (typeof v === "string" ? v.slice(0, n) : "");
  const session = str(run.session, 64);
  const size = str(run.size, 16);
  const engine = str(run.engine, 32);
  const device = str(run.device?.label, 200);
  if (!session || !size || !engine) return json(400, { error: "fields" });
  await env.DB.prepare("INSERT INTO bench_runs (session, device, size, engine, data) VALUES (?, ?, ?, ?, ?)")
    .bind(session, device, size, engine, text)
    .run();
  return json(200, { ok: true });
}

// Analytics Engine row: blob1 kind (run|load|click), blob2 size, blob3 engine, blob4 src (sample|own|-), blob5 os, blob6 browser,
// blob7 mobile (1|0), blob8 lang, blob9 SDK version, blob10 session, blob11 country, blob12 team (1|0), blob13 model source of a
// load (site|hf|-), blob14 click target (data-t name, e.g. star-top, install:js; - otherwise); double1 ms, double2 tokens,
// double3 cores, double4 device memory GB; index1 session. Read with the SQL API (dataset pii_demo_runs). Rows before
// 2026-11-18 have no blob12-14.
const MAX_DEMO_EVENTS = 100;
const pick = (v: unknown, ok: readonly string[]) => (typeof v === "string" && ok.includes(v) ? v : "-");
const target = (v: unknown) => (typeof v === "string" && /^[a-z0-9:-]{1,32}$/.test(v) ? v : "-");
const num = (v: unknown, max: number) => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.min(v, max) : 0);

async function saveDemo(request: Request, env: Env) {
  if (request.method !== "POST") return json(405, { error: "method" });
  if (!env.DEMO) return json(503, { error: "off" });
  const text = await request.text();
  if (text.length > 32 * 1024) return json(413, { error: "size" });
  let b: Record<string, any>;
  try {
    b = JSON.parse(text);
  } catch {
    return json(400, { error: "json" });
  }
  const session = typeof b.session === "string" ? b.session.replace(/[^a-z0-9-]/gi, "").slice(0, 40) : "";
  if (!session || !Array.isArray(b.events)) return json(400, { error: "fields" });
  const d = b.device ?? {};
  const common = [
    pick(d.os, ["iOS", "Android", "macOS", "Windows", "Linux", "ChromeOS", "other"]),
    pick(d.browser, ["Chrome", "Safari", "Firefox", "Edge", "Samsung", "other"]),
    d.mobile ? "1" : "0",
    pick(b.lang, ["en", "ko", "ja", "zh", "zh-Hant", "es", "fr", "de", "pt"]),
    typeof b.v === "string" ? b.v.slice(0, 20) : "-",
    session,
    String((request as any).cf?.country ?? "-").slice(0, 2),
    b.team === true ? "1" : "0",
  ];
  for (const e of b.events.slice(0, MAX_DEMO_EVENTS)) {
    env.DEMO.writeDataPoint({
      blobs: [pick(e.kind, ["run", "load", "click"]), pick(e.size, ["nano", "small", "medium", "large"]), pick(e.engine, ["builtin", "onnxruntime"]),
        pick(e.src, ["sample", "own"]), ...common, pick(e.from, ["site", "hf"]), target(e.target)],
      doubles: [num(e.ms, 600_000), num(e.tokens, 100_000), num(d.cores, 256), num(d.memory, 64)],
      indexes: [session],
    });
  }
  return new Response(null, { status: 204 });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/") {
      const page = await env.ASSETS.fetch(new Request(`${url.origin}/demo/`, request));
      return new HTMLRewriter().on("meta[charset]", { element: (el) => void el.after('<base href="/demo/">', { html: true }) }).transform(page);
    }
    if (url.pathname === "/mobile-bench") return Response.redirect(`${url.origin}/mobile-bench/${url.search}`, 301);
    if (url.pathname === "/api/bench") return saveRun(request, env);
    if (url.pathname === "/api/demo") return saveDemo(request, env);
    if (url.pathname.startsWith("/api/")) return json(404, { error: "not found" });
    if (url.pathname.startsWith("/mobile-bench/models/")) {
      if (!keyOk(env, url.searchParams.get("k"))) return new Response("Forbidden", { status: 403 });
      const res = await env.ASSETS.fetch(new Request(`${url.origin}${url.pathname}`, request));
      const out = new Response(res.body, res);
      out.headers.set("cache-control", "private, no-store");
      return out;
    }
    return env.ASSETS.fetch(request);
  },
} satisfies ExportedHandler<Env>;
