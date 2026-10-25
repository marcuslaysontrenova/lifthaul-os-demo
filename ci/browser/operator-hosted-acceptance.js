#!/usr/bin/env node
'use strict';
// Private operator-local Playwright browser acceptance. Input is an in-memory
// JSON pipe from the approved interactive Python runner; never argv/files.
// No screenshots, video, HAR, traces, raw responses or storage-state exports.
const { chromium } = require('@playwright/test');
const BASE = 'https://www.lifthaul.com.ph';
const PERSONAS = [
  'booker', 'driver_applicant', 'accredited_driver', 'motorcycle_owner',
  'light_vehicle_owner', 'truck_owner', 'fleet_owner', 'specialized_provider',
  'operations', 'finance', 'administrator'
];
const CARRIER = new Set(['motorcycle_owner','light_vehicle_owner','truck_owner',
                         'fleet_owner','specialized_provider']);
const FIELDS = {
  booker:'client.html',driver_applicant:'driver.html',accredited_driver:'driver.html',
  motorcycle_owner:'portal.html',light_vehicle_owner:'portal.html',
  truck_owner:'portal.html',fleet_owner:'portal.html',
  specialized_provider:'portal.html',operations:'console.html',
  finance:'console.html',administrator:'admin-console.html'
};
const allowedWrites = new Set(['/login','/logout']);
async function readInput() {
  let value='';
  for await (const chunk of process.stdin) {
    value+=chunk;
    if (value.length>32000) throw new Error('PRIVATE_INPUT_TOO_LARGE');
  }
  return JSON.parse(value);
}
async function checkPage(page, persona, row) {
  if (persona==='booker') {
    const offers=page.locator('#nav').getByRole('button',{name:'Offers & comparison',exact:true});
    await offers.waitFor({state:'visible',timeout:12000});
    await offers.click();
    await page.getByRole('heading',{name:'Offers & comparison'}).waitFor({state:'visible',timeout:12000});
    return;
  }
  if (CARRIER.has(persona)) {
    await page.locator('#email').fill(row.email);
    await page.locator('#password').fill(row.password);
    await page.evaluate(() => doLogin());
    await page.locator('#appView').waitFor({state:'visible',timeout:15000});
    const fleet=page.locator('#tabs').getByRole('button',{name:'Fleet',exact:true});
    await fleet.waitFor({state:'visible',timeout:10000});
    await fleet.click();
    await page.locator('#body .panel').first().waitFor({state:'visible',timeout:10000});
    return;
  }
  if (persona==='driver_applicant' || persona==='accredited_driver') {
    await page.locator('#email').fill(row.email);
    await page.locator('#password').fill(row.password);
    await page.evaluate(() => doLogin());
    if (persona==='driver_applicant') {
      await page.locator('#msg').getByText('Sign-in failed:',{exact:false}).waitFor({timeout:12000});
      if (await page.locator('#list').isVisible()) throw new Error('PENDING_LOGIN_BYPASS');
    } else {
      await page.locator('#list').waitFor({state:'visible',timeout:15000});
    }
    return;
  }
  if (persona==='operations' || persona==='finance') {
    await page.locator('#u').fill(row.email);
    await page.locator('#p').fill(row.password);
    await page.locator('#loginForm').getByRole('button',{name:'Sign in',exact:false}).click();
    const mod=persona==='finance'?'finance':'bookings';
    const button=page.locator('button.nav[data-mod="'+mod+'"]');
    await button.waitFor({state:'visible',timeout:15000});
    await button.click();
    return;
  }
  if (persona==='administrator') {
    await page.locator('#api').fill(BASE);
    await page.locator('#email').fill(row.email);
    await page.locator('#pw').fill(row.password);
    if (row.mfa) await page.locator('#mfa').fill(row.mfa);
    await page.evaluate(() => doLogin());
    await page.locator('#app').waitFor({state:'visible',timeout:15000});
    await page.locator('#menu').waitFor({state:'visible',timeout:10000});
    if (!(await page.locator('#menu').innerText()).trim()) throw new Error('ADMIN_MENU_EMPTY');
    return;
  }
  throw new Error('UNSUPPORTED_PERSONA');
}
async function verifyForbiddenAdminUrl(page, persona) {
  if (persona==='administrator' || persona==='operations' || persona==='driver_applicant')
    return;
  const status=await page.evaluate(async ({driver}) => {
    const token=sessionStorage.getItem(driver?'lifthaul_driver_token':'lifthaul_api_token');
    if (!token) return 0;
    try {
      const response=await fetch('/admin/users',{
        method:'GET',headers:{'Authorization':'Bearer '+token,'Accept':'application/json'}});
      return response.status;
    } catch {return 0}
  },{driver:persona==='accredited_driver'});
  if (status!==401 && status!==403) throw new Error('UNAUTHORIZED_DIRECT_API_URL_NOT_DENIED');
}
async function revokePageSession(page, persona) {
  return await page.evaluate(async ({driver}) => {
    const token=sessionStorage.getItem(driver?'lifthaul_driver_token':'lifthaul_api_token');
    if (!token) return true; // denied pending-applicant login
    try {
      const headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'};
      const out=await fetch('/logout',{method:'POST',headers,body:'{}'});
      const stale=await fetch('/me/permissions',{method:'GET',headers});
      return (out.status===200 || out.status===204) && (stale.status===401 || stale.status===403);
    } catch { return false; }
  },{driver:persona==='accredited_driver'||persona==='driver_applicant'});
}
async function main() {
  if (process.argv[2]!=='--execute') {
    process.stdout.write(JSON.stringify({result:'BLOCKED',reason:'SAFE_MODE'})+'\n');
    return 2;
  }
  let input;
  try {input=await readInput()} catch {return 2}
  const sha=input.expected_sha, rows=input.manifest;
  if (!/^[a-f0-9]{40}$/.test(sha||'') || !input.approval_reference ||
      !rows || Object.keys(rows).sort().join('|')!==PERSONAS.slice().sort().join('|') ||
      !PERSONAS.every(k=>rows[k] && typeof rows[k].email==='string'
                     && typeof rows[k].password==='string')) return 2;
  const checks={};
  let browser, baseline, finalIdentity;
  try {
    browser=await chromium.launch({headless:true});
    const pre=await browser.newContext({baseURL:BASE,acceptDownloads:false,serviceWorkers:'block'});
    try {
      const ready=await pre.request.get('/readyz');
      baseline=await ready.json();
      if (ready.status()!==200 || baseline.status!=='ready' ||
          baseline.release_sha!==sha || !baseline.deployment_id)
        throw new Error('RELEASE_IDENTITY_MISMATCH');
    } finally {await pre.close()}
    for (const persona of PERSONAS) {
      let context, page, apiToken;
      let result='BLOCKED';
      try {
        context=await browser.newContext({baseURL:BASE,acceptDownloads:false,serviceWorkers:'block'});
        const api=context.request;
        // UI cannot send financial, dispatch or data mutation requests.
        page=await context.newPage();
        await page.route('**/*', route => {
          const method=route.request().method();
          const url=new URL(route.request().url());
          if (url.origin!==BASE || (method!=='GET' && method!=='HEAD' && method!=='OPTIONS' &&
              !(method==='POST' && allowedWrites.has(url.pathname))))
            return route.abort();
          return route.continue();
        });
        if (persona==='booker') {
          const login=await api.post('/login',{data:{email:rows[persona].email,password:rows[persona].password}});
          if (login.status()!==200) throw new Error('BOOKER_LOGIN_FAILED');
          const auth=await login.json();
          apiToken=auth.token||auth.data?.token;
          if (!apiToken) throw new Error('BOOKER_TOKEN_ABSENT');
          await page.addInitScript(t => sessionStorage.setItem('lifthaul_api_token',t),apiToken);
        }
        const landing=await page.goto(BASE+'/'+FIELDS[persona],{waitUntil:'domcontentloaded'});
        if (!landing || !landing.ok()) throw new Error('LANDING_UNAVAILABLE');
        await checkPage(page,persona,rows[persona]);
        await verifyForbiddenAdminUrl(page,persona);
        result='PASS';
      } catch {
        result='FAIL';
      } finally {
        if (page) {
          try {if (!(await revokePageSession(page,persona))) result='FAIL_SESSION_CLEANUP'}
          catch {result='FAIL_SESSION_CLEANUP'}
        }
        if (apiToken && context) {
          try {
            const logout=await context.request.post('/logout',{headers:{Authorization:'Bearer '+apiToken},data:{}});
            const stale=await context.request.get('/me/permissions',{headers:{Authorization:'Bearer '+apiToken}});
            if (![200,204].includes(logout.status()) || ![401,403].includes(stale.status()))
              result='FAIL_SESSION_CLEANUP';
          } catch {result='FAIL_SESSION_CLEANUP'}
        }
        if (context) await context.close().catch(()=>{result='FAIL_SESSION_CLEANUP'});
      }
      checks[persona]=result;
    }
    const post=await browser.newContext({baseURL:BASE,serviceWorkers:'block'});
    try {
      const ready=await post.request.get('/readyz');
      finalIdentity=await ready.json();
      if (ready.status()!==200 || finalIdentity.release_sha!==sha ||
          finalIdentity.deployment_id!==baseline.deployment_id)
        throw new Error('RELEASE_IDENTITY_CHANGED');
    } finally {await post.close()}
    process.stdout.write(JSON.stringify({
      schema:'lifthaul.browser-operator-local.v1',
      result:PERSONAS.every(p=>checks[p]==='PASS')?'PASS':'FAIL',
      release_sha:sha,deployment_id:baseline.deployment_id,
      technical_checks:checks,independent_browser_qa_signoff:'PENDING'
    })+'\n');
    return PERSONAS.every(p=>checks[p]==='PASS')?0:1;
  } catch {
    process.stdout.write(JSON.stringify({result:'BLOCKED',
      reason:'BROWSER_PREREQUISITE_OR_RELEASE_IDENTITY_FAILED'})+'\n');
    return 2;
  } finally {
    if (browser) await browser.close().catch(()=>{});
    input=null;
  }
}
main().then(code=>{process.exitCode=code}).catch(()=>{process.exitCode=2});
