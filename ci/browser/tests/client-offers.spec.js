const { test, expect } = require('@playwright/test');
const APP = process.env.APP_BASE || 'http://127.0.0.1:8787';

const offer = {
  id: 701,
  booking_id: 1046,
  provider_name: 'Verified Fleet A',
  vehicle_category: '10W_WING_VAN',
  vehicle_body_type: 'Wing van',
  amount: 38500,
  currency: 'PHP',
  status: 'VALID',
  submitted_at: '2026-10-13T10:00:00Z',
  valid_until: '2099-10-13T12:00:00Z',
  est_pickup: '07:00',
  est_delivery: '15:00',
  inclusions: 'Driver and fuel',
  exclusions: 'Tolls and parking',
  toll_treatment: 'Direct by booker',
  ferry_treatment: 'Not applicable',
  waiting_rate: 650,
  notes: '<script>safe provider note</script>',
  selection_request_status: null,
};

async function openOffers(page) {
  await page.route('**/config.js', route => route.fulfill({
    contentType: 'application/javascript', body: 'window.RGO_CONFIG={apiBase:location.origin};',
  }));
  await page.addInitScript(() => sessionStorage.setItem('lifthaul_api_token', 'synthetic-ui-test'));
  await page.route('**/portal/client/**', route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/portal/client/offers') {
      return route.fulfill({ json: { data: { offers: [offer] } } });
    }
    if (path === '/portal/client/offers/701/request-selection') {
      return route.fulfill({ json: { data: { request_id: 1, status: 'PENDING_REVIEW' } } });
    }
    return route.fulfill({ json: { data: { summary: {}, recent_bookings: [], action_required: [] } } });
  });
  await page.goto(APP + '/client.html');
  await page.locator('#nav').getByRole('button', { name: 'Offers & comparison', exact: true }).click();
}

for (const width of [390, 768, 1440]) {
  test(`customer can compare eligible offers safely at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await openOffers(page);
    await expect(page.getByRole('heading', { name: 'Offers & comparison' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Verified Fleet A' })).toBeVisible();
    await expect(page.getByText('₱38,500.00')).toBeVisible();
    await expect(page.getByText('10W_WING_VAN · Wing van')).toBeVisible();
    await expect(page.getByText('<script>safe provider note</script>', { exact: false })).toBeVisible();
    expect(await page.locator('script:has-text("safe provider note")').count()).toBe(0);
    await expect(page.getByText('No automatic cheapest-offer selection.', { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test('selection request uses governed endpoint and does not select directly', async ({ page }) => {
  await openOffers(page);
  const requestPromise = page.waitForRequest(req =>
    req.method() === 'POST' && new URL(req.url()).pathname === '/portal/client/offers/701/request-selection');
  await page.getByRole('button', { name: 'Request managed selection' }).click();
  const request = await requestPromise;
  expect(request.postDataJSON()).toEqual({});
  await expect(page.getByText('Selection request sent for governed review')).toBeVisible();
});
