/* eslint-disable @typescript-eslint/no-explicit-any, no-console, @typescript-eslint/no-non-null-assertion */

import { getStorage } from '@/lib/db';

import { AdminConfig } from './admin.types';
import runtimeConfig from './runtime';

export interface ApiSite {
  key: string;
  api: string;
  name: string;
  detail?: string;
}

interface ConfigFileStruct {
  cache_time?: number;
  api_site: {
    [key: string]: ApiSite;
  };
  custom_category?: {
    name?: string;
    type: 'movie' | 'tv';
    query: string;
  }[];
}

export const API_CONFIG = {
  search: {
    path: '?ac=videolist&wd=',
    pagePath: '?ac=videolist&wd={query}&pg={page}',
    headers: {
      'User-Agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
      Accept: 'application/json',
    },
  },
  detail: {
    path: '?ac=videolist&ids=',
    headers: {
      'User-Agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
      Accept: 'application/json',
    },
  },
};

// 在模块加载时根据环境决定配置来源
let fileConfig: ConfigFileStruct;
let cachedConfig: AdminConfig;
// 非 localstorage 下 DB 配置的内存短 TTL（毫秒），命中则跳过读库。
// 后台管理写入走 setAdminConfig 会立即失效；直接调 storage.setAdminConfig 的
// 旧路径最多延迟一个 TTL 可见（默认 15s），serverless 多实例同理最终一致。
let cachedAt = 0;

function getConfigCacheTtlMs(): number {
  const sec = Number(process.env.CONFIG_CACHE_TTL || 15);
  if (!Number.isFinite(sec) || sec < 0) return 15000;
  return sec * 1000;
}

export function invalidateConfigCache(): void {
  cachedAt = 0;
}

function getSiteNameEnv(): string {
  return process.env.NEXT_PUBLIC_SITE_NAME || process.env.SITE_NAME || 'MoonTV';
}

// Docker/nodejs 下从磁盘读取 config.json；失败时返回空结构而非抛错
function loadFileConfigFromDisk(): ConfigFileStruct {
  try {
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const _require = eval('require') as NodeJS.Require;
    const fs = _require('fs') as typeof import('fs');
    const path = _require('path') as typeof import('path');
    const configPath = path.join(process.cwd(), 'config.json');
    const raw = fs.readFileSync(configPath, 'utf-8');
    const parsed = JSON.parse(raw) as ConfigFileStruct;
    console.log('load dynamic config success');
    return {
      cache_time: parsed.cache_time,
      api_site: parsed.api_site || {},
      custom_category: parsed.custom_category || [],
    };
  } catch (e) {
    console.error('读取 config.json 失败，使用空配置:', e);
    return { api_site: {} } as ConfigFileStruct;
  }
}

function loadBuildTimeFileConfig(): ConfigFileStruct {
  const cfg = runtimeConfig as unknown as ConfigFileStruct;
  return {
    cache_time: cfg?.cache_time,
    api_site: cfg?.api_site || {},
    custom_category: cfg?.custom_category || [],
  };
}

function mergeSourceConfigs(
  adminConfig: AdminConfig,
  file: ConfigFileStruct
): void {
  const apiSiteEntries = Object.entries(file.api_site || {});
  const sourceConfigMap = new Map(
    (adminConfig.SourceConfig || []).map((s) => [s.key, s])
  );
  apiSiteEntries.forEach(([key, site]) => {
    const existing = sourceConfigMap.get(key);
    if (existing) {
      existing.name = site.name;
      existing.api = site.api;
      existing.detail = site.detail;
      existing.from = 'config';
    } else {
      sourceConfigMap.set(key, {
        key,
        name: site.name,
        api: site.api,
        detail: site.detail,
        from: 'config',
        disabled: false,
      });
    }
  });
  const keys = new Set(apiSiteEntries.map(([k]) => k));
  sourceConfigMap.forEach((s) => {
    if (!keys.has(s.key)) s.from = 'custom';
  });
  adminConfig.SourceConfig = Array.from(sourceConfigMap.values());
}

