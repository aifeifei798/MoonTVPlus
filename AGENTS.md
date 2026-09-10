# AGENTS.md

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## Quick Start

pnpm 10.14.0, Node 22 (`corepack enable` then `pnpm install --frozen-lockfile`).

Generated files **must** exist before typecheck, test, or build:

```bash
pnpm gen:manifest   # → public/manifest.json
pnpm gen:runtime    # → src/lib/runtime.ts (from config.json)
```

Both are gitignored. CI runs them explicitly.

## Verification Order

```bash
pnpm gen:manifest && pnpm gen:runtime
pnpm lint            # eslint src (warnings OK, errors block)
pnpm typecheck       # tsc --noEmit
pnpm exec jest --ci  # or pnpm test
pnpm build           # Turbopack, outputs standalone
```

`lint:strict` (`--max-warnings=0`) is used in pre-commit via lint-staged.

## Project Structure

Single-package Next.js 16 App Router app (React 19, Tailwind v4). Not a monorepo.

- `src/app/` — pages + API route handlers (21 route groups)
- `src/lib/` — core logic: storage abstraction (`db.ts`), auth (`auth.ts`), downstream search/caching (`downstream.ts`), SSRF protection (`ssrf.ts`)
- `src/components/` — UI components
- `scripts/` — codegen helpers (`generate-runtime.js`, `generate-manifest.js`)
- `config.json` — empty shell by default; must add Apple CMS V10 sources for search/home to work
- `start.js` — Docker entrypoint (standalone server + cron polling)

## Key Conventions

- **Import aliases**: `@/` → `src/`, `~/` → `public/`
- **Runtime**: route handlers use `export const runtime = 'edge'` (Docker build swaps to `'nodejs'` via sed); auth gateway is `src/proxy.ts` (nodejs-only, ex-`middleware.ts`)
- **No `eval`/static `node:*` in shared lib**: `config.ts` / `douban-cache.ts` load fs via guarded dynamic `import()` so Edge builds pass
- **Auth**: HMAC SHA-256 signed httpOnly `auth` cookie, `PASSWORD` env var required
- **Storage**: `NEXT_PUBLIC_STORAGE_TYPE` selects backend (localstorage / redis / kvrocks / upstash / d1)
- **Client state**: `useLocalStorage` / `useSearchHistory` hooks in `src/lib/`; contexts memoized
- **Tests**: colocated with source (`src/lib/*.test.ts`), jsdom environment, mocks `next/router`
- **Style**: single quotes, semicolons, 2-space indent. ESLint (flat config) enforces `simple-import-sort` with specific group order; react-hooks v7 rules on except `set-state-in-effect` (mount-hydrate pattern, deferred)
- **Commits**: conventional commits enforced by commitlint. Husky hooks: pre-commit (lint-staged), commit-msg (commitlint), post-merge (pnpm install).
- **Docker**: multi-stage build, `DOCKER_ENV=true` switches config to on-disk `config.json`
- **Config caching**: `config.ts` and `downstream.ts` use in-memory caches with TTLs
- **No next-pwa / next-on-pages**: PWA via custom `public/sw.js` + `ServiceWorkerRegistration`; Cloudflare Pages path removed (migrate to `@opennextjs/cloudflare` when needed)

## Gotchas

- `config.json` has empty `api_site` — no sources until configured
- Dockerfile sed-replaces `runtime = 'edge'` declarations — format changes will break the build
- `no-console` is a warning in ESLint; many files use eslint-disable headers
