// Real browser with a synthetic API fixture; no live MongoDB or Telegram.
const {chromium} = require('playwright-core');
const assert = require('node:assert/strict');
const base = process.env.NEXT_PREVIEW_TEST_URL || 'http://localhost:3001/next-preview';
(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH || '/tmp/chromium', args:['--no-sandbox','--disable-dev-shm-usage']});
  try {
    for (const width of [320,390,1440]) {
      const page = await browser.newPage({viewport:{width,height:800}, reducedMotion:'reduce'});
      const searches=[]; page.on('request',request=>{if (request.url().includes('/search')) searches.push(request.url())});
      await page.goto(base); await page.getByLabel('One-time key or link').fill('l.valid');
      await page.getByRole('button',{name:'Continue to workspace'}).click();
      await page.getByRole('button',{name:'Find a Studio page'}).waitFor();
      await page.getByRole('button',{name:'Find a Studio page'}).click();
      const dialog=page.getByRole('dialog',{name:'Go somewhere.'}); const input=page.getByLabel('Search Studio pages');
      await dialog.waitFor(); assert.equal(await input.evaluate(e=>document.activeElement===e),true);
      const bounds=await dialog.boundingBox(); assert.ok(bounds && bounds.width<=width && bounds.height<=800, 'dialog must fit the viewport');
      for(let i=0;i<15;i++){await page.keyboard.press('Tab');assert.equal(await dialog.evaluate(e=>e.contains(document.activeElement)),true,'modal focus must stay inside')}
      await input.fill('storage'); assert.equal(await dialog.getByRole('link').count(),1); assert.equal(await dialog.getByRole('link').first().innerText().then(s=>s.includes('File Store')),true);
      await input.fill('nothing-matches-this'); await dialog.getByText('No matching pages.',{exact:false}).waitFor();
      await page.keyboard.press('Escape'); assert.equal(await dialog.isVisible(),false); assert.equal(await page.getByRole('button',{name:'Find a Studio page'}).evaluate(e=>document.activeElement===e),true);
      await page.keyboard.press('Control+k'); await dialog.waitFor(); assert.equal(await input.inputValue(),'');
      await input.fill('personal settings'); await input.press('Enter'); await page.getByLabel('Theme preference').waitFor();
      await page.getByLabel('Theme preference').selectOption('light');
      await page.getByRole('button',{name:'Find a Studio page'}).click(); await input.fill('bot management');
      page.once('dialog',d=>d.dismiss()); await dialog.getByRole('link',{name:/Bot management/}).click();
      assert.equal(await dialog.isVisible(),true,'canceled navigation keeps the palette and draft'); assert.equal(await page.getByLabel('Theme preference').inputValue(),'light');
      page.once('dialog',d=>d.accept()); await dialog.getByRole('link',{name:/Bot management/}).click();
      await page.getByRole('heading',{name:/Your bots,/}).waitFor();
      await page.getByRole('button',{name:'New workspace'}).first().click();
      await page.getByLabel('Workspace name').fill('Not saved yet');
      await page.getByRole('button',{name:'Find a Studio page'}).click(); await input.fill('analytics');
      page.once('dialog',d=>d.dismiss()); await dialog.getByRole('link',{name:/Analytics/}).click();
      assert.equal(await dialog.isVisible(),true,'workspace creation draft is protected');
      await page.keyboard.press('Escape'); assert.equal(await page.getByLabel('Workspace name').inputValue(),'Not saved yet');
      page.once('dialog',d=>d.dismiss()); await page.getByLabel('Select workspace').selectOption('bravo');
      assert.equal(await page.getByLabel('Select workspace').inputValue(),'slow');
      page.once('dialog',d=>d.dismiss()); await page.getByRole('button',{name:'New workspace'}).first().click();
      assert.equal(await page.getByLabel('Workspace name').inputValue(),'Not saved yet');
      page.once('dialog',d=>d.accept()); await page.getByRole('button',{name:'New workspace'}).first().click();
      assert.equal(await page.getByLabel('Workspace name').count(),0);
      await page.getByRole('button',{name:'New workspace'}).first().click(); await page.getByLabel('Workspace name').fill('Discard on switch');
      page.once('dialog',d=>d.accept()); await page.getByLabel('Select workspace').selectOption('bravo');
      await page.getByText('Bravo workspace bot').waitFor(); assert.equal(await page.getByLabel('Workspace name').count(),0);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow at ${width}px`);
      assert.deepEqual(searches,[],'local page filtering should not send search queries');
      await page.close(); console.log(`Next page finder and navigation guard passed at ${width}px`);
    }
    const denied=await browser.newPage(); await denied.goto(base); await denied.getByLabel('One-time key or link').fill('l.noanalytics');
    await denied.getByRole('button',{name:'Continue to workspace'}).click(); await denied.getByRole('button',{name:'Find a Studio page'}).click();
    await denied.getByLabel('Search Studio pages').fill('analytics'); await denied.getByLabel('Search Studio pages').press('Enter');
    await denied.getByText('Analytics access required').waitFor(); await denied.close();
    console.log('Page finder preserves the analytics permission gate');
  } finally {await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