function mergeCustomCategories(
  adminConfig: AdminConfig,
  file: ConfigFileStruct,
  onlyConfigFrom = false
): void {
  const customCategories = file.custom_category || [];
  if (!adminConfig.CustomCategories) adminConfig.CustomCategories = [];
  const map = new Map(
    adminConfig.CustomCategories.map((c) => [c.query + c.type, c])
  );
  customCategories.forEach((category) => {
    const key = category.query + category.type;
    const existed = map.get(key);
    if (existed) {
      if (!onlyConfigFrom || existed.from === 'config') {
        existed.name = category.name;
        existed.query = category.query;
        existed.type = category.type;
        existed.from = 'config';
        if (!onlyConfigFrom) existed.disabled = false;
        else {
          existed.from = 'config';
          existed.disabled = false;
        }
      }
    } else {
      map.set(key, {
        name: category.name,
        type: category.type,
        query: category.query,
        from: 'config',
        disabled: false,
      });
    }
  });
  const keys = new Set(customCategories.map((c) => c.query + c.type));
  map.forEach((c) => {
    if (!keys.has(c.query + c.type)) c.from = 'custom';
  });
  adminConfig.CustomCategories = Array.from(map.values());
}

export function refineConfig(adminConfig: AdminConfig): AdminConfig {
  try {
    fileConfig = JSON.parse(adminConfig.ConfigFile) as ConfigFileStruct;
  } catch (e) {
    fileConfig = {} as ConfigFileStruct;
  }
  // 合并文件中的源信息
  const apiSiteEntries = Object.entries(fileConfig.api_site || []);
  const sourceConfigMap = new Map(
    (adminConfig.SourceConfig || []).map((s) => [s.key, s])
  );

  apiSiteEntries.forEach(([key, site]) => {
    const existingSource = sourceConfigMap.get(key);
    if (existingSource) {
      // 如果已存在，只覆盖 name、api、detail 和 from
      existingSource.name = site.name;
      existingSource.api = site.api;
      existingSource.detail = site.detail;
      existingSource.from = 'config';
    } else {
      // 如果不存在，创建新条目
      sourceConfigMap.set(key, {
        key,
        name: site.name,
        api: site.api,
        detail: site.detail,
        from: 'config',
        disabled: false,
      });
    }
  });

  // 检查现有源是否在 fileConfig.api_site 中，如果不在则标记为 custom
  const apiSiteKeys = new Set(apiSiteEntries.map(([key]) => key));
  sourceConfigMap.forEach((source) => {
    if (!apiSiteKeys.has(source.key)) {
      source.from = 'custom';
    }
  });

  // 将 Map 转换回数组
  adminConfig.SourceConfig = Array.from(sourceConfigMap.values());

  // 覆盖 CustomCategories
  const customCategories = fileConfig.custom_category || [];
  const customCategoriesMap = new Map(
    (adminConfig.CustomCategories || []).map((c) => [c.query + c.type, c])
  );

  customCategories.forEach((category) => {
    const key = category.query + category.type;
    const existedCategory = customCategoriesMap.get(key);
    if (existedCategory) {
      existedCategory.name = category.name;
      existedCategory.query = category.query;
      existedCategory.type = category.type;
      existedCategory.from = 'config';
    } else {
      customCategoriesMap.set(key, {
        name: category.name,
        type: category.type,
        query: category.query,
        from: 'config',
        disabled: false,
      });
    }
  });

  // 检查现有 CustomCategories 是否在 fileConfig.custom_category 中，如果不在则标记为 custom
  const customCategoriesKeys = new Set(
    customCategories.map((c) => c.query + c.type)
  );
  customCategoriesMap.forEach((category) => {
    if (!customCategoriesKeys.has(category.query + category.type)) {
      category.from = 'custom';
    }
  });

  // 将 Map 转换回数组
  adminConfig.CustomCategories = Array.from(customCategoriesMap.values());

  // 同步 cache_time 到 SiteConfig.SiteInterfaceCacheTime
  if (fileConfig.cache_time !== undefined) {
    adminConfig.SiteConfig.SiteInterfaceCacheTime = fileConfig.cache_time;
  }

  return adminConfig;
}

