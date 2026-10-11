const { defineConfig, devices } = require('@playwright/test');
const path = require('path');
const appBase = process.env.APP_BASE || 'http://127.0.0.1:8787';
const repoRoot = path.resolve(__dirname, '../..');

module.exports = defineConfig({
  testDir: './tests',
  testMatch: 'public-ui.spec.js',
  timeout: 60000,
  retries: 0,
  reporter: [['list'], ['html', { outputFolder: '../artifacts/public-ui-report', open: 'never' }]],
  outputDir: '../artifacts/public-ui-results',
  webServer: {
    command: appBase.includes(':8787')
      ? 'python ../../backend/server.py'
      : 'python -m http.server 8080 --directory ../..',
    url: appBase + '/index.html',
    reuseExistingServer: true,
    timeout: 30000,
    env: { ...process.env, APP_ENV: 'test', PUBLIC_ROOT: repoRoot },
  },
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

