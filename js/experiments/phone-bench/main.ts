// Phone benchmark (2026-10-31): the SDK's two engines on the four default model files, run in a phone browser at
// pii.newmean.ai/mobile-bench/?k=<share code>. Per engine and size: download, init (files already in memory, no hash check), first
// detect, and median detect latency on the bench texts (40, 252, 1000 tokens; regex off, one thread). Each run is posted to the
// site's /api/bench (D1, see site/) as soon as it finishes; the page shows this device's runs and can copy them as a fallback.
// The page is icon-first with a few strings in eight languages (LANGS below). Built by build.mjs next to this file.
import { createRedactor, DEFAULT_VARIANT } from "../../src/redactor.js";
import type { EngineName } from "../../src/engines/engine.js";
import type { Size } from "../../src/models.js";
import { relaxedSimdSupported } from "../../src/engines/builtin.js";
import TEXTS from "../custom-wasm/bench/texts.json";

type Run = Record<string, unknown>;
type Phase = "download" | "init" | "run" | "check";

const SIZES: Size[] = ["nano", "small", "medium", "large"];
const ENGINES: EngineName[] = ["builtin", "onnxruntime"];
const RUNS: Record<string, number> = { short: 15, window: 10, multi: 4 };
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

const relaxed = relaxedSimdSupported();
// Optional share code from the link (?k=), for when the site gates the model files and the results API (BENCH_KEY set).
const KEY = new URLSearchParams(location.search).get("k") ?? "";
// ?ref=1 (desktop only): run the consistency set on every size and leave the spans in window.__ref for build tooling.
const REF = new URLSearchParams(location.search).has("ref");
// ?anim=0 stops the waiting animation (to compare timings with and without it); stored with each run as `anim`.
const ANIM = new URLSearchParams(location.search).get("anim") !== "0" && !matchMedia("(prefers-reduced-motion: reduce)").matches;
if (!ANIM) document.body.classList.add("still");

// Consistency set (models/consistency.json, gated like the model files): 108 public test rows, two per language, with the
// spans a desktop browser produced. Each size and engine runs them all, then the first CHECK_REPEAT again.
type Check = { set: { id: string; lang: string; text: string }[]; ref?: Record<string, string[]>; refDevice?: string };
let CHECK: Check | null = null;
const CHECK_REPEAT = 20;
const textUnits = (text: string) => Math.ceil(text.length / 3);
const checkUnits = () => (CHECK ? CHECK.set.reduce((n, x, i) => n + textUnits(x.text) * (i < CHECK_REPEAT ? 2 : 1), 0) : 0);

