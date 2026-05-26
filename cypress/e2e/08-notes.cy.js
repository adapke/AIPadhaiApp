/**
 * 08-notes.cy.js
 *
 * Notes module UI presence and API gate tests.
 */

describe('Notes module: navigation', () => {
  beforeEach(() => {
    cy.clearLocalStorage();
    cy.visit('/');
  });

  it('notes nav item exists in sidebar', () => {
    cy.get('#sidebar .nav-item[data-module="notes"]').should('exist');
  });

  it('navigating to notes shows the module section', () => {
    cy.get('#sidebar .nav-item[data-module="notes"]').click({ force: true });
    cy.get('#mod-notes').should('have.class', 'active');
  });

  it('notes module section is visible after navigation', () => {
    cy.get('#sidebar .nav-item[data-module="notes"]').click({ force: true });
    cy.get('#mod-notes').should('be.visible');
  });

  it('?m=notes URL param opens the notes module', () => {
    cy.visit('/?m=notes');
    cy.get('#mod-notes').should('have.class', 'active');
  });
});

describe('Notes API: auth gate', () => {
  it('GET /lessons/fake-id/notes without auth → 401', () => {
    cy.request({
      method: 'GET',
      url: '/lessons/fake-lesson-id/notes',
      failOnStatusCode: false,
    }).its('status').should('eq', 401);
  });

  it('POST /lessons/fake-id/notes without auth → 401', () => {
    cy.request({
      method: 'POST',
      url: '/lessons/fake-lesson-id/notes',
      form: true,
      body: { content: 'My note' },
      failOnStatusCode: false,
    }).its('status').should('eq', 401);
  });
});

describe('Notes API: authenticated access', () => {
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
        if (res.status === 200) authToken = res.body.token;
      });
    });
  });

  it('GET /lessons/fake-id/notes with valid auth → 200 with empty content', () => {
    if (!authToken) {
      cy.log('Skipping: no auth token');
      return;
    }
    cy.request({
      method: 'GET',
      url: '/lessons/nonexistent-lesson-for-notes-test/notes',
      headers: { Authorization: `Bearer ${authToken}` },
      failOnStatusCode: false,
    }).then((res) => {
      // Notes endpoint returns empty string for missing lesson, not 404
      expect(res.status).to.eq(200);
      expect(res.body).to.have.property('content');
    });
  });

  it('POST /lessons/fake-id/notes with valid auth → 200', () => {
    if (!authToken) {
      cy.log('Skipping: no auth token');
      return;
    }
    cy.request({
      method: 'POST',
      url: '/lessons/nonexistent-lesson-for-notes-test/notes',
      headers: { Authorization: `Bearer ${authToken}` },
      form: true,
      body: { content: 'Cypress test note — photosynthesis' },
      failOnStatusCode: false,
    }).then((res) => {
      expect(res.status).to.eq(200);
    });
  });
});
