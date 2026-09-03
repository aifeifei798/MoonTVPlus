jest.mock('@/lib/db', () => ({
  getStorage: jest.fn(() => null),
}));

import {
  getDetailFromApi,
  isSourceCircuitOpen,
  recordSourceFailure,
  recordSourceSuccess,
  searchFromApiStream,
} from './downstream';

const site = {
  key: 'cachesrc',
  api: 'https://cms.example.com',
  name: 'CacheSrc',
};

function collect<T>(gen: AsyncGenerator<T>): Promise<T[]> {
  const out: T[] = [];
  const pump = async () => {
    for await (const v of gen) out.push(v);
    return out;
  };
  return pump();
}

describe('搜索缓存', () => {
  const realFetch = global.fetch;

  beforeEach(() => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        list: [
          {
            vod_id: '7',
            vod_name: '缓存 测试',
            vod_pic: 'https://img.example.com/7.jpg',
            vod_play_url: '第1集$https://cdn.example.com/7.m3u8',
          },
        ],
        pagecount: 1,
      }),
    });
  });

  afterEach(() => {
    global.fetch = realFetch;
  });

  it('同一词第二次不再请求上游', async () => {
    const q = `cache-q-${Date.now()}`;
    const first = await collect(searchFromApiStream(site, q, true, 5000, 1));
    expect(first).toHaveLength(1);
    expect(global.fetch).toHaveBeenCalledTimes(1);

    const second = await collect(searchFromApiStream(site, q, true, 5000, 1));
    expect(second).toHaveLength(1);
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(second[0][0].title).toEqual(first[0][0].title);
  });

  it('提前 break 不污染缓存', async () => {
    const q = `break-q-${Date.now()}`;
    const gen = searchFromApiStream(site, q, true, 5000, 1);
    await gen.next();
    await gen.return(undefined);
    // 完整消费一次应再次请求上游（之前半截结果未缓存）
    await collect(searchFromApiStream(site, q, true, 5000, 1));
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });
});

describe('源熔断', () => {
  const key = `ckt-${Date.now()}`;

  beforeEach(() => {
    process.env.SOURCE_CIRCUIT_THRESHOLD = '3';
    process.env.SOURCE_CIRCUIT_COOLDOWN_S = '60';
  });

  afterEach(() => {
    delete process.env.SOURCE_CIRCUIT_THRESHOLD;
    delete process.env.SOURCE_CIRCUIT_COOLDOWN_S;
  });

  it('连续失败达阈值打开，成功后复位', () => {
    expect(isSourceCircuitOpen(key)).toBe(false);
    recordSourceFailure(key);
    recordSourceFailure(key);
    expect(isSourceCircuitOpen(key)).toBe(false);
    recordSourceFailure(key);
    expect(isSourceCircuitOpen(key)).toBe(true);
    recordSourceSuccess(key);
    expect(isSourceCircuitOpen(key)).toBe(false);
  });
});

describe('详情缓存', () => {
  const realFetch = global.fetch;

  beforeEach(() => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        list: [
          {
            vod_id: '42',
            vod_name: '详情缓存剧',
            vod_pic: 'https://img.example.com/42.jpg',
            vod_play_url: '第1集$https://cdn.example.com/42.m3u8',
            vod_content: '<p>简介</p>',
          },
        ],
      }),
    });
  });

  afterEach(() => {
    global.fetch = realFetch;
  });

  it('同一 id 第二次走内存', async () => {
    const vid = `v-${Date.now()}`;
    const a = await getDetailFromApi(site, vid);
    const b = await getDetailFromApi(site, vid);
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(b.title).toEqual(a.title);
    expect(b.episodes).toHaveLength(1);
  });
});
