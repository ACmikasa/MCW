const { chromium } = require('C:/Users/23563/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('fs');
const assert = require('assert');
const path = require('path');

(async () => {
    const html = fs.readFileSync(path.join(__dirname, '../web/frontend.html'), 'utf8');
    const browser = await chromium.launch({ channel: 'msedge', headless: true });
    try {
        const page = await browser.newPage();
        page.on('pageerror', error => console.error('PAGE ERROR:', error.message));
        await page.route('http://eeg.test/**', route => route.fulfill(route.request().url().includes('/assets/') ? { contentType: 'text/javascript', body: '' } : { contentType: 'text/html', body: html }));
        await page.addInitScript(() => {
            window.__socket = null;
            window.WebSocket = class {
                static OPEN = 1;
                constructor() { window.__socket = this; setTimeout(() => this.onopen?.(), 0); }
                send() {}
            };
            const stroke = CanvasRenderingContext2D.prototype.stroke;
            window.__strokes = 0;
            CanvasRenderingContext2D.prototype.stroke = function() { window.__strokes++; return stroke.call(this); };
            window.fetch = async () => ({ ok: true, json: async () => ({ display_name: 'test', permissions: [] }) });
        });
        await page.goto('http://eeg.test/');
        const result = await page.evaluate(() => {
            const msg = { type: 'data', eeg: Array.from({ length: 16 }, () => [1, 2]), timestamps: [0, 0.008], stats: { total_samples: 200, dropped_packets: 0 } };
            for (let i = 0; i < 100; i++) window.__socket.onmessage({ data: JSON.stringify(msg) });
            return { strokes: window.__strokes };
        });
        assert(result.strokes < 100, 'data bursts should defer and coalesce drawing');
        await page.waitForTimeout(1000);
        const after = await page.evaluate(() => window.__strokes);
        console.log('strokes during burst:', result.strokes, 'after:', after);
        assert(after > result.strokes, 'coalesced draw should run after the burst');
        assert.match(await page.locator('#status-ch0').textContent(), /0\.50 uVrms/, 'waveform statistics use the received samples');
        assert.strictEqual((await page.locator('#stat-samples').textContent()).replace(/,/g, ''), '200');
        console.log('PASS: burst data retained and rendering deferred');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
