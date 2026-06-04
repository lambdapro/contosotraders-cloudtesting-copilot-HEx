import { defineConfig, devices } from '@playwright/test';
import { name } from 'msal/lib-commonjs/packageMetadata';

/**
 * Read environment variables from file.
 * https://github.com/motdotla/dotenv
 */
require('dotenv').config()

export default defineConfig({
  testDir: './tests',
  /* Maximum time one test can run for. */
  timeout: 80 * 1000,
  expect: {
    // Account for pixel difference between login being enabled/disabled
    toHaveScreenshot: { maxDiffPixels: 100 }
  },
  /* Run tests in files in parallel */
  fullyParallel: true,
  /* Fail the build on CI if you accidentally left test.only in the source code. */
  forbidOnly: !!process.env.CI,
  /* Retry on CI only */
  retries: process.env.CI ? 2 : 0,
  /* Opt out of parallel tests on CI. */
  workers: process.env.CI ? 1 : undefined,
  /* Reporter to use. See https://playwright.dev/docs/test-reporters */
  reporter: [
    ['list'],
    ['html', { open: 'never'}],
    ['junit', { outputFile: 'test-results/junit.xml' }],
    ...(process.env.CI ? [['github'] as ['github']] : []),
  ],

  /* Shared settings for all the projects below. See https://playwright.dev/docs/api/class-testoptions. */
  use: {
    /* https://github.com/microsoft/playwright/issues/14440 - TODO - Investigate later */
    ignoreHTTPSErrors: true,
    /* Base URL to use in actions like `await page.goto('/')`. */
    baseURL: process.env.REACT_APP_BASEURLFORPLAYWRIGHTTESTING || 'http://localhost:3000',
    /* Collect trace when retrying the failed test. See https://playwright.dev/docs/trace-viewer */
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'on-first-retry',
  },

  projects: [
    // Local browser project (name does NOT contain "lambdatest"), so lambda.setup.ts
    // uses Playwright's own chromium running ON the HyperExecute VM instead of
    // connecting out to the cdp.lambdatest.com cloud grid. Combined with
    // `tunnel: true` in hyperexecute.yaml, the VM reaches the runner's
    // localhost:3000 through the HE tunnel. Used by the HyperExecute stage.
    {
      name: "chromium-local",
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1280, height: 720 },
      },
    },
    // Setup project
 //   { name: 'setup', testMatch: /.*\.setup\.ts/ },
    // Test project that requires authentication

   {
      name: "MicrosoftEdge:latest:Windows10@lambdatest",
      use: {
        viewport: { width: 1280, height: 720 },
      }
    },
    {
      name: "chrome:108:Windows10@lambdatest",
      use: {

        viewport: { width: 1280, height: 720 },
      },
    },
    {
      name: "pw-firefox:latest@lambdatest",
      use: {

        viewport: { width: 1280, height: 720 },
      },
    }
   /* {
      name: 'authenticated',
      testMatch: /.account\.ts/,
      use: {
        ...devices['Desktop Chrome'],
        // Use prepared auth state.
        storageState: '.auth/user.json',
      },
      dependencies: ['setup'],
    },
    // Test projects that don't require authentication
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome']
      },
      testIgnore: /api/,
    },
    {
      name: 'firefox',
      use: {
        ...devices['Desktop Firefox']
      },
      testIgnore: /api/,
    },
    {
      name: 'webkit',
      use: {
        ...devices['Desktop Safari']
      },
      testIgnore: /api/,
    },
    {
      name: 'api',
      testMatch: 'tests/api/**.spec.ts',}*/
    

  ],
});