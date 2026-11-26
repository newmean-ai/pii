#!/bin/sh
# The npm package as a user gets it (job "npm" of .github/workflows/vault.yml, and the same on the laptop before a
# release candidate): npm pack (prepack builds and runs check-pack.mjs), the tarball installed into an empty project with
# the LangChain.js peers at the versions of package-lock.json, every entry point imported as ESM and with require(), and
# a consumer file type-checked against the packed types with skipLibCheck off.
#
#   sh scripts/check-packed.sh DIR      (run from js/ after npm ci; DIR is a scratch folder, created if missing)
#
# Exact versions only, resolved as npm had them at BEFORE, so every run installs the same tree: public CI repeats what
# passed on the laptop and never explores. The installed tree is printed (npm ls --all) for the record.
set -eu
dir=${1:?usage: check-packed.sh DIR}
BEFORE=2026-11-26T00:00:00Z
here=$(pwd)
[ -f package.json ] && [ -f package-lock.json ] || { echo "run from js/"; exit 2; }
mkdir -p "$dir/pack" "$dir/app"
dir=$(cd "$dir" && pwd)
rm -f "$dir"/pack/*.tgz

lock() { node -p "require('./package-lock.json').packages['node_modules/$1'].version"; }
name=$(node -p "require('./package.json').name")
version=$(node -p "require('./package.json').version")
langchain=$(lock langchain)
core=$(lock @langchain/core)
typescript=$(lock typescript)
types_node=$(lock @types/node)
echo "packing $name@$version; peers langchain $langchain, @langchain/core $core; typescript $typescript, @types/node $types_node"

npm pack --pack-destination "$dir/pack"
tgz=$(ls "$dir"/pack/*.tgz)
echo "tarball $(basename "$tgz"): $(wc -c < "$tgz" | tr -d ' ') bytes, sha256 $(node -e "process.stdout.write(require('crypto').createHash('sha256').update(require('fs').readFileSync(process.argv[1])).digest('hex'))" "$tgz")"
tar -tzf "$tgz" | sort

cd "$dir/app"
rm -rf node_modules package.json package-lock.json consumer.ts consumer.cts tsconfig.json
echo '{ "name": "packed-consumer", "private": true, "type": "module" }' > package.json
npm install --no-audit --no-fund --loglevel=error --save-exact --before="$BEFORE" \
  "$tgz" "langchain@$langchain" "@langchain/core@$core" "typescript@$typescript" "@types/node@$types_node"
npm ls --all

# every entry point, as ESM and with require(); the version is the package's
node --input-type=module -e '
const [name, version] = process.argv.slice(1);
const root = await import(name);
const aiSdk = await import(name + "/ai-sdk");
const langchain = await import(name + "/langchain");
const need = { [name]: [root, ["createRedactor", "createVault", "VERSION"]], [name + "/ai-sdk"]: [aiSdk, ["piiMiddleware"]], [name + "/langchain"]: [langchain, ["piiMiddleware"]] };
for (const [entry, [mod, names]] of Object.entries(need)) for (const n of names) if (!(n in mod)) throw new Error(entry + " has no export " + n);
if (root.VERSION !== version) throw new Error("VERSION is " + root.VERSION + ", package.json says " + version);
console.log("ESM: " + Object.keys(need).join(", ") + " import; VERSION " + root.VERSION);
' "$name" "$version"
node -e '
const [name, version] = process.argv.slice(1);
const root = require(name), aiSdk = require(name + "/ai-sdk"), langchain = require(name + "/langchain");
if (typeof root.createRedactor !== "function" || typeof aiSdk.piiMiddleware !== "function" || typeof langchain.piiMiddleware !== "function") throw new Error("require() is missing an export");
if (root.VERSION !== version) throw new Error("VERSION is " + root.VERSION);
console.log("require(): " + [name, name + "/ai-sdk", name + "/langchain"].join(", ") + " load");
' "$name" "$version"

# a consumer of the packed types (ESM and CommonJS files), skipLibCheck off
cat > consumer.ts <<EOF
import { createRedactor, createVault, VERSION, type Redactor, type Span, type Vault } from "$name";
import { piiMiddleware as aiSdkMiddleware, type PiiMiddlewareOptions } from "$name/ai-sdk";
import { piiMiddleware as langchainMiddleware } from "$name/langchain";
import { createAgent } from "langchain";

const version: string = VERSION;
const vault: Vault = createVault();
const options: PiiMiddlewareOptions = { vault };
const aiSdk = aiSdkMiddleware(options);
const middleware = langchainMiddleware({ vault });
export const agent = () => createAgent({ model: "openai:gpt-4o-mini", tools: [], middleware: [middleware] });
export async function first(text: string): Promise<Span[]> {
  const redactor: Redactor = await createRedactor({ size: "nano" });
  return redactor.detect(text);
}
export { aiSdk, version };
EOF
cat > consumer.cts <<EOF
import pii = require("$name");
import aiSdk = require("$name/ai-sdk");
export const version: string = pii.VERSION;
export const middleware = aiSdk.piiMiddleware({ vault: pii.createVault() });
EOF
cat > tsconfig.json <<'EOF'
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "nodenext",
    "moduleResolution": "nodenext",
    "strict": true,
    "skipLibCheck": false,
    "noEmit": true,
    "types": ["node"]
  },
  "files": ["consumer.ts", "consumer.cts"]
}
EOF
npx tsc -p tsconfig.json
echo "consumer.ts and consumer.cts type-check against the packed types"
cd "$here"