async function initConfig() {
  if (cachedConfig) {
    return;
  }

  if (process.env.DOCKER_ENV === 'true') {
    fileConfig = loadFileConfigFromDisk();
  } else {
    // 默认使用编译时生成的配置
    fileConfig = loadBuildTimeFileConfig();
  }
  const storageType = process.env.NEXT_PUBLIC_STORAGE_TYPE || 'localstorage';
  if (storageType !== 'localstorage') {
    // 数据库存储，读取并补全管理员配置
    const storage = getStorage();

    try {
      // 尝试从数据库获取管理员配置
      let adminConfig: AdminConfig | null = null;
      if (storage && typeof (storage as any).getAdminConfig === 'function') {
        adminConfig = await (storage as any).getAdminConfig();
      }

      // 获取所有用户名，用于补全 Users
      let userNames: string[] = [];
      if (storage && typeof (storage as any).getAllUsers === 'function') {
        try {
          userNames = await (storage as any).getAllUsers();
        } catch (e) {
          console.error('获取用户列表失败:', e);
        }
      }

      if (adminConfig) {
        try {
          const parsed = JSON.parse(adminConfig.ConfigFile) as ConfigFileStruct;
          fileConfig = {
            cache_time: parsed.cache_time,
            api_site: parsed.api_site || {},
            custom_category: parsed.custom_category || [],
          };
        } catch (e) {
          console.error('解析配置文件失败:', e);
          fileConfig = { api_site: {} } as ConfigFileStruct;
        }
        mergeSourceConfigs(adminConfig, fileConfig);
        mergeCustomCategories(adminConfig, fileConfig);

        const existedUsers = new Set(
          (adminConfig.UserConfig.Users || []).map((u) => u.username)
        );
        userNames.forEach((uname) => {
          if (!existedUsers.has(uname)) {
            adminConfig!.UserConfig.Users.push({
              username: uname,
              role: 'user',
            });
          }
        });
        // 站长
        const ownerUser = process.env.USERNAME;
        if (ownerUser) {
          adminConfig!.UserConfig.Users = adminConfig!.UserConfig.Users.filter(
            (u) => u.username !== ownerUser
          );
          adminConfig!.UserConfig.Users.unshift({
            username: ownerUser,
            role: 'owner',
          });
        }
        // 初始化分组结构（若缺失）
        if (!adminConfig.UserConfig) {
          adminConfig.UserConfig = {
            AllowRegister: false,
            Users: [],
            Groups: [],
          } as any;
        }
        if (
          !('Groups' in adminConfig.UserConfig) ||
          !adminConfig.UserConfig.Groups
        ) {
          (adminConfig.UserConfig as any).Groups = [];
        }
      } else {
        // 数据库中没有配置，使用默认的运行时配置
        if (process.env.DOCKER_ENV === 'true') {
          fileConfig = loadFileConfigFromDisk();
        } else {
          // 默认使用编译时生成的配置
          fileConfig = loadBuildTimeFileConfig();
        }
        // 数据库中没有配置，创建新的管理员配置
        let allUsers = userNames.map((uname) => ({
          username: uname,
          role: 'user',
        }));
        const ownerUser = process.env.USERNAME;
        if (ownerUser) {
          allUsers = allUsers.filter((u) => u.username !== ownerUser);
          allUsers.unshift({
            username: ownerUser,
            role: 'owner',
          });
        }
        adminConfig = {
          ConfigFile: JSON.stringify(fileConfig),
          SiteConfig: {
            SiteName: getSiteNameEnv(),
            Announcement:
              process.env.ANNOUNCEMENT ||
              '本网站仅提供影视信息搜索服务，所有内容均来自第三方网站。本站不存储任何视频资源，不对任何内容的准确性、合法性、完整性负责。',
            SearchDownstreamMaxPage:
              Number(process.env.NEXT_PUBLIC_SEARCH_MAX_PAGE) || 5,
            SiteInterfaceCacheTime: fileConfig.cache_time || 7200,
            DoubanProxyType:
              process.env.NEXT_PUBLIC_DOUBAN_PROXY_TYPE || 'direct',
            DoubanProxy: process.env.NEXT_PUBLIC_DOUBAN_PROXY || '',
            DoubanImageProxyType:
              process.env.NEXT_PUBLIC_DOUBAN_IMAGE_PROXY_TYPE || 'direct',
            DoubanImageProxy: process.env.NEXT_PUBLIC_DOUBAN_IMAGE_PROXY || '',
            DisableYellowFilter:
              process.env.NEXT_PUBLIC_DISABLE_YELLOW_FILTER === 'true',
            DanmakuApiBaseUrl: process.env.NEXT_PUBLIC_DANMU_API_BASE_URL || '',
            TVBoxEnabled: false,
            TVBoxPassword: '',
          },
          UserConfig: {
            AllowRegister: process.env.NEXT_PUBLIC_ENABLE_REGISTER === 'true',
            Users: allUsers as any,
            Groups: [],
          },
          SourceConfig: Object.entries(fileConfig.api_site || {}).map(
            ([key, site]) => ({
              key,
              name: site.name,
              api: site.api,
              detail: site.detail,
              from: 'config',
              disabled: false,
            })
          ),
          CustomCategories: (fileConfig.custom_category || []).map(
            (category) => ({
              name: category.name,
              type: category.type,
              query: category.query,
              from: 'config',
              disabled: false,
            })
          ),
          SubscriptionConfig: {},
        };
      }

      // 写回数据库（更新/创建）
      if (storage && typeof (storage as any).setAdminConfig === 'function') {
        await (storage as any).setAdminConfig(adminConfig);
      }

      // 更新缓存
      cachedConfig = adminConfig;
    } catch (err) {
      console.error('加载管理员配置失败:', err);
    }
  } else {
    // 本地存储直接使用文件配置
    cachedConfig = {
      ConfigFile: JSON.stringify(fileConfig),
      SiteConfig: {
        SiteName: getSiteNameEnv(),
        Announcement:
          process.env.ANNOUNCEMENT ||
          '本网站仅提供影视信息搜索服务，所有内容均来自第三方网站。本站不存储任何视频资源，不对任何内容的准确性、合法性、完整性负责。',
        SearchDownstreamMaxPage:
          Number(process.env.NEXT_PUBLIC_SEARCH_MAX_PAGE) || 5,
        SiteInterfaceCacheTime: fileConfig.cache_time || 7200,
        DoubanProxyType: process.env.NEXT_PUBLIC_DOUBAN_PROXY_TYPE || 'direct',
        DoubanProxy: process.env.NEXT_PUBLIC_DOUBAN_PROXY || '',
        DoubanImageProxyType:
          process.env.NEXT_PUBLIC_DOUBAN_IMAGE_PROXY_TYPE || 'direct',
        DoubanImageProxy: process.env.NEXT_PUBLIC_DOUBAN_IMAGE_PROXY || '',
        DisableYellowFilter:
          process.env.NEXT_PUBLIC_DISABLE_YELLOW_FILTER === 'true',
        DanmakuApiBaseUrl: process.env.NEXT_PUBLIC_DANMU_API_BASE_URL || '',
        TVBoxEnabled: false,
        TVBoxPassword: '',
      },
      UserConfig: {
        AllowRegister: process.env.NEXT_PUBLIC_ENABLE_REGISTER === 'true',
        Users: [],
        Groups: [],
      },
      SourceConfig: Object.entries(fileConfig.api_site || {}).map(
        ([key, site]) => ({
          key,
          name: site.name,
          api: site.api,
          detail: site.detail,
          from: 'config',
          disabled: false,
        })
      ),
      CustomCategories:
        fileConfig.custom_category?.map((category) => ({
          name: category.name,
          type: category.type,
          query: category.query,
          from: 'config',
          disabled: false,
        })) || [],
      SubscriptionConfig: {},
    } as AdminConfig;
  }
}

