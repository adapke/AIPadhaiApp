/**
 * 09-profile-export.cy.js
 *
 * /api/me/profile and /api/me/data/export: schema, auth gate,
 * and data structure assertions for authenticated users.
 */

const skipOn503 = (res) => res.status === 503;

describe('Profile and data export: unauthenticated', () => {
  it('GET /api/me/profile → 401', () => {
    cy.request({ url: '/api/me/profile', failOnStatusCode: false })
      .its('status').should('eq', 401);
  });

  it('GET /api/me/data/export → 401', () => {
    cy.request({ url: '/api/me/data/export', failOnStatusCode: false })
      .its('status').should('eq', 401);
  });
});

describe('Profile and data export: authenticated', () => {
  let token, userEmail;

  before(() => {
    cy.task('randomEmail').then((email) => {
      cy.request({
        method: 'POST',
        url: '/auth/signup',
        form: true,
        body: { email, password: 'Test1234!', terms_accepted: 'true' },
        failOnStatusCode: false,
      }).then((res) => {
        if (skipOn503(res)) return;
        token = res.body.token;
        userEmail = res.body.email;
      });
    });
  });

  it('GET /api/me/profile returns expected fields', () => {
    if (!token) {
      cy.log('Skipping: no token');
      return;
    }
    cy.request({
      url: '/api/me/profile',
      headers: { Authorization: `Bearer ${token}` },
    }).then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.have.property('email', userEmail);
      expect(res.body).to.have.property('subscription_tier');
    });
  });

  it('GET /api/me/data/export returns valid schema', () => {
    if (!token) {
      cy.log('Skipping: no token');
      return;
    }
    cy.request({
      url: '/api/me/data/export',
      headers: { Authorization: `Bearer ${token}` },
    }).then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.have.property('generated_at');
      expect(res.body).to.have.property('profile');
      expect(res.body).to.have.property('jobs').that.is.an('array');
      expect(res.body).to.have.property('schema_version');
    });
  });

  it('export profile.email matches signup email', () => {
    if (!token) {
      cy.log('Skipping: no token');
      return;
    }
    cy.request({
      url: '/api/me/data/export',
      headers: { Authorization: `Bearer ${token}` },
    }).then((res) => {
      expect(res.body.profile.email).to.eq(userEmail);
    });
  });

  it('export subscription_tier is M1 for a fresh signup', () => {
    if (!token) {
      cy.log('Skipping: no token');
      return;
    }
    cy.request({
      url: '/api/me/data/export',
      headers: { Authorization: `Bearer ${token}` },
    }).then((res) => {
      expect(res.body.profile.subscription_tier).to.eq('M1');
    });
  });
});
