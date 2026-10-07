const { defineConfig, devices } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests',
  testMatch: 'public-ui.spec.js',
  timeout: 60000,
  retries: 0,
  reporter: [['list'], ['html', { outputFolder: '../artifacts/public-ui-report', open: 'never' }]],
  outputDir: '../artifacts/public-ui-results',
  use: {
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
  ],
});

