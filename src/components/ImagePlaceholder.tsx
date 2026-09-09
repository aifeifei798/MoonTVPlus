// 图片占位符组件 - 实现骨架屏效果（样式定义在 globals.css）
const ImagePlaceholder = ({ aspectRatio }: { aspectRatio: string }) => (
  <div className={`skeleton-shimmer w-full ${aspectRatio} rounded-lg`} />
);

export { ImagePlaceholder };
