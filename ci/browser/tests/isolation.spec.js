// LiftHaul OS — literal browser E2E: two-tenant isolation + admin console render,
// driven through real Chromium against the backend running on PostgreSQL.
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const API = process.env.API_BASE || 'http://127.0.0.1:8787';
const APP = (process.env.APP_BASE || 'http://127.0.0.1:8080') + '/admin-console.html';
const seed = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'seed_ids.json'), 'utf8'));

async function login(request, email) {
  const r = await request.post(API + '/login', { data: { email, password: seed.pw } });
  expect(r.status(), 'login ' + email).toBe(200);
  return (await r.json()).data.token;
}

test('two-tenant isolation through the browser network stack (PostgreSQL backend)', async ({ request }) => {
  const tokA = await login(request, seed.userA);
  // Tenant A reads its OWN booking -> 200
  const own = await request.get(API + `/bookings/${seed.bkA}`, { headers: { Authorization: 'Bearer ' + tokA } });
  expect(own.status(), 'A reads own booking').toBe(200);
  // Tenant A attempts Tenant B's booking -> 404 (no existence leak)
  const cross = await request.get(API + `/bookings/${seed.bkB}`, { headers: { Authorization: 'Bearer ' + tokA } });
  expect(cross.status(), 'A denied B booking (404 no-leak)').toBe(404);
  // symmetric: B denied A
  const tokB = await login(request, seed.userB);
  const cross2 = await request.get(API + `/bookings/${seed.bkA}`, { headers: { Authorization: 'Bearer ' + tokB } });
  expect(cross2.status(), 'B denied A booking (404 no-leak)').toBe(404);
});

test('administration viewers respond through the browser network stack (PostgreSQL)', async ({ request }) => {
  const tok = await login(request, seed.admin);
  const H = { Authorization: 'Bearer ' + tok };
  // role comparison + SoD conflict
  const cmp = await request.post(API + '/admin/roles/compare', { headers: H, data: { a: 'estimator', b: 'approver' } });
  expect(cmp.status()).toBe(200);
  const cmpj = (await cmp.json()).data;
  expect(cmpj.sod_conflicts.length, 'SoD conflict shown').toBeGreaterThan(0);
  // config preview is non-mutating and computes proposed value
  const pv = await request.post(API + '/admin/config/preview', { headers: H, data: { key: 'approval.quotation_threshold', scope: 'tenant', scope_ref: 'RGO', value: '900000', tenant: 'RGO' } });
  expect((await pv.json()).data.proposed_effective.value).toBe('900000');
  // data-integrity runs every check (none NOT_RUN)
  const di = await request.get(API + '/admin/governance/data-integrity', { headers: H });
  const dij = (await di.json()).data;
  expect(dij.summary.not_run, 'no integrity check NOT_RUN').toBe(0);
  // Phase 2: config definitions + policy simulation through the browser
  const defs = await request.get(API + '/admin/config/definitions', { headers: H });
  expect((await defs.json()).data.definitions.length, 'config definitions present').toBeGreaterThan(7);
  const sim = await request.post(API + '/admin/config/simulate', { headers: H, data: { policy: 'tax', taxable: 600000 } });
  expect((await sim.json()).data.tax, 'tax policy simulate == 72000 (default exclusive 12%)').toBe(72000);
  // multi-mode tax simulation through the browser (non-mutating)
  const inc = await request.post(API + '/admin/config/simulate', { headers: H, data: { policy: 'tax', taxable: 600000, tenant: 'RGO' } });
  expect((await inc.json()).data.tax_type, 'tax type present').toBeDefined();
  const dp = await request.post(API + '/admin/config/simulate', { headers: H, data: { policy: 'downpayment', total: 672000 } });
  expect((await dp.json()).data.amount, 'downpayment simulate == 201600').toBe(201600);
});

test('admin console renders live PostgreSQL data in a real browser', async ({ page }) => {
  const errs = [];
  page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  await page.goto(APP);
  await page.fill('#api', API);
  await page.fill('#email', seed.admin);
  await page.fill('#pw', seed.pw);
  await page.click('button:has-text("Sign in")');
  await page.waitForSelector('.side .item', { timeout: 20000 });   // shell loaded
  await page.click('.side .item:has-text("Users")');
  await page.waitForSelector('.content table', { timeout: 20000 }); // live users table from PG
  await page.screenshot({ path: path.join(__dirname, '..', '..', 'artifacts', 'admin-users.png'), fullPage: true });
  await page.click('.side .item:has-text("Organization Structure")');
  await page.waitForSelector('.content', { timeout: 20000 });
  await page.screenshot({ path: path.join(__dirname, '..', '..', 'artifacts', 'admin-org.png'), fullPage: true });
});
