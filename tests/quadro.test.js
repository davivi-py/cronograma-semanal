// Testes de ponta a ponta do quadro (app/index.html) num Chromium sem interface.
// Rodam o arquivo real, com o relógio fixo e um banco de documentos simulado.
// Uso: npm test
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const APP = 'file://' + path.join(ROOT, 'app', 'index.html');
const demo = JSON.parse(fs.readFileSync(path.join(ROOT, 'exemplos', 'semana-exemplo.json'), 'utf8'));
const NOW = new Date('2026-11-26T21:30:00-03:00'); // quinta à noite, semana do exemplo

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser.close(); });

// Banco falso com a mesma interface que a plataforma entrega em window.claude.use("db").
function fakeDb(store) {
  window.__store = store;
  window.__writes = [];
  const listeners = {};
  const snap = (p) => ({ exists: p in store, id: p.split('/').pop(), data: () => JSON.parse(JSON.stringify(store[p])) });
  const col = (c) => ({ docs: Object.keys(store).filter((k) => k.split('/').length === 2 && k.startsWith(c + '/')).map(snap) });
  const db = {
    doc: (p) => ({
      set: async (b) => { store[p] = JSON.parse(JSON.stringify(b)); window.__writes.push(p); (listeners[p] || []).forEach((f) => f(snap(p))); },
      get: async () => snap(p),
      delete: async () => { delete store[p]; },
      onSnapshot: (ok) => { (listeners[p] = listeners[p] || []).push(ok); setTimeout(() => ok(snap(p)), 5); return () => {}; },
    }),
    collection: (c) => ({ get: async () => col(c), onSnapshot: (ok) => { setTimeout(() => ok(col(c)), 5); return () => {}; } }),
  };
  window.claude = { use: async (name) => (name === 'db' ? db : null) };
}

async function openApp({ store } = {}) {
  const ctx = await browser.newContext({ timezoneId: 'America/Sao_Paulo', viewport: { width: 1300, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.clock.install({ time: NOW });
  if (store) await page.addInitScript(fakeDb, store);
  else await page.addInitScript((d) => localStorage.setItem('quadro-semana-v1', JSON.stringify({ ...d, palette: null })), demo);
  await page.goto(APP);
  await page.clock.runFor(1000);
  return { page, ctx, errors };
}

function storeFromDemo(stripKinds = false) {
  const store = { 'config/types': { types: demo.types, palette: 'mirtilo' } };
  for (const [wk, blocks] of Object.entries(demo.weeks)) {
    store['weeks/' + wk] = { blocks: stripKinds ? blocks.map(({ kind, subject, ...b }) => b) : blocks };
  }
  if (stripKinds) store['config/types'].types = demo.types.map(({ blockKind, kindConfirmed, ...t }) => t);
  return store;
}

test('abre sem erros no modo local com os dados de exemplo', async () => {
  const { page, ctx, errors } = await openApp();
  assert.deepEqual(errors, []);
  assert.equal(await page.locator('.day-slots').count(), 7);
  assert.match(await page.locator('#study').textContent(), /Estudo: .*h de .*h/);
  await ctx.close();
});

test('gera meta/checkin-hoje com aula e refeição fora da lista', async () => {
  const { page, ctx, errors } = await openApp({ store: storeFromDemo() });
  const doc = await page.evaluate(() => window.__store['meta/checkin-hoje']);
  assert.deepEqual(errors, []);
  assert.equal(doc.date, '2026-11-26');
  assert.equal(doc.weekDoc, 'weeks/2026-11-23');
  const kinds = new Set([...doc.pendentes, ...doc.registrados].map((b) => b.kind));
  assert.ok(!kinds.has('aula') && !kinds.has('refeicao'), 'aula e refeição não pedem registro');
  assert.ok(doc.ignorados.some((b) => b.kind === 'aula'));
  assert.equal(doc.contagem.pendentes, doc.pendentes.length);
  for (const b of doc.pendentes) assert.match(b.start, /^\d\d:\d\d$/);
  await ctx.close();
});

test('dados antigos sem classe continuam abrindo e vão para semClasse', async () => {
  const { page, ctx, errors } = await openApp({ store: storeFromDemo(true) });
  const doc = await page.evaluate(() => window.__store['meta/checkin-hoje']);
  assert.deepEqual(errors, []);
  assert.equal(doc.pendentes.length + doc.registrados.length, 0);
  assert.ok(doc.semClasse.length > 0);
  await ctx.close();
});

test('tipo novo exige classe e é espelhado em types/<id>', async () => {
  const { page, ctx } = await openApp({ store: storeFromDemo() });
  await page.click('#btn-types');
  await page.fill('#new-type-name', 'Estudo - Física');
  await page.click('.new-type button[type=submit]');
  assert.equal(await page.locator('.type-row .nm', { hasText: 'Estudo - Física' }).count(), 0, 'sem classe não cria');
  await page.selectOption('#new-type-kind', 'estudo');
  await page.click('.new-type button[type=submit]');
  await page.clock.runFor(500);
  const mirror = await page.evaluate(() => Object.entries(window.__store).find(([k, v]) => k.startsWith('types/') && v.label === 'Estudo - Física'));
  assert.ok(mirror, 'types/<id> escrito');
  assert.equal(mirror[1].kind, 'estudo');
  assert.equal(mirror[1].loggable, true);
  await ctx.close();
});

test('salvar a semana copia a classe do tipo para os blocos, sem mexer no log', async () => {
  const store = storeFromDemo(true);
  store['config/types'] = { types: demo.types, palette: 'mirtilo' };
  const before = JSON.parse(JSON.stringify(store['weeks/2026-11-23'].blocks));
  const { page, ctx } = await openApp({ store });
  await page.evaluate(() => document.querySelector('#v-week').click());
  await page.click('.day-slots[data-day="4"] .block >> nth=1');
  await page.click('#modal-card button.accent');
  await page.clock.runFor(500);
  const after = await page.evaluate(() => window.__store['weeks/2026-11-23'].blocks);
  assert.ok(after.every((b) => b.kind), 'todos os blocos ganharam kind');
  for (const b of before) {
    const a = after.find((x) => x.id === b.id);
    assert.deepEqual(a.log, b.log);
  }
  await ctx.close();
});
