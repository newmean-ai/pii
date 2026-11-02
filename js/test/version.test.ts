import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { VERSION } from "../src/index";

describe("VERSION", () => {
  it("equals package.json", () => {
    expect(VERSION).toBe(JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")).version);
  });
});
