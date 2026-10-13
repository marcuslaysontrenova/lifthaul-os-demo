const { test, expect } = require('@playwright/test');
const APP = process.env.APP_BASE || 'http://127.0.0.1:8787';

const payment = {
  protected_payment_id: 81,
  transaction_no: 'TEST-PP-81',
  booking_id: 42,
  state: 'DELIVERY_EVIDENCE_PENDING',
  status: 'DELIVERY_EVIDENCE_PENDING',
  contract_value: 12000,
  funded_amount: 12000,
  protected_amount: 12000,
  released_amount: 0,
  refunded_amount: 0,
  fee_breakdown: { platform_fee: 1200, processing_fee: 0, tax: 0 },
  provider: 'Synthetic provider',
  provider_transaction_id: 'SYNTHETIC-81',
  payment_method: 'Synthetic channel',
  created_at: '2026-10-13T10:00:00Z',
  last_status_update: '2026-10-13T11:00:00Z',
  pickup_location: 'Makati',
  delivery_location: 'Pasig',
  service_provider: 'Synthetic carrier',
  release_conditions: 'No release without verified evidence and an enabled customer workflow.',
  dispute_window_expires_at: '2026-10-15T11:00:00Z',
  timeline: [],
  what_happened: 'Synthetic evidence awaits review.',
  next_actor: 'Client booker',
  next_action: 'Use the approved assisted process.',
  verification_reminder: 'SYNTHETIC ONLY — no money moved.',
};

test('unimplemented payment actions are honestly disabled and cannot mutate state', async ({ page }) => {
  await page.route('**/config.js', route => route.fulfill({
    contentType: 'application/javascript', body: 'window.RGO_CONFIG={apiBase:location.origin};',
  }));
  await page.addInitScript(() => sessionStorage.setItem('lifthaul_api_token', 'synthetic-ui-test'));
  const writes = [];
  await page.route('**/portal/client/**', route => {
    const request = route.request();
    if (request.method() !== 'GET') writes.push(request.url());
    const path = new URL(request.url()).pathname;
    const data = path === '/portal/client/payments/81' ? payment : path === '/portal/client/payments'
      ? { payments: [payment] } : { summary: {}, recent_bookings: [], action_required: [] };
    return route.fulfill({ json: { data } });
  });
  await page.goto(APP + '/client.html');
  await page.locator('#nav').getByRole('button', { name: 'Protected payments', exact: true }).click();
  await page.getByRole('button', { name: 'View details' }).click();
  await expect(page.getByRole('button', { name: 'Receipt download unavailable' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Delivery confirmation unavailable' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Problem reporting unavailable' })).toBeDisabled();
  await expect(page.getByText('Online receipt download, delivery confirmation, and dispute submission are not enabled in this controlled MVP.')).toBeVisible();
  expect(writes).toEqual([]);
});
