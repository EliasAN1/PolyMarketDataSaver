/* Run with Node and Playwright available (NODE_PATH may point to your install).
   BROWSER_EXECUTABLE optionally selects a local Chromium/Edge installation.
   The isolated server below serves fixtures only; no trading server is contacted. */
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

const staticRoot = path.resolve(__dirname, '../src/pmtrader/ui/static');
const midnight = new Date(); midnight.setHours(0, 0, 0, 0);
const start = Math.floor(+midnight / 1000) + 3600;
const records = [];
for (let i = 0; i < 45; i++) {
  const ts = start + i * 900;
  const entry = { event: i === 43 ? 'reject' : 'entry', order_id: `test-${i}`, slug: `btc-updown-5m-${ts}`, side: i % 3 ? 'UP' : 'DOWN', entry_ts: ts + 12, window_end_ts: ts + 300, fill_price: .52, fill_shares: 50, stake_usd: 26, unmatched: i === 42, ok: i !== 43 };
  records.push(entry);
  if (i < 43) records.push({ event: 'resolve', order_id: entry.order_id, won: i % 4 !== 1, window_end_ts: ts + 300 });
}
const live = { running: true, seconds_left: 166, state: 'skip:too_late', side: 'DOWN', ptb: 83666.76, btc: 83604.03, btc_delta: -62.73, twap: 83626.05, twap_delta: -40.71, up_ask: .29, down_ask: .73, up_mid: .28, down_mid: .72, config: { watch_from_s: 0, watch_to_s: 120, min_btc_away: 0, max_btc_away: 200, odds_min: .45, odds_max: .55, fak_limit: .60 }, checks: Array.from({ length: 9 }, (_, i) => ({ id: `check${i}`, name: `Entry check ${i}`, enabled: true, ok: i % 3 !== 0, target: 'Strategy requirement', value: i % 3 ? 'Passing' : 'Waiting' })) };
const profile = { wallet: '0x123456789012345678901234', signature_type: 3, clob: { balance_collateral: { balance: '871190000' }, open_orders: [] }, data: { value: [{ value: 1036.42 }], positions: [{ title: 'BTC 5m', curPrice: .7, size: 50, avgPrice: .52, outcome: 'Down', cashPnl: 9 }] } };
const fixtures = { '/api/logs': { live: 'trades.jsonl', files: [{ id: 'live', label: 'Live', file: 'trades.jsonl', live: true }] }, '/api/logs/trades.jsonl': records.map(JSON.stringify).join('\n'), '/api/logs/balance.json': { balance_pusd: 871.19 }, '/api/live': live, '/api/profile': profile };
const server = http.createServer(async (req, res) => {
  try {
    const name = new URL(req.url, 'http://localhost').pathname;
    if (name.startsWith('/api/')) {
      // Unknown APIs, including any trade-execution endpoint, are never forwarded.
      const fixture = fixtures[name];
      if (fixture === undefined) { res.writeHead(404); return res.end('{}'); }
      res.setHeader('Content-Type', typeof fixture === 'string' ? 'text/plain' : 'application/json');
      return res.end(typeof fixture === 'string' ? fixture : JSON.stringify(fixture));
    }
    const file = path.resolve(staticRoot, '.' + (name === '/' ? '/index.html' : decodeURIComponent(name)));
    if (!file.startsWith(staticRoot + path.sep)) { res.writeHead(403); return res.end(); }
    const data = await fs.readFile(file);
    res.setHeader('Content-Type', { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(file)] || 'application/octet-stream');
    res.end(data);
  } catch { res.writeHead(404); res.end(); }
});

