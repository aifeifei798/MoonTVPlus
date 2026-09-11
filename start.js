#!/usr/bin/env node

/* eslint-disable no-console */
const http = require('http');
const path = require('path');

// 调用 generate-manifest.js 生成 manifest.json
function generateManifest() {
  console.log('Generating manifest.json for Docker deployment...');

  try {
    const generateManifestScript = path.join(
      __dirname,
      'scripts',
      'generate-manifest.js',
    );
    require(generateManifestScript);
  } catch (error) {
    console.error('❌ Error calling generate-manifest.js:', error);
    throw error;
  }
}

generateManifest();

// 直接在当前进程中启动 standalone Server（`server.js`）
require('./server.js');

// 每 1 秒轮询一次，直到请求成功
// 注意：HOSTNAME=0.0.0.0 仅用于监听，轮询必须走 127.0.0.1
const POLL_HOST =
  !process.env.HOSTNAME ||
  process.env.HOSTNAME === '0.0.0.0' ||
  process.env.HOSTNAME === '::'
    ? '127.0.0.1'
    : process.env.HOSTNAME;
const TARGET_URL = `http://${POLL_HOST}:${process.env.PORT || 3000}/login`;

const intervalId = setInterval(() => {
  console.log(`Fetching ${TARGET_URL} ...`);

  const req = http.get(TARGET_URL, (res) => {
    // 2xx/3xx/401 都视为“服务已起”（401=未设 PASSWORD 跳 /warning 前的正常态）
    const code = res.statusCode || 0;
    if ((code >= 200 && code < 400) || code === 401) {
      console.log('Server is up, stop polling.');
      clearInterval(intervalId);

      // 服务器启动后，立即执行一次 cron 任务
      executeCronJob();

      // 然后按 CRON_INTERVAL_MINUTES 间隔执行（默认 60 分钟，0 表示只跑启动那一次）
      const raw = process.env.CRON_INTERVAL_MINUTES ?? '60';
      const intervalMinutes = Number(raw);
      if (!Number.isFinite(intervalMinutes)) {
        console.warn(
          `CRON_INTERVAL_MINUTES 非法 (${raw})，仅执行启动时的一次 cron 任务`,
        );
      } else if (intervalMinutes > 0) {
        setInterval(
          () => {
            executeCronJob();
          },
          intervalMinutes * 60 * 1000,
        );
      } else {
        console.log('CRON_INTERVAL_MINUTES<=0，仅执行启动时的一次 cron 任务');
      }
    } else {
      try {
        res.resume();
      } catch {
        // ignore
      }
    }
  });

  req.on('error', (err) => {
    // 启动前 ECONNREFUSED 为预期噪音，降级为 debug 输出
    console.log(`Poll waiting: ${err.message}`);
  });

  req.setTimeout(2000, () => {
    req.destroy();
  });
}, 1000);

// 执行 cron 任务的函数
function executeCronJob() {
  const cronUrl = `http://${POLL_HOST}:${process.env.PORT || 3000}/api/cron`;

  console.log(`Executing cron job: ${cronUrl}`);

  const headers = {};
  if (process.env.CRON_SECRET) {
    headers['x-cron-secret'] = process.env.CRON_SECRET;
  }
  const req = http.get(cronUrl, { headers }, (res) => {
    let data = '';

    res.on('data', (chunk) => {
      data += chunk;
    });

    res.on('end', () => {
      if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
        console.log('Cron job executed successfully:', data);
      } else {
        console.error('Cron job failed:', res.statusCode, data);
      }
    });
  });

  req.on('error', (err) => {
    console.error('Error executing cron job:', err);
  });

  req.setTimeout(30000, () => {
    console.error('Cron job timeout');
    req.destroy();
  });
}
