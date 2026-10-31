import * as ort from "onnxruntime-web/wasm";
import { createRedactor, DEFAULT_VARIANT, type EngineName, LABELS, LEVEL1_LABELS, MODEL_FILES, MODEL_VERSION, type Redactor, type Size, type Span, type Variant } from "../src/index";
import { SAMPLES } from "./samples";

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const SIZES: Size[] = ["nano", "small", "medium", "large"];
const MB = (b: number) => `${(b / 1e6).toFixed(1)} MB`;
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

const modelUrl = (size: Size) => new URL(`./models/pii-${size}/`, location.href).href;

const loaded = new Map<string, Redactor>();
let current: Redactor | null = null;

function settings() {
  return {
    sensitivity: Number($<HTMLInputElement>("sens").value),
    labels: $<HTMLInputElement>("l2").checked ? LABELS : LEVEL1_LABELS,
    regex: $<HTMLInputElement>("regex").checked,
    engine: $<HTMLSelectElement>("engine").value as EngineName,
  };
}

function setStatus(msg: string, cls = "") {
  const el = $("status");
  el.textContent = msg;
  el.className = `status ${cls}`;
}

async function getRedactor(size: Size, variant: Variant, showProgress: boolean): Promise<Redactor> {
  const s = settings();
  const key = `${s.engine}/${size}/${variant}/${s.regex}/${s.labels.length}`;
  let r = loaded.get(key);
  if (!r) {
    const bytes: Record<string, [number, number, boolean]> = {};
    const t0 = performance.now();
    r = await createRedactor({
      engine: s.engine,
      size,
      variant,
      modelUrl: modelUrl(size),
      sensitivity: s.sensitivity,
      labels: s.labels,
      regex: s.regex,
      onProgress: showProgress
        ? (p) => {
            bytes[p.file] = [p.loaded, p.total, p.cached];
            const done = Object.values(bytes).reduce((a, [l]) => a + l, 0);
            const total = MODEL_FILES[size][variant].bytes + MODEL_FILES[size].tokenizer.bytes;
            $("bar").style.width = `${Math.min(100, (100 * done) / total)}%`;
            setStatus(`내려받는 중… ${MB(done)} / ${MB(total)}`);
          }
        : undefined,
    });
    const ms = performance.now() - t0;
    const fromCache = Object.values(bytes).length > 0 && Object.values(bytes).every(([, , c]) => c);
    if (showProgress) {
      $("bar").style.width = "100%";
      setStatus(
        `pii-${size} (${variant}, ${s.engine}) 준비 완료 — ${MB(MODEL_FILES[size][variant].bytes)} + 토크나이저 ${MB(MODEL_FILES[size].tokenizer.bytes)}, ` +
          `${(ms / 1000).toFixed(2)}초${fromCache ? " (브라우저 캐시에서)" : ""}. SHA-256 확인 통과.`,
        "ok",
      );
    }
    loaded.set(key, r);
  }
  r.setSensitivity(settings().sensitivity);
  return r;
}

function variantOf(size: Size): Variant {
  return ($<HTMLSelectElement>("variant").value as Variant) || DEFAULT_VARIANT[size];
}

async function load() {
  const size = $<HTMLSelectElement>("size").value as Size;
  $<HTMLButtonElement>("load").disabled = true;
  try {
    current = await getRedactor(size, variantOf(size), true);
    await run();
  } catch (e) {
    setStatus(String(e), "err");
  } finally {
    $<HTMLButtonElement>("load").disabled = false;
  }
}

function highlight(text: string, spans: Span[]): string {
  let out = "";
  let last = 0;
  for (const s of spans) {
    out += esc(text.slice(last, s.start));
    out += `<mark title="${s.label} · ${s.source}">${esc(text.slice(s.start, s.end))}<sup>${s.label}</sup></mark>`;
    last = s.end;
  }
  return out + esc(text.slice(last));
}

async function run() {
  if (!current) return;
  const text = $<HTMLTextAreaElement>("input").value;
  const mode = $<HTMLSelectElement>("mode").value as "tag" | "mask" | "blank";
  const t0 = performance.now();
  const { text: red, spans } = await current.redact(text, { mode });
  const total = performance.now() - t0;
  const st = current.lastRun!;
  $("highlight").innerHTML = highlight(text, spans) || '<span class="hint">(빈 입력)</span>';
  $("redacted").textContent = red;
  $("spans").innerHTML =
    "<tr><th>텍스트</th><th>라벨</th><th>찾은 쪽</th><th>위치</th></tr>" +
    spans.map((s) => `<tr><td>${esc(text.slice(s.start, s.end))}</td><td>${s.label}</td><td>${s.source}</td><td>${s.start}–${s.end}</td></tr>`).join("");
  $("stats").innerHTML =
    `<span>모델 <b>pii-${current.size} ${current.variant}</b></span><span>엔진 <b>${current.engine}</b></span>` +
    `<span>글자 <b>${text.length}</b></span><span>토큰 <b>${st.tokens}</b></span><span>창 <b>${st.windows}</b></span>` +
    `<span>모델 실행 <b>${st.ms.toFixed(1)} ms</b></span><span>전체 <b>${total.toFixed(1)} ms</b></span><span>스팬 <b>${spans.length}</b></span>`;
}

