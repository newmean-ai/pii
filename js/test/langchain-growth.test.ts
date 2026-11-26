// The doubling tests of the LangChain.js middleware (REVIEW-GUIDE rule 4): time at n, 2n and 4n, at most 3x per
// doubling. They run alone in the timing phase of the test runner (files named *growth*), not under the parallel load
// of the other suites, where two of them failed in the gate run of 1.7.1 (since 1.7.2 they are here, out of
// test/langchain.test.ts, whose functional checks they keep).
import { describe, expect, it } from "vitest";
import { AIMessage, HumanMessage, tool } from "langchain";
import { z } from "zod";
import { FakeListChatModel } from "@langchain/core/utils/testing";
import { piiMiddleware } from "../src/langchain";
import { applyRedaction, withVaultSpans } from "../src/redactor";
import type { Redactor } from "../src/redactor";
import { createVault } from "../src/vault";

// a redactor that finds one fixed name, with the vault handling of the real one
const kimSpans = async (text: string) => {
  const found = [];
  for (let i = text.indexOf("Kim Minsu"); i >= 0; i = text.indexOf("Kim Minsu", i + 1)) found.push({ start: i, end: i + 9, label: "PERSON", source: "model" as const });
  return found;
};
const kim = {
  detect: kimSpans,
  async redact(text: string, o?: Parameters<Redactor["redact"]>[1]) {
    const found = await kimSpans(text);
    const spans = o?.vault ? withVaultSpans(text, found, o.vault) : found;
    return { text: applyRedaction(text, spans, o), spans };
  },
} as unknown as Redactor;
const none = { sensitivity: 0, detect: async () => [] } as unknown as Redactor;
const hook = (mw: ReturnType<typeof piiMiddleware>) =>
  mw.wrapModelCall as unknown as (request: object, handler: (r: any) => Promise<unknown>) => Promise<any>;
const known = () => {
  const vault = createVault();
  vault.tag("PERSON", "Kim Minsu");
  vault.tag("EMAIL", "kim@example.com");
  return vault;
};

