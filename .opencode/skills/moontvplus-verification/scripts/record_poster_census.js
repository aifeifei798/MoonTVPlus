// Record-card poster chain census: 豆瓣 → 源 → 站点图标.
// Run it through cdp_eval.cjs, e.g.
//   node scripts/cdp_eval.cjs "$AUTH" "/" @scripts/record_poster_census.js 60000
//
// It answers the question `poster_census.js` cannot: not "is the image broken" but
// "which link of the chain is the card actually rendering, and did it paint". A card
// showing the source poster because douban had no match is correct; one showing it
// because the douban lookup silently failed is not, and only this probe can tell the
// two apart. Cards are scoped to sections whose heading is one of the record rows
// (继续观看 / 追更 / 收藏), so the douban and search rows — which never do the lookup
// — do not pollute the counts.
//
// The home page is a CapsuleSwitch with tabs 首页 / 历史 / 追更 / 收藏夹, and only one
// view is mounted at a time, so a census that measures the default view sees only the
// 继续观看 cards. This probe clicks each record tab (by label text, in the top switch)
// and censuses each view. `element.click()` is enough here: React's scheduler finishes
// the tab switch in a later task, so the probe sleeps after the click, and the douban
// lookups (4 concurrent, `poster.client.ts`) need a few seconds more before the chain
// has advanced.
//
// To exercise the third link, seed a record whose cover 404s and whose title douban
// does not know, through the panel's own route (never by writing the db file from a
// second connection — the running server would keep serving the old blob):
//   curl -X POST -H "Cookie: $AUTH" -H 'Content-Type: application/json' \
//     -d '{"key":"IK+900001","record":{"title":"ZZQProbeMissingPoster","source":"IK","source_name":"IK","id":"900001","episodes":[1],"year":"2024","cover":"https://img.bfzypic.com/definitely-not-a-real-poster.jpg"}}' \
//     http://127.0.0.1:3000/api/playrecords
// and delete it afterwards with `DELETE /api/playrecords?key=IK%2B900001`. The same
// shape seeds the other two rows: `POST /api/favorites` with `{"key","favorite"}` and
// `POST /api/followings` with `{"key","following"}`, deleted with
// `DELETE /api/favorites?key=IK%2B900002` / `DELETE /api/followings?key=IK%2B900003`.
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const classify = (src) => {
    if (!src) return "none";
    if (src.indexOf("poster-fallback.svg") >= 0) return "fallback-logo";
    if (decodeURIComponent(src).indexOf("doubanio") >= 0) return "douban";
    return "source";
  };

  const censusView = () => {
    for (const img of document.querySelectorAll("img")) img.loading = "eager";
    const recordSections = [...document.querySelectorAll("section")].filter((s) => {
      const heading = (s.innerText || "").slice(0, 60);
      return (
        heading.indexOf("继续观看") >= 0 ||
        heading.indexOf("追更") >= 0 ||
        heading.indexOf("收藏") >= 0
      );
    });
    return recordSections.flatMap((section) =>
      [...section.querySelectorAll("img")].map((img) => ({
        section: (section.innerText || "").trim().split("\n")[0].slice(0, 20),
        title: (img.alt || "").slice(0, 40),
        link: classify(img.src),
        painted: img.complete && img.naturalWidth > 0,
        natural: `${img.naturalWidth}x${img.naturalHeight}`,
        rendered: `${img.width}x${img.height}`,
        proxied: (img.src || "").indexOf("/api/image-proxy") >= 0,
        retried: !!img.dataset.imageProxyRetried,
      }))
    );
  };

  // The top CapsuleSwitch, not the mobile bottom nav: the tab buttons are the only
  // buttons whose text is exactly a tab label.
  const clickTab = (label) => {
    const button = [...document.querySelectorAll("button")].find(
      (b) => (b.innerText || "").trim() === label
    );
    if (button) button.click();
    return !!button;
  };

  const tabs = ["历史", "追更", "收藏夹"];
  const views = [];
  for (const label of tabs) {
    const found = clickTab(label);
    await sleep(2500); // React's scheduler mounts the view in a later task
    await sleep(9000); // the douban lookups and the poster chain need room
    views.push({ tab: label, buttonFound: found, rows: censusView() });
  }

  let cacheEntries = -1;
  try {
    const raw = localStorage.getItem("moontv_douban_poster");
    cacheEntries = raw ? Object.keys(JSON.parse(raw)).length : 0;
  } catch (_) {
    cacheEntries = -1;
  }

  const all = views.flatMap((v) => v.rows);
  return JSON.stringify(
    {
      page: location.pathname + location.search,
      recordCards: all.length,
      doubanPoster: all.filter((r) => r.link === "douban").length,
      sourcePoster: all.filter((r) => r.link === "source").length,
      fallbackLogo: all.filter((r) => r.link === "fallback-logo").length,
      unpainted: all.filter((r) => !r.painted).length,
      posterCacheEntries: cacheEntries,
      views,
      errs: (window.__errs || []).slice(0, 5),
    },
    null,
    1
  );
})();
