const { chromium } = require('C:/Users/23563/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

(async () => {
    const browser = await chromium.launch({ channel: 'msedge', headless: true });
    try {
        const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        const html = fs.readFileSync(path.join(__dirname, '../web/frontend.html'), 'utf8');
        await page.route('http://eeg.test/**', route => route.fulfill(route.request().url().includes('/assets/') ? { contentType: 'text/javascript', body: '' } : { contentType: 'text/html', body: html }));
        await page.addInitScript(() => {
            window.WebSocket = class { static OPEN = 1; constructor() { window.__socket = this; } send() {} };
            window.fetch = async () => ({ ok: true, json: async () => ({ display_name: 'test', permissions: [] }) });
        });
        await page.goto('http://eeg.test/');
        await page.waitForSelector('#filter-highpass');
        await page.evaluate(() => {
            window.__socket.onmessage({ data: JSON.stringify({ type: 'state', sampling_rate: 250 }) });
            const data = Array.from({ length: 1250 }, (_, i) => 10 * Math.sin(2 * Math.PI * 10 * i / 250) + 10 * Math.sin(2 * Math.PI * 50 * i / 250));
            window.__socket.onmessage({ data: JSON.stringify({ type: 'data', eeg: Array.from({ length: 16 }, () => data), timestamps: data.map((_, i) => i / 250) }) });
        });
        const rms = async () => Number((await page.locator('#status-ch0 .rms').textContent()).split(' ')[0]);
        await page.waitForFunction(() => document.querySelector('#status-ch0 .rms')?.textContent.includes('10.00 uVrms'));
        assert(Math.abs(await rms() - 10) < 0.1, 'unfiltered 10 Hz + 50 Hz signal has expected RMS');
        await page.locator('#filter-notch').click();
        assert((await rms()) < 8, '50 Hz notch attenuates the 50 Hz component');
        await page.locator('#filter-notch').click();
        assert(Math.abs(await rms() - 10) < 0.1, 'disabling filter restores unfiltered display');
        await page.locator('#filter-highpass-hz').fill('20');
        await page.locator('#filter-highpass-hz').dispatchEvent('change');
        await page.locator('#filter-highpass').click();
        assert((await rms()) < 8, 'high-pass attenuates lower frequencies');
        await page.locator('#filter-highpass').click();
        await page.locator('#filter-lowpass-hz').fill('5');
        await page.locator('#filter-lowpass-hz').dispatchEvent('change');
        await page.locator('#filter-lowpass').click();
        assert((await rms()) < 3, 'low-pass attenuates higher frequencies');
        await page.locator('#filter-notch-hz').fill('125');
        await page.locator('#filter-notch-hz').dispatchEvent('change');
        await page.locator('#filter-notch').click();
        assert.strictEqual(await page.locator('#filter-notch').getAttribute('aria-pressed'), 'false');
        assert.match(await page.locator('#filter-feedback').textContent(), /采样率/);
        await page.locator('#filter-highpass-hz').fill('10');
        await page.locator('#filter-highpass-hz').dispatchEvent('change');
        await page.locator('#filter-highpass').click();
        assert.strictEqual(await page.locator('#filter-highpass').getAttribute('aria-pressed'), 'false', 'high-pass must be below active low-pass');
        assert.match(await page.locator('#filter-feedback').textContent(), /高通频率须小于低通/);
        await page.locator('#filter-lowpass').click();
        await page.locator('#filter-notch-hz').fill('50');
        await page.locator('#filter-notch-hz').dispatchEvent('change');
        await page.locator('#filter-notch').click();
        const wholeRms = await rms();
        await page.locator('#clear-btn').click();
        await page.evaluate(() => {
            for (let start = 0; start < 1250; start += 25) {
                const data = Array.from({ length: 25 }, (_, i) => 10 * Math.sin(2 * Math.PI * 10 * (i + start) / 250) + 10 * Math.sin(2 * Math.PI * 50 * (i + start) / 250));
                window.__socket.onmessage({ data: JSON.stringify({ type: 'data', eeg: Array.from({ length: 16 }, () => data), timestamps: data.map((_, i) => (i + start) / 250) }) });
            }
        });
        await page.waitForFunction(() => {
            const value = parseFloat(document.querySelector('#status-ch0 .rms')?.textContent);
            return value > 6 && value < 8;
        });
        assert(Math.abs(await rms() - wholeRms) < 0.05, 'filter state must continue across packet boundaries');
        await page.evaluate(() => window.__socket.onmessage({ data: JSON.stringify({ type: 'state', sampling_rate: 100 }) }));
        assert.strictEqual(await page.locator('#filter-notch').getAttribute('aria-pressed'), 'false', 'invalid filter disables on sampling rate change');
        assert.match(await page.locator('#filter-feedback').textContent(), /采样率变化/);
        assert(Math.abs(await rms() - 10) < 0.1, 'display redraws immediately after invalid filter is disabled');
        if (process.env.EEG_FILTER_SCREENSHOT) await page.screenshot({ path: process.env.EEG_FILTER_SCREENSHOT });
        assert.deepStrictEqual(errors, []);
        console.log('PASS: filter controls, frequency attenuation, bypass and invalid frequency handling');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
