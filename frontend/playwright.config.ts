import { defineConfig } from '@playwright/test';

const setupPort = 18888;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: 'http://127.0.0.1:4174',
    trace: 'on-first-retry',
  },
  webServer: [
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 4174',
      url: 'http://127.0.0.1:4174',
      timeout: 30_000,
      reuseExistingServer: !process.env.CI,
    },
    {
      command:
        `uv run --project .. python -m dduo_solo_founder.cli_bridge --host 127.0.0.1 --port ${setupPort} --token browser-test-token`,
      url: `http://127.0.0.1:${setupPort}/health`,
      timeout: 30_000,
      reuseExistingServer: false,
    },
  ],
});
