import { getConfig, invalidateConfigCache } from './config';

jest.mock('@/lib/db', () => ({
  getStorage: jest.fn(),
}));

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { getStorage } = require('@/lib/db');

const baseAdminConfig = () => ({
  ConfigFile: JSON.stringify({ api_site: {}, custom_category: [] }),
  SiteConfig: {
    SiteName: 'TestSite',
    Announcement: 'ann',
    SearchDownstreamMaxPage: 5,
    SiteInterfaceCacheTime: 7200,
    DoubanProxyType: 'direct',
    DoubanProxy: '',
    DoubanImageProxyType: 'direct',
    DoubanImageProxy: '',
    DisableYellowFilter: false,
    DanmakuApiBaseUrl: '',
    TVBoxEnabled: false,
    TVBoxPassword: '',
  },
  UserConfig: { AllowRegister: false, Users: [], Groups: [] },
  SourceConfig: [],
  CustomCategories: [],
  SubscriptionConfig: {},
});

describe('getConfig 短 TTL 缓存', () => {
  const OLD_ENV = process.env;

  beforeEach(() => {
    jest.clearAllMocks();
    process.env = {
      ...OLD_ENV,
      NEXT_PUBLIC_STORAGE_TYPE: 'redis',
      PASSWORD: 'test-secret',
      USERNAME: 'owner1',
      CONFIG_CACHE_TTL: '60',
    };
    invalidateConfigCache();
    (getStorage as jest.Mock).mockReturnValue({
      getAdminConfig: jest.fn().mockResolvedValue(baseAdminConfig()),
    });
  });

  afterAll(() => {
    process.env = OLD_ENV;
  });

  it('TTL 内二次调用不重复读库', async () => {
    const storage = (getStorage as jest.Mock)();
    await getConfig();
    await getConfig();
    expect(storage.getAdminConfig).toHaveBeenCalledTimes(1);
  });

  it('invalidate 后下一次调用重新读库', async () => {
    const storage = (getStorage as jest.Mock)();
    await getConfig();
    invalidateConfigCache();
    await getConfig();
    expect(storage.getAdminConfig).toHaveBeenCalledTimes(2);
  });
});
