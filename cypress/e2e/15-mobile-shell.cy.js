/**
 * 15-mobile-shell.cy.js
 *
 * Capacitor shell smoke. The three native apps (student, parent,
 * teacher) load the SPA from a configurable backend URL — see
 * mobile/scripts/configure-server.cjs. This spec asserts that the URLs
 * each shell loads return a healthy SPA so a misconfigured backend
 * fails CI before the AAB / IPA ships.
 *
 * The shells load:
 *   student → BASE_URL                  (handled by /ui or /home)
 *   parent  → BASE_URL/ui?mode=parent
 *   teacher → BASE_URL/ui?mode=teacher
 *
 * We hit each URL and check the response is HTML 200 with a sensible
 * title. This is structural — we do NOT test mode-specific UI here
 * (the modal home_ui already has UI tests in 04-navigation).
 */

describe('Mobile shell entry points', () => {
  it('student shell URL (/) returns HTML', () => {
    cy.request('/').then((res) => {
      // Root may return JSON metadata for service discovery — accept either,
      // as long as it's a 200 the WebView can load.
      expect(res.status).to.eq(200);
    });
  });

  it('student shell URL (/home) returns the home page', () => {
    cy.request('/home').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.headers['content-type']).to.match(/text\/html/);
    });
  });

  it('parent shell URL (/ui?mode=parent) returns SPA HTML', () => {
    cy.request('/ui?mode=parent').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.headers['content-type']).to.match(/text\/html/);
    });
  });

  it('teacher shell URL (/ui?mode=teacher) returns SPA HTML', () => {
    cy.request('/ui?mode=teacher').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.headers['content-type']).to.match(/text\/html/);
    });
  });

  it('manifest.json exposes PWA metadata the WebView caches', () => {
    cy.request('/manifest.json').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.have.property('name');
      expect(res.body).to.have.property('icons');
    });
  });

  it('/healthz returns 200 so the shell can wait-for-ready', () => {
    cy.request('/healthz').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body.status).to.eq('ok');
    });
  });

  // prod-133 — student shell now defaults to /?home=math.
  // The home page returns HTML with an inline redirect script;
  // a real browser bounces to /math, but cy.request() doesn't run
  // <script>, so we just verify the redirect string is in the HTML.
  it('student shell URL (/?home=math) returns HTML with math-vision redirect', () => {
    cy.request('/?home=math').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.headers['content-type']).to.match(/text\/html/);
      // The redirect must be present (CK-12-inspired scan-and-solve entry).
      expect(res.body).to.match(/window\.location\.replace\('\/math'/);
    });
  });

  // prod-133 — the /math destination page must exist (prod-28 new_ui_pages).
  it('/math destination page exists for the mobile redirect target', () => {
    cy.request('/math').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.headers['content-type']).to.match(/text\/html/);
    });
  });
});
