English | [한국어](README.ko.md)

# pii.newmean.ai

A Cloudflare Worker with static assets (`public/`) and a D1 database (`pii-site`).

- `/`: the demo (`js/playground`), served from `public/demo/` with a `<base href="/demo/">` so its relative paths resolve. The
  demo loads the four default model files from `public/demo/models/` (same CDN as the page) and falls back to Hugging Face
  when that fails.
- `/mobile-bench/?k=<share code>`: the phone benchmark (`js/experiments/phone-bench`). The model files under
  `/mobile-bench/models/` and the results API `/api/bench` need the share code, which is the Worker secret `BENCH_KEY`. Each
  finished run is stored in the D1 table `bench_runs` (`schema.sql`); the stored fields are the device information the browser
  reports and the measurements, not IP addresses or other request metadata. Results are published per device model (the
  page says so), never per run.
- `/results/`: miss % and over-masking % of the shipped v1.1 models per language and per label, for every size
  (`public/results/`: a static page, no build step and no request other than its `data.json`; the page sends nothing back). It
  offers the demo's languages and keeps the choice under the demo's browser key (`pii-demo-lang`). `data.json` is written by
  `training/scripts/results_by_language.py --json`, which also prints the tables of
  `docs/results/models-v1.1-by-language-label.md`; regenerate both together.
- `/api/demo`: the demo's speed numbers. On pii.newmean.ai the demo sends, when the page is hidden or closed, the model size,
  engine and time of each run and model load, the token count of each run, a coarse device class (OS, browser, phone or not, CPU cores, memory),
  the page language, the SDK version, whether the text was one of the samples, where each model load came from (`site` or
  `hf`), the fixed name of each marked link or button that was used (`data-t` in `index.html`, such as `star-top` or
  `install:js`), and a random ID made on each page load (not stored in the browser, so a reload is a new one) that ties one
  visit's events together. Never the text or what was found; the line under the demo title names what is recorded about the visit and links to
  this list. A browser opened once with `?team=1` marks its numbers as the team's (`?team=0` undoes
  it) so they can be left out. The Worker writes one data point per event to the Workers Analytics Engine dataset `pii_demo_runs` (binding `DEMO`) with the
  country Cloudflare reports, and no IP address. Read it with the Analytics Engine SQL API (an API token with Account
  Analytics: Read). The numbers are published only in aggregate (medians by model size and device class), as the line
  under the demo title says; countries, sessions and single runs are not published.

## Setup (once)

```bash
wrangler d1 create pii-site                                    # put the printed database_id into wrangler.jsonc
wrangler d1 execute pii-site --remote --file schema.sql        # creates bench_runs
wrangler secret put BENCH_KEY                                  # the share code for the phone benchmark
```

`wrangler.jsonc` binds the custom domain `pii.newmean.ai`; change or remove `routes` to deploy your own copy.

## Build and deploy

Run `npm install` in `js/` first; both build scripts use its esbuild and onnxruntime-web.

```bash
node ../js/playground/build.mjs public/demo                                         # demo page, bundle, ORT wasm, model files (from Hugging Face, SHA-256 checked)
node ../js/experiments/phone-bench/build.mjs public/mobile-bench <models dir>       # bench page, bundle, ORT wasm, model files
wrangler deploy
```

`<models dir>` holds local copies of the four Hugging Face model repos (`pii-nano/`, `pii-small/`, `pii-medium/`,
`pii-large/`). `public/demo/` and `public/mobile-bench/` are build output and stay out of git.

Read the stored runs:

```bash
wrangler d1 execute pii-site --remote --command "SELECT created_at, device, size, engine, data FROM bench_runs ORDER BY id"
```

Each run's `data` holds the SDK version of the page that measured it (`sdk`; runs before 1.5.0 have none), so speed can be
compared by release:

```bash
wrangler d1 execute pii-site --remote --command "SELECT json_extract(data, '$.sdk') AS sdk, size, engine, count(*) AS runs FROM bench_runs GROUP BY 1, 2, 3"
```

The demo's speed numbers carry it too (`blob9` of `pii_demo_runs`).
