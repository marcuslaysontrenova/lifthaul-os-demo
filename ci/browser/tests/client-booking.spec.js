const { test, expect } = require('@playwright/test');
const APP = process.env.APP_BASE || 'http://127.0.0.1:8787';

const booking = {
  id: 42, booking_reference: 'LH-000042', status: 'ASSIGNED', status_bucket: 'ACTIVE',
  pickup_location: 'Makati warehouse', delivery_location: 'Pasig warehouse',
  cargo_type: '<script>private cargo</script>', service_provider: 'Test carrier',
  assigned_truck: '6W · TEST-42', trip_id: 7, trip_status: 'IN_TRANSIT',
  progress_pct: 42, eta: '2026-10-13T12:00:00Z', last_ping_at: '2026-10-13T10:00:00Z',
  tracking_label: 'MOCK_ONLY', available_action: 'Track Booking',
  trip_events: [{ to_status: 'IN_TRANSIT', occurred_at: '2026-10-13T10:00:00Z' }],
};

async function openWorkspace(page, response = booking) {
  await page.route('**/config.js', route => route.fulfill({
    contentType: 'application/javascript', body: 'window.RGO_CONFIG={apiBase:location.origin};',
  }));
  await page.addInitScript(() => sessionStorage.setItem('lifthaul_api_token', 'synthetic-ui-test'));
  await page.route('**/portal/client/**', route => {
    const path = new URL(route.request().url()).pathname;
    const data = path.endsWith('/bookings/42') ? response : path.endsWith('/bookings')
      ? { bookings: [booking] } : { summary: {}, recent_bookings: [], action_required: [] };
    return route.fulfill({ json: { data }, headers: { 'Access-Control-Allow-Origin': '*' } });
  });
  await page.goto(APP + '/client.html');
  await page.locator('#nav').getByRole('button', { name: 'Bookings', exact: true }).click();
  await page.getByRole('button', { name: 'Track booking', exact: true }).click();
}

for (const width of [390, 768, 1440]) {
  test(`customer booking detail and refresh work at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await openWorkspace(page);
    await expect(page.getByRole('heading', { name: 'LH-000042', exact: true })).toBeVisible();
    await expect(page.getByText('MOCK_ONLY', { exact: true })).toBeVisible();
    await expect(page.getByText('42%', { exact: true })).toBeVisible();
    await expect(page.getByLabel('Recorded trip milestones')).toContainText('IN TRANSIT');
    await expect(page.getByText('<script>private cargo</script>', { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.route('**/portal/client/bookings/42', route => route.fulfill({
      json: { data: { ...booking, progress_pct: 100, trip_status: 'ARRIVED_DESTINATION' } },
      headers: { 'Access-Control-Allow-Origin': '*' },
    }));
    await page.getByRole('button', { name: 'Refresh status' }).click();
    await expect(page.getByText('100%', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Back to bookings' }).click();
    await expect(page.getByRole('button', { name: 'Track booking', exact: true })).toBeVisible();
  });
}

test('missing booking displays an error and a working return to bookings', async ({ page }) => {
  await openWorkspace(page);
  await page.route('**/portal/client/bookings/42', route => route.fulfill({
    status: 404, json: { error: 'booking not found' }, headers: { 'Access-Control-Allow-Origin': '*' },
  }));
  await page.getByRole('button', { name: 'Refresh status' }).click();
  await expect(page.getByRole('alert')).toHaveText('booking not found');
  await page.getByRole('button', { name: 'Back to bookings' }).click();
  await expect(page.getByRole('button', { name: 'Track booking', exact: true })).toBeVisible();
});

test('slow booking response cannot overwrite a different workspace tab', async ({ page }) => {
  await openWorkspace(page);
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  await page.route('**/portal/client/bookings/42', async route => {
    await pending;
    await route.fulfill({ json: { data: booking } });
  });
  const requested = page.waitForRequest('**/portal/client/bookings/42');
  await page.getByRole('button', { name: 'Refresh status' }).click();
  await requested;
  await page.locator('#nav').getByRole('button', { name: 'Dashboard', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();
  const response = page.waitForResponse('**/portal/client/bookings/42');
  finish();
  await response;
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();
});
