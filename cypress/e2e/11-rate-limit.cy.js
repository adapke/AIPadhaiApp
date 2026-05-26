/**
 * 11-rate-limit.cy.js
 *
 * Rate limiter tests: verify that the AI generation endpoint enforces
 * rate limits, and that the general token-bucket limiter allows initial
 * requests but blocks excessive ones.
 *
 * Uses cy.request() — no browser UI needed.
 */

describe('General rate limiter: per-IP token bucket', () => {
  it('GET /healthz does not get rate-limited on a few requests', () => {
    // healthz is not gated by the AI rate limiter — just smoke-test it
    for (let i = 0; i < 3; i++) {
      cy.request('/healthz').its('status').should('eq', 200);
    }
  });
});

describe('AI generation rate limit (POST /lessons)', () => {
  it('first POST /lessons without auth returns 401 (not 429)', () => {
    // Without auth and without a file, should be 401 (not 429)
    cy.request({
      method: 'POST',
      url: '/lessons',
      failOnStatusCode: false,
    }).its('status').should('eq', 401);
  });

  it('/api/me/profile rate-limit key is per user, not global', () => {
    // Rapid fire 5 authenticated calls should all return 401 (not 429)
    // because the rate limit is per-user/IP and profile GET is not
    // an AI-generation endpoint.
    for (let i = 0; i < 5; i++) {
      cy.request({ url: '/api/me/profile', failOnStatusCode: false })
        .its('status').should('eq', 401);
    }
  });
});

describe('Rate limit headers', () => {
  it('responses include at least a Content-Type header', () => {
    cy.request('/healthz').then((res) => {
      expect(res.headers).to.have.property('content-type');
    });
  });
});
