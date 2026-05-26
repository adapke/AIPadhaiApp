/**
 * 02-auth-api.cy.js
 *
 * Auth endpoint tests via cy.request() — fast, no browser UI overhead.
 * Mirrors test_auth.py but from the browser-side HTTP client perspective.
 *
 * All tests skip gracefully when DATABASE_URL is not set (HTTP 503).
 */

const skipOn503 = (res) => {
  if (res.status === 503) {
    cy.log('Skipping: auth service returned 503 (no database)');
    return true;
  }
  return false;
};

describe('POST /auth/signup', () => {
  it('succeeds with valid email + password + terms_accepted', () => {
    cy.task('randomEmail').then((email) => {
      cy.request({
        method: 'POST',
        url: '/auth/signup',
        form: true,
        body: { email, password: 'Test1234!', terms_accepted: 'true' },
        failOnStatusCode: false,
      }).then((res) => {
        if (skipOn503(res)) return;
        expect(res.status).to.eq(200);
        expect(res.body.email).to.eq(email);
        expect(res.body.token).to.be.a('string').and.have.length.greaterThan(10);
        expect(res.body.subscription_tier).to.eq('M1');
      });
    });
  });

  it('returns 400 when terms_accepted is false', () => {
    cy.task('randomEmail').then((email) => {
      cy.request({
        method: 'POST',
        url: '/auth/signup',
        form: true,
        body: { email, password: 'Test1234!', terms_accepted: 'false' },
        failOnStatusCode: false,
      }).then((res) => {
        if (skipOn503(res)) return;
        expect(res.status).to.eq(400);
        expect(res.body.detail).to.include('Terms');
      });
    });
  });

  it('returns 400 when terms_accepted is omitted', () => {
    cy.task('randomEmail').then((email) => {
      cy.request({
        method: 'POST',
        url: '/auth/signup',
        form: true,
        body: { email, password: 'Test1234!' },
        failOnStatusCode: false,
      }).then((res) => {
        if (skipOn503(res)) return;
        expect(res.status).to.eq(400);
      });
    });
  });

  it('returns 409 on duplicate email', () => {
    cy.task('randomEmail').then((email) => {
      const body = { email, password: 'Test1234!', terms_accepted: 'true' };
      cy.request({ method: 'POST', url: '/auth/signup', form: true, body, failOnStatusCode: false })
        .then((res) => {
          if (skipOn503(res)) return;
          // Second signup with same email
          cy.request({ method: 'POST', url: '/auth/signup', form: true, body, failOnStatusCode: false })
            .then((res2) => {
              expect(res2.status).to.eq(409);
            });
        });
    });
  });

  it('returns 400 for invalid email format', () => {
    cy.request({
      method: 'POST',
      url: '/auth/signup',
      form: true,
      body: { email: 'notanemail', password: 'Test1234!', terms_accepted: 'true' },
      failOnStatusCode: false,
    }).then((res) => {
      if (skipOn503(res)) return;
      expect(res.status).to.eq(400);
    });
  });

  it('returns 400 for weak password (no digit)', () => {
    cy.task('randomEmail').then((email) => {
      cy.request({
        method: 'POST',
        url: '/auth/signup',
        form: true,
        body: { email, password: 'NoDigitHere!', terms_accepted: 'true' },
        failOnStatusCode: false,
      }).then((res) => {
        if (skipOn503(res)) return;
        expect(res.status).to.eq(400);
      });
    });
  });

  it('returns 400 for weak password (no letter)', () => {
    cy.task('randomEmail').then((email) => {
      cy.request({
        method: 'POST',
        url: '/auth/signup',
        form: true,
        body: { email, password: '12345678', terms_accepted: 'true' },
        failOnStatusCode: false,
      }).then((res) => {
        if (skipOn503(res)) return;
        expect(res.status).to.eq(400);
      });
    });
  });

  it('does NOT allow setting a custom subscription tier at signup', () => {
    cy.task('randomEmail').then((email) => {
      cy.request({
        method: 'POST',
        url: '/auth/signup',
        form: true,
        body: { email, password: 'Test1234!', terms_accepted: 'true', subscription_tier: 'M4e' },
        failOnStatusCode: false,
      }).then((res) => {
        if (skipOn503(res)) return;
        // Even if someone sends subscription_tier, backend must ignore it and use M1
        if (res.status === 200) {
          expect(res.body.subscription_tier).to.eq('M1');
        }
      });
    });
  });
});

describe('POST /auth/login', () => {
  let sharedEmail;

  before(() => {
    cy.task('randomEmail').then((email) => {
      sharedEmail = email;
      cy.request({
        method: 'POST',
        url: '/auth/signup',
        form: true,
        body: { email, password: 'Test1234!', terms_accepted: 'true' },
        failOnStatusCode: false,
      });
    });
  });

  it('returns 200 and a token for correct credentials', () => {
    if (!sharedEmail) return;
    cy.request({
      method: 'POST',
      url: '/auth/login',
      form: true,
      body: { email: sharedEmail, password: 'Test1234!' },
      failOnStatusCode: false,
    }).then((res) => {
      if (skipOn503(res)) return;
      expect(res.status).to.eq(200);
      expect(res.body.token).to.be.a('string');
    });
  });

  it('returns 401 for wrong password', () => {
    if (!sharedEmail) return;
    cy.request({
      method: 'POST',
      url: '/auth/login',
      form: true,
      body: { email: sharedEmail, password: 'WrongPass9!' },
      failOnStatusCode: false,
    }).then((res) => {
      if (skipOn503(res)) return;
      expect(res.status).to.eq(401);
    });
  });

  it('returns 401 for unknown email', () => {
    cy.request({
      method: 'POST',
      url: '/auth/login',
      form: true,
      body: { email: 'nobody@nowhere.invalid', password: 'Test1234!' },
      failOnStatusCode: false,
    }).then((res) => {
      if (skipOn503(res)) return;
      expect(res.status).to.eq(401);
    });
  });

  it('treats email as case-insensitive', () => {
    if (!sharedEmail) return;
    cy.request({
      method: 'POST',
      url: '/auth/login',
      form: true,
      body: { email: sharedEmail.toUpperCase(), password: 'Test1234!' },
      failOnStatusCode: false,
    }).then((res) => {
      if (skipOn503(res)) return;
      expect(res.status).to.eq(200);
    });
  });
});

describe('Auth-protected endpoints (unauthenticated)', () => {
  it('GET /api/me/profile → 401', () => {
    cy.request({ url: '/api/me/profile', failOnStatusCode: false })
      .its('status').should('eq', 401);
  });

  it('GET /api/me/data/export → 401', () => {
    cy.request({ url: '/api/me/data/export', failOnStatusCode: false })
      .its('status').should('eq', 401);
  });

  it('DELETE /api/me/account → 401', () => {
    cy.request({ method: 'DELETE', url: '/api/me/account', failOnStatusCode: false })
      .its('status').should('eq', 401);
  });

  it('POST /api/uploads → 401', () => {
    cy.request({ method: 'POST', url: '/api/uploads', failOnStatusCode: false })
      .its('status').should('eq', 401);
  });
});
