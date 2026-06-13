/**
 * 18-curator-and-quota.cy.js — prod-45
 *
 * Exercises two new dashboard surfaces:
 *   • AI quota chip (prod-38)   — /api/me/cost-today + #aiQuotaChip rendering
 *   • Curator queue (prod-41/42) — admin-only /api/admin/concept-videos/queue
 *                                  and /update endpoint contract
 *
 * Auth gating is router-level (prod-9). Anonymous users hit 401 on the admin
 * endpoints; that's an explicit assertion below — if the gate ever regresses
 * to anonymous-accessible, this spec catches it.
 */

describe('AI quota chip (prod-38)', () => {
  beforeEach(() => {
    // Use a fresh signed-in user so we're sure the quota chip path runs.
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

  it('GET /api/me/cost-today returns the contract shape', () => {
    cy.window().then((win) => {
      const tok = win.localStorage.getItem('pathshala_token');
      if (!tok) {
        cy.log('No token — skipping');
        return;
      }
      cy.request({
        method: 'GET',
        url: '/api/me/cost-today',
        headers: { Authorization: `Bearer ${tok}` },
      }).then((res) => {
        expect(res.status).to.eq(200);
        // Contract: must have tier + spent + cap + pct_used + status.
        expect(res.body).to.have.property('tier');
        expect(res.body).to.have.property('spent_paise_today');
        expect(res.body).to.have.property('cap_paise_today');
        expect(res.body).to.have.property('spent_rupees_today');
        expect(res.body).to.have.property('cap_rupees_today');
        expect(res.body).to.have.property('pct_used');
        expect(res.body).to.have.property('status');
        // Status is one of the documented enum values.
        expect([
          'ok', 'near_limit', 'over_budget',
          'premium_feature_gated', 'uncapped',
        ]).to.include(res.body.status);
      });
    });
  });

  it('GET /api/me/cost-today requires auth', () => {
    cy.request({
      method: 'GET',
      url: '/api/me/cost-today',
      failOnStatusCode: false,
    }).then((res) => {
      // Should be 401/403 — never 200 for anonymous.
      expect(res.status).to.be.oneOf([401, 403]);
    });
  });

  it('/dashboard renders the #aiQuotaChip element', () => {
    cy.window().then((win) => {
      const tok = win.localStorage.getItem('pathshala_token');
      if (!tok) return;
      cy.request({
        url: '/dashboard',
        headers: { Authorization: `Bearer ${tok}` },
      }).then((res) => {
        expect(res.status).to.eq(200);
        // The HTML must include the chip slot the JS hydrates.
        expect(res.body).to.include('aiQuotaChip');
        expect(res.body).to.include('loadAiQuota');
        // prod-48 — also the curator chip element + loader.
        expect(res.body).to.include('curatorChip');
        expect(res.body).to.include('loadCuratorChip');
      });
    });
  });
});


describe('Curator queue admin endpoints (prod-41/42)', () => {
  it('GET /api/admin/concept-videos/queue requires admin auth (anonymous → 401)', () => {
    cy.request({
      method: 'GET',
      url: '/api/admin/concept-videos/queue',
      failOnStatusCode: false,
    }).then((res) => {
      // Router-level admin gate (prod-9) catches anonymous before handler.
      expect(res.status).to.be.oneOf([401, 403]);
    });
  });

  it('POST /api/admin/concept-videos/{id}/update requires admin', () => {
    cy.request({
      method: 'POST',
      url: '/api/admin/concept-videos/abc123/update',
      body: { title: 'attacker', source_url: 'https://evil.example/v=xx' },
      failOnStatusCode: false,
    }).then((res) => {
      expect(res.status).to.be.oneOf([401, 403]);
    });
  });

  it('POST /api/admin/concept-videos/{id}/verify requires admin', () => {
    cy.request({
      method: 'POST',
      url: '/api/admin/concept-videos/abc123/verify',
      failOnStatusCode: false,
    }).then((res) => {
      expect(res.status).to.be.oneOf([401, 403]);
    });
  });

  it('POST /api/admin/concept-videos/{id}/reject requires admin', () => {
    cy.request({
      method: 'POST',
      url: '/api/admin/concept-videos/abc123/reject',
      body: { reason: 'dead URL' },
      failOnStatusCode: false,
    }).then((res) => {
      expect(res.status).to.be.oneOf([401, 403]);
    });
  });

  it('signed-in non-admin user hitting /queue gets 401/403 (not 200)', () => {
    cy.task('randomEmail').then((email) => {
      cy.apiSignup(email).then((body) => {
        if (!body) return null;
        cy.request({
          method: 'GET',
          url: '/api/admin/concept-videos/queue',
          headers: { Authorization: `Bearer ${body.token}` },
          failOnStatusCode: false,
        }).then((res) => {
          // Even with a valid signed-in token, non-admins hit the admin gate.
          expect(res.status).to.be.oneOf([401, 403]);
        });
      });
    });
  });
});


describe('Public concept-video endpoints stay accessible', () => {
  // These are the read-side endpoints the SPA hits on every dashboard load.
  // They must NOT regress to require auth — that would break the curated-
  // content surface for anonymous landing visitors.

  it('GET /api/concept-videos/stats is public', () => {
    cy.request('/api/concept-videos/stats').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.have.property('total');
      expect(res.body).to.have.property('by_quality_tier');
    });
  });

  it('GET /api/concept-videos search is public', () => {
    cy.request('/api/concept-videos?limit=5').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.have.property('rows');
      expect(res.body).to.have.property('count');
    });
  });
});
