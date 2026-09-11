// 此文件由 scripts/convert-changelog.js 自动生成
// 请勿手动编辑

export interface ChangelogEntry {
  version: string;
  date: string;
  added: string[];
  changed: string[];
  fixed: string[];
}

export const changelog: ChangelogEntry[] = [
  {
    version: '4.0.0',
    date: '2026-09-11',
    added: [
      '密码哈希存储（SHA-256 + 每用户随机盐，旧明文登录时自动升级）',
      'SSRF 纵深防御：safeFetch 手动跟跳转逐跳复检，拦截十六/十/八进制变形 IP 与 IPv6 映射地址',
      '统一日志门面 src/lib/logger.ts（LOG_LEVEL 门控，生产默认仅 warn/error）',
      'Docker 稳健 runtime 替换脚本（兼容单双引号与空格，残留 edge 即 fail-fast）',
      'engines 锁定 Node 22 + pnpm 10.14，typecheck/test 前自动 gen',
    ],
    changed: [
      '【不兼容】cron 鉴权只认 x-cron-secret 请求头，不再接受 ?secret=（防 URL/日志/CDN 泄露）',
      '用户相关搜索/资源接口缓存改为 private + no-store，防 CDN 跨用户串味',
      'Upstash 清数据改为命名空间 scan 删除，不再 flushall 影响共享实例',
      '边下边存模式分片失败即中断（不再静默跳片产出损坏文件）',
      'MP4 批量转码仅首片带 initSegment，避免重复 moov',
      'Dockerfile 基座 node:20 → 22，与 .nvmrc/CI 对齐',
      '移除零引用的 @vidstack/react 依赖',
      'TVBox 分类内调豆瓣不再经 HTTP 回环（防 Host 投毒 SSRF）',
      'lint:strict 清零（bare catch、logger 收敛、无用依赖清理）',
    ],
    fixed: [
      '变形 IP（0x7f/0177/大整数/混合点分）与 IPv6 内网/映射地址 SSRF 绕过',
      'image-proxy 302 跳转到内网/元数据地址',
      '下游 fetch 无 SSRF 校验，分页单页失败丢弃首批结果',
      '订阅接口无 SSRF/超时/体积限制',
      'cron 日志打印 request.url 泄露 secret、刷新任务不 await',
      'D1 存储键含 + 时错位解析，cron 同类问题',
      'm3u8 相对 URL 拼接错误、EXTINF 非法值污染时长、分片无超时、SAMPLE-AES 误解密、Key 拉取无超时、重试无退避、pendingWrites 无界',
      'mp4 转码 data 竞态、reset 泄漏/丢 writeError、finish 吞错、TS 格式误判（仅 188 字节）',
      '豆瓣 tag/category 未编码、AbortError 与下游超时口径不一、Edge 缓存哈希碰撞率高',
      'release/docker/version-manager 工作流断点（产物路径、action 版本、git add 范围）',
    ],
  },
  {
    version: '3.9.5',
    date: '2026-09-03',
    added: ['新增追更功能'],
    changed: [
      '首页选项卡添加追更页',
      '右键及长按弹窗和播放页面添加追更按钮',
      'd1-init新增追更表(使用cf+d1部署的请自行更新数据库)',
    ],
    fixed: ['修复用户未登录状态调用user/online 接口问题'],
  },
  {
    version: '3.8.4',
    date: '2026-08-26',
    added: [
      // 无新增内容
    ],
    changed: [
      // 无变更内容
    ],
    fixed: [
      '修复容器环境下tvbox对地址获取的问题',
      '修复切换集数时推出全屏的问题',
    ],
  },
  {
    version: '3.8.2',
    date: '2026-01-04',
    added: [
      '导航栏加入下载管理页面',
      '视频下载中可查看片段下载状况: 可手动进行错误重试',
      '可设置自动弹幕获取尝试次数',
      '普通模式下载可在下载过程中立即存储: 存储已经下载的片段，不影响正常下载过程',
    ],
    changed: [
      '导航栏框架分离: 切换导航页面时不再刷新',
      '完善边下边存功能: 可手动选择三种下载模式',
      '完善mp4转码功能',
    ],
    fixed: ['修复移动端边下边存文件后缀错误', '修复登录页面导航栏错误加载'],
  },
  {
    version: '3.7.0',
    date: '2026-01-01',
    added: [
      '加入M3U8视频在线下载功能',
      '支持多线程并发下载（1-16线程可调）',
      '支持TS和MP4格式转换',
      '支持范围下载（指定起始和结束片段）',
      '支持AES-128加密视频解密',
      '支持边下边存功能（解决大文件内存占用问题）',
      '下载任务管理（暂停/继续/删除）',
      '批量操作（全部开始/全部暂停/清空全部）',
      '本地存储任务持久化',
      '自动保留用户配置偏好（格式、线程数、边下边存）',
    ],
    changed: ['播放页面添加下载视频按钮', '显示用户最后在线时间'],
    fixed: ['修复历史影片重复保存问题', '修复设置重置不完全的问题'],
  },
  {
    version: '3.6.2',
    date: '2025-12-09',
    added: ['添加默认优选换源开关'],
    changed: [
      '优化弹幕自动加载',
      '默认为空壳项目(无内置源和弹幕)',
      '优化优选换源加载形式',
    ],
    fixed: ['OrionTV本地存储播放无源'],
  },
  {
    version: '3.5.7',
    date: '2025-12-06',
    added: ['自动弹幕加载功能', '订阅配置功能'],
    changed: ['优化弹幕加载', '使用artplayer弹幕库', '弹幕加载状态提示'],
    fixed: [
      // 无修复内容
    ],
  },
  {
    version: '3.5.0',
    date: '2025-11-30',
    added: ['加入视频播放弹幕功能'],
    changed: [
      '可手动搜索弹幕源',
      '可控制弹幕的显示行数',
      '可控制弹幕的显示密度',
      '分页渲染搜索结果',
    ],
    fixed: [
      '修复CF数据导出的不完整',
      '修复docker非本地存储管理员配置的读取修改',
    ],
  },
  {
    version: '3.4.2',
    date: '2025-10-21',
    added: ['添加数据迁移功能', '加入卡片右键长按选项卡'],
    changed: [
      '调整选项卡收藏按钮显示逻辑',
      '微调UI及收藏按钮显示逻辑',
      '优化移动端卡片长按复制问题',
      '微调播放页面UI',
      '简化调整播放页面',
      '调整分组配置弹窗UI',
      '微调视频源配置UI',
    ],
    fixed: ['修复CF数据迁入问题', '修复本地存储搜索', '修复本地存储TVBox接口'],
  },
  {
    version: '3.3.0',
    date: '2025-10-16',
    added: ['用户组分配视频源功能', '视频源配置支持批量操作', '添加加载动画'],
    changed: [
      '非本地存储默认关闭TVBox',
      '简化明暗模式变化',
      '优化 docker 构建流程',
    ],
    fixed: ['非本地模式视频源配置初始化'],
  },
  {
    version: '3.2.0',
    date: '2025-10-04',
    added: ['添加Docker镜像自动构建工作流', '搜索建议添加开关控制'],
    changed: [
      '优化搜索设置UI布局',
      '调整搜索建议排序逻辑',
      '优化搜索参数设置逻辑',
      '调整并简化播放页面UI',
      '调整移动端播放页面UI',
      '微调站点配置UI',
      '微调TVBox配置UI',
    ],
    fixed: [
      // 无修复内容
    ],
  },
  {
    version: '3.1.0',
    date: '2025-10-02',
    added: [
      // 无新增内容
    ],
    changed: [
      'TVBox的设置改为在管理面板中，原位置显示状态及其链接',
      '修改桌面端导航栏布局为顶栏',
      '桌面搜索页改为顶栏搜索框',
    ],
    fixed: ['卡片显示详情链接跳转'],
  },
  {
    version: '3.0.0',
    date: '2025-09-30',
    added: ['添加TVBox配置接口', '添加对Selene的兼容'],
    changed: [
      // 无变更内容
    ],
    fixed: ['修复首页动漫接口引起的崩溃', '修复docker播放刷新崩溃问题'],
  },
  {
    version: '2.9.1',
    date: '2025-09-19',
    added: [
      // 无新增内容
    ],
    changed: [
      '搜索栏左侧源配置获取采用缓存',
      '优化搜索结果的筛选组件UI',
      '搜索结果区分为包含与不包含搜索词的结果',
      '删除播放存储无用缓存的逻辑',
      '开放分类配置中的添加分类',
    ],
    fixed: ['缓存失效时播放加载源不一致', '修复d1数据库管理配置保存报错'],
  },
  {
    version: '2.8.4',
    date: '2025-09-14',
    added: ['添加搜索结果的排序选择'],
    changed: ['优化搜索逻辑'],
    fixed: ['修复对OrionTV的兼容', '修复d1数据库继续观看无法收藏'],
  },
  {
    version: '2.8.0',
    date: '2025-09-09',
    added: ['添加cf部署', '添加d1支持'],
    changed: [
      // 无变更内容
    ],
    fixed: [
      // 无修复内容
    ],
  },
  {
    version: '2.7.6',
    date: '2025-09-04',
    added: [
      // 无新增内容
    ],
    changed: ['调整搜索超时时间可选范围，及默认值', '添加docker部署支持'],
    fixed: ['修复播放中搜索超时时间不生效'],
  },
  {
    version: '2.7.4',
    date: '2025-08-31',
    added: [
      // 无新增内容
    ],
    changed: [
      '可自定义搜索超时时间',
      '播放时的搜索播放源超时时间同上',
      '细化失败源原因的展示',
    ],
    fixed: ['修复播放搜索无结果也缓存在localstorage'],
  },
  {
    version: '2.7.0',
    date: '2025-08-30',
    added: ['搜索添加对源的选择功能'],
    changed: ['优化筛选', '简洁模式跳过豆瓣数据加载'],
    fixed: [
      // 无修复内容
    ],
  },
  {
    version: '2.6.4',
    date: '2025-08-30',
    added: [
      // 无新增内容
    ],
    changed: ['优化搜索:调整超时时间(获取首页超时时间过长)'],
    fixed: ['修复播放缓存key不唯一', '修复聚合结果不准确'],
  },
  {
    version: '2.6.1',
    date: '2025-08-29',
    added: ['添加简洁模式'],
    changed: [
      // 无变更内容
    ],
    fixed: [
      // 无修复内容
    ],
  },
  {
    version: '2.5.1',
    date: '2025-08-28',
    added: [
      // 无新增内容
    ],
    changed: [
      '优化收藏状态检测,减少数据库请求',
      '优化搜索筛选展示逻辑',
      '边缘点击不触发播放',
    ],
    fixed: ['修复动漫番剧详情页面跳转'],
  },
  {
    version: '2.4.7',
    date: '2025-08-26',
    added: ['搜索结果添加筛选功能'],
    changed: [
      // 无变更内容
    ],
    fixed: ['主页展示评分', '搜索结果展示年份'],
  },
  {
    version: '2.4.0',
    date: '2025-08-25',
    added: [
      // 无新增内容
    ],
    changed: ['站点配置可直接修改', '进一步优化搜索速度'],
    fixed: ['修复搜索结果只能打开同一个'],
  },
  {
    version: '2.3.6',
    date: '2025-08-24',
    added: [
      // 无新增内容
    ],
    changed: ['优化播放优选换源'],
    fixed: ['修复播放视频源缓存'],
  },
  {
    version: '2.3.4',
    date: '2025-08-24',
    added: [
      // 无新增内容
    ],
    changed: [
      // 无变更内容
    ],
    fixed: [
      '修复管理功能不生效',
      '修复netlify无法部署',
      '修复非本地数据库初始化',
    ],
  },
  {
    version: '2.3.1',
    date: '2025-08-23',
    added: [
      // 无新增内容
    ],
    changed: [
      // 无变更内容
    ],
    fixed: [
      '修复配置文件不生效',
      '修复无config.json文件报错',
      '修复失败源不准确',
    ],
  },
  {
    version: '2.2.8',
    date: '2025-08-22',
    added: [
      // 无新增内容
    ],
    changed: ['转移视频源优选按钮至播放页面'],
    fixed: ['优化播放换源', '优化失败源显示逻辑', '修复搜索路由问题'],
  },
  {
    version: '2.2.1',
    date: '2025-08-22',
    added: ['搜索结果展示失败源'],
    changed: ['移除无效代理Cors Anywhere'],
    fixed: ['修复一次搜索两个请求的问题'],
  },
  {
    version: '2.1.0',
    date: '2025-08-21',
    added: ['支持流式搜索搜索模式', '搜索结果展示视频源'],
    changed: ['重新支持localstorage', '独立缓存播放源'],
    fixed: ['修复视频播放缓存逻辑问题'],
  },
  {
    version: '2.0.1',
    date: '2025-08-13',
    added: [
      // 无新增内容
    ],
    changed: ['版本检查和变更日志请求 Github'],
    fixed: ['微调管理面板样式'],
  },
  {
    version: '2.0.0',
    date: '2025-08-13',
    added: [
      '支持配置文件在线配置和编辑',
      '搜索页搜索框实时联想',
      '去除对 localstorage 模式的支持',
    ],
    changed: ['播放记录删除按钮改为垃圾桶图标以消除歧义'],
    fixed: ['限制设置面板的最大长度，防止超出视口'],
  },
  {
    version: '1.1.1',
    date: '2025-08-12',
    added: [
      // 无新增内容
    ],
    changed: ['修正 zwei 提供的 cors proxy 地址', '移除废弃代码'],
    fixed: ['[运维] docker workflow release 日期使用东八区日期'],
  },
  {
    version: '1.1.0',
    date: '2025-08-12',
    added: ['每日新番放送功能，展示每日新番放送的番剧'],
    changed: [
      // 无变更内容
    ],
    fixed: ['修复远程 CHANGELOG 无法提取变更内容的问题'],
  },
  {
    version: '1.0.5',
    date: '2025-08-12',
    added: [
      // 无新增内容
    ],
    changed: ['实现基于 Git 标签的自动 Release 工作流'],
    fixed: [
      // 无修复内容
    ],
  },
  {
    version: '1.0.4',
    date: '2025-08-11',
    added: ['优化版本管理工作流，实现单点修改'],
    changed: ['版本号现在从 CHANGELOG 自动提取，无需手动维护 VERSION.txt'],
    fixed: [
      // 无修复内容
    ],
  },
  {
    version: '1.0.3',
    date: '2025-08-11',
    added: [
      // 无新增内容
    ],
    changed: ['升级播放器 Artplayer 至版本 5.2.5'],
    fixed: [
      // 无修复内容
    ],
  },
  {
    version: '1.0.2',
    date: '2025-08-11',
    added: [
      // 无新增内容
    ],
    changed: [
      '版本号比较机制恢复为数字比较，仅当最新版本大于本地版本时才认为有更新',
      '[运维] 自动替换 version.ts 中的版本号为 VERSION.txt 中的版本号',
    ],
    fixed: [
      // 无修复内容
    ],
  },
  {
    version: '1.0.1',
    date: '2025-08-11',
    added: [
      // 无新增内容
    ],
    changed: [
      // 无变更内容
    ],
    fixed: ['修复版本检查功能，只要与最新版本号不一致即认为有更新'],
  },
  {
    version: '1.0.0',
    date: '2025-08-10',
    added: [
      '基于 Semantic Versioning 的版本号机制',
      '版本信息面板，展示本地变更日志和远程更新日志',
    ],
    changed: [
      // 无变更内容
    ],
    fixed: [
      // 无修复内容
    ],
  },
];

export default changelog;
