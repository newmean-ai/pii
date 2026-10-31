// node --test tests/js — the page logic (lb.js). Parity with the Python checks is in tests/test_static.py.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as lb from "../../lb.js";

const CFG = { architectures: ["BertForTokenClassification"], id2label: { 0: "O", 1: "B-PER", 2: "I-PER", 3: "B-EMAIL", 4: "I-EMAIL" } };
const hub = ({ files = { "config.json": 900, "model.safetensors": 4e8 }, priv = false, gated = false, cfg = CFG } = {}) => async (url) => {
  if (url.includes("nobody/missing")) { const e = new Error("404"); e.status = 404; throw e; }
  if (url.includes("/api/models/")) return { sha: "a".repeat(40), private: priv, gated, siblings: Object.entries(files).map(([rfilename, size]) => ({ rfilename, size })) };
  if (url.endsWith("/config.json")) return cfg;
  throw new Error(`unexpected ${url}`);
};
const GOOD = JSON.stringify({ PER: "PERSON", EMAIL: "EMAIL" });
const base = { model: "org/ner", revision: "main", adapter: "transformers", labelMap: GOOD };

test("label maps", () => {
  assert.equal(lb.parseLabelMap(""), "auto");
  assert.deepEqual(lb.parseLabelMap('{"PER": "PERSON", "LOC": "ignore"}'), { PER: "PERSON", LOC: "ignore" });
  for (const bad of ['{"PER": "NAME"}', "[1]", "{not json", '{"PER": "ignore"}']) assert.throws(() => lb.parseLabelMap(bad), lb.Rejected);
});

test("a valid model passes with the resolved commit", async () => {
  const f = await lb.checkModel(hub(), base);
  assert.deepEqual(f, { model: "org/ner", revision: "a".repeat(40), adapter: "transformers", variant: null, label_map: { PER: "PERSON", EMAIL: "EMAIL" } });
});

test("rejections", async () => {
  const cases = [
    [{ model: "no-slash" }, {}, /Hub id/], [{ model: "nobody/missing" }, {}, /cannot read/], [{}, { priv: true }, /public/],
    [{}, { gated: "auto" }, /public/], [{}, { files: { "config.json": 1, "pytorch_model.bin": 10 } }, /safetensors/],
    [{}, { files: { "config.json": 1, "model.safetensors": 4e9 } }, /limit/], [{}, { cfg: { ...CFG, auto_map: { AutoModel: "x.Y" } } }, /trust_remote_code/],
    [{}, { cfg: { ...CFG, architectures: ["BertModel"] } }, /token-classification/], [{ labelMap: '{"PER": "PERSON"}' }, {}, /missing \['EMAIL'\]/],
    [{ adapter: "gliner" }, {}, /adapter/], [{ adapter: "newmean-onnx" }, {}, /only for newmean-ai/],
  ];
  for (const [over, h, re] of cases) await assert.rejects(lb.checkModel(hub(h), { ...base, ...over }), re);
});

test("newmean-onnx uses the SDK default file", async () => {
  const files = { "onnx/model_q4.onnx": 1, "onnx/model_q4e.onnx": 1 };
  assert.equal((await lb.checkModel(hub({ files }), { model: "newmean-ai/pii-large", adapter: "newmean-onnx" })).variant, "q4e");
  assert.equal((await lb.checkModel(hub({ files }), { model: "newmean-ai/pii-nano", adapter: "newmean-onnx" })).variant, "q4");
  await assert.rejects(lb.checkModel(hub({ files: { "onnx/model_q4.onnx": 1 } }), { model: "newmean-ai/pii-medium", adapter: "newmean-onnx" }), /model_q4e.onnx not found/);
});

test("request body carries the marker and one JSON block", () => {
  const body = lb.requestBody({ model: "org/ner", revision: "a".repeat(40), adapter: "transformers", variant: null, label_map: "auto" });
  assert.ok(body.startsWith(lb.MARKER));
  assert.deepEqual(JSON.parse(body.match(/```json\s*(\{[\s\S]*?\})\s*```/)[1]).model, "org/ner");
});

test("weekly count ignores rejected and old submissions", () => {
  const now = new Date("2026-11-10T00:00:00Z");
  const reqs = [{ submitter: "a", status: "done", submitted_at: "2026-11-09T00:00:00Z" }, { submitter: "a", status: "rejected", submitted_at: "2026-11-09T00:00:00Z" },
    { submitter: "a", status: "done", submitted_at: "2026-11-01T00:00:00Z" }, { submitter: "b", status: "pending", submitted_at: "2026-11-09T00:00:00Z" }];
  assert.equal(lb.recentBy(reqs, "a", now).length, 1);
});

test("tables sort by leak and mark private pending", () => {
  const r = (name, leak, priv) => ({ display_name: name, model: name, adapter: "transformers", public: { overall: { leak }, by_language: { ko: { leak } } }, private: priv });
  const rows = lb.overallRows([r("b", 0.5, { status: "pending" }), r("a", 0.1, { status: "done", overall: { leak: 0.2 } })]);
  assert.deepEqual(rows.map((x) => [x.model, x.leak, x.private_leak]), [["a", 10, 20], ["b", 50, "pending"]]);
  assert.deepEqual(lb.breakdown([r("b", 0.5, {}), r("a", 0.1, {})], "by_language", "leak"), { columns: ["ko"], rows: [{ model: "a", values: [10] }, { model: "b", values: [50] }] });
});