async function bench() {
  if (!current) return;
  const text = $<HTMLTextAreaElement>("input").value;
  const times: number[] = [];
  await current.detect(text); // warm-up
  for (let i = 0; i < 20; i++) {
    const t0 = performance.now();
    await current.detect(text);
    times.push(performance.now() - t0);
  }
  times.sort((a, b) => a - b);
  setStatus(
    `속도 (pii-${current.size} ${current.variant}, ${current.engine}, 토큰 ${current.lastRun!.tokens}개, 20회): 중앙값 ${times[10].toFixed(1)} ms, ` +
      `최소 ${times[0].toFixed(1)} ms, 최대 ${times[19].toFixed(1)} ms`,
    "ok",
  );
}

async function compare() {
  const text = $<HTMLTextAreaElement>("input").value;
  const mode = $<HTMLSelectElement>("mode").value as "tag" | "mask" | "blank";
  $("cmpbox").hidden = false;
  const rows: string[] = [];
  $("cmp").innerHTML = "<tr><td>불러오는 중…</td></tr>";
  for (const size of SIZES) {
    for (const variant of ["q4", "q4e"] as Variant[]) {
      const r = await getRedactor(size, variant, false);
      await r.detect(text);
      const times: number[] = [];
      let res = { text: "", spans: [] as Span[] };
      for (let i = 0; i < 5; i++) {
        const t0 = performance.now();
        res = await r.redact(text, { mode });
        times.push(performance.now() - t0);
      }
      times.sort((a, b) => a - b);
      rows.push(
        `<tr><td>${size}${variant === DEFAULT_VARIANT[size] ? "" : ""}</td><td>${variant}${variant === DEFAULT_VARIANT[size] ? " (기본)" : ""}</td>` +
          `<td class="num">${MB(MODEL_FILES[size][variant].bytes)}</td><td class="num">${times[2].toFixed(1)} ms</td>` +
          `<td class="num">${res.spans.length}</td><td>${highlight(text, res.spans)}</td></tr>`,
      );
      $("cmp").innerHTML = "<tr><th>크기</th><th>가중치</th><th>파일</th><th>시간(중앙값)</th><th>스팬</th><th>결과</th></tr>" + rows.join("");
    }
  }
}

function env() {
  const gpu = "gpu" in navigator ? "있음" : "없음";
  $("env").innerHTML =
    `<span>패키지 모델 버전 <b>${MODEL_VERSION}</b></span><span>onnxruntime-web <b>${ort.env.versions?.web ?? "?"}</b></span>` +
    `<span>백엔드 <b>wasm</b></span><span>스레드 <b>${crossOriginIsolated ? "여러 개 가능" : "1 (교차 출처 격리 아님)"}</b></span>` +
    `<span>WebGPU <b>${gpu}</b></span><span>CPU 코어 <b>${navigator.hardwareConcurrency ?? "?"}</b></span>` +
    `<span>브라우저 <b>${esc(navigator.userAgent)}</b></span>`;
}

// --- wiring ---
for (const s of SAMPLES) {
  const b = document.createElement("button");
  b.textContent = s.name;
  b.onclick = () => {
    $<HTMLTextAreaElement>("input").value = s.text;
    run();
  };
  $("samples").append(b);
}
$<HTMLTextAreaElement>("input").value = SAMPLES[0].text;

let timer = 0;
$("input").addEventListener("input", () => {
  if (!$<HTMLInputElement>("live").checked) return;
  clearTimeout(timer);
  timer = window.setTimeout(run, 150);
});
$("load").onclick = load;
$("run").onclick = run;
$("bench").onclick = bench;
$("compare").onclick = compare;
$("clear").onclick = async () => {
  await caches.delete("newmean-pii-models");
  setStatus("브라우저 캐시를 비웠습니다. 다음에 불러올 때 다시 내려받습니다.");
};
$("sens").addEventListener("input", () => {
  $("sensv").textContent = $<HTMLInputElement>("sens").value;
  current?.setSensitivity(settings().sensitivity);
  run();
});
for (const id of ["l2", "regex"]) $(id).addEventListener("change", () => current && load());
$("mode").addEventListener("change", run);
$("size").addEventListener("change", load);
$("variant").addEventListener("change", load);
$("engine").addEventListener("change", load);
env();
load();
