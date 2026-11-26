#!/bin/sh
# Installs the AI SDK versions the boundary gate runs against (test/vault-boundary.test.ts) into a folder outside this
# package, at exact versions: ai@5 as "ai5", ai@6 as "ai", ai@7 as "ai7". The AI SDK is not a dependency of the package.
#
#   sh scripts/install-gate-ai-sdk.sh DIR
#   AI_SDK_DIR=DIR PII_GATE=1 npx vitest run test/vault-boundary.test.ts
set -eu
dir=${1:?usage: install-gate-ai-sdk.sh DIR}
mkdir -p "$dir"
cd "$dir"
[ -f package.json ] || printf '{ "private": true }\n' > package.json
npm install --no-audit --no-fund --save-exact ai@6.0.332 ai5@npm:ai@5.0.290 ai7@npm:ai@7.0.173 zod@4.6.7