// iPhone model guess (algorithm 2). Web pages cannot read the model, so the page proposes the
// models whose portrait layout width (points; the page uses width=device-width) and pixel ratio match, and the tester can
// correct it in a dropdown. A default left untouched is not a confirmation. Page zoom, Display Zoom, landscape, pinch zoom
// and in-app browsers make the guess unreliable, so the raw signals are stored with every run (see signals()). Safari 26+
// reports a fixed screen size and freezes the iOS version in the user agent; Firefox on iOS freezes it too.
const IPHONE_ALGO = 2;
const IPHONE_MODELS: [string, number, number][] = [
  ["iPhone 17 Pro Max", 440, 3], ["iPhone 17 Pro", 402, 3], ["iPhone Air", 420, 3], ["iPhone 17", 402, 3], ["iPhone 16e", 390, 3],
  ["iPhone 16 Pro Max", 440, 3], ["iPhone 16 Pro", 402, 3], ["iPhone 16 Plus", 430, 3], ["iPhone 16", 393, 3],
  ["iPhone 15 Pro Max", 430, 3], ["iPhone 15 Pro", 393, 3], ["iPhone 15 Plus", 430, 3], ["iPhone 15", 393, 3],
  ["iPhone 14 Pro Max", 430, 3], ["iPhone 14 Pro", 393, 3], ["iPhone 14 Plus", 428, 3], ["iPhone 14", 390, 3],
  ["iPhone SE (3rd gen)", 375, 2], ["iPhone 13 Pro Max", 428, 3], ["iPhone 13 Pro", 390, 3], ["iPhone 13", 390, 3], ["iPhone 13 mini", 375, 3],
  ["iPhone 12 Pro Max", 428, 3], ["iPhone 12 Pro", 390, 3], ["iPhone 12", 390, 3], ["iPhone 12 mini", 375, 3],
  ["iPhone SE (2nd gen)", 375, 2], ["iPhone 11 Pro Max", 414, 3], ["iPhone 11 Pro", 375, 3], ["iPhone 11", 414, 2],
  ["iPhone XS Max", 414, 3], ["iPhone XS", 375, 3], ["iPhone XR", 414, 2], ["iPhone X", 375, 3], ["iPhone 8 Plus", 414, 3], ["iPhone 8", 375, 2],
];
const IN_APP: [RegExp, string][] = [
  [/KAKAOTALK/i, "KakaoTalk"], [/\bLine\//, "LINE"], [/Instagram/, "Instagram"], [/FBAN|FBAV/, "Facebook"], [/Telegram/i, "Telegram"],
  [/NAVER\(inapp/i, "NAVER"], [/DaumApps/, "Daum"], [/Twitter/i, "X"], [/Snapchat/, "Snapchat"], [/WhatsApp/i, "WhatsApp"],
];
type IphoneGuess = { algorithmVersion: number; candidates: string[]; confidence: "medium" | "low" | "unknown"; reasons: string[] };

function iphoneGuess(app: string | null): IphoneGuess {
  const reasons: string[] = [];
  const dpr = devicePixelRatio;
  const scale = visualViewport?.scale ?? 1;
  let candidates: string[] = [];
  if (innerHeight <= innerWidth) reasons.push("landscape");
  else if (dpr !== 2 && dpr !== 3) reasons.push(`dpr ${dpr}`);
  else {
    candidates = IPHONE_MODELS.filter(([, w, d]) => w === innerWidth && d === dpr).map(([n]) => n);
    reasons.push(`width ${innerWidth}@${dpr}${candidates.length ? "" : " unmatched"}`);
  }
  if (Math.abs(scale - 1) > 0.01) reasons.push(`pinch ${Math.round(scale * 100) / 100}`);
  if (app) reasons.push(`in-app ${app}`);
  const confidence = !candidates.length ? "unknown" : app || Math.abs(scale - 1) > 0.01 ? "low" : "medium";
  return { algorithmVersion: IPHONE_ALGO, candidates, confidence, reasons };
}

const groupLabel = (c: string[]) => c.map((n, i) => (i ? n.replace("iPhone ", "") : n)).join(" / ");

// ---- strings -------------------------------------------------------------------------------------------------------------

const KEYS = ["tagline", "start", "time", "screen", "auto", "privacy", "estimating", "left", "lessMin", "done", "unsent", "copy", "copied", "noKey", "options", "results", "guess", "unknownModel", "otherModel", "pickModel", "otherPh"] as const;
type Key = (typeof KEYS)[number];
const LANGS: Record<string, [string, string[]]> = {
  en: ["English", ["newmean-pii SDK speed test", "Start", "5–10 min", "Keep screen on", "Auto-sent", "No IP stored", "Estimating…", "~{n} min left", "< 1 min left", "Done. Thank you!", "Some results weren't sent. Please copy and send them to us.", "Copy", "Copied", "Access code missing. Open the full link.", "Options", "Results (ms)", "estimated", "Don't know", "Not listed", "Which iPhone?", "Model name"]],
  ko: ["한국어", ["newmean-pii SDK 속도 측정", "시작", "5~10분", "화면 켜 두기", "자동 전송", "IP 저장 안 함", "남은 시간 계산 중", "약 {n}분 남음", "1분 이내", "완료! 감사합니다", "일부 결과가 전송되지 않았어요. 복사해서 보내 주세요.", "복사", "복사됨", "접근 코드가 없습니다. 받은 링크 전체를 열어 주세요.", "설정", "결과 (ms)", "추정", "모름", "목록에 없음", "어떤 아이폰인가요?", "모델 이름"]],
  ja: ["日本語", ["newmean-pii SDK 速度テスト", "開始", "5〜10分", "画面をオンのまま", "自動送信", "IPは保存しません", "残り時間を計算中", "残り約{n}分", "残り1分未満", "完了。ありがとうございます！", "一部の結果を送信できませんでした。コピーして送ってください。", "コピー", "コピーしました", "アクセスコードがありません。リンク全体を開いてください。", "設定", "結果 (ms)", "推定", "わからない", "リストにない", "どのiPhoneですか？", "機種名"]],
  zh: ["中文", ["newmean-pii SDK 速度测试", "开始", "5–10 分钟", "保持屏幕常亮", "自动发送", "不保存 IP", "正在估算剩余时间", "约剩 {n} 分钟", "不到 1 分钟", "完成，谢谢！", "部分结果未发送，请复制后发给我们。", "复制", "已复制", "缺少访问码，请打开完整链接。", "设置", "结果 (ms)", "推测", "不知道", "不在列表中", "哪款 iPhone？", "型号名称"]],
  es: ["Español", ["Prueba de velocidad del SDK newmean-pii", "Empezar", "5–10 min", "Pantalla encendida", "Envío automático", "Sin guardar IP", "Calculando…", "Quedan ~{n} min", "Queda < 1 min", "¡Listo, gracias!", "Algunos resultados no se enviaron. Cópialos y envíanoslos.", "Copiar", "Copiado", "Falta el código de acceso. Abre el enlace completo.", "Opciones", "Resultados (ms)", "estimado", "No lo sé", "No está en la lista", "¿Qué iPhone?", "Nombre del modelo"]],
  fr: ["Français", ["Test de vitesse du SDK newmean-pii", "Démarrer", "5–10 min", "Écran allumé", "Envoi auto", "Aucune IP enregistrée", "Estimation…", "Encore ~{n} min", "Moins d'1 min", "Terminé, merci !", "Certains résultats n'ont pas été envoyés. Copiez-les et envoyez-les-nous.", "Copier", "Copié", "Code d'accès manquant. Ouvrez le lien complet.", "Options", "Résultats (ms)", "estimé", "Je ne sais pas", "Absent de la liste", "Quel iPhone ?", "Nom du modèle"]],
  de: ["Deutsch", ["Geschwindigkeitstest des newmean-pii SDK", "Starten", "5–10 Min.", "Bildschirm an", "Automatisch gesendet", "Keine IP gespeichert", "Wird berechnet…", "Noch ca. {n} Min.", "Noch < 1 Min.", "Fertig. Danke!", "Einige Ergebnisse wurden nicht gesendet. Bitte kopieren und an uns schicken.", "Kopieren", "Kopiert", "Zugangscode fehlt. Bitte den vollständigen Link öffnen.", "Optionen", "Ergebnisse (ms)", "geschätzt", "Weiß nicht", "Nicht aufgeführt", "Welches iPhone?", "Modellname"]],
  pt: ["Português", ["Teste de velocidade do SDK newmean-pii", "Começar", "5–10 min", "Tela ligada", "Envio automático", "Sem guardar IP", "Calculando…", "Faltam ~{n} min", "Falta < 1 min", "Pronto, obrigado!", "Alguns resultados não foram enviados. Copie e envie para nós.", "Copiar", "Copiado", "Código de acesso ausente. Abra o link completo.", "Opções", "Resultados (ms)", "estimado", "Não sei", "Não está na lista", "Qual iPhone?", "Nome do modelo"]],
};

function pickLang(): string {
  try {
    const saved = localStorage.getItem("pii-bench-lang");
    if (saved && LANGS[saved]) return saved;
  } catch {}
  for (const l of navigator.languages ?? [navigator.language]) {
    const base = l.toLowerCase().split("-")[0];
    if (LANGS[base]) return base;
  }
  return "en";
}
let lang = pickLang();
const t = (k: Key, n?: number) => {
  const s = LANGS[lang][1][KEYS.indexOf(k)] ?? LANGS.en[1][KEYS.indexOf(k)];
  return n === undefined ? s : s.replace("{n}", String(n));
};

function applyLang() {
  document.documentElement.lang = lang;
  for (const el of document.querySelectorAll<HTMLElement>("[data-i18n]")) el.textContent = t(el.dataset.i18n as Key);
  fillModelSelect();
  refreshNote();
  if (progress.total) tick(0);
}

// ---- device --------------------------------------------------------------------------------------------------------------

let detected: { model: string; os: string; browser: string; app: string | null; screen: string; iphone: IphoneGuess | null; uaCH: Record<string, unknown> | null } = {
  model: "", os: "", browser: "", app: null, screen: "", iphone: null, uaCH: null,
};

// Best-effort device name: the Android model from User-Agent Client Hints where the browser gives it (Chromium), otherwise OS
// and browser from the user agent; on iPhone a model guess (iphoneGuess) that the tester can correct.
async function detectDevice() {
  const ua = navigator.userAgent;
  const nav = navigator as Navigator & { userAgentData?: { getHighEntropyValues(h: string[]): Promise<Record<string, any>> } };
  let model = "";
  let os = "";
  let uaCH: Record<string, unknown> | null = null;
  try {
    const h = await nav.userAgentData?.getHighEntropyValues(["model", "platform", "platformVersion"]);
    if (h && !/iPhone|iPad/.test(navigator.userAgent)) {
      uaCH = { model: h.model, platform: h.platform, platformVersion: h.platformVersion, mobile: h.mobile };
      model = String(h.model ?? "").trim();
      if (h.platform) os = `${h.platform} ${String(h.platformVersion ?? "").split(".")[0]}`.trim();
    }
  } catch {}
  const iphone = /iPhone/.test(ua);
  if (!os) {
    const m = ua.match(/Android [\d.]+/);
    if (m) os = m[0];
    else if (iphone) {
      // only Chrome on iOS reports the real version; Safari 26+ and Firefox freeze it
      const v = ua.match(/OS ([\d_]+) like Mac OS X/)?.[1].replace(/_/g, ".");
      os = /CriOS/.test(ua) && v ? `iOS ${v}` : "";
    } else if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) os = "iPad";
    else os = /Mac OS X/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows" : "";
  }
  if (!model && /Android/.test(ua)) {
    const m = ua.match(/Android [\d.]+; ([^;)]+)\)/);
    if (m && m[1] !== "K") model = m[1].replace(/\s*Build\/.*/, "").trim();
  }
  const app = IN_APP.find(([re]) => re.test(ua))?.[1] ?? null;
  const browser = app
    ? `${app} in-app`
    : (ua.match(/SamsungBrowser\/[\d]+/)?.[0].replace("SamsungBrowser/", "Samsung Internet ") ??
      ua.match(/(?:Edg|EdgA|EdgiOS)\/[\d]+/)?.[0].replace(/Edg\w*\//, "Edge ") ??
      ua.match(/(?:CriOS|Chrome)\/[\d]+/)?.[0].replace(/\w+\//, "Chrome ") ??
      ua.match(/(?:FxiOS|Firefox)\/[\d]+/)?.[0].replace(/\w+\//, "Firefox ") ??
      ua.match(/Version\/[\d.]+.*Safari/)?.[0].replace(/Version\/([\d]+).*/, "Safari $1") ??
      "");
  const screenTxt = `${screen.width}×${screen.height}@${Math.round(devicePixelRatio * 10) / 10}x`;
  detected = { model, os, browser, app, screen: screenTxt, iphone: iphone && !model ? iphoneGuess(app) : null, uaCH };
  return detected;
}

// Model shown and stored: on iPhone the tester's choice if any, else the guessed group.
function modelLabel() {
  if (!detected.iphone) return detected.model;
  const v = $<HTMLSelectElement>("model").value;
  if (v === "other") return ($<HTMLInputElement>("model-other").value || "").trim() || "iPhone (other)";
  if (v && v !== "unknown") return v;
  return detected.iphone.candidates.length ? groupLabel(detected.iphone.candidates) : "iPhone";
}

function deviceLabel() {
  const m = modelLabel();
  return [m || detected.os, m ? detected.os : "", detected.browser, m ? "" : detected.screen].filter(Boolean).join(" · ") || "unknown";
}

// Raw signals behind the guess, kept so the model can be re-derived later.
function signals() {
  const probe = document.createElement("div");
  probe.style.cssText = "position:fixed;visibility:hidden;padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left)";
  document.body.append(probe);
  const cs = getComputedStyle(probe);
  const safeArea = [cs.paddingTop, cs.paddingRight, cs.paddingBottom, cs.paddingLeft].map((x) => parseFloat(x) || 0);
  probe.remove();
  const vv = visualViewport;
  return {
    dpr: devicePixelRatio,
    screen: [screen.width, screen.height],
    avail: [screen.availWidth, screen.availHeight],
    inner: [innerWidth, innerHeight],
    outer: [outerWidth, outerHeight],
    client: [document.documentElement.clientWidth, document.documentElement.clientHeight],
    visualViewport: vv ? [vv.width, vv.height, vv.scale] : null,
    orientation: screen.orientation?.type ?? null,
    safeArea,
    platform: navigator.platform,
    maxTouchPoints: navigator.maxTouchPoints,
    uaCH: detected.uaCH,
  };
}

function device() {
  const n = navigator as Navigator & { deviceMemory?: number; gpu?: unknown };
  const sel = detected.iphone ? $<HTMLSelectElement>("model").value : "";
  return {
    label: deviceLabel(),
    model: modelLabel() || null,
    iphone: detected.iphone
      ? { ...detected.iphone, userChoice: sel || null, userConfirmed: !!sel, other: sel === "other" ? $<HTMLInputElement>("model-other").value.trim() : null, iosMajorFromSafari: Number(navigator.userAgent.match(/Version\/(\d+)[\d.]*.*Safari/)?.[1]) || null }
      : null,
    ua: navigator.userAgent,
    lang: navigator.language,
    screen: `${screen.width}x${screen.height}@${Math.round(devicePixelRatio * 10) / 10}x`,
    signals: signals(),
    cores: navigator.hardwareConcurrency ?? null,
    memoryGB: n.deviceMemory ?? null,
    relaxedSimd: relaxed,
    webgpu: "gpu" in n,
  };
}

// iPhone model dropdown: the guess (unconfirmed) first, then the matching models, every model newest first, don't know, other.
function fillModelSelect() {
  const g = detected.iphone;
  if (!g) return;
  const sel = $<HTMLSelectElement>("model");
  const keep = sel.value;
  const opt = (label: string, value: string) => new Option(label, value);
  const group = (label: string, names: string[]) => {
    const og = document.createElement("optgroup");
    og.label = label;
    og.append(...names.map((n) => opt(n, n)));
    return og;
  };
  sel.replaceChildren(
    opt(g.candidates.length ? `${groupLabel(g.candidates)} (${t("guess")})` : t("pickModel"), ""),
    ...(g.candidates.length > 1 ? [group(t("guess"), g.candidates)] : []),
    group("iPhone", IPHONE_MODELS.map(([n]) => n)),
    opt(t("unknownModel"), "unknown"),
    opt(t("otherModel"), "other"),
  );
  sel.value = keep;
  $<HTMLInputElement>("model-other").placeholder = t("otherPh");
}

// ---- progress ------------------------------------------------------------------------------------------------------------

// Progress in work units: detect calls weighted by tokens and by a per-size cost (iPhone 16 Pro, 1000-token latency relative to
// nano), plus a fixed share for download and init. The remaining-time estimate is elapsed time per unit so far.
const SIZE_COST: Record<string, number> = { nano: 1, small: 1.15, medium: 1.85, large: 1.9 };
const SETUP_UNITS = 2000;
const TEXT_UNITS = 40 + TEXTS.reduce((n, x) => n + (2 + RUNS[x.name]) * x.tokens, 0);
const progress = { total: 0, done: 0, started: 0, finished: false };
const comboUnits = (size: string) => SIZE_COST[size] * (SETUP_UNITS + TEXT_UNITS + checkUnits());
const RING = 2 * Math.PI * 52;

function tick(units: number) {
  progress.done = Math.min(progress.total, progress.done + units);
  const frac = progress.total ? progress.done / progress.total : 0;
  $("ring").style.strokeDashoffset = String(RING * (1 - frac));
  $("pct").textContent = `${Math.floor(frac * 100)}%`;
  const elapsed = (performance.now() - progress.started) / 1000;
  let eta = t("estimating");
  if (progress.finished) eta = "";
  else if (progress.done > 0 && elapsed > 20) {
    const left = (elapsed * (progress.total - progress.done)) / progress.done;
    eta = left < 60 ? t("lessMin") : t("left", Math.ceil(left / 60));
  }
  $("eta").textContent = eta;
}

function setStep(i: number, state: "active" | "ok" | "fail") {
  $("steps").children[i]?.setAttribute("data-state", state);
}

function setPhase(phase: Phase, size: Size, engine: EngineName, tokens?: number) {
  $("now").dataset.phase = phase;
  $("now-text").textContent = `${size} · ${engine}${tokens ? ` · ${tokens} tok` : ""}`;
}

// ---- benchmark -----------------------------------------------------------------------------------------------------------

const median = (xs: number[]) => xs.sort((a, b) => a - b)[Math.floor(xs.length / 2)];
const r1 = (x: number) => Math.round(x * 10) / 10;
const spanStr = (s: { start: number; end: number; label: string }[]) => s.map((y) => `${y.start}-${y.end}:${y.label}`).join(" ");

async function fetchBytes(url: string) {
  const res = await fetch(KEY ? `${url}?k=${encodeURIComponent(KEY)}` : url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return new Uint8Array(await res.arrayBuffer());
}

async function runOne(engine: EngineName, size: Size): Promise<Run> {
  const variant = DEFAULT_VARIANT[size];
  const base = `models/pii-${size}/`;
  const cost = SIZE_COST[size];
  setPhase("download", size, engine);
  const t0 = performance.now();
  const files = new Map<string, Uint8Array>([
    [base + "tokenizer.web.json", await fetchBytes(base + "tokenizer.web.json")],
    [base + `onnx/model_${variant}.onnx`, await fetchBytes(base + `onnx/model_${variant}.onnx`)],
  ]);
  const downloadMs = performance.now() - t0;
  tick((SETUP_UNITS / 2) * cost);
  const memFetch = (async (input: RequestInfo | URL) => {
    const b = files.get(String(input));
    if (!b) throw new Error(`not prefetched: ${String(input)}`);
    return new Response(b.slice().buffer);
  }) as typeof fetch;

  setPhase("init", size, engine);
  const t1 = performance.now();
  const r = await createRedactor({ engine, size, variant, modelUrl: base, fetch: memFetch, cache: false, verify: false, regex: false, wasmPaths: new URL("ort/", document.baseURI).href, threads: 1 });
  const initMs = performance.now() - t1;
  const t2 = performance.now();
  await r.detect(TEXTS[0].text);
  const firstMs = performance.now() - t2;
  tick((SETUP_UNITS / 2 + 40) * cost);

  const latency: Record<string, number> = {};
  const spans: Record<string, string> = {};
  let stable = true; // every timed run of a bench text gave the same spans
  for (const x of TEXTS) {
    setPhase("run", size, engine, x.tokens);
    for (let i = 0; i < 2; i++) {
      await r.detect(x.text);
      tick(x.tokens * cost);
    }
    const ms: number[] = [];
    for (let i = 0; i < RUNS[x.name]; i++) {
      const a = performance.now();
      const s = await r.detect(x.text);
      ms.push(performance.now() - a);
      tick(x.tokens * cost);
      const str = spanStr(s);
      if (i > 0 && str !== spans[x.name]) stable = false;
      spans[x.name] = str;
    }
    latency[x.name] = r1(median(ms));
  }

  // consistency: spans on the check set against the desktop reference, and a second pass on the first CHECK_REPEAT texts
  let consistency: Record<string, unknown> | undefined;
  let checkSpans: string[] | undefined;
  if (CHECK) {
    setPhase("check", size, engine, CHECK.set.length);
    const got: string[] = [];
    for (const x of CHECK.set) {
      got.push(spanStr(await r.detect(x.text)));
      tick(textUnits(x.text) * cost);
    }
    let repeatSame = 0;
    for (let i = 0; i < Math.min(CHECK_REPEAT, CHECK.set.length); i++) {
      if (spanStr(await r.detect(CHECK.set[i].text)) === got[i]) repeatSame++;
      tick(textUnits(CHECK.set[i].text) * cost);
    }
    checkSpans = got;
    const ref = CHECK.ref?.[size];
    consistency = { n: got.length, repeat: Math.min(CHECK_REPEAT, got.length), repeatSame, refDevice: CHECK.refDevice ?? null };
    if (ref) {
      let agree = 0, refSpans = 0, matched = 0, extra = 0;
      const diffs: { id: string; ref: string; got: string }[] = [];
      got.forEach((g, i) => {
        const a = new Set(ref[i] ? ref[i].split(" ") : []), b = new Set(g ? g.split(" ") : []);
        refSpans += a.size;
        for (const x of b) a.has(x) ? matched++ : extra++;
        if (g === ref[i]) agree++;
        else if (diffs.length < 10) diffs.push({ id: CHECK!.set[i].id, ref: ref[i], got: g });
      });
      Object.assign(consistency, { agree, refSpans, matched, extra, diffs });
    }
  }

  const engineUsed = engine === "builtin" ? (relaxed ? "builtin (relaxed SIMD)" : "builtin (SIMD128)") : "onnxruntime-web 1.31.1";
  await r.dispose();
  return { engine, engineUsed, size, variant, downloadMs: Math.round(downloadMs), initMs: r1(initMs), firstMs: r1(firstMs), latency, spans, stable, consistency, ...(REF ? { checkSpans } : {}) };
}

// ---- page ----------------------------------------------------------------------------------------------------------------

const rows: Run[] = [];
let unsent = 0;

function render() {
  const body = $("rows");
  body.replaceChildren();
  for (const r of rows) {
    const tr = document.createElement("tr");
    const lat = (r.latency ?? {}) as Record<string, number>;
    const cells = [
      String(r.size ?? ""),
      String(r.engine ?? ""),
      r.error ? "—" : `${r.initMs}`,
      r.error ? "—" : `${lat.short ?? "—"}`,
      r.error ? "—" : `${lat.window ?? "—"}`,
      r.error ? "—" : `${lat.multi ?? "—"}`,
      r.error ? "⚠" : r.parity === undefined ? "" : r.parity ? "=" : "≠",
      r.error ? "" : (c => (c?.agree === undefined ? "" : `${c.agree}/${c.n}`))(r.consistency as any),
    ];
    cells.forEach((c, i) => {
      const td = document.createElement("td");
      td.textContent = c;
      if (i >= 2) td.className = "num";
      if (r.error && i === 6) td.title = String(r.error);
      tr.append(td);
    });
    body.append(tr);
  }
}

function refreshNote() {
  const note = $("note");
  note.hidden = !unsent;
  note.textContent = unsent ? t("unsent") : "";
  $("copy").hidden = !unsent;
}

async function save(run: Run) {
  rows.push(run);
  render();
  try {
    const res = await fetch("/api/bench", { method: "POST", headers: { "content-type": "application/json", ...(KEY ? { "x-bench-key": KEY } : {}) }, body: JSON.stringify(run) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return true;
  } catch {
    unsent++;
    refreshNote();
    $<HTMLDetailsElement>("results").open = true;
    return false;
  }
}

// Background tracking: iOS and Android suspend a page that is not visible.
const MAX_RETRIES = 2;
const away = { count: 0, ms: 0 };
let hiddenSince = 0;
let lock: any = null;
const wakeLock = async () => {
  try {
    lock = await (navigator as any).wakeLock?.request("screen");
  } catch {}
};
const visible = () => (document.hidden ? new Promise<void>((ok) => document.addEventListener("visibilitychange", () => ok(), { once: true })) : Promise.resolve());
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    away.count++;
    hiddenSince = performance.now();
  } else {
    if (hiddenSince) away.ms += performance.now() - hiddenSince;
    hiddenSince = 0;
    // the screen wake lock is released while hidden
    if (document.body.dataset.state === "running") wakeLock();
  }
});

async function runAll() {
  const btn = $<HTMLButtonElement>("run");
  btn.disabled = true;
  await wakeLock();
  const sizes = SIZES.filter((s) => $<HTMLInputElement>(`size-${s}`).checked);
  const engines = REF ? (["onnxruntime"] as EngineName[]) : ENGINES.filter((e) => $<HTMLInputElement>(`engine-${e}`).checked);
  const dev = device();
  const session = new Date().toISOString();
  const count = sizes.length * engines.length;
  try {
    CHECK = JSON.parse(new TextDecoder().decode(await fetchBytes("models/consistency.json")));
  } catch {
    CHECK = null;
  }

  $("steps").replaceChildren(...Array.from({ length: count }, () => document.createElement("i")));
  document.body.dataset.state = "running";
  progress.total = sizes.reduce((n, s) => n + comboUnits(s), 0) * engines.length;
  progress.done = 0;
  progress.started = performance.now();
  progress.finished = false;
  tick(0);

  const bySize: Record<string, Record<string, string>> = {};
  let i = 0;
  for (const size of sizes) {
    for (const engine of engines) {
      setStep(i, "active");
      const at = progress.done;
      let ok = false;
      try {
        // a combination during which the page went to the background is timed again (up to MAX_RETRIES times): the
        // browser suspends the page, so its download and latency numbers would include the time away
        let run: Run;
        let retries = 0;
        for (;;) {
          const seen = away.count;
          let failed: unknown = null;
          try {
            run = await runOne(engine, size);
          } catch (e) {
            failed = e;
          }
          const interrupted = away.count !== seen || !!hiddenSince;
          if (!interrupted || retries === MAX_RETRIES) {
            if (failed) throw failed;
            run!.interrupted = interrupted;
            run!.retries = retries;
            break;
          }
          retries++;
          await visible();
          tick(at - progress.done);
        }
        const prev = bySize[size];
        if (prev) run.parity = JSON.stringify(prev) === JSON.stringify(run.spans);
        else bySize[size] = run.spans as Record<string, string>;
        if (REF) {
          ((window as any).__ref ??= { refDevice: `${dev.label} · ${run.engineUsed}`, ref: {} }).ref[size] = run.checkSpans;
          ok = true;
        } else ok = await save({ ...run, anim: ANIM, device: dev, session });
      } catch (e) {
        await save({ engine, size, device: dev, session, error: String((e as Error)?.message ?? e) });
      }
      setStep(i++, ok ? "ok" : "fail");
      tick(at + comboUnits(size) - progress.done);
    }
  }
  progress.finished = true;
  tick(progress.total);
  document.body.dataset.state = "done";
  try {
    await lock?.release();
  } catch {}
  btn.disabled = false;
}

// Short text for a messenger: one line per run, no spans (span parity is reported as same/differs).
function summary(): string {
  if (!rows.length) return "";
  const d = rows[0].device as ReturnType<typeof device>;
  const head = [`PII Phone Bench · ${d.label}`, `${d.cores ?? "?"} cores · relaxed SIMD ${d.relaxedSimd ? "yes" : "no"} · screen ${d.screen}`, d.ua];
  const lines = rows.map((r) => {
    const lat = (r.latency ?? {}) as Record<string, number>;
    if (r.error) return `${r.size} ${r.engine}: ERROR ${String(r.error).slice(0, 120)}`;
    const par = r.parity === undefined ? "" : r.parity ? " same" : " DIFFERS";
    return `${r.size} ${r.engine}: init ${r.initMs} | ${lat.short} / ${lat.window} / ${lat.multi} ms${par}`;
  });
  return [...head, "size engine: init | 40 / 252 / 1000 tokens", ...lines].join("\n");
}

async function copyResults() {
  const text = summary();
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    $("copy-label").textContent = t("copied");
  } catch {
    const ta = $<HTMLTextAreaElement>("dump");
    ta.hidden = false;
    ta.value = text;
    ta.select();
  }
}

async function start() {
  const sel = $<HTMLSelectElement>("lang");
  sel.replaceChildren(...Object.entries(LANGS).map(([code, [name]]) => new Option(name, code, false, code === lang)));
  sel.addEventListener("change", () => {
    lang = sel.value;
    try {
      localStorage.setItem("pii-bench-lang", lang);
    } catch {}
    applyLang();
  });

  await detectDevice();
  const showName = () => ($("devname").textContent = detected.iphone ? [detected.os, detected.browser].filter(Boolean).join(" · ") : deviceLabel());
  if (detected.iphone) {
    const sel = $<HTMLSelectElement>("model");
    sel.hidden = false;
    sel.addEventListener("change", () => {
      $("model-other").hidden = sel.value !== "other";
      sel.classList.toggle("set", !!sel.value);
    });
  }
  showName();
  const d = device();
  $("devinfo").textContent = `${d.label} · ${d.cores ?? "?"} cores · ${d.memoryGB ? `${d.memoryGB} GB` : "? GB"} · relaxed SIMD ${d.relaxedSimd ? "yes" : "no"} · WebGPU ${d.webgpu ? "yes" : "no"}`;

  $("run").addEventListener("click", runAll);
  $("copy").addEventListener("click", copyResults);
  applyLang();
  if (REF) runAll();
}
start();
