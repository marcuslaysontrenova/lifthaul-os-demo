#!/usr/bin/env node
'use strict';
// Operator-private Playwright verification. JSON credentials arrive on stdin
// from the approved interactive Python runner, never as arguments or files.
// No screenshots, traces, video, storage-state exports or response bodies.
const { chromium } = require('@playwright/test');
const BASE = 'https://www.lifthaul.com.ph';
const UI = {
  booker: '/client.html',
  accredited_driver: '/driver.html',
  motorcycle_owner: '/portal.html',
  light_vehicle_owner: '/portal.html',
  truck_owner: '/portal.html',
  fleet_owner: '/portal.html',
  specialized_provider: '/portal.html',
  operations: '/console.html',
  finance: '/console.html',
  administrator: '/admin-console.html'
};
async function readPrivateInput() {
  let raw = '';
  for await (const chunk of process.stdin) {
    raw += chunk;
    if (raw.length > 50000) throw new Error('INVALID_PRIVATE_INPUT');
  }
  return JSON.parse(raw);
}
async function run() {
  if (process.argv[2] !== '--execute') {
    process.stdout.write(JSON.stringify({result:'BLOCKED',reason:'SAFE_MODE'})+'\n');
    return 2;
  }
  let payload;
  try { payload=await readPrivateInput(); } catch { return 2; }
  const sha=payload.expected_sha, manifest=payload.manifest;
  if (!/^[a-f0-9]{40}$/.test(sha||'') ||
      !payload.approval_reference || !manifest ||
      Object.keys(manifest).sort().join('|') !== Object.keys({...UI,driver_applicant:null}).sort().join('|')) return 2;
  let browser, context;
  const checks={};
  try {
    browser=await chromium.launch({headless:true});
    context=await browser.newContext({baseURL:BASE,acceptDownloads:false,
                                    recordVideo:undefined,serviceWorkers:'block'});
    const api=context.request;
    const before=await api.get('/readyz');
    const identity=await before.json();
    if (before.status()!==200 || identity.release_sha!==sha || !identity.deployment_id)
      throw new Error('BLOCKED_RELEASE_IDENTITY_MISMATCH');
    for (const persona of Object.keys(UI)) {
      let token;
      let page;
      try {
        const login=await api.post('/login',{data:{email:manifest[persona].email,
                                                  password:manifest[persona].password}});
        if (login.status()!==200) throw new Error('AUTH_FAILED');
        const response=await login.json();
        token=response.token || response.data?.token;
        if (!token) throw new Error('AUTH_FAILED');
        page=await context.newPage();
        await page.addInitScript(t => sessionStorage.setItem('lifthaul_api_token',t),token);
        const responsePage=await page.goto(BASE+UI[persona],{waitUntil:'domcontentloaded'});
        if (!responsePage || !responsePage.ok()) throw new Error('LANDING_UNAVAILABLE');
        // Visible HTML with an authenticated app-specific workspace is required;
        // generic public shell alone cannot pass this check.
        const hasWorkspace=await page.locator('main, [role="main"], #app, #nav').first().isVisible();
        if (!hasWorkspace) throw new Error('AUTHENTICATED_WORKSPACE_MISSING');
        checks[persona]='BLOCKED_ROLE_NAVIGATION_NOT_YET_ASSERTED';
      } catch {
        checks[persona]='FAIL';
      } finally {
        if (token) {
          try { await api.post('/logout',{headers:{Authorization:'Bearer '+token},data:{}}); }
          catch { checks[persona]='FAIL_CLEANUP'; }
        }
        if (page) await page.close().catch(()=>{checks[persona]='FAIL_CLEANUP';});
      }
    }
    const after=await api.get('/readyz');
    const final=await after.json();
    if (final.release_sha!==sha || final.deployment_id!==identity.deployment_id)
      throw new Error('BLOCKED_RELEASE_IDENTITY_CHANGED');
    process.stdout.write(JSON.stringify({result:'BLOCKED',release_sha:sha,
      deployment_id:identity.deployment_id,technical_checks:checks,
      browser_navigation:'BLOCKED_MEANINGFUL_ROLE_NAVIGATION',
      human_signoff:'PENDING'})+'\n');
    return 1;
  } catch {
    process.stdout.write(JSON.stringify({result:'BLOCKED',reason:'BROWSER_EXECUTION_OR_IDENTITY_FAILED'})+'\n');
    return 2;
  } finally {
    if (context) await context.close().catch(()=>{});
    if (browser) await browser.close().catch(()=>{});
    payload=null;
  }
}
run().then(code=>{process.exitCode=code}).catch(()=>{process.exitCode=2});
