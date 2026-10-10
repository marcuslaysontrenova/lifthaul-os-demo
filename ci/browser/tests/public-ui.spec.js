// LiftHaul public journey release gate: canonical catalogue, responsive layout,
// persistent single selection, and safe registration navigation.
const { test, expect } = require('@playwright/test');

const APP = process.env.APP_BASE || 'http://127.0.0.1:8080';

async function openCleanCatalogue(page) {
  await page.goto(APP + '/driver-register.html?review=release-gate');
  await page.evaluate(() => sessionStorage.clear());
  await page.reload();
  await page.locator('.catalog-card').first().waitFor();
}

test('canonical vehicle catalogue is complete, distinct, and defaults to All Vehicles', async ({ page }) => {
  await openCleanCatalogue(page);
  await expect(page.getByRole('tab', { name: 'All Vehicles' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.catalog-card')).toHaveCount(34);

  const images = await page.locator('.catalog-card img').evaluateAll(nodes => nodes.map(node => node.getAttribute('src')));
  expect(new Set(images).size, 'every active vehicle has its own image asset').toBe(34);

  const codes = await page.locator('.catalog-card').evaluateAll(nodes => nodes.map(node => node.dataset.vehicleCode));
  expect(new Set(codes).size, 'vehicle codes are unique').toBe(34);
  await expect(page.locator('a[href="#"]')).toHaveCount(0);
});

test('one canonical vehicle selection persists without hiding other categories', async ({ page }) => {
  await openCleanCatalogue(page);
  const miniVan = page.locator('[data-vehicle-code="mini_van"]');
  await miniVan.getByRole('button', { name: 'Select This Vehicle' }).click();
  await expect(miniVan).toHaveClass(/selected/);
  await expect(page.locator('.catalog-card.selected')).toHaveCount(1);

  await page.reload();
  await page.locator('.catalog-card').first().waitFor();
  await expect(page.getByRole('tab', { name: 'All Vehicles' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.catalog-card')).toHaveCount(34);
  await expect(page.locator('.catalog-card.selected')).toHaveCount(1);
  await expect(page.locator('[data-vehicle-code="mini_van"]')).toHaveClass(/selected/);
});

for (const layout of [
  { name: 'desktop', width: 1440, height: 900, expectedSameRow: 4 },
  { name: 'tablet', width: 768, height: 1024, expectedSameRow: 2 },
  { name: 'mobile', width: 390, height: 844, expectedSameRow: 1 },
]) {
  test(`${layout.name} catalogue layout has the required column count and no horizontal overflow`, async ({ page }) => {
    await page.setViewportSize({ width: layout.width, height: layout.height });
    await openCleanCatalogue(page);
    const result = await page.locator('.vehicle-catalog').first().evaluate((grid, expectedSameRow) => {
      const cards = [...grid.querySelectorAll('.catalog-card')].slice(0, expectedSameRow + 1);
      const top = cards.map(card => Math.round(card.getBoundingClientRect().top));
      return {
        sameRow: top.slice(0, expectedSameRow).every(value => value === top[0]),
        nextRow: top[expectedSameRow] !== top[0],
        overflow: document.documentElement.scrollWidth - window.innerWidth,
      };
    }, layout.expectedSameRow);
    expect(result.sameRow).toBe(true);
    expect(result.nextRow).toBe(true);
    expect(result.overflow).toBeLessThanOrEqual(0);
  });
}

test('vehicle owner route carries the canonical code and provides Home and Back exits', async ({ page }) => {
  await openCleanCatalogue(page);
  await page.locator('[data-vehicle-code="mini_van"] .owner-route').click();
  await expect(page).toHaveURL(/provider\.html\?.*vehicle=mini_van/);
  await expect(page.getByRole('heading', { name: 'Mini Van selected' })).toBeVisible();
  await expect(page.locator('a[href*="index.html"]').first()).toBeVisible();
  await expect(page.getByText(/Back/).first()).toBeVisible();
  await expect(page.locator('a[href="#"]')).toHaveCount(0);
});

test('public journey uses one complete top navigation with a visible fare calculator', async ({ page }) => {
  for (const entry of [
    ['/index.html', 'Home'],
    ['/book.html', 'Services'],
    ['/fare-calculator.html', 'Fare Calculator'],
    ['/track.html', 'Track Booking'],
    ['/driver-register.html', 'Partner With Us'],
    ['/provider.html?track=LIGHT', 'Partner With Us'],
  ]) {
    await page.goto(APP + entry[0]);
    const nav = page.getByRole('navigation', { name: 'Primary navigation' });
    await expect(nav).toBeVisible();
    await expect(nav.getByRole('link', { name: 'Home', exact: true })).toHaveAttribute('href', 'index.html');
    await expect(nav.getByRole('link', { name: 'Fare Calculator' })).toHaveAttribute('href', 'fare-calculator.html');
    await expect(nav.getByRole('link', { name: entry[1], exact: true })).toHaveAttribute('aria-current', 'page');
    // Leaflet renders its third-party zoom controls as anchors with href="#". The application
    // itself must not introduce placeholder links.
    await expect(page.locator('a[href="#"]:not(.leaflet-control-zoom-in):not(.leaflet-control-zoom-out)')).toHaveCount(0);
  }
});

test('public navigation changes once at the governed breakpoint without an intermediate overlay', async ({ page }) => {
  for (const state of [
    { width: 1300, menu: 'flex', toggle: 'none', position: 'static' },
    { width: 1280, menu: 'flex', toggle: 'none', position: 'static' },
    { width: 1201, menu: 'flex', toggle: 'none', position: 'static' },
    { width: 1200, menu: 'none', toggle: 'grid', position: 'absolute' },
    { width: 1120, menu: 'none', toggle: 'grid', position: 'absolute' },
    { width: 390, menu: 'none', toggle: 'grid', position: 'absolute' },
  ]) {
    await page.setViewportSize({ width: state.width, height: 844 });
    await page.goto(APP + '/index.html?review=navigation-breakpoint');
    const result = await page.locator('.lh-public-nav').evaluate(nav => {
      const menu = nav.querySelector('.links');
      const toggle = nav.querySelector('.navtoggle');
      return {
        menu: getComputedStyle(menu).display,
        position: getComputedStyle(menu).position,
        toggle: getComputedStyle(toggle).display,
        overflow: document.documentElement.scrollWidth > window.innerWidth,
      };
    });
    expect(result).toEqual({
      menu: state.menu,
      position: state.position,
      toggle: state.toggle,
      overflow: false,
    });
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(APP + '/index.html?review=navigation-keyboard');
  const toggle = page.locator('.lh-public-nav .navtoggle');
  await expect(toggle).toHaveAttribute('aria-label', 'Open navigation');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(toggle).toHaveAttribute('aria-label', 'Close navigation');
  await expect(page.locator('.lh-public-nav .links')).toHaveCSS('display', 'grid');
  await page.keyboard.press('Escape');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(toggle).toBeFocused();
});

test('fare calculator shows recommendation, versioned breakdown and no payment action', async ({ page }) => {
  await page.route('**/public/bookings/estimate', async route => {
    const request = route.request();
    const payload = request.postDataJSON();
    expect(payload.weight_kg).toBe(50);
    expect(payload.additional_stops).toBe(2);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data: {
        estimate: 800.80,
        estimate_status: 'QUOTED_INDICATIVE',
        estimate_generated_at: '2026-10-09T00:00:00+00:00',
        selected_vehicle: { id: 'sedan', name: 'Sedan', reason: 'Best-fit verified category.' },
        quote_breakdown: {
          amount: 800.80, transport_subtotal: 650, administration_fee: 65, tax_amount: 85.80,
          rate_version: 'LH-PH-2026-10-09-BENCHMARK-01', rate_effective_date: '2026-10-09',
          components: [
            { code: 'base_transport', label: 'Base transportation charge', amount: 120 },
            { code: 'distance', label: 'Distance charge', amount: 140 },
            { code: 'additional_stops', label: 'Additional stops', amount: 90 },
            { code: 'waiting_time', label: 'Chargeable waiting time', amount: 100 },
            { code: 'helpers', label: 'Loading / unloading helpers', amount: 200 },
          ],
          excluded_charges: ['Expressway tolls', 'Parking'],
        },
      } }),
    });
  });
  await page.goto(APP + '/fare-calculator.html');
  await page.selectOption('#originIsland', 'Luzon');
  await page.selectOption('#destinationIsland', 'Luzon');
  await page.fill('#route', 'Makati City to Quezon City');
  await page.fill('#distance', '10');
  await page.fill('#weight', '50');
  await page.fill('#length', '50');
  await page.fill('#width', '40');
  await page.fill('#height', '30');
  await page.fill('#stops', '2');
  await page.fill('#waiting', '60');
  await page.fill('#helpers', '1');
  await page.getByRole('button', { name: /Recommend Vehicle/ }).click();
  await expect(page.getByText('Sedan', { exact: true })).toBeVisible();
  await expect(page.getByText('₱800.80')).toBeVisible();
  await expect(page.getByText(/LH-PH-2026-10-09-BENCHMARK-01/)).toBeVisible();
  await expect(page.getByText('LiftHaul administration fee (10%)')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Continue to Booking' })).toHaveAttribute('href', 'book.html');
  await expect(page.getByRole('button', { name: /Pay|Checkout|Confirm payment/i })).toHaveCount(0);
});
