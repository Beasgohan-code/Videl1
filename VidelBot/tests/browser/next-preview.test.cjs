// Synthetic API fixture only; does not test real MongoDB, Telegram or the deployed service.
const {chromium} = require('playwright-core');
const assert = require('node:assert/strict');
const base = process.env.NEXT_PREVIEW_TEST_URL || 'http://localhost:3001/next-preview';
(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH || '/tmp/chromium', args: ['--no-sandbox', '--disable-dev-shm-usage']});
  try {
    for (const width of [320, 390, 1440]) {
      const page = await browser.newPage({viewport: {width, height: 900}, reducedMotion: 'reduce'});
      await page.goto(base + '#l.valid', {waitUntil: 'domcontentloaded'});
      await page.getByText('A one-time key was found.').waitFor();
      assert.equal(new URL(page.url()).hash, '', 'the one-time fragment must be removed');
      await page.getByRole('button', {name: 'Continue to workspace'}).click();
      await page.getByLabel('Select workspace').waitFor({state: 'visible'});
      await page.waitForFunction(() => !document.querySelector('#workspace').disabled);
      await page.getByLabel('Select workspace').selectOption('bravo');
      await page.getByText('Bravo workspace bot').waitFor();
      await page.waitForTimeout(1300);
      assert.equal(await page.getByText('Slow workspace bot').count(), 0, 'stale workspace data must not render');
      await page.getByRole('button', {name: 'New workspace'}).first().click();
      await page.getByLabel('Workspace name').fill('Fixture new workspace');
      await page.getByRole('button', {name: 'Create workspace'}).click();
      await page.getByText('Fixture new workspace bot').waitFor();
      assert.equal(await page.locator('#workspace option:checked').textContent(), 'Fixture new workspace');
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
      assert.equal(overflow, false, `unexpected horizontal overflow at ${width}px`);
      await page.getByRole('button', {name: 'Sign out'}).click();
      await page.getByText('Signed out.', {exact:true}).waitFor();
      assert.equal(await page.getByText('Fixture new workspace bot').count(), 0);
      await page.close();
      console.log(`Next preview synthetic browser checks passed at ${width}px`);
    }
    const page = await browser.newPage();
    await page.goto(base);
    await page.getByLabel('One-time key or link').fill('https://example.org/app#l.empty');
    await page.getByRole('button', {name: 'Continue to workspace'}).click();
    await page.getByText('No workspace available.').waitFor();
    await page.getByRole('button', {name: 'New workspace'}).last().click();
    await page.getByLabel('Workspace name').fill('First workspace');
    await page.getByRole('button', {name: 'Create workspace'}).click();
    await page.getByText('First workspace bot').waitFor();
    console.log('Next preview synthetic empty-workspace and pasted-link checks passed');
    await page.route('**/api/v1/auth/me', route => route.fulfill({status: 401, contentType: 'application/json', body: JSON.stringify({error: 'Unauthorized'})}));
    await page.getByRole('button', {name: 'Refresh'}).click();
    await page.getByText('Your session has expired. Sign in again.').waitFor();
    assert.equal(await page.getByText('First workspace bot').count(), 0, 'expired sessions must clear private content');
    console.log('Next preview synthetic session expiry check passed');
    await page.close();
    const failed = await browser.newPage();
    await failed.route('**/api/v1/workspaces/bravo/bots?*', route => route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: 'Synthetic outage'})}));
    await failed.goto(base);
    await failed.getByLabel('One-time key or link').fill('l.valid');
    await failed.getByRole('button', {name: 'Continue to workspace'}).click();
    await failed.waitForFunction(() => { const e = document.querySelector('#workspace'); return e && !e.disabled; });
    await failed.getByLabel('Select workspace').selectOption('bravo');
    await failed.getByText('Could not load workspace bot records.', {exact:false}).waitFor();
    assert.equal(await failed.getByText('Synthetic outage').count(), 0, 'private server diagnostics must not render');
    assert.equal(await failed.getByText('No bot records returned.').count(), 0, 'failed reads must not be shown as empty records');
    assert.equal(await failed.getByText('Loading workspace…').count(), 0, 'failed reads must not be shown as loading');
    console.log('Next preview synthetic read-failure check passed');
    await failed.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
