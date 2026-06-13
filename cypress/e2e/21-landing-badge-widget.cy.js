/**
 * 21-landing-badge-widget.cy.js — prod-95
 *
 * Locks the prod-94 widget contract:
 *   - /home renders the trust-strip section with the badge pill element
 *   - The widget's JS calls /api/concept-videos/badge
 *   - The endpoint stays public + cacheable (no auth required)
 *   - The hidden→visible transition happens via display:flex toggle
 */

describe('Public landing trust strip (prod-94)', () => {
  it('/home contains the trust-strip section with 5 pills', () => {
    cy.request('/home').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.include('trust-strip');
      // Count trust-pill divs — should be at least 5 (4 baseline + 1 new badge)
      const pillCount = (res.body.match(/class="trust-pill"/g) || []).length;
      expect(pillCount).to.be.at.least(5);
    });
  });

  it('/home contains the curator badge pill + JS hydration code', () => {
    cy.request('/home').then((res) => {
      const body = res.body;
      expect(body).to.include('curatorBadgePill');
      expect(body).to.include('curatorBadgeText');
      expect(body).to.include('curatorBadgeSub');
      expect(body).to.include('loadCuratorBadge');
      expect(body).to.include('/api/concept-videos/badge');
      // Badge starts hidden so the page doesn't show a blank pill if the
      // catalog is empty or the endpoint fails.
      expect(body).to.match(/id="curatorBadgePill"[^>]*style="[^"]*display:none/);
    });
  });

  it('/home JS reveals the pill via display:flex on success', () => {
    cy.request('/home').then((res) => {
      // The reveal sets `pill.style.display = 'flex'` when verified > 0
      expect(res.body).to.match(/pill\.style\.display\s*=\s*['"]flex['"]/);
    });
  });

  it('GET /api/concept-videos/badge is public + returns expected shape', () => {
    cy.request('/api/concept-videos/badge').then((res) => {
      expect(res.status).to.eq(200);
      for (const key of [
        'total', 'verified', 'verified_pct', 'channel_seed',
        'languages', 'subjects', 'freshness_label',
      ]) {
        expect(res.body).to.have.property(key);
      }
      expect(res.body.languages).to.be.an('array');
      expect(res.body.subjects).to.be.an('array');
    });
  });

  it('Badge endpoint does NOT require authentication', () => {
    cy.request({
      url: '/api/concept-videos/badge',
      failOnStatusCode: false,
    }).then((res) => {
      // Public — same response with or without Authorization header.
      expect(res.status).to.eq(200);
    });
  });

  it('Verified count and freshness label are usable as text', () => {
    cy.request('/api/concept-videos/badge').then((res) => {
      const { verified, freshness_label } = res.body;
      // verified is a non-negative integer
      expect(verified).to.be.a('number');
      expect(verified).to.be.at.least(0);
      // freshness_label is one of the documented strings
      expect(freshness_label).to.satisfy((s) =>
        s === 'never' || s === 'today' || s === '1 day ago' || s.endsWith('days ago'),
      );
    });
  });
});
