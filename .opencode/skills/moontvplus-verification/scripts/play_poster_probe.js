// Play-page poster chain probe: 豆瓣 → 源 → 兜底图标.
//   node scripts/cdp_eval.cjs "$AUTH" "/play?query=...&source=...&id=...&stype=movie" @scripts/play_poster_probe.js 18000
//
// The play page has two posters, and they are fed by different code paths: the
// panel's `<img>` (VideoDetailPanel's chain, `posterIndex` advancing on onError)
// and Artplayer's `poster` option (usePlayEngine's `videoCover`, which
// `preferDoubanPoster` overwrites once the douban lookup lands). A page that
// renders fine but still shows the source's own art is the failure mode this
// probe catches, so it reports the *identity* of each poster, not just whether it
// painted.
(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  for (const img of document.querySelectorAll("img")) img.loading = "eager";
  await sleep(12000);

  const classify = (src) => {
    if (!src) return "none";
    if (src.indexOf("poster-fallback.svg") >= 0) return "fallback-logo";
    if (decodeURIComponent(src).indexOf("doubanio") >= 0) return "douban";
    if (src.indexOf("/api/image-proxy") >= 0) return "proxied";
    return "source";
  };

  const painted = (img) => img.complete && img.naturalWidth > 0;

  const panel = [...document.querySelectorAll("img")]
    .filter((img) => (img.getAttribute("alt") || "").length > 0)
    .map((img) => ({
      src: (img.src || "").slice(0, 140),
      kind: classify(img.src || ""),
      painted: painted(img),
      box: `${img.width}x${img.height}`,
    }));

  const video = document.querySelector("video");
  const artPoster = document.querySelector(".art-poster");
  const artBackground = artPoster ? artPoster.style.backgroundImage || "" : "";

  return JSON.stringify(
    {
      page: location.pathname + location.search,
      title: (document.querySelector("h1, .video-title") || {}).textContent || "",
      panelPosters: panel,
      doubanPosterUsed: panel.some((p) => p.kind === "douban" || p.kind === "proxied"),
      // Artplayer renders the poster as a CSS background on `.art-poster`, not as
      // the video's `poster` attribute, so the attribute is always empty here.
      playerPoster: artBackground.slice(0, 160),
      playerPosterKind: classify(artBackground.replace(/^url\(["']?/, "").replace(/["']?\)$/, "")),
      videoPoster: video ? (video.getAttribute("poster") || "").slice(0, 140) : null,
      playerPresent: !!artPoster,
      errs: (window.__errs || []).slice(0, 10),
    },
    null,
    1
  );
})();
