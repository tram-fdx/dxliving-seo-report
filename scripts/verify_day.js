#!/usr/bin/env node
/*
 * verify_day.js — kiểm tra một ngày vừa được đóng băng / check a freshly frozen day.
 *
 * Two things get checked: the new archive/<date>.html renders correctly as a standalone frozen
 * page, and index.html's date picker offers the new day and routes every stored day to the right
 * place. Structural only — it cannot see stale copy or collided labels, so ALWAYS open the
 * screenshots it writes to /tmp/verify-<date>-{vi,en}.png afterwards.
 *
 * Serve the repo first (the picker fetches data/index.json, which file:// blocks):
 *   cd /tmp/newrepo && nohup python3 -m http.server 8899 --bind 127.0.0.1 >/tmp/srv.log 2>&1 &
 *
 * Usage:
 *   node verify_day.js --date 2026-07-30 [--base http://127.0.0.1:8899]
 *
 * Chromium is preinstalled at /opt/pw-browsers/chromium. Never run `playwright install`.
 */
const { chromium } = require('playwright');

const argv = process.argv.slice(2);
const arg = (k, d) => { const i = argv.indexOf('--' + k); return i === -1 ? d : argv[i + 1]; };
const DATE = arg('date');
const BASE = (arg('base', 'http://127.0.0.1:8899') || '').replace(/\/$/, '');
if (!DATE || !/^\d{4}-\d{2}-\d{2}$/.test(DATE)) {
  console.error('usage: node verify_day.js --date YYYY-MM-DD [--base http://127.0.0.1:8899]');
  process.exit(2);
}
const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const dmy = s => s.split('-').reverse().join('/');
const dMonY = s => { const [y,m,d] = s.split('-'); return Number(d) + ' ' + MON[Number(m)-1] + ' ' + y; };

const problems = [];
const bad = m => { problems.push(m); };

/* The local server serves exactly one 404, /favicon.ico, and Chromium also logs a bare
   "Failed to load resource" with no URL attached that cannot be attributed. Filter both; the
   response-based collector below is the real gate. */
function watch(page, tag) {
  const errs = [], f404 = [];
  page.on('pageerror', e => errs.push(tag + ' pageerror: ' + e.message));
  page.on('console', m => {
    if (m.type() === 'error' && !/favicon|Failed to load resource/.test(m.text())) {
      errs.push(tag + ' console: ' + m.text());
    }
  });
  page.on('response', r => { if (r.status() === 404 && !/favicon/.test(r.url())) f404.push(r.url()); });
  return { errs, f404 };
}

