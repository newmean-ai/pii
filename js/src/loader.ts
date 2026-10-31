// Downloads model files once, keeps them in the Cache API, and checks each file's SHA-256 against the pinned value.
import type { FileInfo } from "./models.js";

export interface Progress {
  file: string;
  loaded: number;
  total: number;
  /** true when the file came from the browser cache */
  cached: boolean;
}

export interface LoadOptions {
  cache: boolean;
  verify: boolean;
  fetch: typeof fetch;
  onProgress?: (p: Progress) => void;
}

const CACHE_NAME = "newmean-pii-models";

async function sha256(bytes: Uint8Array): Promise<string | null> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return null; // not a secure context: hashing unavailable
  const d = new Uint8Array(await subtle.digest("SHA-256", bytes as BufferSource));
  return Array.from(d, (b) => b.toString(16).padStart(2, "0")).join("");
}

async function readBody(res: Response, name: string, total: number, opts: LoadOptions): Promise<Uint8Array> {
  if (!res.body || !opts.onProgress) return new Uint8Array(await res.arrayBuffer());
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let loaded = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.length;
    opts.onProgress({ file: name, loaded, total, cached: false });
  }
  const out = new Uint8Array(loaded);
  let o = 0;
  for (const c of chunks) {
    out.set(c, o);
    o += c.length;
  }
  return out;
}

export async function loadFile(url: string, name: string, info: FileInfo | undefined, opts: LoadOptions): Promise<Uint8Array> {
  const cache = opts.cache && typeof caches !== "undefined" ? await caches.open(CACHE_NAME).catch(() => null) : null;
  const check = async (bytes: Uint8Array) => {
    if (!opts.verify || !info) return true;
    if (bytes.length !== info.bytes) return false;
    const h = await sha256(bytes);
    if (h === null) {
      console.warn("[@newmean-ai/pii] crypto.subtle is unavailable (insecure context); model files are not verified");
      return true;
    }
    return h === info.sha256;
  };

  if (cache) {
    const hit = await cache.match(url).catch(() => undefined);
    if (hit) {
      const bytes = new Uint8Array(await hit.arrayBuffer());
      if (await check(bytes)) {
        opts.onProgress?.({ file: name, loaded: bytes.length, total: bytes.length, cached: true });
        return bytes;
      }
      await cache.delete(url);
    }
  }

  const res = await opts.fetch(url);
  if (!res.ok) throw new Error(`[@newmean-ai/pii] failed to download ${url}: HTTP ${res.status}`);
  const total = Number(res.headers.get("content-length")) || info?.bytes || 0;
  const bytes = await readBody(res, name, total, opts);
  if (!(await check(bytes))) {
    throw new Error(`[@newmean-ai/pii] ${url} does not match the published model file (size or SHA-256 differs)`);
  }
  if (cache) {
    await cache
      .put(url, new Response(bytes as BodyInit, { headers: { "content-type": "application/octet-stream" } }))
      .catch(() => undefined); // quota or private mode: keep working without the cache
  }
  return bytes;
}