async function run() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  const errors = [];
  try {
    for (const width of [320, 390, 430, 1280]) {
      const context = await browser.newContext({ viewport: { width, height: 844 }, isMobile: width < 640, hasTouch: width < 640, reducedMotion: 'reduce' });
      const page = await context.newPage();
      page.on('pageerror', e => errors.push(e.message));
      // Keep the test independent of remote fonts or connectivity.
      await page.route(/^https:\/\//, route => route.abort());
      await page.goto(url);
      await page.waitForFunction(() => document.querySelector('#overview-recent .compact-trade'));
      assert.equal(await page.locator('#stats-pane').isVisible(), true, 'Overview opens for a new visitor');
      assert.match(await page.locator('#hero-net').innerText(), /\$\d/);
      for (const tab of ['stats', 'live', 'days', 'trades', 'wallet', 'settings']) {
        await page.locator(`.tab-btn[data-tab="${tab}"]`).click();
        if (tab === 'wallet') await page.waitForSelector('[data-close-run]');
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
        assert.equal(overflow, false, `${tab} has no horizontal page overflow at ${width}px`);
      }
      if (width === 390) {
        await page.locator('.tab-btn[data-tab="live"]').click();
        assert.equal(await page.locator('#odds-up-percent').innerText(), '28%');
        assert.equal(await page.locator('#odds-down-percent').innerText(), '72%');
        await page.locator('#checklist-toggle').click();
        assert.equal(await page.locator('#detail-sheet').isVisible(), true);
        assert.equal(await page.locator('.sheet-checks .check-card').count(), 9);
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#detail-sheet').isVisible(), false);
        const odds = await page.evaluate(async () => {
          const { oddsSplit, renderLive } = await import('/js/live.js?v=23');
          const valid = [oddsSplit(0, 1), oddsSplit(1, 0), oddsSplit(.28, .72)];
          const absent = [oddsSplit(null, 1), oddsSplit(undefined, .5), oddsSplit(0, 0), oddsSplit(NaN, .5)];
          renderLive({ running: true, up_mid: 0, down_mid: 1 });
          const zero = document.querySelector('#odds-up-percent').textContent;
          renderLive({ running: true });
          return { valid, absent, zero, missing: document.querySelector('#odds-ratio-bar').classList.contains('is-unavailable') };
        });
        assert.deepEqual(odds, { valid: [0, 100, 28.000000000000004], absent: [null, null, null, null], zero: '0%', missing: true });

        await page.locator('.tab-btn[data-tab="stats"]').click();
        await page.locator('#would-section summary').click();
        assert.equal(await page.locator('.would-tile').count(), 3);
        assert.match(await page.locator('#would-grid').innerText(), /Expected P&L/i);
        const actual = await page.locator('#hero-net').innerText();
        const expected = await page.locator('#would-summary').innerText();
        assert.notEqual(actual, expected, 'Unfilled hypothetical outcome does not enter realized profit');
        assert.match(await page.locator('#would-section').innerText(), /Unfilled orders only/);
        await page.locator('#would-section summary').click();
        await page.locator('#equity-expand-btn').click();
        assert.equal(await page.locator('#equity-card').evaluate(e => e.classList.contains('is-expanded')), true);
        await page.locator('#equity-expand-btn').click();
        for (const mode of ['day', 'week', 'month', 'all']) {
          await page.locator(`[data-eq-mode="${mode}"]`).click();
          assert.equal(await page.locator('svg.equity-chart').count(), 1);
        }
        // Chart inspection must be usable by keyboard and survive live refreshes.
        const chartTotal = await page.locator('[data-readout-value]').innerText();
        await page.locator('[data-scrubber]').focus();
        await page.keyboard.press('Home');
        const firstTradeValue = await page.locator('[data-readout-value]').innerText();
        assert.notEqual(firstTradeValue, chartTotal);
        assert.match(await page.locator('[data-point-note]').innerText(), /this trade/);
        await page.waitForTimeout(4300);
        assert.equal(await page.locator('[data-readout-value]').innerText(), firstTradeValue, 'Live refresh preserves the inspected trade');
        await page.locator('[data-latest]').click();
        assert.equal(await page.locator('[data-readout-value]').innerText(), chartTotal);
        await page.locator('[data-metric="drawdown"]').click();
        assert.match(await page.locator('.pc-footnote').innerText(), /below the previous profit peak/);
        assert.equal(await page.locator('.pc-chart').evaluate(e => getComputedStyle(e).touchAction), 'pan-y');
        await page.locator('[data-metric="net"]').click();
        await page.locator('[data-compare]').click();
        assert.equal(await page.locator('.pc-comparison').isVisible(), true);
        const comparison = await page.locator('[data-range-result="a"]').innerText();
        await page.locator('[data-range="a"][data-end="1"]').focus();
        await page.keyboard.press('Home');
        assert.notEqual(await page.locator('[data-range-result="a"]').innerText(), comparison);
        assert.match(await page.locator('[data-range-result="a"]').innerText(), /1 trade/);
        const calculations = await page.evaluate(async () => {
          const { performancePoints } = await import('/js/performance-chart.js?v=24');
          const result = performancePoints([10,-6,8].map((netPnl,i) => ({ oid: `math-${i}`, entryTs: i+1, resolved: true, won: netPnl > 0, netPnl })));
          return { net: result.net, dip: result.maxDip, wins: result.wins, drawdowns: result.points.map(p=>p.drawdown) };
        });
        assert.deepEqual(calculations, { net: 12, dip: -6, wins: 2, drawdowns: [0,-6,0] });
        await page.locator('[data-compare]').click();
        await page.locator('#equity-expand-btn').click();
        assert.equal(await page.locator('.pc-dialog').isVisible(), true);
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('.pc-dialog').isVisible(), false);
        await page.locator('#overview-recent [data-trade-detail]').first().click();
        assert.match(await page.locator('#sheet-content').innerText(), /Order ID/);
        await page.locator('[data-sheet-close]').click();

        await page.locator('.tab-btn[data-tab="days"]').click();
        assert.ok(await page.locator('.cal-week-result').count() >= 4);
        await page.locator('.cal-cell.has-trades').first().click();
        assert.equal(await page.locator('.cal-month').isVisible(), false);
        assert.ok(await page.locator('[data-cal-hour]').count() > 2);
        const totalMarkets = await page.locator('.cal-fill').count();
        await page.locator('[data-cal-hour]:not([data-cal-hour="all"])').first().click();
        assert.ok(await page.locator('.cal-fill').count() < totalMarkets);
        assert.match(await page.locator('.cal-markets-head').innerText(), /:00/);
        await page.locator('[data-cal-hour-step="1"]').click();
        assert.equal(await page.locator('[data-cal-hour][aria-selected="true"]').count(), 1);
        await page.locator('[data-cal-back]').click();
        assert.equal(await page.locator('.cal-month').isVisible(), true);

        await page.locator('.tab-btn[data-tab="trades"]').click();
        assert.equal(await page.locator('#trades-pane input[type="search"]').count(), 0);
        assert.equal(await page.locator('#mobile-trades-list .compact-trade').count(), 30);
        await page.locator('#trades-load-more').click();
        assert.equal(await page.locator('#mobile-trades-list .compact-trade').count(), 45);
        for (const [filter, count] of [['won', 31], ['lost', 11], ['open', 1], ['unfilled', 2]]) {
          await page.locator(`[data-trade-filter="${filter}"]`).click();
          assert.equal(await page.locator('#mobile-trades-list .compact-trade').count(), Math.min(count, 30));
          assert.match(await page.locator('#trades-count').innerText(), new RegExp(`^${count} of`));
        }
        await page.locator('#mobile-trades-list .compact-trade').last().click();
        assert.match(await page.locator('#sheet-content').innerText(), /Hypothetical result/);
        await page.locator('[data-sheet-close]').click();

        await page.locator('.tab-btn[data-tab="wallet"]').click();
        const posts = [];
        await page.route('**/api/close-positions', route => {
          const body = route.request().postDataJSON(); posts.push(body);
          return route.fulfill({ json: { scan_only: body.scan_only, jobs: [], redeemed: 0, sold: 0, skipped: 0 } });
        });
        page.once('dialog', dialog => dialog.dismiss());
        await page.locator('[data-close-run]').click();
        assert.equal(posts.length, 0, 'Cancel never sends a close request');
        await page.locator('[data-close-scan]').click();
        await page.waitForFunction(() => document.querySelector('[data-close-status]').textContent.startsWith('Scan:'));
        assert.deepEqual(posts[0], { scan_only: true, confirm: false, dry_run: false });
        page.once('dialog', dialog => dialog.accept());
        await page.locator('[data-close-run]').click();
        await page.waitForFunction(() => document.querySelector('[data-close-status]').textContent.startsWith('Closed:'));
        assert.deepEqual(posts[1], { scan_only: false, confirm: true, dry_run: false });
        await page.locator('summary').filter({ hasText: 'Active Positions' }).click();
        assert.match(await page.locator('#profile-body').innerText(), /BTC 5m/);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'Expanded wallet tables scroll within their section');
      }
      await context.close();
      console.log(`PASS ${width}px layout and controls`);
    }
    for (const mode of ['empty', 'offline']) {
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/api/logs/trades.jsonl*', route => mode === 'empty' ? route.fulfill({ body: '' }) : route.fulfill({ status: 503, body: '{}' }));
      await page.route('**/api/live', route => route.fulfill({ json: { running: false } }));
      await page.goto(url);
      if (mode === 'empty') {
        await page.waitForSelector('#overview-recent .compact-empty');
        assert.match(await page.locator('#hero-net').innerText(), /0.00/);
      } else {
        await page.waitForSelector('#data-status');
        assert.match(await page.locator('#data-status').innerText(), /VPN/);
      }
      await page.locator('.tab-btn[data-tab="live"]').click();
      assert.equal(await page.locator('#connection-label').innerText(), 'Offline');
      await page.close();
      console.log(`PASS ${mode} state`);
    }
    assert.deepEqual(errors, [], 'No browser JavaScript errors');
  } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => server.close());