(async () => {
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });

  /* ---------- 1. the manifest ---------- */
  const mp = await b.newPage();
  const mres = await mp.goto(BASE + '/data/index.json?v=' + Date.now());
  if (!mres || mres.status() !== 200) bad('data/index.json did not load: ' + (mres && mres.status()));
  let ix = {};
  try { ix = JSON.parse(await mp.evaluate(() => document.body.innerText)); }
  catch (e) { bad('data/index.json is not parseable: ' + e.message); }
  await mp.close();

  const snaps = (ix.snapshots || []).slice().sort((x, y) => x.date < y.date ? -1 : 1);
  const row = snaps.filter(s => s.date === DATE)[0];
  if (!row) bad('data/index.json has no snapshot for ' + DATE);
  if (row && row.report !== 'archive/' + DATE + '.html') {
    bad('manifest report path for ' + DATE + ' is ' + JSON.stringify(row.report));
  }
  if (row && (!row.label_vi || !row.label_en)) bad(DATE + ' is missing label_vi and/or label_en');
  const sortedOk = snaps.map(s => s.date).join() === (ix.snapshots || []).map(s => s.date).join();
  if (!sortedOk) bad('snapshots[] is not sorted ascending by date');
  if (ix.latest !== snaps[snaps.length - 1].date) {
    bad('latest is ' + ix.latest + ' but the newest snapshot is ' + snaps[snaps.length - 1].date);
  }

  /* ---------- 2. the frozen archive page ---------- */
  const p = await b.newPage({ viewport: { width: 1280, height: 1000 } });
  const w = watch(p, 'archive');
  const ares = await p.goto(BASE + '/archive/' + DATE + '.html', { waitUntil: 'networkidle' });
  if (!ares || ares.status() !== 200) bad('archive page did not load: ' + (ares && ares.status()));
  await p.waitForTimeout(300);

  const info = await p.evaluate(() => {
    const bn = document.querySelector('[data-archive-banner]');
    const sw = document.querySelector('.langsw');
    const vs = bn && bn.querySelector('.vi');
    const r = n => n ? n.getBoundingClientRect() : null;
    const br = r(bn), sr = r(sw), vr = r(vs);
    return {
      banner: !!bn,
      bannerTop: br ? Math.round(br.top) : null,
      overlap: (vr && sr) ? (vr.right > sr.left && vr.top < sr.bottom && vr.bottom > sr.top) : null,
      hoverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      picker: !!document.getElementById('sec-filter'),
      cards: document.querySelectorAll('.card').length,
      svgs: document.querySelectorAll('svg').length,
      rootLinks: Array.from(document.querySelectorAll('a[href]'))
        .map(a => a.getAttribute('href'))
        .filter(h => h && !/^(https?:|\/\/|mailto:|#|\.\.\/)/.test(h)),
    };
  });

  if (!info.banner) bad('archive page has no [data-archive-banner]');
  if (info.banner && info.bannerTop !== 0) bad('banner is not at the top (top=' + info.bannerTop + ')');
  if (info.overlap) bad('banner text overlaps the VI/EN switch — check its padding-right');
  if (info.hoverflow) bad('archive page overflows horizontally');
  if (info.picker) bad('#sec-filter survived the freeze — the picker must be stripped from archives');
  if (info.rootLinks.length) bad('links still pointing at the repo root: ' + JSON.stringify(info.rootLinks));
  if (!info.cards) bad('archive page has no .card sections — did the copy truncate?');

  const langs = {};
  for (const lang of ['vi', 'en']) {
    await p.evaluate(l => setLang(l), lang);
    await p.waitForTimeout(200);
    const txt = await p.evaluate(() => document.body.innerText);
    const need = lang === 'vi'
      ? ['Bản lưu ngày', 'đã đóng băng', 'Xem báo cáo mới nhất', 'Lịch sử dữ liệu theo ngày', dmy(DATE)]
      : ['Archived snapshot', 'frozen and no longer updated', 'Open the latest report', 'Daily data history', dMonY(DATE)];
    const leak = lang === 'vi'
      ? ['Archived snapshot', 'Open the latest report']
      : ['Bản lưu ngày', 'Xem báo cáo mới nhất'];
    const missing = need.filter(s => !txt.includes(s));
    const leaked = leak.filter(s => txt.includes(s));
    langs[lang] = { chars: txt.length, missing, leaked };
    if (missing.length) bad(lang + ' archive missing ' + JSON.stringify(missing));
    if (leaked.length) bad(lang + ' archive leaks the other language: ' + JSON.stringify(leaked));
    await p.screenshot({ path: '/tmp/verify-' + DATE + '-' + lang + '.png', fullPage: false });
  }

  /* The banner says one date; the report's own "Cập nhật / Updated" tag says another. If they
     disagree, index.html was frozen before step 3 updated it — the archive would then claim to be
     a day it does not actually report on. */
  await p.evaluate(() => setLang('vi'));
  await p.waitForTimeout(150);
  const stamped = await p.evaluate(() => {
    const m = document.body.innerText.match(/Cập nhật:\s*(\d{2}\/\d{2}\/\d{4})/);
    return m ? m[1] : null;
  });
  if (stamped === null) bad('no "Cập nhật: dd/mm/yyyy" stamp found on the frozen page');
  else if (stamped !== dmy(DATE)) {
    bad('banner says ' + dmy(DATE) + ' but the report stamps itself ' + stamped
      + ' — index.html was frozen before it was updated for this day (skipped step 3?)');
  }

  const blinks = await p.evaluate(() =>
    Array.from(document.querySelectorAll('[data-archive-banner] a')).map(a => a.getAttribute('href')));
  for (const h of ['../index.html', '../history.html']) {
    if (!blinks.includes(h)) bad('banner is missing a link to ' + h);
  }
  for (const h of blinks) {
    const r = await p.request.get(new URL(h, BASE + '/archive/' + DATE + '.html').toString());
    if (r.status() !== 200) bad('banner link ' + h + ' resolves to HTTP ' + r.status());
  }
  if (w.errs.length) bad('archive JS: ' + JSON.stringify(w.errs));
  if (w.f404.length) bad('archive 404s: ' + JSON.stringify(w.f404));
  await p.close();

  /* ---------- 3. index.html picker sees the new day ---------- */
  const q = await b.newPage({ viewport: { width: 1440, height: 1100 } });
  const w2 = watch(q, 'index');
  await q.goto(BASE + '/index.html?v=' + Date.now(), { waitUntil: 'networkidle' });
  /* .dp-day cells do not exist until the manifest fetch resolves */
  await q.waitForFunction(() => !!document.querySelector('#dpbtn'), null, { timeout: 5000 })
    .catch(() => bad('#dpbtn never appeared — did data/index.json fail to load?'));
  const shown = await q.evaluate(() => {
    const s = document.getElementById('sec-filter');
    return s ? getComputedStyle(s).display !== 'none' : false;
  });
  if (!shown) bad('#sec-filter stayed hidden on index.html');

  await q.click('#dpbtn');
  await q.waitForTimeout(400);

  const cell = await q.evaluate(d => {
    const c = document.querySelector('.dp-day[data-d="' + d + '"]');
    if (!c) return null;
    return { has: c.classList.contains('has'), dot: !!c.querySelector('.dot'), off: c.classList.contains('off') };
  }, DATE);
  if (!cell) bad(DATE + ' has no cell in the visible calendar (it may be off-month — check by hand)');
  else {
    if (!cell.has) bad(DATE + ' is not marked as a stored day in the calendar');
    if (!cell.dot) bad(DATE + ' has no dot — the reader cannot tell it was scanned');
    if (cell.off) bad(DATE + ' is drawn dim despite being stored');
  }

  /* Routing: every stored day must resolve to the target the contract promises. The newest day
     is deliberately NOT a link — this page already is that report, so it renders as
     <span class="daylink cur">. Anything else there would be a link back to itself. */
  const routes = await q.evaluate(() => {
    const out = [];
    document.querySelectorAll('#daylinks a.daylink').forEach(a =>
      out.push({ href: a.getAttribute('href'), text: a.textContent.trim().slice(0, 40) }));
    return out;
  });
  const curText = await q.evaluate(() => {
    const c = document.querySelector('#daylinks .daylink.cur');
    return c ? c.textContent.trim() : null;
  });
  if (curText === null) bad('no .daylink.cur — the newest day should be marked, not linked');
  else if (!curText.includes(dmy(ix.latest))) {
    bad('.daylink.cur reads ' + JSON.stringify(curText.slice(0, 40)) + ' but latest is ' + ix.latest);
  }
  for (const s of snaps) {
    if (s.date === ix.latest) continue;   // marked, not linked — checked above
    const want = s.report ? s.report : 'history.html?day=' + s.date;
    if (!routes.filter(r => r.href === want).length) {
      bad('no day link routes ' + s.date + ' to ' + want);
    }
  }
  if (routes.length !== snaps.length - 1) {
    bad('expected ' + (snaps.length - 1) + ' day links plus the current day, got ' + routes.length);
  }
  for (const r of routes) {
    const res = await q.request.get(new URL(r.href.split('?')[0], BASE + '/').toString());
    if (res.status() !== 200) bad('day link ' + r.href + ' resolves to HTTP ' + res.status());
  }
  if (w2.errs.length) bad('index JS: ' + JSON.stringify(w2.errs));
  if (w2.f404.length) bad('index 404s: ' + JSON.stringify(w2.f404));
  await q.close();
  await b.close();

  /* ---------- report ---------- */
  console.log('manifest  latest=' + ix.latest + '  days=' + snaps.length);
  console.log('archive   ' + JSON.stringify(info));
  console.log('languages ' + JSON.stringify(langs));
  console.log('routes    ' + JSON.stringify(routes.map(r => r.href)));
  console.log('shots     /tmp/verify-' + DATE + '-vi.png  /tmp/verify-' + DATE + '-en.png');
  if (problems.length) {
    console.log('\nPROBLEMS (' + problems.length + '):');
    problems.forEach(m => console.log('  - ' + m));
    process.exitCode = 1;
  } else {
    console.log('\nALL CHECKS OK for ' + DATE);
    console.log('Now OPEN the two screenshots. Assertions cannot see stale copy or collided labels.');
  }
})().catch(e => { console.error('verify_day crashed: ' + e.message); process.exit(1); });
