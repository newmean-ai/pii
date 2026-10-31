// Logic of the static leaderboard page (no DOM, so node can test it: tests/js/lb.test.mjs).
// The submission checks mirror pii_leaderboard/submit.py; the weekly job runs the Python ones again on every submission.

export const ORG = "newmean-ai";
export const SPACE = `${ORG}/pii-leaderboard`;
export const REQUESTS = `${ORG}/pii-leaderboard-requests`;
export const RESULTS = `${ORG}/pii-leaderboard-results`;
export const PUBLIC_TEST = `${ORG}/pii-dataset`;
export const HUB = "https://huggingface.co";

export const LABELS = ["PERSON", "PERSON_L2", "EMAIL", "PHONE", "ADDRESS", "ID_NUMBER", "FINANCIAL", "ORG", "USERNAME", "URL",
  "URL_L2", "DATE_OF_BIRTH", "SECRET"];
export const MAP_TARGETS = [...LABELS, "OTHER", "ignore"];
export const USER_ADAPTERS = ["transformers", "newmean-onnx"];
export const VARIANTS = ["q4", "q4e"];
export const DEFAULT_VARIANT = { large: "q4e", medium: "q4e", small: "q4", nano: "q4" };   // js/src/redactor.ts
export const MAX_MODEL_GB = 3;
export const MAX_PER_WEEK = 2;
export const MARKER = "<!-- pii-leaderboard-request v1 -->";

export class Rejected extends Error {}

export const stripBio = (raw) => raw.replace(/^[BIES]-/, "");
export const defaultVariant = (model) => DEFAULT_VARIANT[model.split("/").pop().replace(/^pii-/, "")] ?? "q4";

const py = (x) => JSON.stringify(x).replaceAll('"', "'").replaceAll(",", ", ");   // Python repr of a list of strings

export function parseLabelMap(text) {
  text = (text ?? "").trim();
  if (text === "" || text === "auto") return "auto";
  let m;
  try { m = JSON.parse(text); } catch (e) { throw new Rejected(`label map is not valid JSON: ${e.message}`); }
  if (m === null || typeof m !== "object" || Array.isArray(m) || !Object.entries(m).every(([k, v]) => typeof v === "string"))
    throw new Rejected('label map must be a JSON object {"model label": "OUR_LABEL" | "OTHER" | "ignore"}');
  const bad = [...new Set(Object.values(m).filter((v) => !MAP_TARGETS.includes(v)))].sort();
  if (bad.length) throw new Rejected(`unknown target labels ${py(bad)}; allowed: ${py(MAP_TARGETS)}`);
  if (Object.values(m).every((v) => v === "ignore")) throw new Rejected("the label map ignores every label");
  return m;
}

export function modelLabels(cfg) {
  const s = new Set(Object.values(cfg.id2label ?? {}).map(stripBio));
  s.delete("O");
  return [...s].sort();
}

/** Browser-side checks through the public Hub API (anonymous). fetchJson(url) → parsed JSON, throws on HTTP errors.
 *  Returns the fields of the request to post: {model, revision (commit), adapter, variant, label_map}. */
