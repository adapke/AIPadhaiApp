/**
 * 06-upload-api.cy.js
 *
 * Upload endpoint tests via cy.request():
 * authentication gate, file size limit, content-type validation,
 * and valid image acceptance.
 */

const _25MB = 25 * 1024 * 1024;

// Minimal valid 1×1 white PNG
const PNG_1X1_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADklEQVQI12P4z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg==';

describe('POST /api/uploads — auth gate', () => {
  it('returns 401 without auth token', () => {
    cy.request({
      method: 'POST',
      url: '/api/uploads',
      failOnStatusCode: false,
    }).its('status').should('eq', 401);
  });
});

describe('POST /api/uploads — authenticated', () => {
  let authToken;

  before(() => {
    cy.task('randomEmail').then((email) => {
      cy.request({
        method: 'POST',
        url: '/auth/signup',
        form: true,
        body: { email, password: 'Test1234!', terms_accepted: 'true' },
        failOnStatusCode: false,
      }).then((res) => {
        if (res.status === 503) {
          cy.log('Auth 503 — upload tests with auth will be skipped');
          return;
        }
        authToken = res.body.token;
      });
    });
  });

  it('rejects a file larger than 25 MB (413)', () => {
    // Build a >25 MB blob directly in JS (browser context)
    cy.window().then((win) => {
      const big = new win.Blob([new win.Uint8Array(_25MB + 1024)], { type: 'image/jpeg' });
      const fd = new win.FormData();
      fd.append('file', big, 'big.jpg');

      const headers = authToken ? { Authorization: `Bearer ${authToken}` } : {};
      return win.fetch('/api/uploads', { method: 'POST', body: fd, headers })
        .then((r) => {
          expect(r.status).to.eq(413);
        });
    });
  });

  it('rejects application/octet-stream as bad content type (400 or 415)', () => {
    cy.window().then((win) => {
      const blob = new win.Blob([new win.Uint8Array([0x4d, 0x5a, 0x90, 0x00])], {
        type: 'application/octet-stream',
      });
      const fd = new win.FormData();
      fd.append('file', blob, 'evil.exe');

      const headers = authToken ? { Authorization: `Bearer ${authToken}` } : {};
      return win.fetch('/api/uploads', { method: 'POST', body: fd, headers })
        .then((r) => {
          expect([400, 415]).to.include(r.status);
        });
    });
  });

  it('accepts a valid 1×1 PNG (status < 400)', () => {
    if (!authToken) {
      cy.log('Skipping: no auth token (DB not available)');
      return;
    }
    cy.window().then((win) => {
      // Decode the base64 PNG
      const binary = win.atob(PNG_1X1_B64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      const blob = new win.Blob([bytes], { type: 'image/png' });

      const fd = new win.FormData();
      fd.append('file', blob, 'photo.png');

      return win.fetch('/api/uploads', {
        method: 'POST',
        body: fd,
        headers: { Authorization: `Bearer ${authToken}` },
      }).then((r) => {
        expect(r.status).to.be.lessThan(400);
      });
    });
  });
});
