// pii.newmean.ai Worker. Static pages come from the assets binding; the Worker only runs for:
// - /: the demo (public/demo/), served at the root with a <base> so its relative paths still resolve to /demo/;
// - /mobile-bench: redirect to the trailing-slash URL so the page's relative paths resolve (query kept);
// - /mobile-bench/models/*: model files; while the Worker secret BENCH_KEY is set, only with that share code (?k=);
// - /api/bench: phone-bench results from the page, stored in D1. No IP or other request metadata is stored.

interface Env {
  ASSETS: Fetcher;
  DB: D1Database;
  BENCH_KEY?: string;
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

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/") {
      const page = await env.ASSETS.fetch(new Request(`${url.origin}/demo/`, request));
      return new HTMLRewriter().on("meta[charset]", { element: (el) => void el.after('<base href="/demo/">', { html: true }) }).transform(page);
    }
    if (url.pathname === "/mobile-bench") return Response.redirect(`${url.origin}/mobile-bench/${url.search}`, 301);
    if (url.pathname === "/api/bench") return saveRun(request, env);
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