function cloneConfig<T>(cfg: T): T {
  try {
    return JSON.parse(JSON.stringify(cfg)) as T;
  } catch {
    return cfg;
  }
}

export async function getConfig(): Promise<AdminConfig> {
  const storageType = process.env.NEXT_PUBLIC_STORAGE_TYPE || 'localstorage';
  if (storageType === 'localstorage') {
    await initConfig();
    // 返回深拷贝，避免跨请求共享可变单例
    return cloneConfig(cachedConfig);
  }

  // 非本地存储：TTL 内直接返回内存缓存，跳过读库（每次请求至少省 1 次 DB roundtrip）
  if (cachedConfig && Date.now() - cachedAt < getConfigCacheTtlMs()) {
    return cloneConfig(cachedConfig);
  }

  // 非本地存储，直接读 db 配置
  const storage = getStorage();
  let adminConfig: AdminConfig | null = null;
  if (storage && typeof (storage as any).getAdminConfig === 'function') {
    adminConfig = await (storage as any).getAdminConfig();
  }

  if (adminConfig) {
    // 确保 CustomCategories 被初始化
    if (!adminConfig.CustomCategories) {
      adminConfig.CustomCategories = [];
    }

    // 数据库优先，环境变量仅在缺省时回退
    adminConfig.SiteConfig.SiteName =
      adminConfig.SiteConfig.SiteName || getSiteNameEnv();
    adminConfig.SiteConfig.Announcement =
      adminConfig.SiteConfig.Announcement ||
      process.env.ANNOUNCEMENT ||
      '本网站仅提供影视信息搜索服务，所有内容均来自第三方网站。本站不存储任何视频资源，不对任何内容的准确性、合法性、完整性负责。';
    adminConfig.UserConfig.AllowRegister =
      typeof adminConfig.UserConfig.AllowRegister === 'boolean'
        ? adminConfig.UserConfig.AllowRegister
        : process.env.NEXT_PUBLIC_ENABLE_REGISTER === 'true';
    adminConfig.SiteConfig.DoubanProxyType =
      adminConfig.SiteConfig.DoubanProxyType ||
      process.env.NEXT_PUBLIC_DOUBAN_PROXY_TYPE ||
      'direct';
    adminConfig.SiteConfig.DoubanProxy =
      adminConfig.SiteConfig.DoubanProxy ||
      process.env.NEXT_PUBLIC_DOUBAN_PROXY ||
      '';
    adminConfig.SiteConfig.DoubanImageProxyType =
      adminConfig.SiteConfig.DoubanImageProxyType ||
      process.env.NEXT_PUBLIC_DOUBAN_IMAGE_PROXY_TYPE ||
      'direct';
    adminConfig.SiteConfig.DoubanImageProxy =
      adminConfig.SiteConfig.DoubanImageProxy ||
      process.env.NEXT_PUBLIC_DOUBAN_IMAGE_PROXY ||
      '';
    adminConfig.SiteConfig.DisableYellowFilter =
      typeof adminConfig.SiteConfig.DisableYellowFilter === 'boolean'
        ? adminConfig.SiteConfig.DisableYellowFilter
        : process.env.NEXT_PUBLIC_DISABLE_YELLOW_FILTER === 'true';

    // 弹幕接口配置：数据库优先，其次环境变量，最后使用默认值
    adminConfig.SiteConfig.DanmakuApiBaseUrl =
      adminConfig.SiteConfig.DanmakuApiBaseUrl ||
      process.env.NEXT_PUBLIC_DANMU_API_BASE_URL ||
      '';
    // TVBox 开关与密码默认值（此处仅非 localstorage 分支可达，localstorage 已提前返回）
    adminConfig.SiteConfig.TVBoxEnabled =
      typeof adminConfig.SiteConfig.TVBoxEnabled === 'boolean'
        ? adminConfig.SiteConfig.TVBoxEnabled
        : false;
    adminConfig.SiteConfig.TVBoxPassword =
      typeof adminConfig.SiteConfig.TVBoxPassword === 'string'
        ? adminConfig.SiteConfig.TVBoxPassword
        : '';

    try {
      const parsed = JSON.parse(adminConfig.ConfigFile) as ConfigFileStruct;
      fileConfig = {
        cache_time: parsed.cache_time,
        api_site: parsed.api_site || {},
        custom_category: parsed.custom_category || [],
      };
    } catch (e) {
      console.error('解析配置文件失败:', e);
      fileConfig = { api_site: {} } as ConfigFileStruct;
    }

    mergeSourceConfigs(adminConfig, fileConfig);
    // 非 localstorage 下仅覆盖 from 为 config 的分类，避免覆盖用户自定义禁用态
    mergeCustomCategories(adminConfig, fileConfig, true);

    // 同步 cache_time 到 SiteConfig.SiteInterfaceCacheTime
    if (fileConfig.cache_time !== undefined) {
      adminConfig.SiteConfig.SiteInterfaceCacheTime = fileConfig.cache_time;
    }

    // 初始化分组结构（若缺失）
    if (!adminConfig.UserConfig) {
      adminConfig.UserConfig = {
        AllowRegister: false,
        Users: [],
        Groups: [],
      } as any;
    }
    if (
      !('Groups' in adminConfig.UserConfig) ||
      !adminConfig.UserConfig.Groups
    ) {
      (adminConfig.UserConfig as any).Groups = [];
    }

    const ownerUser = process.env.USERNAME || '';
    // 未配置 USERNAME 时不写入空站长，避免毒数据
    if (ownerUser) {
      // 检查配置中的站长用户是否和 USERNAME 匹配，如果不匹配则降级为普通用户
      let containOwner = false;
      adminConfig.UserConfig.Users.forEach((user) => {
        if (user.username !== ownerUser && user.role === 'owner') {
          user.role = 'user';
        }
        if (user.username === ownerUser) {
          containOwner = true;
          user.role = 'owner';
        }
      });

      // 如果不在则添加
      if (!containOwner) {
        adminConfig.UserConfig.Users.unshift({
          username: ownerUser,
          role: 'owner',
        });
      }
    } else {
      // 无站长配置时，确保无人持有 owner 权限
      adminConfig.UserConfig.Users.forEach((user) => {
        if (user.role === 'owner') {
          user.role = 'user';
        }
      });
    }
    cachedConfig = adminConfig;
    cachedAt = Date.now();
  } else {
    await initConfig();
  }

  return cloneConfig(cachedConfig);
}

