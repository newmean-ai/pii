// Mask what an agent reads, not only what the user types (Vercel AI SDK, @newmean-ai/pii 1.7).
//
// The user asks about a customer. The model calls a CRM tool and a ticket search; their results hold e-mail addresses,
// phone numbers and other people's names the user never typed. The middleware masks tool results (text and JSON)
// before every model call, so the hosted model only sees placeholders. With a vault, lookupCustomer still gets the
// real name, and the answer comes back with the values.
//
//   npm install @newmean-ai/pii ai @ai-sdk/openai
//   OPENAI_API_KEY=... node ai-sdk-agent.mjs "Did the refund for Kim Minsu go through?"
import { generateText, jsonSchema, stepCountIs, tool, wrapLanguageModel } from "ai";
import { createRedactor, createVault } from "@newmean-ai/pii";
import { piiMiddleware } from "@newmean-ai/pii/ai-sdk";

// Stand-ins for a CRM and a ticket search (fake people)
const CUSTOMERS = { "Kim Minsu": { email: "minsu.kim@example.com", phone: "+82 10-2345-6789", plan: "Pro", openTicket: "T-1042" } };
const TICKETS = [
  "T-1042: Refund requested on 2026-11-20. Agent Park Jiyoung called the customer back at +82 10-2345-6789 " +
    "and promised the refund by Friday. Escalated to Lee Sora (lee.sora@example.com).",
];

export const tools = {
  lookupCustomer: tool({
    description: "Look up a customer record by full name.",
    inputSchema: jsonSchema({ type: "object", properties: { name: { type: "string" } }, required: ["name"] }),
    // a detector may take "Kim Minsu's" as the name
    execute: async ({ name }) => CUSTOMERS[name.trim().replace(/['’]s$/, "")] ?? { error: `no customer named ${name}` },
  }),
  searchTickets: tool({
    description: "Search support tickets.",
    inputSchema: jsonSchema({ type: "object", properties: { query: { type: "string" } }, required: ["query"] }),
    execute: async ({ query }) => TICKETS.filter((t) => query.split(/\s+/).some((w) => t.toLowerCase().includes(w.toLowerCase()))).join("\n") || "no tickets",
  }),
};

/** The model with the middleware: user messages, earlier answers and tool results are masked before each call; only
 *  lookupCustomer gets real values back. One vault per conversation. */
export function protect(model, redactor, vault) {
  return wrapLanguageModel({ model, middleware: piiMiddleware({ redactor, vault, restoreTools: ["lookupCustomer"] }) });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const { openai } = await import("@ai-sdk/openai");
  const redactor = await createRedactor({ size: process.env.PII_SIZE ?? "medium" });
  const { text } = await generateText({
    model: protect(openai(process.env.MODEL ?? "gpt-6.1"), redactor, createVault()),
    tools,
    stopWhen: stepCountIs(5),
    system: "You help support staff. Use the tools, then answer briefly.",
    prompt: process.argv[2] ?? "Did the refund for Kim Minsu go through?",
  });
  console.log(text);
}
