const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests',
  timeout: 60000,
  retries: 0,
  reporter: [['list'], ['html', { outputFolder: '../artifacts/playwright-report', open: 'never' }]],
  outputDir: '../artifacts/test-results',
  use: {
    screenshot: 'on',
    trace: 'on',
    video: 'on',
  },
});
