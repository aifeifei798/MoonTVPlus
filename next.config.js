/** @type {import('next').NextConfig} */

const nextConfig = {
  output: 'standalone',

  reactStrictMode: true,

  // 聚合播放器需直出任意第三方图床（api_site/豆瓣），故保持 unoptimized；
  // remotePatterns 仅作文档用途，unoptimized 下不生效，切勿误以为是白名单
  images: {
    unoptimized: true,
  },
};

module.exports = nextConfig;