describe.skipIf(process.env.PII_SKIP_TIMING === "1")("piiMiddleware with a vault: time per doubling (LangChain.js)", () => {
  it("takes time in proportion to the length of a name with known values in it (doubling test)", async () => {
    const values = Array.from({ length: 50 }, (_, i) => `Given${i} Family${i}`);
    const best = async (n: number) => {
      let ms = Infinity;
      let name: string | undefined;
      for (let r = 0; r < 3; r++) {
        const vault = createVault();
        for (const v of values) vault.tag("PERSON", v);
        const mw = piiMiddleware({ redactor: kim, vault });
        const messages = [new HumanMessage({ content: "hi", name: Array.from({ length: n }, (_, i) => values[i % 50]).join(", ") })];
        const t = performance.now();
        await hook(mw)({ messages }, async (req) => ((name = req.messages[0].name), new AIMessage("ok")));
        ms = Math.min(ms, performance.now() - t);
      }
      expect(name).not.toContain("Given0 Family0");
      expect(name).toContain("PERSON_50");
      return ms;
    };
    await best(500);
    const [a, b, c] = [await best(1000), await best(2000), await best(4000)];
    expect([b / a, c / b].every((r) => r <= 3), `ms ${a.toFixed(1)}/${b.toFixed(1)}/${c.toFixed(1)}`).toBe(true);
  });

  it("takes time in proportion to the number of tools, schema properties and model settings (doubling test)", async () => {
    const mw = piiMiddleware({ redactor: kim, vault: known() });
    // (n / 10 tools of ten properties each, and n model settings: the size grows with n)
    const properties = Object.fromEntries(Array.from({ length: 10 }, (_, i) => [`p${i}`, { type: "string", description: `field ${i} of Kim Minsu` }]));
    const request = (n: number) => {
      const tools = Array.from({ length: n / 10 }, (_, i) =>
        i % 2 === 0
          ? { type: "function", function: { name: `t${i}`, description: "for Kim Minsu", parameters: { type: "object", properties } } }
          : tool(async () => "ok", { name: `t${i}`, description: "for Kim Minsu", schema: z.object({ q: z.string().describe("kim@example.com") }) }),
      );
      return { messages: [new HumanMessage("hi")], tools, modelSettings: Object.fromEntries(Array.from({ length: n }, (_, i) => [`k${i}`, "Kim Minsu"])) };
    };
    const best = async (n: number) => {
      let ms = Infinity;
      for (let r = 0; r < 3; r++) {
        const req = request(n);
        let got: any;
        const t = performance.now();
        await hook(mw)(req, async (x) => ((got = x), new AIMessage("ok")));
        ms = Math.min(ms, performance.now() - t);
        expect(JSON.stringify([got.tools, got.modelSettings])).not.toMatch(/Kim Minsu|kim@example/);
      }
      return ms;
    };
    await best(200);
    const [a, b, c] = [await best(400), await best(800), await best(1600)];
    expect([b / a, c / b].every((r) => r <= 3), `ms ${a.toFixed(1)}/${b.toFixed(1)}/${c.toFixed(1)}`).toBe(true);
  });

  it("restores the keys of a structured response in time that grows with their number (doubling test)", async () => {
    const best = async (n: number) => {
      const answer = Object.fromEntries(Array.from({ length: n }, (_, i) => [i % 2 === 0 ? `[PERSON_1] ${i}` : `k${i}`, "[PERSON_1]"]));
      let ms = Infinity;
      let got: any;
      for (let r = 0; r < 3; r++) {
        const mw = piiMiddleware({ redactor: kim, vault: createVault() });
        const t = performance.now();
        got = await hook(mw)({ messages: [new HumanMessage("Call Kim Minsu.")] }, async () => ({ structuredResponse: { contacts: answer }, messages: [] }));
        ms = Math.min(ms, performance.now() - t);
      }
      expect(got.structuredResponse.contacts["Kim Minsu 0"]).toBe("Kim Minsu");
      expect(Object.keys(got.structuredResponse.contacts)).toHaveLength(n);
      return ms;
    };
    await best(1000);
    const [a, b, c] = [await best(2000), await best(4000), await best(8000)];
    expect([b / a, c / b].every((r) => r <= 3), `ms ${a.toFixed(1)}/${b.toFixed(1)}/${c.toFixed(1)}`).toBe(true);
  });

  it("reads a name of known values joined with '_' and '-' in time that grows with its length (doubling test)", async () => {
    const vault = createVault();
    const names = Array.from({ length: 50 }, (_, i) => `Given${i} Family${i}`);
    for (const v of names) vault.tag("PERSON", v);
    const mw = piiMiddleware({ redactor: none, vault });
    const best = async (n: number) => {
      const name = Array.from({ length: n }, (_, i) => names[i % 50].replace(" ", "_")).join("-");
      let ms = Infinity;
      let got = "";
      for (let r = 0; r < 3; r++) {
        const t = performance.now();
        await hook(mw)({ messages: [new HumanMessage({ content: "hi", name })] }, async (x) => ((got = x.messages[0].name), new AIMessage("ok")));
        ms = Math.min(ms, performance.now() - t);
      }
      expect(got).not.toContain("Given0_Family0");
      expect(got).toContain("PERSON_50");
      return ms;
    };
    await best(200);
    const [a, b, c] = [await best(400), await best(800), await best(1600)];
    expect([b / a, c / b].every((r) => r <= 3), `ms ${a.toFixed(1)}/${b.toFixed(1)}/${c.toFixed(1)}`).toBe(true);
  });

  it("hides the tools and options of the final binding in time that grows with their number (doubling test)", async () => {
    const vault = createVault();
    vault.tag("PERSON", "Kim Minsu");
    const mw = piiMiddleware({ redactor: none, vault });
    const best = async (n: number) => {
      const tools = Array.from({ length: n }, (_, i) => ({ type: "function", function: { name: `t${i}`, description: "for Kim Minsu", parameters: { type: "object", properties: { q: { type: "string", format: "date" } } } } }));
      const options = { response_format: { type: "json_schema", json_schema: { name: "a", schema: { type: "object", properties: Object.fromEntries(tools.map((_t, i) => [`p${i}`, { type: "string", format: "date", description: "Kim Minsu" }])) } } } };
      let ms = Infinity;
      for (let r = 0; r < 3; r++) {
        let got: any;
        const model = new (class extends FakeListChatModel {
          bindTools(t: unknown[], o?: Record<string, unknown>): any {
            got = [t, o];
            return this;
          }
        })({ responses: ["x"] });
        await hook(mw)({ messages: [new HumanMessage("Kim Minsu")], model }, async (req) => {
          const t = performance.now();
          await req.model.bindTools(tools, options);
          ms = Math.min(ms, performance.now() - t);
          return new AIMessage("ok");
        });
        expect(JSON.stringify(got)).not.toContain("Kim Minsu");
        expect(JSON.stringify(got[1])).not.toContain('"format"');
      }
      return ms;
    };
    await best(200);
    const [a, b, c] = [await best(400), await best(800), await best(1600)];
    expect([b / a, c / b].every((r) => r <= 3), `ms ${a.toFixed(1)}/${b.toFixed(1)}/${c.toFixed(1)}`).toBe(true);
  });

  it("hides the model's own request fields in time that grows with their size (doubling test, gate run of 1.7.1)", async () => {
    const vault = known();
    const mw = piiMiddleware({ redactor: none, vault });
    // a model shaped like ChatOpenAI: n metadata entries and n stored-prompt variables in modelKwargs, n / 10 stop words
    class Model extends FakeListChatModel {
      modelKwargs: Record<string, unknown> = {};
      stop: string[] = [];
      invocationParams(_options?: unknown): any {
        return { model: "gpt-x", stop: this.stop, ...this.modelKwargs };
      }
    }
    const make = (n: number) =>
      Object.assign(new Model({ responses: ["x"] }), {
        modelKwargs: {
          metadata: Object.fromEntries(Array.from({ length: n }, (_, i) => [i % 2 === 0 ? `Kim Minsu ${i}` : `k${i}`, i % 3 === 0 ? "kim@example.com" : `v${i}`])),
          prompt: { id: "pmpt_1", variables: Object.fromEntries(Array.from({ length: n }, (_, i) => [`v${i}`, "Kim Minsu"])) },
        },
        stop: Array.from({ length: n / 10 }, (_, i) => `Kim Minsu ${i}`),
      });
    const best = async (n: number) => {
      let ms = Infinity;
      for (let r = 0; r < 3; r++) {
        const model = make(n);
        let params: any;
        const t = performance.now();
        await hook(mw)({ messages: [new HumanMessage("hi")], model, responseFormat: undefined }, async (req) => ((params = req.model.invocationParams({})), new AIMessage("ok")));
        ms = Math.min(ms, performance.now() - t);
        expect(JSON.stringify(params)).not.toMatch(/Kim Minsu|kim@example\.com/);
        expect(Object.keys(params.metadata)).toHaveLength(n);
      }
      return ms;
    };
    await best(200);
    const [a, b, c] = [await best(400), await best(800), await best(1600)];
    expect([b / a, c / b].every((r) => r <= 3), `ms ${a.toFixed(1)}/${b.toFixed(1)}/${c.toFixed(1)}`).toBe(true);
  });
  it("sends the model's own data as it was read once, in time that grows with its size (doubling test, release gate 8 on f25d519, item 2)", async () => {
    // a model shaped like ChatOpenAI (a wrapper that reports its parameters through a chat model it keeps, both made
    // from one modelKwargs object, and a new model from it for the binding) and one shaped like ChatAnthropic (its kwargs
    // spread into the request once more): n metadata entries, half of them toJSON objects, half with a known value;
    // the provider reports the parameters twice and writes the request as JSON
    let calls = 0;
    const metadata = (n: number) =>
      Object.fromEntries(Array.from({ length: n }, (_, i) => [`k${i}`, i % 2 === 0 ? { toJSON: () => (calls++, `note ${i}`) } : { name: "Kim Minsu", tier: `t${i}` }]));
    const answer = { generations: [{ text: "done", message: new AIMessage("done") }] };
    const openAI = (kwargs: Record<string, unknown>, sent: unknown[]): any => {
      class Api extends FakeListChatModel {
        modelKwargs = kwargs;
        invocationParams(): any {
          return { model: "gpt-x", ...this.modelKwargs };
        }
        async _generate(_m?: unknown, _o?: unknown): Promise<any> {
          this.invocationParams();
          sent.push(JSON.parse(JSON.stringify(this.invocationParams())));
          return answer;
        }
      }
      class Model extends FakeListChatModel {
        modelKwargs = kwargs;
        completions = new Api({ responses: ["x"] });
        invocationParams(): any {
          return this.completions.invocationParams();
        }
        async _generate(m: any, o: any): Promise<any> {
          return this.completions._generate(m, o);
        }
        bindTools(): any {
          return openAI(kwargs, sent);
        }
      }
      return new Model({ responses: ["x"] });
    };
    const anthropic = (kwargs: Record<string, unknown>, sent: unknown[]): any => {
      class Model extends FakeListChatModel {
        invocationKwargs = kwargs;
        invocationParams(): any {
          return { model: "claude-x", ...this.invocationKwargs };
        }
        async _generate(): Promise<any> {
          this.invocationParams();
          sent.push(JSON.parse(JSON.stringify({ ...this.invocationParams(), ...this.invocationKwargs })));
          return answer;
        }
        bindTools(): any {
          return this;
        }
      }
      return new Model({ responses: ["x"] });
    };
    const best = async (n: number) => {
      let ms = Infinity;
      for (let r = 0; r < 3; r++)
        for (const shape of [openAI, anthropic]) {
          const sent: any[] = [];
          const model = shape({ metadata: metadata(n) }, sent);
          calls = 0;
          const t = performance.now();
          await hook(piiMiddleware({ redactor: none, vault: known() }))({ messages: [new HumanMessage("hi")], model, responseFormat: undefined }, async (req) => {
            await req.model.invoke([new HumanMessage("hi")]);
            return new AIMessage("ok");
          });
          ms = Math.min(ms, performance.now() - t);
          expect(sent).toHaveLength(1);
          expect(JSON.stringify(sent[0])).not.toContain("Kim Minsu");
          expect(Object.keys(sent[0].metadata)).toHaveLength(n);
          expect(calls).toBe(n / 2);
        }
      return ms;
    };
    await best(100);
    const [a, b, c] = [await best(200), await best(400), await best(800)];
    expect([b / a, c / b].every((r) => r <= 3), `ms ${a.toFixed(1)}/${b.toFixed(1)}/${c.toFixed(1)}`).toBe(true);
  });
});
