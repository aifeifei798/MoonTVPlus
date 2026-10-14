// Poster census: run it through cdp_eval.cjs, e.g.
//   node scripts/cdp_eval.cjs "$AUTH" "/" @scripts/poster_census.js 12000
//
// Why the `loading = "eager"` pass is mandatory: every card poster is
// `loading="lazy"`, so below the fold nothing has attempted a fetch yet and
// `naturalWidth === 0` says nothing about the fallback chain. The census only
// counts images the browser actually tried (`img.complete`), and it reports how
// many of them ended up served by `/api/image-proxy` — the rescue path that
// `retryImageThroughProxy` in `frontend/src/lib/utils.ts` installs.
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  for (const img of document.querySelectorAll("img")) {
    img.loading = "eager";
  }
  await sleep(6000);

  const imgs = [...document.querySelectorAll("img")];
  const attempted = imgs.filter((i) => i.complete);
  const broken = attempted
    .filter((i) => i.naturalWidth === 0)
    .map((i) => ({
      src: (i.src || "").slice(0, 120),
      fallbackUsed: !!i.dataset.imageProxyRetried,
    }));

  const sections = [...document.querySelectorAll("section")].map((s) => {
    const title = (s.innerText || "").trim().slice(0, 30).replace(/\n/g, " / ");
    const inSection = [...s.querySelectorAll("img")];
    const att = inSection.filter((i) => i.complete);
    return {
      section: title,
      imgs: inSection.length,
      attempted: att.length,
      broken: att.filter((i) => i.naturalWidth === 0).length,
      viaProxy: inSection.filter(
        (i) => (i.src || "").indexOf("/api/image-proxy") >= 0
      ).length,
    };
  });

  return JSON.stringify(
    {
      page: location.pathname + location.search,
      total: imgs.length,
      attempted: attempted.length,
      brokenCount: broken.length,
      broken: broken.slice(0, 15),
      sections,
      errs: (window.__errs || []).slice(0, 5),
    },
    null,
    1
  );
})();
