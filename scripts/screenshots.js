// Gera as capturas de tela do README a partir de exemplos/semana-exemplo.json (dados fictícios).
// Uso: npm run screenshots
const { chromium, devices } = require('playwright');
const fs = require('fs');
const path = require('path');
const R = path.resolve(__dirname, '..');
const demo = JSON.parse(fs.readFileSync(R + '/exemplos/semana-exemplo.json'));
const seed = { weekStart: demo.weekStart, types: demo.types, weeks: demo.weeks, palette: null, tasks: demo.tasks };
const out = process.argv[2] || path.join(R, 'docs');
async function open(browser, opts, prefs) {
  const ctx = await browser.newContext({ timezoneId: 'America/Sao_Paulo', locale: 'pt-BR', ...opts });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(e.message));
  await page.clock.install({ time: new Date('2026-11-26T16:20:00-03:00') });
  await page.addInitScript(([s, p]) => {
    if (!sessionStorage.getItem('seeded')) {
      localStorage.clear();
      localStorage.setItem('quadro-semana-v1', JSON.stringify(s));
      localStorage.setItem('quadro-prefs-v2', JSON.stringify(p));
      sessionStorage.setItem('seeded', '1');
    }
  }, [seed, prefs]);
  await page.goto('file://' + R + '/app/index.html');
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.evaluate(() => document.fonts.ready);
  await page.clock.runFor(1500);
  return { page, ctx, errs };
}
(async () => {
  const browser = await chromium.launch({ args: ['--lang=pt-BR'] });
  // computador
  let { page, ctx, errs } = await open(browser, { viewport: { width: 1400, height: 860 }, deviceScaleFactor: 2 },
    { view: 'week', palClosed: false, grpOpen: { fixo: false, estudo: true, outros: false } });
  await page.evaluate(() => { document.querySelector('#scroller').scrollTop = 2 * 28 * 1 + 28 * 2; });
  await page.screenshot({ path: out + '/quadro-desktop.png' });
  await page.click('#study'); await page.clock.runFor(800);
  await page.screenshot({ path: out + '/quadro-resumo.png' });
  await page.keyboard.press('Escape');
  await page.click('#btn-types'); await page.clock.runFor(300);
  await page.evaluate(() => { const c = document.querySelector('#modal-card'); const r = [...c.querySelectorAll('.type-row')].find(n => (n.querySelector('.nm')||{}).textContent === 'CodeLabs'); if (r) r.scrollIntoView({ block: 'center' }); });
  await page.screenshot({ path: out + '/quadro-tipos.png' });
  await page.keyboard.press('Escape');
  // ficha de um bloco com sessão registrada (terça 14:00)
  await page.evaluate(() => { document.querySelector('#scroller').scrollTop = 7 * 28 * 2 - 100; });
  const b = await page.$('.day-slots[data-day="1"] .block[aria-label*="14:00"]');
  if (b) { await b.click(); await page.clock.runFor(300); await page.click('.sess-more > summary'); await page.screenshot({ path: out + '/quadro-ficha.png' }); }
  console.log('desktop errors', errs);
  await ctx.close();
  // celular
  ({ page, ctx, errs } = await open(browser, { ...devices['iPhone 13'] }, { view: 'day' }));
  await page.screenshot({ path: out + '/quadro-celular.png' });
  const reg = await page.$('#now button.go');
  if (reg) { await reg.click(); await page.clock.runFor(300); await page.screenshot({ path: out + '/quadro-registro.png' }); }
  console.log('phone errors', errs, !!reg);
  await browser.close();
})();
