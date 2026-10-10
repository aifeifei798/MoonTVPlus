/* eslint-disable */

// 全局类型声明
// 允许以副作用方式导入 .css 文件（例如布局中的 globals.css 以及
// sweetalert2 等第三方样式），避免 TypeScript 报 ts(2882)
// “找不到模块或其对应的类型声明”。
declare module '*.css';
declare module '*.css?*';

// Backend-inlined runtime values (substituted into dist/index.html at serve
// time by `backend/moontv/static.py`). Declared here so `src/main.tsx` does
// not need `(window as any)` workarounds and `tsc --noEmit` stays clean.
interface Window {
  RUNTIME_CONFIG?: Record<string, unknown>;
  RUNTIME_LAYOUT?: Record<string, unknown>;
}
