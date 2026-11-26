// Runs before `npm pack` and `npm publish` (prepack, after the build): refuses to pack a package without its built files.
// 1.6.1 was published from a folder that had never been built and held only the README and licence files.
import { existsSync, readFileSync } from "node:fs";

const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
const need = new Set([pkg.main, pkg.types]);
for (const entry of Object.values(pkg.exports)) for (const file of Object.values(entry)) need.add(file);
const missing = [...need].filter((file) => !existsSync(new URL(`../${file}`, import.meta.url)));
if (missing.length) {
  console.error(`not publishing ${pkg.name}@${pkg.version}: missing ${missing.join(", ")}`);
  process.exit(1);
}
const { VERSION } = await import("../dist/index.js");
if (VERSION !== pkg.version) {
  console.error(`not publishing ${pkg.name}@${pkg.version}: dist/index.js is version ${VERSION} (rebuild)`);
  process.exit(1);
}
console.log(`${pkg.name}@${pkg.version}: ${need.size} entry files present`);
