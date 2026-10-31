// Loads the published model_q4/q4e.onnx files directly (no .piiw step): a minimal protobuf reader for the parts of
// ModelProto the engine needs, and a port of export_wasm_weights.py that lays the initializers out as a .piiw image
// in memory. Same checks as the Python converter: anything unexpected throws.

type Bytes = Uint8Array;

interface Tensor {
  name: string;
  dims: number[];
  dataType: number;
  raw: Bytes; // raw_data (or float_data / int64_data re-encoded)
}
interface Attr {
  name: string;
  f?: number;
  i?: number;
  t?: Tensor;
}
interface Node {
  opType: string;
  inputs: string[];
  outputs: string[];
  attrs: Map<string, Attr>;
}

const dec = new TextDecoder();
/** A copy in a fresh, aligned buffer (input may be a Node Buffer view, where slice() does not copy). */
const copy = (b: Bytes) => new Uint8Array(b);

class Reader {
  pos: number;
  readonly b: Bytes;
  readonly end: number;
  constructor(b: Bytes, start = 0, end = b.length) {
    this.b = b;
    this.pos = start;
    this.end = end;
  }
  varint(): number {
    let x = 0;
    let mul = 1;
    for (;;) {
      const c = this.b[this.pos++];
      x += (c & 0x7f) * mul;
      if (c < 0x80) return x;
      mul *= 128;
    }
  }
  /** Signed int64 varint (negative values come as 10-byte two's complement). */
  int64(): number {
    const x = this.varint();
    return x >= 2 ** 63 ? x - 2 ** 64 : x;
  }
  /** Calls fn(field, wireType, reader for length-delimited fields) for every field; skips what fn ignores. */
  fields(fn: (field: number, wire: number, sub: Reader | null) => void) {
    while (this.pos < this.end) {
      const key = this.varint();
      const field = Math.floor(key / 8);
      const wire = key & 7;
      if (wire === 2) {
        const len = this.varint();
        const sub = new Reader(this.b, this.pos, this.pos + len);
        this.pos += len;
        fn(field, wire, sub);
      } else {
        const at = this.pos;
        fn(field, wire, null);
        if (this.pos === at) this.skip(wire); // fn did not read the value
      }
    }
  }
  skip(wire: number) {
    if (wire === 0) this.varint();
    else if (wire === 1) this.pos += 8;
    else if (wire === 5) this.pos += 4;
    else throw new Error(`[@newmean-ai/pii] protobuf wire type ${wire}`);
  }
  bytes(): Bytes {
    return this.b.subarray(this.pos, this.end);
  }
  str(): string {
    return dec.decode(this.bytes());
  }
}

function packedInts(r: Reader, out: number[]) {
  while (r.pos < r.end) out.push(r.int64());
}

function readTensor(r: Reader): Tensor {
  const t: Tensor = { name: "", dims: [], dataType: 0, raw: new Uint8Array(0) };
  const floats: number[] = [];
  const ints: number[] = [];
  const i32: number[] = [];
  r.fields((f, w, s) => {
    if (f === 1) {
      if (s) packedInts(s, t.dims);
      else t.dims.push(r.int64());
    } else if (f === 2) t.dataType = r.varint();
    else if (f === 8 && s) t.name = s.str();
    else if (f === 9 && s) t.raw = s.bytes();
    else if (f === 4 && s) for (let p = s.pos; p < s.end; p += 4) floats.push(new DataView(s.b.buffer, s.b.byteOffset + p, 4).getFloat32(0, true));
    else if (f === 5) {
      // int32_data: also holds uint8 / int8 / int4 values when raw_data is not used
      if (s) packedInts(s, i32);
      else i32.push(r.int64());
    } else if (f === 7) {
      if (s) packedInts(s, ints);
      else ints.push(r.int64());
    } else if (f === 13 || (f === 14 && r.varint() === 1)) throw new Error("[@newmean-ai/pii] external tensor data is not supported");
  });
  if (!t.raw.length && floats.length) t.raw = new Uint8Array(new Float32Array(floats).buffer);
  if (!t.raw.length && i32.length && t.dataType === UINT8) t.raw = Uint8Array.from(i32);
  else if (!t.raw.length && i32.length) throw new Error(`[@newmean-ai/pii] ${t.name}: int32_data of type ${t.dataType} is not supported`);
  if (!t.raw.length && ints.length) t.raw = new Uint8Array(new BigInt64Array(ints.map(BigInt)).buffer);
  return t;
}