export function configSelfCheck(adminConfig: AdminConfig): AdminConfig {
  // 确保必要的属性存在和初始化
  if (!adminConfig.UserConfig) {
    adminConfig.UserConfig = { AllowRegister: false, Users: [] };
  }
  if (
    !adminConfig.UserConfig.Users ||
    !Array.isArray(adminConfig.UserConfig.Users)
  ) {
    adminConfig.UserConfig.Users = [];
  }
  if (!adminConfig.SourceConfig || !Array.isArray(adminConfig.SourceConfig)) {
    adminConfig.SourceConfig = [];
  }
  if (
    !adminConfig.CustomCategories ||
    !Array.isArray(adminConfig.CustomCategories)
  ) {
    adminConfig.CustomCategories = [];
  }
  if (!adminConfig.SubscriptionConfig) {
    adminConfig.SubscriptionConfig = {};
  }

  // 站长变更自检：未配置 USERNAME 时不写入毒数据，直接返回去重后的配置
  const ownerUser = process.env.USERNAME;
  if (!ownerUser) {
    return adminConfig;
  }
  const originalOwner = adminConfig.UserConfig.Users.find(
    (u) => u.username === ownerUser
  );

  // 去重
  const seenUsernames = new Set<string>();
  adminConfig.UserConfig.Users = adminConfig.UserConfig.Users.filter((user) => {
    if (seenUsernames.has(user.username)) {
      return false;
    }
    seenUsernames.add(user.username);
    return true;
  });
  // 过滤站长
  adminConfig.UserConfig.Users = adminConfig.UserConfig.Users.filter(
    (user) => user.username !== ownerUser
  );
  // 其他用户不得拥有 owner 权限
  adminConfig.UserConfig.Users.forEach((user) => {
    if (user.role === 'owner') {
      user.role = 'user';
    }
  });
  // 重新添加回站长
  adminConfig.UserConfig.Users.unshift({
    username: ownerUser!,
    role: 'owner',
    banned: originalOwner?.banned ?? false,
    group: originalOwner?.group,
    lastOnline: originalOwner?.lastOnline,
  });

  // 采集源去重
  const seenSourceKeys = new Set<string>();
  adminConfig.SourceConfig = adminConfig.SourceConfig.filter((source) => {
    if (seenSourceKeys.has(source.key)) {
      return false;
    }
    seenSourceKeys.add(source.key);
    return true;
  });

  // 自定义分类去重
  const seenCustomCategoryKeys = new Set<string>();
  adminConfig.CustomCategories = adminConfig.CustomCategories.filter(
    (category) => {
      if (seenCustomCategoryKeys.has(category.query + category.type)) {
        return false;
      }
      seenCustomCategoryKeys.add(category.query + category.type);
      return true;
    }
  );

  return adminConfig;
}

