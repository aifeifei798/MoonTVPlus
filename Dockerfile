# ---- 第 1 阶段：安装依赖 ----
FROM node:22-alpine AS deps

# 启用 corepack 并激活与 packageManager 一致的 pnpm，避免 latest 漂移
RUN corepack enable && corepack prepare pnpm@10.14.0 --activate

WORKDIR /app

# 仅复制依赖清单，提高构建缓存利用率
COPY package.json pnpm-lock.yaml ./

# 安装所有依赖（含 devDependencies，后续会裁剪）
RUN pnpm install --frozen-lockfile

# ---- 第 2 阶段：构建项目 ----
FROM node:22-alpine AS builder
RUN corepack enable && corepack prepare pnpm@10.14.0 --activate
WORKDIR /app

# 复制依赖
COPY --from=deps /app/node_modules ./node_modules
# 复制全部源代码
COPY . .

# 在构建阶段也显式设置 DOCKER_ENV，
# 确保 Next.js 在编译时即选择 Node Runtime 而不是 Edge Runtime
# 稳健替换脚本兼容单/双引号与空格变化，残留 edge 即 fail-fast
RUN node scripts/docker-replace-runtime.js
ENV DOCKER_ENV=true

# For Docker builds, force dynamic rendering to read runtime environment variables.
# 幂等处理：已存在 force-dynamic 则跳过，避免重复插入导致构建失败
RUN node -e "const fs=require('fs');const f='src/app/layout.tsx';let s=fs.readFileSync(f,'utf8');if(!s.includes(\"export const dynamic = 'force-dynamic'\")){s=s.replace(/export const runtime = 'nodejs';/, \"export const runtime = 'nodejs';\nexport const dynamic = 'force-dynamic';\");fs.writeFileSync(f,s);}console.log('dynamic 检查完成')"

# 生成生产构建
RUN pnpm run build

# ---- 第 3 阶段：生成运行时镜像 ----
FROM node:22-alpine AS runner

# 创建非 root 用户
RUN addgroup -g 1001 -S nodejs && adduser -u 1001 -S nextjs -G nodejs

WORKDIR /app
ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
ENV DOCKER_ENV=true

# 从构建器中复制 standalone 输出
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
# 从构建器中复制 scripts 目录
COPY --from=builder --chown=nextjs:nodejs /app/scripts ./scripts
# 从构建器中复制 start.js
COPY --from=builder --chown=nextjs:nodejs /app/start.js ./start.js
# 从构建器中复制 config.json
COPY --from=builder --chown=nextjs:nodejs /app/config.json ./config.json
# 从构建器中复制 public 和 .next/static 目录
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

# 切换到非特权用户
USER nextjs

EXPOSE 3000

# 使用自定义启动脚本，先预加载配置再启动服务器
CMD ["node", "start.js"] 