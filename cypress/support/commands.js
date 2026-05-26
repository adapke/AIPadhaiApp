// ---------------------------------------------------------------------------
// Custom Cypress commands shared across all specs.
// ---------------------------------------------------------------------------

/**
 * cy.apiSignup(email, password)
 * ─────────────────────────────
 * Signs up a new user directly via the API (no UI interaction).
 * Yields { email, token } on success.
 * Skips the test when the auth service is unavailable (HTTP 503).
 */
Cypress.Commands.add('apiSignup', (email, password = Cypress.env('TEST_USER_PASSWORD')) => {
  const fd = new FormData();
  fd.set('email', email);
  fd.set('password', password);
  fd.set('terms_accepted', 'true');

  cy.request({
    method: 'POST',
    url: '/auth/signup',
    body: { email, password, terms_accepted: 'true' },
    form: true,
    failOnStatusCode: false,
  }).then((res) => {
    if (res.status === 503) {
      // Auth requires a database — skip gracefully.
      cy.log('AUTH 503 — skipping: database not configured');
      return null;
    }
    expect(res.status).to.eq(200);
    return res.body;
  });
});

/**
 * cy.apiLogin(email, password)
 * ────────────────────────────
 * Logs in via the API. Yields { email, token }.
 * Skips the test when the auth service is unavailable.
 */
Cypress.Commands.add('apiLogin', (email, password = Cypress.env('TEST_USER_PASSWORD')) => {
  cy.request({
    method: 'POST',
    url: '/auth/login',
    body: { email, password },
    form: true,
    failOnStatusCode: false,
  }).then((res) => {
    if (res.status === 503) {
      cy.log('AUTH 503 — skipping: database not configured');
      return null;
    }
    expect(res.status).to.eq(200);
    return res.body;
  });
});

/**
 * cy.seedUser()
 * ─────────────
 * Creates a fresh test user and injects their token into localStorage so
 * the app boots in the authenticated state. Yields { email, token }.
 * Skips the test when auth is unavailable.
 */
Cypress.Commands.add('seedUser', () => {
  cy.task('randomEmail').then((email) => {
    cy.apiSignup(email).then((body) => {
      if (!body) return null;
      cy.window().then((win) => {
        win.localStorage.setItem('pathshala_token', body.token);
        win.localStorage.setItem('pathshala_email', body.email);
      });
      return body;
    });
  });
});

/**
 * cy.navigateToModule(name)
 * ─────────────────────────
 * Clicks the sidebar nav item for the given module name and asserts that
 * the corresponding section becomes active.
 */
Cypress.Commands.add('navigateToModule', (name) => {
  cy.get(`#sidebar .nav-item[data-module="${name}"]`).click({ force: true });
  cy.get(`#mod-${name}`).should('have.class', 'active');
});

/**
 * cy.openAuthModal()
 * ──────────────────
 * Clicks the "Sign in" button to open the auth modal.
 */
Cypress.Commands.add('openAuthModal', () => {
  cy.get('#auth-corner #signin-btn').click();
  cy.get('#auth-modal').should('have.class', 'open');
});