export async function resetConfig() {
  const storage = getStorage();
  // 获取所有用户名，用于补全 Users
  let userNames: string[] = [];
  if (storage && typeof (storage as any).getAllUsers === 'function') {
    try {
      userNames = await (storage as any).getAllUsers();
    } catch (e) {
      console.error('获取用户列表失败:', e);
    }
  }

  if (process.env.DOCKER_ENV === 'true') {
    fileConfig = loadFileConfigFromDisk();
  } else {
    // 默认使用编译时生成的配置
    fileConfig = loadBuildTimeFileConfig();
  }

  const apiSiteEntries = Object.entries(fileConfig.api_site || {});
  const customCategories = fileConfig.custom_category || [];
  let allUsers = userNames.map((uname) => ({
    username: uname,
    role: 'user',
  }));
  const ownerUser = process.env.USERNAME;
  if (ownerUser) {
    allUsers = allUsers.filter((u) => u.username !== ownerUser);
    allUsers.unshift({
      username: ownerUser,
      role: 'owner',
    });
  }
  const adminConfig = {
    ConfigFile: JSON.stringify(fileConfig),
    SiteConfig: {
      SiteName: getSiteNameEnv(),
      Announcement:
        process.env.ANNOUNCEMENT ||
        '本网站仅提供影视信息搜索服务，所有内容均来自第三方网站。本站不存储任何视频资源，不对任何内容的准确性、合法性、完整性负责。',
      SearchDownstreamMaxPage:
        Number(process.env.NEXT_PUBLIC_SEARCH_MAX_PAGE) || 5,
      SiteInterfaceCacheTime: fileConfig.cache_time || 7200,
      DoubanProxyType: process.env.NEXT_PUBLIC_DOUBAN_PROXY_TYPE || 'direct',
      DoubanProxy: process.env.NEXT_PUBLIC_DOUBAN_PROXY || '',
      DoubanImageProxyType:
        process.env.NEXT_PUBLIC_DOUBAN_IMAGE_PROXY_TYPE || 'direct',
      DoubanImageProxy: process.env.NEXT_PUBLIC_DOUBAN_IMAGE_PROXY || '',
      DisableYellowFilter:
        process.env.NEXT_PUBLIC_DISABLE_YELLOW_FILTER === 'true',
      DanmakuApiBaseUrl: process.env.NEXT_PUBLIC_DANMU_API_BASE_URL || '',
      TVBoxEnabled: false,
      TVBoxPassword: '',
    },
    UserConfig: {
      AllowRegister: process.env.NEXT_PUBLIC_ENABLE_REGISTER === 'true',
      Users: allUsers as any,
    },
    SourceConfig: apiSiteEntries.map(([key, site]) => ({
      key,
      name: site.name,
      api: site.api,
      detail: site.detail,
      from: 'config',
      disabled: false,
    })),
    CustomCategories:
      customCategories?.map((category) => ({
        name: category.name,
        type: category.type,
        query: category.query,
        from: 'config',
        disabled: false,
      })) || [],
    SubscriptionConfig: {},
  } as AdminConfig;

  if (storage && typeof (storage as any).setAdminConfig === 'function') {
    await (storage as any).setAdminConfig(adminConfig);
  }
  if (cachedConfig == null) {
    // serverless 环境，直接使用 adminConfig
    cachedConfig = adminConfig;
    cachedAt = Date.now();
    return;
  }
  cachedConfig.ConfigFile = adminConfig.ConfigFile;
  cachedConfig.SiteConfig = adminConfig.SiteConfig;
  cachedConfig.UserConfig = adminConfig.UserConfig;
  cachedConfig.SourceConfig = adminConfig.SourceConfig;
  cachedConfig.CustomCategories = adminConfig.CustomCategories || [];
  cachedConfig.SubscriptionConfig = adminConfig.SubscriptionConfig;
  cachedAt = Date.now();
}

