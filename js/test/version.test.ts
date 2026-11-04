import { existsSync, readdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { describe, expect, it } from "vitest";
import { VERSION } from "../src/index";

describe("VERSION", () => {
  it("equals package.json", () => {
    expect(VERSION).toBe(JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")).version);
  });
});

// The built package (js/dist, from `npm run build`); skipped before the first build.
const dist = new URL("../dist/", import.meta.url);
const built = existsSync(new URL("index.js", dist));

describe("built package", () => {
  // require(esm) is on by default from Node 20.19 and 22.12; older Node versions skip
  it.skipIf(!built || !(process.features as { require_module?: boolean }).require_module)("loads with require()", () => {
    // self-reference: resolved through package.json "exports", as an app's require would be
    const pkg = createRequire(import.meta.url)("@newmean-ai/pii");
    expect(pkg.VERSION).toBe(VERSION);
    expect(typeof pkg.createRedactor).toBe("function");
  });

  it.skipIf(!built)("has no declarations of removed sources", () => {
    const src = new URL("../src/", import.meta.url);
    const stale = (readdirSync(dist, { recursive: true }) as string[])
      .filter((f) => f.endsWith(".d.ts") && !existsSync(new URL(f.replace(/\.d\.ts$/, ".ts"), src)));
    expect(stale).toEqual([]);
  });
});
