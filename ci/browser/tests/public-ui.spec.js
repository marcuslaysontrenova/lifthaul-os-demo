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