export async function checkModel(fetchJson, { model, revision, adapter, labelMap, variant }) {
  model = (model ?? "").trim();
  if (!model || model.split("/").length !== 2) throw new Rejected("model must be a Hub id like org/name");
  if (!USER_ADAPTERS.includes(adapter))
    throw new Rejected(`adapter must be one of ${py(USER_ADAPTERS)}; other formats are added by the maintainers (open a discussion)`);
  if (adapter === "newmean-onnx" && !model.startsWith(`${ORG}/pii-`))
    throw new Rejected("the newmean-onnx adapter is only for newmean-ai/pii-<size> models");
  const label_map = adapter === "transformers" ? parseLabelMap(labelMap) : null;
  variant = adapter === "newmean-onnx" ? (variant || defaultVariant(model)) : null;
  if (variant !== null && !VARIANTS.includes(variant)) throw new Rejected(`variant must be one of ${py(VARIANTS)}`);
  const rev = (revision ?? "").trim() || "main";
  let info;
  try {
    info = await fetchJson(`${HUB}/api/models/${model}/revision/${encodeURIComponent(rev)}?blobs=true`);
  } catch (e) {
    throw new Rejected(`cannot read ${model}@${rev} on the Hub (${e.status ?? e.name})`);
  }
  if (info.private || info.gated) throw new Rejected("the model must be public and not gated");
  const files = Object.fromEntries((info.siblings ?? []).map((s) => [s.rfilename, s.size ?? 0]));
  const out = { model, revision: info.sha, adapter, variant, label_map };
  if (adapter === "newmean-onnx") {
    if (!(`onnx/model_${variant}.onnx` in files)) throw new Rejected(`onnx/model_${variant}.onnx not found`);
    return out;
  }
  if (!("config.json" in files)) throw new Rejected("config.json not found");
  const weights = Object.keys(files).filter((f) => f.endsWith(".safetensors"));
  if (!weights.length) throw new Rejected("safetensors weights are required (pickled .bin weights are not loaded)");
  const gb = weights.reduce((a, f) => a + files[f], 0) / 1e9;
  if (gb > MAX_MODEL_GB) throw new Rejected(`weights are ${gb.toFixed(1)} GB; the limit is ${MAX_MODEL_GB} GB`);
  const cfg = await fetchJson(`${HUB}/${model}/resolve/${info.sha}/config.json`);
  if (cfg.auto_map && Object.keys(cfg.auto_map).length) throw new Rejected("models that need trust_remote_code are not run");
  if (!(cfg.architectures ?? []).some((a) => a.endsWith("ForTokenClassification")))
    throw new Rejected("the model must be a transformers token-classification model (…ForTokenClassification)");
  if (label_map !== "auto") {
    const labels = modelLabels(cfg);
    const missing = labels.filter((l) => !(l in label_map)).sort();
    const extra = Object.keys(label_map).filter((l) => !labels.includes(l)).sort();
    if (missing.length || extra.length)
      throw new Rejected(`label map must cover exactly the model labels ${py(labels)}; missing ${py(missing)}, unknown ${py(extra)}`);
  }
  return out;
}

export function requestBody(fields) {
  return `${MARKER}\nSubmission to the newmean-pii leaderboard. It is checked and scored by the weekly job, which replies here.\n\n` +
    "```json\n" + JSON.stringify(fields, null, 1) + "\n```\n";
}

export const requestTitle = (f) => `Submit ${f.model}@${f.revision.slice(0, 12)}`;

/** Submissions of a user counted against the weekly limit (queue.json rows; rejected ones do not count). */
export function recentBy(requests, user, now = new Date()) {
  const since = now.getTime() - 7 * 24 * 3600 * 1000;
  return requests.filter((r) => r.submitter === user && r.status !== "rejected" && Date.parse(r.submitted_at) > since);
}

// --- tables -------------------------------------------------------------------------------------------------------------
export const pct = (x) => (x == null ? null : Number((100 * x).toFixed(1)));   // as Python f"{100 * x:.1f}" (dataset card)
const PCT = new Set(["leak", "strict_leak", "overmask", "entity_leak", "recall", "exact_recall"]);
const leakOf = (r) => r.public?.overall?.leak ?? 1;

export function overallRows(results) {
  return [...results].sort((a, b) => leakOf(a) - leakOf(b)).map((r) => {
    const o = r.public?.overall ?? {}, p = r.private ?? {}, po = p.overall ?? {}, done = p.status === "done";
    return {
      model: r.display_name, href: r.model === "regex" ? null : `${HUB}/${r.model}`, size: r.size ?? "",
      leak: pct(o.leak), overmask: pct(o.overmask), exact_f1: o.exact_f1, span_f1: o.span_f1, entity_leak: pct(o.entity_leak),
      private_leak: done ? pct(po.leak) : "pending", private_overmask: done ? pct(po.overmask) : "pending",
      private_exact_f1: done ? po.exact_f1 : "pending",
      adapter: r.adapter + (r.variant ? ` (${r.variant})` : ""), revision: (r.revision ?? "").slice(0, 12), hardware: r.hardware ?? "",
    };
  });
}

/** {columns, rows} for part = by_label | by_language | by_level and one metric. */
export function breakdown(results, part, metric) {
  const sorted = [...results].sort((a, b) => leakOf(a) - leakOf(b));
  const cols = [...new Set(sorted.flatMap((r) => Object.keys(r.public?.[part] ?? {})))].sort();
  const rows = sorted.map((r) => ({
    model: r.display_name,
    values: cols.map((c) => { const v = r.public?.[part]?.[c]?.[metric]; return PCT.has(metric) ? pct(v) : v ?? null; }),
  }));
  return { columns: cols, rows };
}