function readNode(r: Reader): Node {
  const n: Node = { opType: "", inputs: [], outputs: [], attrs: new Map() };
  r.fields((f, _w, s) => {
    if (!s) return;
    if (f === 1) n.inputs.push(s.str());
    else if (f === 2) n.outputs.push(s.str());
    else if (f === 4) n.opType = s.str();
    else if (f === 5) {
      const a: Attr = { name: "" };
      s.fields((g, _w2, s2) => {
        if (g === 1 && s2) a.name = s2.str();
        else if (g === 2) {
          a.f = new DataView(s.b.buffer, s.b.byteOffset + s.pos, 4).getFloat32(0, true);
          s.pos += 4;
        } else if (g === 3) a.i = s.int64();
        else if (g === 5 && s2) a.t = readTensor(s2);
      });
      n.attrs.set(a.name, a);
    }
  });
  return n;
}

function readGraph(model: Bytes): { nodes: Node[]; inits: Map<string, Tensor> } {
  const nodes: Node[] = [];
  const inits = new Map<string, Tensor>();
  new Reader(model).fields((f, _w, s) => {
    if (f !== 7 || !s) return; // ModelProto.graph
    s.fields((g, _w2, s2) => {
      if (!s2) return;
      if (g === 1) nodes.push(readNode(s2));
      else if (g === 5) {
        const t = readTensor(s2);
        inits.set(t.name, t);
      }
    });
  });
  return { nodes, inits };
}

// ---------------------------------------------------------------- ONNX -> .piiw image (export_wasm_weights.py)
const ROLE = /layer\.(\d+)\/(?:block\/)?(attention\/self\/(?:query|key|value)|attention\/output\/dense|intermediate\/dense|output\/dense)(?:_\d+)?\/MatMul/;
const ROLES = ["attention/self/query", "attention/self/key", "attention/self/value", "attention/output/dense", "intermediate/dense", "output/dense"];
const NAMES: Record<string, string> = {
  "attention/self/query": "attention.self.query",
  "attention/self/key": "attention.self.key",
  "attention/self/value": "attention.self.value",
  "attention/output/dense": "attention.output.dense",
  "intermediate/dense": "intermediate.dense",
  "output/dense": "output.dense",
};
const INT4 = 22;
const UINT8 = 2;
const FLOAT = 1;

interface Mat {
  b: Tensor;
  s: Tensor;
  K: number;
  N: number;
}

class Out {
  parts: Bytes[] = [];
  size = 0;
  add(b: Bytes) {
    const pad = -this.size & 15;
    if (pad) this.push(new Uint8Array(pad));
    this.push(b);
  }
  private push(b: Bytes) {
    this.parts.push(b);
    this.size += b.length;
  }
  f32(a: ArrayLike<number>) {
    this.add(new Uint8Array(Float32Array.from(a).buffer));
  }
  join(): Bytes {
    const pad = -this.size & 15;
    const out = new Uint8Array(this.size + pad);
    let o = 0;
    for (const p of this.parts) {
      out.set(p, o);
      o += p.length;
    }
    return out;
  }
}

const f32view = (t: Tensor) => {
  if (t.raw.length % 4) throw new Error(`[@newmean-ai/pii] ${t.name}: ${t.raw.length} bytes, type ${t.dataType}, dims ${t.dims}`);
  return new Float32Array(copy(t.raw).buffer); // copy: raw_data may be unaligned
};

