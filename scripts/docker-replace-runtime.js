#!/usr/bin/env node
/* eslint-disable no-console */
// Docker 构建用：将 `export const runtime = 'edge'` 稳健替换为 nodejs
// 兼容单/双引号、多空格、有无分号、带类型注解等写法
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', 'src');
const PATTERN =
  /export\s+const\s+runtime\s*(?::\s*string\s*)?=\s*['"]edge['"]\s*;?/g;

function collect(dir, out) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) collect(p, out);
    else if (
      e.isFile() &&
      (e.name === 'route.ts' ||
        e.name === 'layout.tsx' ||
        e.name === 'not-found.tsx')
    )
      out.push(p);
  }
  return out;
}

const files = collect(SRC, []);
let replaced = 0;
let touched = 0;
for (const f of files) {
  const raw = fs.readFileSync(f, 'utf-8');
  if (!raw.includes('runtime')) continue;
  const next = raw.replace(PATTERN, "export const runtime = 'nodejs';");
  if (next !== raw) {
    fs.writeFileSync(f, next);
    replaced++;
  }
  if (next.includes("runtime = 'nodejs'")) touched++;
}

console.log(`runtime 替换：${replaced} 文件改动，${touched} 文件含 nodejs`);

// 强校验：不允许残留 edge 声明
const { execSync } = require('child_process');
try {
  const left = execSync(
    `grep -rn --include='route.ts' --include='layout.tsx' --include='not-found.tsx' -E "export\\s+const\\s+runtime\\s*(::?\\s*string)?\\s*=\\s*['\\"]edge['\\"]" "${SRC}" || true`,
    { encoding: 'utf-8' },
  ).trim();
  if (left) {
    console.error('❌ 仍残留 edge runtime 声明：\n' + left);
    process.exit(1);
  }
} catch (e) {
  console.error('❌ runtime 残留检查失败', e.message);
  process.exit(1);
}
if (touched === 0) {
  console.error('❌ edge->nodejs 替换未生效，检查 runtime 声明格式');
  process.exit(1);
}
