#!/usr/bin/env node
/* eslint-disable @typescript-eslint/no-var-requires */

const fs = require("fs");
const path = require("path");

// 读取 config.json 文件路径
const configPath = path.join(process.cwd(), "config.json");
const outputPath = path.join(process.cwd(), "src/lib/runtime.ts");

try {
  /** @type {Record<string, any>} */
  let config = {};

  if (fs.existsSync(configPath)) {
    // 有 config.json 就读取；解析失败直接 fail-fast，避免把坏配置固化进构建
    const configContent = fs.readFileSync(configPath, "utf-8");
    try {
      config = JSON.parse(configContent);
    } catch (err) {
      console.error("❌ 解析 config.json 失败，终止构建:", err.message);
      process.exit(1);
    }
    if (!config || typeof config !== "object") {
      console.error("❌ config.json 内容非法，终止构建");
      process.exit(1);
    }
    if (!config.api_site || Object.keys(config.api_site).length === 0) {
      console.warn("⚠️ config.json 中 api_site 为空，站点将无播放源（空壳站），请检查配置");
    }
  } else {
    console.warn("⚠️ 未找到 config.json 文件，将生成空配置");
  }

  // 生成 TypeScript 代码
  const tsContent = `// 该文件由 scripts/generate-runtime.js 自动生成，请勿手动修改
/* eslint-disable */

export default ${JSON.stringify(config, null, 2)};
`;

  // 确保目录存在
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });

  // 写入文件
  fs.writeFileSync(outputPath, tsContent, "utf-8");
  console.log("✅ runtime.ts 文件生成成功");
} catch (error) {
  console.error("❌ 生成 runtime.ts 文件失败:", error.message);
  process.exit(1);
}