/** MatMulNBits B [N, K/32, 16] + scales -> panel layout (8 columns x 32 rows per block, signed nibbles q-8). */
function q4Matrix(out: Out, m: Mat) {
  const { K, N } = m;
  const nb = K / 32;
  if (K % 32 || m.b.raw.length !== N * nb * 16) throw new Error(`[@newmean-ai/pii] MatMulNBits shape ${m.b.dims}`);
  const sc = f32view(m.s);
  const Np = Math.ceil(N / 8) * 8;
  const res = new Uint8Array((Np / 8) * nb * 160);
  const sv = new Float32Array(res.buffer);
  const B = m.b.raw;
  let o = 0;
  for (let p = 0; p < Np / 8; p++) {
    for (let b = 0; b < nb; b++, o += 160) {
      for (let j = 0; j < 8; j++) {
        const n = p * 8 + j;
        sv[o / 4 + j] = n < N ? sc[n * nb + b] : 0;
      }
      // byte j of row k: column j (low nibble), column j+4 (high nibble); value q-8 as a signed nibble = q ^ 8
      for (let j = 0; j < 4; j++) {
        const n0 = p * 8 + j;
        const n1 = n0 + 4;
        const s0 = n0 < N ? (n0 * nb + b) * 16 : -1;
        const s1 = n1 < N ? (n1 * nb + b) * 16 : -1;
        for (let i = 0; i < 16; i++) {
          const b0 = s0 >= 0 ? B[s0 + i] ^ 0x88 : 0;
          const b1 = s1 >= 0 ? B[s1 + i] ^ 0x88 : 0;
          // rows 2i (low nibbles) and 2i+1 (high nibbles)
          res[o + 32 + (2 * i) * 4 + j] = (b0 & 0x0f) | ((b1 & 0x0f) << 4);
          res[o + 32 + (2 * i + 1) * 4 + j] = (b0 >> 4) | (b1 & 0xf0);
        }
      }
    }
  }
  out.add(res);
}

