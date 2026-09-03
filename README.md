# MoonTVPlus

> 基于 [MoonTechLab/LunaTV](https://github.com/MoonTechLab/LunaTV) 维护的分支，聚焦稳定性、安全加固与追更/TVBox 体验。

<div align="center">
  <img src="public/logo.png" alt="MoonTVPlus Logo" width="120">
</div>

> 🎬 **MoonTVPlus** 是一个开箱即用的、跨平台的影视聚合播放器。它基于 **Next.js 14** + **Tailwind&nbsp;CSS 3** + **TypeScript 5** 构建，支持多资源搜索、在线播放、追更、收藏同步、播放记录、本地/云端存储，让你可以随时随地畅享海量免费影视内容。

<div align="center">

![Next.js](https://img.shields.io/badge/Next.js-14-000?logo=nextdotjs)
![TailwindCSS](https://img.shields.io/badge/TailwindCSS-3-38bdf8?logo=tailwindcss)
![TypeScript](https://img.shields.io/badge/TypeScript-5.x-3178c6?logo=typescript)
![License](https://img.shields.io/badge/License-MIT-green)
![Docker Ready](https://img.shields.io/badge/Docker-ready-blue?logo=docker)

</div>

---

## ✨ 功能特性

- 🔍 **多源聚合搜索**：Apple CMS V10 多资源站并发搜索，支持流式/聚合两种模式。
- 📄 **丰富详情页**：剧集列表、演员、年份、简介等完整信息展示。
- ▶️ **流畅在线播放**：集成 HLS.js & ArtPlayer。
- 📥 **视频下载**：M3U8 多线程并发、断点重试、AES-128 解密、TS/MP4 转码、边下边存（Chrome/Edge）。
- 🔔 **追更**：收藏/播放记录集数变化自动提醒，Docker 下每小时 `cron` 刷新。
- ⏭️ **跳过片头片尾**：按源+剧集记忆跳过配置。
- ❤️ **收藏 + 继续观看**：localstorage / Redis / Kvrocks / Upstash / D1 多端同步。
- 👥 **用户分组**：按分组限制可用资源站（非 localstorage）。
- 📺 **TVBox / OrionTV / Selene**：标准订阅接口，可做 TV 端后端。
- 💬 **弹幕支持**：以 [danmu_api](https://github.com/huangxd-/danmu_api) 为后端，需自行部署。
- 📱 **PWA**：仅 Docker/自托管生产环境启用 Service Worker；Vercel/Netlify/Cloudflare 默认禁用。
- 🌗 **响应式布局**：桌面侧边栏 + 移动底部导航。
- 👿 **智能去广告**：自动跳过切片广告（实验性）。

### 注意：部署后项目为空壳项目，无内置播放源，需要自行收集，需要弹幕请自行部署后端

<details>
  <summary>点击查看项目截图</summary>
  <img src="public/screenshot1.png" alt="项目截图" style="max-width:600px">
</details>

## 🗺 目录

- [MoonTVPlus](#moontvplus)
  - [✨ 功能特性](#-功能特性)
  - [🗺 目录](#-目录)
  - [技术栈](#技术栈)
  - [本地开发](#本地开发)
  - [部署](#部署)
    - [Vercel 部署](#vercel-部署)
    - [Netlify 部署](#netlify-部署)
    - [Cloudflare 部署](#cloudflare-部署)
    - [Docker 部署](#docker-部署)
  - [环境变量](#环境变量)
  - [配置说明](#配置说明)
  - [管理员配置](#管理员配置)
  - [AndroidTV 使用](#androidtv-使用)
  - [TVBox 对接](#tvbox-对接)
  - [Selene 使用](#selene-使用)
  - [安全与隐私提醒](#安全与隐私提醒)
  - [License](#license)
  - [致谢](#致谢)

## 技术栈

| 分类      | 主要依赖                                                                                              |
| --------- | ----------------------------------------------------------------------------------------------------- |
| 前端框架  | [Next.js 14](https://nextjs.org/) · App Router                                                        |
| UI & 样式 | [Tailwind&nbsp;CSS 3](https://tailwindcss.com/)                                                       |
| 语言      | TypeScript 5                                                                                          |
| 播放器    | [ArtPlayer](https://github.com/zhw2590582/ArtPlayer) · [HLS.js](https://github.com/video-dev/hls.js/) |
| 包管理    | pnpm 10 · Node 20（`.nvmrc`）                                                                         |
| 代码质量  | ESLint · Prettier · Jest + `tsc --noEmit`                                                             |
| 存储      | localstorage · Redis · Kvrocks · Upstash Redis · Cloudflare D1                                        |
| 部署      | Docker · Vercel · Netlify · Cloudflare Pages                                                          |

## 本地开发

```bash
nvm use 20
corepack enable && corepack prepare pnpm@10.14.0 --activate
pnpm install --frozen-lockfile

# 最小可用配置：localstorage + 设置登录密码
# .env.local
# PASSWORD=123456
# NEXT_PUBLIC_STORAGE_TYPE=localstorage

pnpm gen:runtime   # 从 config.json 生成 src/lib/runtime.ts（构建前自动执行）
pnpm dev           # http://localhost:3000
pnpm typecheck     # tsc --noEmit
pnpm exec jest --ci
pnpm run lint      # warnings 允许；提交前 lint-staged 会跑 lint:strict
pnpm build
```

> `config.json` 为空 `api_site` 时站点是空壳，搜索/首页无数据属正常，先填 1-2 个 Apple CMS V10 源。

## 部署

本项目**支持 Docker、Vercel、Netlify、Cloudflare** 部署。

存储支持矩阵

|               | Docker | Vercel | Netlify | Cloudflare |
| :-----------: | :----: | :----: | :-----: | :--------: |
| localstorage  |   ✅   |   ✅   |   ✅    |     ✅     |
|  原生 redis   |   ✅   |        |         |            |
|    kvrocks    |   ✅   |        |         |            |
| Cloudflare D1 |        |        |         |     ✅     |
| Upstash Redis |   ☑️   |   ✅   |   ✅    |     ✅     |

✅：经测试支持

☑️：理论上支持，未测试

### Vercel 部署

#### 普通部署（localstorage）

1. **Fork** 本仓库到你的 GitHub 账户。
2. 登陆 [Vercel](https://vercel.com/)，点击 **Add New → Project**，选择 Fork 后的仓库。
3. 设置 `PASSWORD` 环境变量（必填，未设置将无法登录）。
4. 保持默认设置完成首次部署。
5. 如需自定义 `config.json`，请直接修改 Fork 后仓库中该文件后重新部署（构建时烘焙）。
6. 每次 Push 到 `main` 分支将自动触发重新构建。

部署完成后即可通过分配的域名访问，也可以绑定自定义域名。

#### Upstash Redis 支持

0. 完成普通部署并成功访问。
1. 在 [upstash](https://upstash.com/) 注册账号并新建一个 Redis 实例，名称任意。
2. 复制新数据库的 **HTTPS ENDPOINT 和 TOKEN**
3. 返回你的 Vercel 项目，新增环境变量 **UPSTASH_URL 和 UPSTASH_TOKEN**，值为第二步复制的 endpoint 和 token
4. 设置环境变量 NEXT_PUBLIC_STORAGE_TYPE，值为 **upstash**；设置 USERNAME 和 PASSWORD 作为站长账号
5. 重试部署

### Netlify 部署

#### 普通部署（localstorage）

1. **Fork** 本仓库到你的 GitHub 账户。
2. 登陆 [Netlify](https://www.netlify.com/)，点击 **Add New project → Importing an existing project**，授权 Github，选择 Fork 后的仓库。
3. 设置 `PASSWORD` 环境变量（必填）。
4. 保持默认设置完成首次部署。
5. 每次 Push 到 `main` 分支将自动触发重新构建。

部署完成后即可通过分配的域名访问，也可以绑定自定义域名。

#### Upstash Redis 支持

0. 完成普通部署并成功访问。
1. 在 [upstash](https://upstash.com/) 注册账号并新建一个 Redis 实例，名称任意。
2. 复制新数据库的 **HTTPS ENDPOINT 和 TOKEN**
3. 返回你的 Netlify 项目，**Project Configuration → Environment variables** 新增环境变量 **UPSTASH_URL 和 UPSTASH_TOKEN**，值为第二步复制的 endpoint 和 token
4. 设置环境变量 NEXT_PUBLIC_STORAGE_TYPE，值为 **upstash**；设置 USERNAME 和 PASSWORD 作为站长账号
5. 重试部署

### Cloudflare 部署

**Cloudflare Pages 的环境变量尽量设置为密钥而非文本**

#### 普通部署（localstorage）

1. **Fork** 本仓库到你的 GitHub 账户。
2. 登陆 [Cloudflare](https://cloudflare.com)，点击 **计算（Workers）-> Workers 和 Pages**，点击创建
3. 选择 Pages，导入现有的 Git 存储库，选择 Fork 后的仓库
4. 构建命令填写 **pnpm run pages:build**，预设框架为无，**构建输出目录**为 `.vercel/output/static`
5. 保持默认设置完成首次部署。进入设置，将兼容性标志设置为 `nodejs_compat`，无需选择，直接粘贴
6. 首次部署完成后进入设置，新增 PASSWORD 密钥（变量和机密下），而后重试部署。
7. 如需自定义 `config.json`，请直接修改 Fork 后仓库中该文件。
8. 每次 Push 到 `main` 分支将自动触发重新构建。

#### D1 支持

0. 完成普通部署并成功访问
1. 点击 **存储和数据库 -> D1 SQL 数据库**，创建一个新的数据库，名称随意
2. 进入刚创建的数据库，点击左上角的 Explore Data，将[d1-init](d1-init.sql) 中的内容粘贴到 Query 窗口后点击 **Run All**，等待运行完成
3. 返回你的 pages 项目，进入 **设置 -> 绑定**，添加绑定 D1 数据库，选择你刚创建的数据库，变量名称填 **DB**
4. 设置环境变量 NEXT_PUBLIC_STORAGE_TYPE，值为 **d1**；设置 USERNAME 和 PASSWORD 作为站长账号
5. 重试部署

### Docker 部署

GitHub Actions 会在手动触发后构建并推送多架构镜像（`linux/amd64,linux/arm64`）：

```bash
docker pull ghcr.io/aifeifei798/moontvplus:latest
```

如需自行构建：`docker build -t moontvplus:latest .`

#### 直接运行（最简单，localstorage）

```bash
docker run -d --name moontvplus -p 3000:3000 --env PASSWORD=your_password ghcr.io/aifeifei798/moontvplus:latest
```

#### Docker Compose

##### local storage 存储

```yaml
services:
  moontvplus:
    image: ghcr.io/aifeifei798/moontvplus:latest
    container_name: moontvplus
    restart: on-failure
    ports:
      - '3000:3000'
    environment:
      - PASSWORD=password
```

##### Kvrocks 存储（推荐）

```yml
services:
  moontvplus:
    image: ghcr.io/aifeifei798/moontvplus:latest
    container_name: moontvplus
    restart: on-failure
    ports:
      - '3000:3000'
    environment:
      - USERNAME=admin
      - PASSWORD=admin_password
      - NEXT_PUBLIC_STORAGE_TYPE=kvrocks
      - KVROCKS_URL=redis://moontv-kvrocks:6666
    networks:
      - moontv-network
    depends_on:
      - moontv-kvrocks
  moontv-kvrocks:
    image: apache/kvrocks
    container_name: moontv-kvrocks
    restart: unless-stopped
    volumes:
      - kvrocks-data:/var/lib/kvrocks
    networks:
      - moontv-network
networks:
  moontv-network:
    driver: bridge
volumes:
  kvrocks-data:
```

##### Redis 存储（有一定的丢数据风险）

```yml
services:
  moontvplus:
    image: ghcr.io/aifeifei798/moontvplus:latest
    container_name: moontvplus
    restart: on-failure
    ports:
      - '3000:3000'
    environment:
      - USERNAME=admin
      - PASSWORD=admin_password
      - NEXT_PUBLIC_STORAGE_TYPE=redis
      - REDIS_URL=redis://moontv-redis:6379
    networks:
      - moontv-network
    depends_on:
      - moontv-redis
  moontv-redis:
    image: redis:alpine
    container_name: moontv-redis
    restart: unless-stopped
    networks:
      - moontv-network
    # 请开启持久化，否则升级/重启后数据丢失
    volumes:
      - ./data:/data
networks:
  moontv-network:
    driver: bridge
```

##### Upstash 存储

```yaml
services:
  moontvplus:
    image: ghcr.io/aifeifei798/moontvplus:latest
    container_name: moontvplus
    restart: on-failure
    ports:
      - '3000:3000'
    environment:
      - USERNAME=admin
      - PASSWORD=admin_password
      - NEXT_PUBLIC_STORAGE_TYPE=upstash
      - UPSTASH_URL= https 开头的 HTTPS ENDPOINT
      - UPSTASH_TOKEN= TOKEN
```

## 环境变量

| 变量                                | 说明                                                               | 可选值                                    | 默认值                                                                                                                     |
| ----------------------------------- | ------------------------------------------------------------------ | ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| USERNAME                            | 非 localstorage 部署时的站长账号（localstorage 下可空）            | 任意字符串                                | （空）                                                                                                                     |
| PASSWORD                            | 登录密码/签名密钥，**必填**，空密码拒绝登录                        | 任意字符串                                | （空）                                                                                                                     |
| NEXT_PUBLIC_SITE_NAME               | 站点名称（`SITE_NAME` 仅作兼容回退）                               | 任意字符串                                | MoonTV                                                                                                                     |
| ANNOUNCEMENT                        | 站点公告                                                           | 任意字符串                                | 本网站仅提供影视信息搜索服务，所有内容均来自第三方网站。本站不存储任何视频资源，不对任何内容的准确性、合法性、完整性负责。 |
| NEXT_PUBLIC_STORAGE_TYPE            | 播放记录/收藏的存储方式                                            | localstorage、redis、kvrocks、d1、upstash | localstorage                                                                                                               |
| REDIS_URL                           | redis 连接 url                                                     | 连接 url                                  | 空                                                                                                                         |
| KVROCKS_URL                         | kvrocks 连接 url（协议同 redis）                                   | 连接 url                                  | 空                                                                                                                         |
| UPSTASH_URL                         | upstash redis 连接 url                                             | 连接 url                                  | 空                                                                                                                         |
| UPSTASH_TOKEN                       | upstash redis 连接 token                                           | 连接 token                                | 空                                                                                                                         |
| NEXT_PUBLIC_ENABLE_REGISTER         | 是否开放注册，仅在非 localstorage 部署时生效                       | true / false                              | false                                                                                                                      |
| NEXT_PUBLIC_SEARCH_MAX_PAGE         | 搜                                                                 |
| 索接口可拉取的最大页数              | 1-50                                                               | 5                                         |
| NEXT_PUBLIC_DOUBAN_PROXY_TYPE       | 豆瓣数据源请求方式                                                 | 见下方                                    | direct                                                                                                                     |
| NEXT_PUBLIC_DOUBAN_PROXY            | 自定义豆瓣数据代理 URL                                             | url prefix                                | (空)                                                                                                                       |
| NEXT_PUBLIC_DOUBAN_IMAGE_PROXY_TYPE | 豆瓣图片代理类型                                                   | 见下方                                    | direct                                                                                                                     |
| NEXT_PUBLIC_DOUBAN_IMAGE_PROXY      | 自定义豆瓣图片代理 URL                                             | url prefix                                | (空)                                                                                                                       |
| NEXT_PUBLIC_DISABLE_YELLOW_FILTER   | 关闭色情内容过滤                                                   | true/false                                | false                                                                                                                      |
| NEXT_PUBLIC_DANMU_API_BASE_URL      | 弹幕接口地址                                                       | 接口地址                                  | (空)                                                                                                                       |
| TVBOX_ENABLED                       | 本地模式 TVBox 开关                                                | true/false                                | true                                                                                                                       |
| CRON_SECRET                         | 定时任务鉴权密钥（设置后调用 /api/cron 必须携带 x-cron-secret 头） | 任意字符串                                | (空，即不校验，仅建议 Docker/自托管设置)                                                                                   |
| CRON_INTERVAL_MINUTES               | Docker 启动脚本执行 cron 的间隔分钟数，0 表示只跑启动那一次        | 数字                                      | 60                                                                                                                         |
| CRON_CONCURRENCY                    | cron 刷新详情的并发数（1~20）                                      | 数字                                      | 5                                                                                                                          |
| CRON_ACTIVE_DAYS                    | 仅刷新 N 天内活跃用户，0 表示不过滤（无 lastOnline 视为活跃）      | 数字                                      | 7                                                                                                                          |
| CONFIG_CACHE_TTL                    | 非 localstorage 下配置内存缓存秒数，管理后台写入立即失效           | 数字                                      | 15                                                                                                                         |

NEXT_PUBLIC_DOUBAN_PROXY_TYPE 选项解释：

- direct: 由服务器直接请求豆瓣源站
- cors-proxy-zwei: 浏览器向 cors proxy 请求豆瓣数据，该 cors proxy 由 [Zwei](https://github.com/bestzwei) 搭建
- cmliussss-cdn-tencent: 浏览器向豆瓣 CDN 请求数据，该 CDN 由 [CMLiussss](https://github.com/cmliu) 搭建，并由腾讯云 cdn 提供加速
- cmliussss-cdn-ali: 浏览器向豆瓣 CDN 请求数据，该 CDN 由 [CMLiussss](https://github.com/cmliu) 搭建，并由阿里云 cdn 提供加速

- custom: 用户自定义 proxy，由 NEXT_PUBLIC_DOUBAN_PROXY 定义

NEXT_PUBLIC_DOUBAN_IMAGE_PROXY_TYPE 选项解释：

- direct：由浏览器直接请求豆瓣分配的默认图片域名
- server：由服务器代理请求豆瓣分配的默认图片域名
- img3：由浏览器请求豆瓣官方的精品 cdn（阿里云）
- cmliussss-cdn-tencent：由浏览器请求豆瓣 CDN，该 CDN 由 [CMLiussss](https://github.com/cmliu) 搭建，并由腾讯云 cdn 提供加速
- cmliussss-cdn-ali：由浏览器请求豆瓣 CDN，该 CDN 由 [CMLiussss](https://github.com/cmliu) 搭建，并由阿里云 cdn 提供加速
- custom: 用户自定义 proxy，由 NEXT_PUBLIC_DOUBAN_IMAGE_PROXY 定义

## 配置说明

localstorage 模式所有可自定义项集中在根目录的 `config.json`；非 localstorage 可在部署好的网页 `/admin` 中直接配置。

```json
{
  "cache_time": 7200,
  "api_site": {
    "dyttzy": {
      "api": "http://caiji.dyttzyapi.com/api.php/provide/vod",
      "name": "电影天堂资源",
      "detail": "http://caiji.dyttzyapi.com"
    }
    // ...更多站点
  },
  "custom_category": [
    {
      "name": "华语",
      "type": "movie",
      "query": "华语"
    }
  ]
}
```

- `cache_time`：接口缓存时间（秒）。
- `api_site`：你可以增删或替换任何资源站，字段说明：
  - `key`：唯一标识，保持小写字母/数字。
  - `api`：资源站提供的 `vod` JSON API 根地址。
  - `name`：在人机界面中展示的名称。
  - `detail`：（可选）部分无法通过 API 获取剧集详情的站点，需要提供网页详情根 URL，用于爬取。
- `custom_category`：自定义分类配置，用于在导航中添加个性化的影视分类。以 type + query 作为唯一标识。支持以下字段：
  - `name`：分类显示名称（可选，如不提供则使用 query 作为显示名）
  - `type`：分类类型，支持 `movie`（电影）或 `tv`（电视剧）
  - `query`：搜索关键词，用于在豆瓣 API 中搜索相关内容

custom_category 支持的自定义分类已知如下：

- movie：热门、最新、经典、豆瓣高分、冷门佳片、华语、欧美、韩国、日本、动作、喜剧、爱情、科幻、悬疑、恐怖、治愈
- tv：热门、美剧、英剧、韩剧、日剧、国产剧、港剧、日本动画、综艺、纪录片

也可输入如 "哈利波特" 效果等同于豆瓣搜索

MoonTVPlus 支持标准的苹果 CMS V10 API 格式。

- Docker/自托管：修改 `config.json` 后重启容器即生效（`DOCKER_ENV=true` 时运行时读取）。
- Vercel/Netlify/Cloudflare：`config.json` 在构建时烘焙进 `src/lib/runtime.ts`，修改后需重新部署。

## 管理员配置

**该特性目前仅支持通过非 localstorage 存储的部署方式使用**

支持在运行时动态变更服务配置

设置环境变量 USERNAME 和 PASSWORD 即为站长用户，站长可设置用户为管理员

站长或管理员访问 `/admin` 即可进行管理员配置（含用户/分组/资源站/分类/订阅/数据迁移）。

## AndroidTV 使用

目前该项目可以配合 [OrionTV](https://github.com/zimplexing/OrionTV) 在 Android TV 上使用，可以直接作为 OrionTV 后端

## TVBox 对接

- 非本地模式：在首页右上角的“设置”中开启“启用 TVBox 接口”，可随机生成或自定义访问密码，复制 `https://你的域名/api/tvbox/config?pwd=你的口令&un=...` 填入 TVBox 订阅即可。
- 如需关闭对接，关闭开关即可。

### 本地存储(localstorage)模式

- 开关由环境变量控制：`TVBOX_ENABLED=true|false`（默认 true，未设置即开启）
- 接口访问口令使用登录密码：`PASSWORD`，调用时必须显式携带 `?pwd=`（不再自动填充，匿名无口令直接 401）
- 生成的订阅地址示例：`https://你的域名/api/tvbox/config?pwd=$PASSWORD`
- 设置面板中的开关与保存在本地模式下仅用于展示（被禁用），请通过环境变量控制。
- 管理后台 `/api/admin/tvbox` 在本地模式下同样需要先登录，不再匿名返回密码。

## Selene 使用

该项目已兼容 [Selene](https://github.com/MoonTechLab/Selene) 在移动端上使用，可以直接作为 Selene 后端(本地存储不支持)

## 安全与隐私提醒

### 请设置密码保护并关闭公网注册

为了您的安全和避免潜在的法律风险，我们要求在部署时设置密码保护并**强烈建议关闭公网注册**：

- **避免公开访问**：不设置密码的实例任何人都可以访问，可能被恶意利用（本分支空 `PASSWORD` 直接拒绝登录并跳警告页）
- **防范版权风险**：公开的视频搜索服务可能面临版权方的投诉举报
- **保护个人隐私**：设置密码可以限制访问范围，保护您的使用记录

当前分支的安全行为：

- 登录凭证为 `httpOnly` 的 `auth` Cookie（前端仅读不含密钥的 `auth_info`）；本地模式不再下发明文密码，签名绑定角色+时间戳，7 天过期。
- TVBox 全接口必须显式携带口令；`image-proxy/m3u8` 仅允许公网 http/https 并拦截内网地址，图片限 10MB。
- 自托管建议设置 `CRON_SECRET`，避免 `/api/cron` 被匿名触发全量刷新。

### 部署要求

1. **设置环境变量 `PASSWORD`**：为您的实例设置一个强密码
2. **仅供个人使用**：请勿将您的实例链接公开分享或传播
3. **遵守当地法律**：请确保您的使用行为符合当地法律法规

### 重要声明

- 本项目仅供学习和个人使用
- 请勿将部署的实例用于商业用途或公开服务
- 如因公开分享导致的任何法律问题，用户需自行承担责任
- 项目开发者不对用户的使用行为承担任何法律责任

## License

[MIT](LICENSE) © 2025 MoonTVPlus & Contributors

## 致谢

- [MoonTechLab/LunaTV](https://github.com/MoonTechLab/LunaTV) — 本分支的直接上游。
- [LibreTV](https://github.com/LibreSpark/LibreTV) — 上游的灵感来源。
- [ts-nextjs-tailwind-starter](https://github.com/theodorusclarence/ts-nextjs-tailwind-starter) — 项目最初基于该脚手架。
- [ArtPlayer](https://github.com/zhw2590582/ArtPlayer) — 提供强大的网页视频播放器。
- [HLS.js](https://github.com/video-dev/hls.js) — 实现 HLS 流媒体在浏览器中的播放支持。
- [Zwei](https://github.com/bestzwei) — 提供获取豆瓣数据的 cors proxy
- [CMLiussss](https://github.com/cmliu) — 提供豆瓣 CDN 服务
- 感谢所有提供免费影视接口的站点。
