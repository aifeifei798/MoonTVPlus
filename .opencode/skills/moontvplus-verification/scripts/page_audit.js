// Page audit: what is broken on a rendered page, not what the wire says.
//   node scripts/cdp_eval.cjs "$AUTH" "/search?q=Spider" @scripts/page_audit.js 12000
// Reports console/runtime errors captured by cdp_eval's window.__errs, every
// <img> that the browser attempted and failed to paint, links whose href is
// empty or obviously dead, and buttons whose label is empty.
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  for (const img of document.querySelectorAll("img")) img.loading = "eager";
  await sleep(8000);

  const brokenImages = [...document.querySelectorAll("img")]
    .filter((img) => img.complete && img.naturalWidth === 0)
    .map((img) => ({
      src: (img.src || "").slice(0, 120),
      alt: (img.alt || "").slice(0, 40),
      box: `${img.width}x${img.height}`,
    }));

  const deadLinks = [...document.querySelectorAll("a")]
    .filter((a) => {
      const href = a.getAttribute("href") || "";
      return href === "" || href === "#" || href === "javascript:;";
    })
    .map((a) => (a.innerText || "").trim().slice(0, 40));

  const unlabeledButtons = [...document.querySelectorAll("button")]
    .filter((b) => !b.innerText.trim() && !b.getAttribute("aria-label") && !b.getAttribute("title"))
    .map((b) => b.className.slice(0, 60));

  const overflow = [...document.querySelectorAll("section, div")]
    .filter((el) => {
      const text = (el.innerText || "").trim();
      if (text.length <= 3) return false;
      if (el.className.includes("overflow")) return false;
      if (el.scrollWidth <= el.clientWidth + 4) return false;
      // A row that scrolls on its own (`overflow-x: auto`) makes its ancestors
      // report a larger scrollWidth than their own box; that is the horizontal
      // carousel working, not the page overflowing.
      return ![...el.querySelectorAll("*")].some((child) => {
        const o = getComputedStyle(child).overflowX;
        return o === "auto" || o === "scroll" || o === "hidden";
      });
    })
    .slice(0, 5)
    .map((el) => ({ tag: el.tagName, cls: el.className.slice(0, 60), text: (el.innerText || "").trim().slice(0, 40) }));

  return JSON.stringify(
    {
      page: location.pathname + location.search,
      errs: (window.__errs || []).slice(0, 10),
      images: document.querySelectorAll("img").length,
      brokenImages,
      deadLinks,
      unlabeledButtons,
      overflowing: overflow,
      bodyLen: document.body.innerHTML.length,
    },
    null,
    1
  );
})();