/** Builds the .piiw image of an ONNX token classifier exported by export_onnx.py / export_web.py. */
export function onnxToPiiw(model: Bytes): Bytes {
  const { nodes, inits } = readGraph(model);
  const init = (name: string) => {
    const t = inits.get(name);
    if (!t) throw new Error(`[@newmean-ai/pii] missing initializer ${name}`);
    return t;
  };
  const mats = new Map<string, Mat>();
  const order: number[] = [];
  let proj: Mat | null = null;
  let cls: Mat | null = null;
  const nbits = (n: Node): Mat => {
    const at = (k: string) => n.attrs.get(k)?.i;
    if (at("bits") !== 4 || at("block_size") !== 32 || n.inputs.length !== 3 || (at("accuracy_level") ?? 0) !== 0) throw new Error("[@newmean-ai/pii] unsupported MatMulNBits");
    return { b: init(n.inputs[1]), s: init(n.inputs[2]), K: at("K")!, N: at("N")! };
  };
  for (const n of nodes) {
    if (n.opType !== "MatMulNBits") continue;
    const out = n.outputs[0];
    const r = ROLE.exec(out);
    if (r) {
      const key = `${r[1]}|${r[2]}`;
      if (!mats.has(key)) mats.set(key, nbits(n));
      if (r[2] === "attention/self/query") order.push(Number(r[1]));
    } else if (out.includes("/word_embeddings/proj/")) proj = nbits(n);
    else if (out.includes("/classifier/")) cls = nbits(n);
    else throw new Error(`[@newmean-ai/pii] unknown MatMulNBits ${out}`);
  }
  const NL = new Set([...mats.keys()].map((k) => Number(k.split("|")[0]))).size;
  for (let l = 0; l < NL; l++) for (const r of ROLES) if (!mats.has(`${l}|${r}`)) throw new Error(`[@newmean-ai/pii] layer ${l} misses ${r}`);
  if (!cls) throw new Error("[@newmean-ai/pii] no classifier");
  const blockPrefix = [...inits.keys()].some((k) => k.startsWith("m.roberta.encoder.layer.0.block."));
  const prefix = (l: number) => `m.roberta.encoder.layer.${l}.` + (blockPrefix ? "block." : "");

  const hidden = mats.get("0|attention/self/query")!.N;
  const inter = mats.get("0|intermediate/dense")!.N;
  let headSize = 0;
  let pad = -1;
  const eps = new Set<number>();
  const byOut = new Map(nodes.map((n) => [n.outputs[0], n]));
  const constInt = (name: string) => {
    const t = byOut.get(name)?.attrs.get("value")?.t;
    if (!t) return NaN;
    const dv = new DataView(t.raw.buffer, t.raw.byteOffset, t.raw.length);
    return t.dataType === 7 ? Number(dv.getBigInt64(0, true)) : dv.getInt32(0, true);
  };
  for (const n of nodes) {
    if (n.opType === "Constant" && !headSize && n.outputs[0].includes("/attention/self/Constant_1_output_0")) headSize = constInt(n.outputs[0]);
    if (n.opType === "Equal" && n.inputs[0] === "input_ids") pad = constInt(n.inputs[1]);
    if (n.opType === "LayerNormalization") eps.add(n.attrs.get("epsilon")?.f ?? 1e-5);
  }
  if (!headSize || pad < 0 || eps.size !== 1) throw new Error("[@newmean-ai/pii] could not read head size, padding id or LayerNorm eps");

  const embName = proj ? "m.roberta.embeddings.word_embeddings.emb.weight" : "m.roberta.embeddings.word_embeddings.weight";
  const kind = inits.has(embName + "_Q4") ? 2 : 1;
  const word = init(embName + (kind === 2 ? "_Q4" : "_quantized"));
  const [vocab, embDim] = word.dims;
  const maxPos = init("m.roberta.embeddings.position_embeddings.weight" + (kind === 2 ? "_Q4" : "_quantized")).dims[0];

  const out = new Out();
  const hdr = new DataView(new ArrayBuffer((14 + order.length) * 4));
  [0x57494950, 1, hidden, hidden / headSize, inter, cls.N, vocab, embDim, kind, NL, order.length, pad, maxPos].forEach((v, i) => hdr.setUint32(i * 4, v, true));
  hdr.setFloat32(13 * 4, [...eps][0], true);
  order.forEach((v, i) => hdr.setUint32((14 + i) * 4, v, true));
  out.add(new Uint8Array(hdr.buffer));

  const table = (base: string, rows: number, cols: number) => {
    if (kind === 1) {
      const q = init(base + "_quantized");
      if (q.dataType !== UINT8 || q.raw.length !== rows * cols) throw new Error(`[@newmean-ai/pii] ${base}: expected uint8 [${rows}, ${cols}]`);
      const zp = init(base + "_zero_point");
      out.f32([f32view(init(base + "_scale"))[0], zp.raw[0]]);
      out.add(q.raw);
    } else {
      const q = init(base + "_Q4");
      if (q.dataType !== INT4 || q.raw.length !== (rows * cols) / 2) throw new Error(`[@newmean-ai/pii] ${base}: expected int4 [${rows}, ${cols}]`);
      const s = init(base + "_scales");
      if (s.dataType !== FLOAT || s.raw.length !== (rows * cols) / 32 * 4) throw new Error(`[@newmean-ai/pii] ${base}: scales`);
      out.add(copy(s.raw));
      out.add(q.raw);
    }
  };
  table(embName, vocab, embDim);
  table("m.roberta.embeddings.position_embeddings.weight", maxPos, hidden);
  table("m.roberta.embeddings.token_type_embeddings.weight", 1, hidden);
  const vec = (name: string) => out.add(copy(init(name).raw));
  if (proj) q4Matrix(out, proj);
  vec("m.roberta.embeddings.LayerNorm.weight");
  vec("m.roberta.embeddings.LayerNorm.bias");
  for (let l = 0; l < NL; l++) {
    const p = prefix(l);
    for (const r of ROLES) {
      q4Matrix(out, mats.get(`${l}|${r}`)!);
      vec(p + NAMES[r] + ".bias");
      if (r === "attention/output/dense") {
        vec(p + "attention.output.LayerNorm.weight");
        vec(p + "attention.output.LayerNorm.bias");
      }
    }
    vec(p + "output.LayerNorm.weight");
    vec(p + "output.LayerNorm.bias");
  }
  q4Matrix(out, cls);
  vec("m.classifier.bias");
  return out.join();
}
