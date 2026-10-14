// 换源 list order probe: clicking a source must move it to the TOP of the list
// with the 生效源 badge, and push the source that was active before to the BOTTOM.
// Run it through cdp_eval.cjs, e.g.
//   node scripts/cdp_eval.cjs "$AUTH" "$(cat /tmp/opencode/playurl.txt)" \
//       @scripts/source_order_probe.js 15000
//
// Rows carry `data-source-key` / `data-active` so the probe never has to type CJK
// (this machine's I/O path transcodes it) and never depends on the title text.
// It clicks twice — once to switch away, once to switch back — because the
// "previous source sinks" rule is only provable in both directions.
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const snapshot = () =>
    Array.from(document.querySelectorAll('[data-source-key]')).map((el) => ({
      key: el.getAttribute('data-source-key'),
      active: el.getAttribute('data-active') === '1',
      badge: el.querySelector('span.bg-apple-500') ? 1 : 0,
    }));

  const click = (idx) => {
    const rows = Array.from(document.querySelectorAll('[data-source-key]'));
    if (rows[idx]) rows[idx].click();
  };

  // the 换源 tab is the parent of the 优选 (blue lightning) button
  const bestBtn = document.querySelector('div.bg-blue-500');
  if (bestBtn && bestBtn.parentElement) bestBtn.parentElement.click();
  await sleep(2500);

  const s0 = snapshot();
  if (s0.length < 2) return JSON.stringify({ error: 'fewer than 2 sources', s0 });

  click(1);
  await sleep(3000);
  const s1 = snapshot();

  // the row now at the very bottom is the source that was active before
  click(s1.length - 1);
  await sleep(3000);
  const s2 = snapshot();

  const fmt = (s) =>
    s.map((r, i) => i + ':' + r.key + (r.active ? '[active]' : '')).join(' ');

  return JSON.stringify({
    s0: fmt(s0),
    s1: fmt(s1),
    s2: fmt(s2),
    topIsClicked: s1[0].key === s0[1].key,
    topIsActive: s1[0].active && s1[0].badge === 1,
    prevActiveAtBottom: s1[s1.length - 1].key === s0[0].key,
    prevActiveBadgeGone: s1[s1.length - 1].active === false,
    middleOrderUnchanged:
      s1.slice(1, s1.length - 1).map((r) => r.key).join(',') ===
      s0.slice(1).filter((r) => r.key !== s0[1].key).map((r) => r.key).join(','),
    backToTop: s2[0].key === s0[0].key && s2[0].active,
    swappedDown: s2[s2.length - 1].key === s0[1].key && !s2[s2.length - 1].active,
    badgeCount: s2.reduce((n, r) => n + r.badge, 0),
    errs: (window.__errs || []).length,
  });
})()
