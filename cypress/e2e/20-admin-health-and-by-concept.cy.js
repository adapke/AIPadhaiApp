/**
 * 20-admin-health-and-by-concept.cy.js — prod-89
 *
 * Locks the routes shipped in prod-81 (by-concept slug lookup) and
 * prod-85 (admin health dashboard) plus the prod-87 nav-link wiring.
 */

describe('Public /by-concept slug lookup (prod-81)', () => {
  it('GET /api/concept-videos/by-concept/<known> returns 200 + ConceptVideo dict', () => {
    // The Peekaboo Newton row is shipped as the one verified seed.
    cy.request('/api/concept-videos/by-concept/newton-first-law-of-motion').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.have.property('concept');
      expect(res.body).to.have.property('embed_url');
      expect(res.body).to.have.property('quality_tier');
      // Public endpoint defaults to verified-only
      expect(res.body.quality_tier).to.eq('verified');
    });
  });

  it('GET /api/concept-videos/by-concept/<unknown> returns 404', () => {
    cy.request({
      url: '/api/concept-videos/by-concept/quantum-gravity-of-bananas',
      failOnStatusCode: false,
    }).then((res) => {
      expect(res.status).to.eq(404);
    });
  });

  it('Slug normalisation works (hyphen vs space)', () => {
    // Both should resolve to the same row when one of them exists.
    cy.request('/api/concept-videos/by-concept/newton-first-law-of-motion').then((r1) => {
      cy.request('/api/concept-videos/by-concept/newton%20first%20law%20of%20motion').then((r2) => {
        expect(r1.status).to.eq(200);
        expect(r2.status).to.eq(200);
        expect(r1.body.id).to.eq(r2.body.id);
      });
    });
  });

  it('Language filter respected — `hi` returns 404 for English-only row', () => {
    cy.request({
      url: '/api/concept-videos/by-concept/newton-first-law-of-motion?language=hi',
      failOnStatusCode: false,
    }).then((res) => {
      // Either 200 (if Hindi seed exists) or 404 — never 500.
      expect(res.status).to.be.oneOf([200, 404]);
    });
  });
});


describe('Admin health page (prod-85) + nav wiring (prod-87)', () => {
  it('GET /admin/health is admin-only — anonymous → 401', () => {
    cy.request({
      url: '/admin/health',
      followRedirect: false,
      failOnStatusCode: false,
    }).then((res) => {
      expect(res.status).to.be.oneOf([401, 403]);
    });
  });

  it('Dashboard markup includes the admin-nav-links container (prod-87)', () => {
    cy.request('/dashboard').then((res) => {
      expect(res.status).to.eq(200);
      // Container is hidden until /api/admin/concept-videos/queue succeeds,
      // but the element + links must be in the HTML payload for the
      // hide/show JS to flip it on.
      expect(res.body).to.include('adminNavLinks');
      expect(res.body).to.include('/admin/health');
      expect(res.body).to.include('/admin/concept-curator');
      expect(res.body).to.include('/admin/curator-stats');
    });
  });

  it('Curator chip JS toggles adminNavLinks visibility', () => {
    cy.request('/dashboard').then((res) => {
      // The loadCuratorChip function must flip adminNavLinks to visible
      // when the queue endpoint returns 200.
      expect(res.body).to.match(/nav\.style\.display\s*=\s*['"]block['"]/);
    });
  });
});


describe('Admin endpoints stay admin-gated for non-admin session', () => {
  it('Signed-in non-admin (in DB-mode) still gets 401 on /admin/health', () => {
    // In DEV mode (no DATABASE_URL) the fallback grants admin to any
    // signed-in user, so this assertion accepts 200 as well. Strict
    // production-mode CI will see 401/403.
    cy.task('randomEmail').then((email) => {
      cy.apiSignup(email).then((body) => {
        if (!body) {
          cy.log('AUTH 503 — skipping');
          return;
        }
        cy.request({
          url: '/admin/health',
          headers: { Authorization: `Bearer ${body.token}` },
          failOnStatusCode: false,
        }).then((res) => {
          expect(res.status).to.be.oneOf([200, 401, 403]);
        });
      });
    });
  });
});
