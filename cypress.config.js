const { defineConfig } = require('cypress');

module.exports = defineConfig({
  e2e: {
    // Point at the local dev server — `python run.py` on port 8000.
    // Override via env: CYPRESS_BASE_URL=https://staging.example.com npx cypress run
    baseUrl: 'http://127.0.0.1:8000',

    // Spec pattern — all files under cypress/e2e/
    specPattern: 'cypress/e2e/**/*.cy.js',

    supportFile: 'cypress/support/e2e.js',
    fixturesFolder: 'cypress/fixtures',

    // Timeouts (ms)
    defaultCommandTimeout: 8000,
    requestTimeout: 10000,
    responseTimeout: 10000,
    pageLoadTimeout: 30000,

    // Viewport — matches common mobile-first design breakpoint
    viewportWidth: 1280,
    viewportHeight: 800,

    // Retry flaky tests once on CI
    retries: {
      runMode: 1,
      openMode: 0,
    },

    // Record videos only in CI (CYPRESS_RECORD_VIDEO=1)
    video: !!process.env.CYPRESS_RECORD_VIDEO,

    // Screenshots on failure are always useful
    screenshotOnRunFailure: true,

    env: {
      // Override in .env.cypress or via CYPRESS_* env vars
      TEST_USER_PASSWORD: 'Test1234!',
    },

    setupNodeEvents(on, config) {
      // Task: generate a unique test email address
      on('task', {
        randomEmail() {
          const rand = Math.random().toString(36).slice(2, 10);
          return `cy-test-${rand}@example.com`;
        },
      });
      return config;
    },
  },
});
