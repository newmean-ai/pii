// What the README says about the vault middlewares, tested where a README sentence rests on behaviour no other test pins
// (review round of SDK 1.7.1, class "docs").
import { describe, expect, it } from "vitest";
import { AIMessage, createAgent, dynamicSystemPromptMiddleware, HumanMessage } from "langchain";
import { FakeListChatModel } from "@langchain/core/utils/testing";
import { piiMiddleware as aiSdkMiddleware } from "../src/ai-sdk";
import { piiMiddleware } from "../src/langchain";
import { applyRedaction, type Redactor } from "../src/redactor";
import { createVault, valueSpans } from "../src/vault";

// a detector that finds every "Kim Minsu"
const kim = {
  sensitivity: 0,
  async detect(text: string) {
    const found = [];
    for (let i = text.indexOf("Kim Minsu"); i >= 0; i = text.indexOf("Kim Minsu", i + 1)) found.push({ start: i, end: i + 9, label: "PERSON", source: "model" as const });
    return found;
  },
} as unknown as Redactor;

const known = () => {
  const vault = createVault();
  vault.tag("PERSON", "Kim Minsu");
  vault.tag("EMAIL", "kim@example.com");
  return vault;
};

describe("README: list the middleware last (guarantee note F10)", () => {
  it("LangChain.js createAgent: a dynamic system prompt of a middleware listed before it is masked, one listed after it is not", async () => {
    const profile = dynamicSystemPromptMiddleware(() => "You help Kim Minsu (kim@example.com).");
    const run = async (order: (mw: ReturnType<typeof piiMiddleware>) => unknown[]) => {
      const sent: string[][] = [];
      class Model extends FakeListChatModel {
        bindTools(): any {
          return this;
        }
        async invoke(messages: any): Promise<any> {
          sent.push((messages as any[]).map((m) => String(m.content)));
          return new AIMessage("Done.");
        }
      }
      const agent: any = createAgent({ model: new Model({ responses: ["x"] }), tools: [], middleware: order(piiMiddleware({ redactor: kim, vault: known() })) as never });
      await agent.invoke({ messages: [new HumanMessage("Hi, I am Kim Minsu.")] });
      return sent[0];
    };
    const last = await run((mw) => [profile, mw]);
    expect(last).toEqual(["You help [PERSON_1] ([EMAIL_1]).", "Hi, I am [PERSON_1]."]);
    const first = await run((mw) => [mw, profile]);
    expect(first).toEqual(["You help Kim Minsu (kim@example.com).", "Hi, I am [PERSON_1]."]); // the documented limit
  });

  it("AI SDK: a middleware's transformParams before this one is masked, one after it is not (wrapLanguageModel runs them in list order)", async () => {
    const addSystem = async ({ params }: { params: any }) => ({ ...params, prompt: [{ role: "system", content: "You help Kim Minsu." }, ...params.prompt] });
    const user = { role: "user", content: [{ type: "text", text: "Hi" }] };
    const run = async (piiFirst: boolean) => {
      const pii = aiSdkMiddleware({ redactor: kim, vault: known() });
      let params: any = { prompt: [user] };
      for (const step of piiFirst ? [pii.transformParams, addSystem] : [addSystem, pii.transformParams]) params = await step({ params } as never);
      return params.prompt[0].content;
    };
    expect(await run(false)).toBe("You help [PERSON_1].");
    expect(await run(true)).toBe("You help Kim Minsu."); // the documented limit
  });
});

describe("the edges of a span the model found: what is not part of the value is left out before the vault learns it (L2-B2)", () => {
  // (the cases every package shares are in python/tests/vault_cases.json, "redact"; these are the npm package's own)
  it("leaves block-list and known-value spans as they are, and a SECRET loses only its white space", () => {
    const spans = [
      { start: 0, end: 13, label: "ORG", source: "block" },
      { start: 14, end: 24, label: "PERSON", source: "vault" },
      { start: 25, end: 35, label: "PERSON", source: "model" },
      { start: 36, end: 45, label: "SECRET", source: "model" },
    ];
    const text = "Tiffany & Co. Kim Minsu. Kim Minsu. hunter2! x";
    expect(valueSpans(text, spans).map((s) => [s.start, s.end])).toEqual([[0, 13], [14, 24], [25, 34], [36, 44]]);
  });

  it("applyRedaction with a vault learns the value too", () => {
    const text = "Please look up Kim Minsu.";
    const vault = createVault();
    expect(applyRedaction(text, [{ start: 15, end: 25, label: "PERSON", source: "model" }], { vault })).toBe("Please look up [PERSON_1].");
    expect(vault.entries().map((e) => e.value)).toEqual(["Kim Minsu"]);
  });
});
