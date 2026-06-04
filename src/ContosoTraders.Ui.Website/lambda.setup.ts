/**
 * Add the file in your test suite to run tests on LambdaTest.
 * Import `test` object from this file in the tests.
 */

import { Accessibility } from "@mui/icons-material";
import * as base from "@playwright/test";
import path from "path";
import { chromium } from "playwright";

// Resolve the locally-installed Playwright version. LambdaTest's cloud grid
// REQUIRES playwrightClientVersion in the caps and will close the session on
// connect ("Browser has been closed") if it is missing/mismatched.
let playwrightClientVersion = "1.47.2";
try {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  playwrightClientVersion = require("@playwright/test/package.json").version;
} catch (e) {
  /* fall back to the pinned version above */
}

// LambdaTest capabilities
const capabilities = {
  browserName: "Chrome", // Browsers allowed: `Chrome`, `MicrosoftEdge`, `pw-chromium`, `pw-firefox` and `pw-webkit`
  browserVersion: "latest",
  "LT:Options": {
    platform: "Windows 10",
    build: "Playwright Build",
    name: "Github copilot Test Build",
    user: process.env.LT_USERNAME,
    accessKey: process.env.LT_ACCESS_KEY,
    playwrightClientVersion: playwrightClientVersion,
    network: true,
    video: true,
    console: true,
    accessibility:true,
    // Tunnel: route the LambdaTest cloud browser to the app on the runner's
    // localhost:3000. tunnel:true is required; tunnelName binds a specific tunnel
    // (HE-provisioned in CI, or a locally-started LT tunnel). Empty name uses the
    // account's active/global tunnel.
    tunnel: true,
    tunnelName: process.env.LT_TUNNEL_NAME || process.env.HYPEREXECUTE_TUNNEL_NAME || "",
    geoLocation: "", // country code can be fetched from https://www.lambdatest.com/capabilities-generator/
  },

};

// Patching the capabilities dynamically according to the project name.
const modifyCapabilities = (configName, testName) => {
  let config = configName.split("@lambdatest")[0];
  let [browserName, browserVersion, platform] = config.split(":");
  capabilities.browserName = browserName
    ? browserName
    : capabilities.browserName;
  capabilities.browserVersion = browserVersion
    ? browserVersion
    : capabilities.browserVersion;
  capabilities["LT:Options"]["platform"] = platform
    ? platform
    : capabilities["LT:Options"]["platform"];
  capabilities["LT:Options"]["name"] = testName;
};

const getErrorMessage = (obj, keys) =>
  keys.reduce(
    (obj, key) => (typeof obj == "object" ? obj[key] : undefined),
    obj
  );

const test = base.test.extend({
  page: async ({ page, playwright }, use, testInfo) => {
    // Configure LambdaTest platform for cross-browser testing
    let fileName = testInfo.file.split(path.sep).pop();
    if (testInfo.project.name.match(/lambdatest/)) {
      modifyCapabilities(
        testInfo.project.name,
        `${testInfo.title} - ${fileName}`
      );

      const browser = await chromium.connect({
        wsEndpoint: `wss://cdp.lambdatest.com/playwright?capabilities=${encodeURIComponent(
          JSON.stringify(capabilities)
        )}`,
      });

      // Pass baseURL explicitly: pages created on a manually-connected browser
      // do NOT inherit the playwright.config top-level `use.baseURL`, so relative
      // gotos like page.goto('/') would otherwise fail.
      const ltPage = await browser.newPage({
        ...testInfo.project.use,
        baseURL:
          process.env.REACT_APP_BASEURLFORPLAYWRIGHTTESTING ||
          "http://localhost:3000",
      });
      await use(ltPage);

      const testStatus = {
        action: "setTestStatus",
        arguments: {
          status: testInfo.status,
          remark: getErrorMessage(testInfo, ["error", "message"]),
        },
      };
      await ltPage.evaluate(() => {},
      `lambdatest_action: ${JSON.stringify(testStatus)}`);
      await ltPage.close();
      await browser.close();
    } else {
      // Run tests in local in case of local config provided
      await use(page);
    }
  },
});

export default test;