export async function getCacheTime(): Promise<number> {
  const config = await getConfig();
  return config.SiteConfig.SiteInterfaceCacheTime || 7200;
}

export async function getAvailableApiSites(
  username?: string
): Promise<ApiSite[]> {
  const config = await getConfig();
  const all = config.SourceConfig.filter((s) => !s.disabled);
  if (
    !username ||
    !config.UserConfig?.Groups ||
    config.UserConfig.Groups.length === 0
  ) {
    return all.map((s) => ({
      key: s.key,
      name: s.name,
      api: s.api,
      detail: s.detail,
    }));
  }
  const user = config.UserConfig.Users.find((u) => u.username === username);
  const groupName = user?.group;
  if (!groupName) {
    return all.map((s) => ({
      key: s.key,
      name: s.name,
      api: s.api,
      detail: s.detail,
    }));
  }
  const group = config.UserConfig.Groups.find((g) => g.name === groupName);
  if (!group) {
    return all.map((s) => ({
      key: s.key,
      name: s.name,
      api: s.api,
      detail: s.detail,
    }));
  }
  const allowed = new Set(group.sourceKeys);
  const filtered = all.filter((s) => allowed.has(s.key));
  return filtered.map((s) => ({
    key: s.key,
    name: s.name,
    api: s.api,
    detail: s.detail,
  }));
}

export async function setCachedConfig(config: AdminConfig) {
  cachedConfig = config;
  cachedAt = Date.now();
}
