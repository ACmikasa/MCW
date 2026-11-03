const { chromium } = require('C:/Users/23563/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

(async () => {
    const html = fs.readFileSync(path.join(__dirname, '../web/frontend.html'), 'utf8');
    const browser = await chromium.launch({ channel: 'msedge', headless: true });
    try {
        const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.route('http://eeg.test/**', route => route.fulfill(route.request().url().includes('/assets/') ? { contentType: 'text/javascript', body: '' } : { contentType: 'text/html', body: html }));
        await page.addInitScript(() => {
            window.WebSocket = class { static OPEN = 1; constructor() { window.__socket = this; } send() {} };
            window.fetch = async () => ({ ok: true, json: async () => ({ display_name: 'test', permissions: [] }) });
        });
        await page.goto('http://eeg.test/');
        await page.waitForSelector('.eeg-channel');
        const handle = page.locator('[data-resize-channel="0"]');
        assert.strictEqual(await handle.count(), 1, 'each channel needs a resize handle');
        const first = page.locator('.eeg-channel').nth(0);
        const second = page.locator('.eeg-channel').nth(1);
        const h0 = (await first.boundingBox()).height;
        const h1 = (await second.boundingBox()).height;
        const box = await handle.boundingBox();
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        await page.mouse.down();
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2 + 80, { steps: 5 });
        await page.mouse.up();
        assert((await first.boundingBox()).height >= h0 + 75, 'drag must enlarge only the selected channel');
        assert.strictEqual((await second.boundingBox()).height, h1, 'neighbor channel height must stay unchanged');
        const canvas = await page.locator('#canvas-ch0').evaluate(el => ({ height: el.height, css: el.getBoundingClientRect().height }));
        assert(Math.abs(canvas.height - canvas.css * 2) <= 2, 'canvas resolution follows row height');
        await handle.focus();
        await page.keyboard.press('ArrowDown');
        const resizedHeight = (await first.boundingBox()).height;
        assert(resizedHeight >= h0 + 85, 'keyboard resize works');
        await page.locator('#channel-select').selectOption(['1']);
        assert.strictEqual(await page.locator('#channel-row-0').count(), 0, 'unselected channel is hidden');
        await page.locator('#channel-select').selectOption(['0', '1']);
        assert.strictEqual((await page.locator('#channel-row-0').boundingBox()).height, resizedHeight, 'height survives channel selection');
        await page.evaluate(() => window.__socket.onmessage({ data: JSON.stringify({ type: 'data', eeg: Array.from({ length: 16 }, () => [1, 2]), timestamps: [0, 0.008], stats: { total_samples: 2, dropped_packets: 0 } }) }));
        await page.waitForTimeout(300);
        assert.match(await page.locator('#status-ch0').textContent(), /0\.50 uVrms/, 'resized channel still processes and displays data');
        assert.deepStrictEqual(errors, []);
        console.log('PASS: per-channel mouse and keyboard resize, neighbor unchanged, canvas resolution updated');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
