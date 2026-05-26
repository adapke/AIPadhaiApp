/**
 * 07-flashcards.cy.js
 *
 * Flashcard module UI presence and API gate tests.
 */

describe('Flashcard module: navigation', () => {
  beforeEach(() => {
    cy.clearLocalStorage();
    cy.visit('/');
  });

  it('flashcards nav item exists in sidebar', () => {
    cy.get('#sidebar .nav-item[data-module="flashcards"]').should('exist');
  });

  it('navigating to flashcards shows the module section', () => {
    cy.get('#sidebar .nav-item[data-module="flashcards"]').click({ force: true });
    cy.get('#mod-flashcards').should('have.class', 'active');
  });

  it('flashcards module section is visible after navigation', () => {
    cy.get('#sidebar .nav-item[data-module="flashcards"]').click({ force: true });
    cy.get('#mod-flashcards').should('be.visible');
  });

  it('?m=flashcards URL param opens the flashcards module', () => {
    cy.visit('/?m=flashcards');
    cy.get('#mod-flashcards').should('have.class', 'active');
  });
});

describe('Flashcard module: API gates', () => {
  it('POST /lessons/fake-id/flashcards without auth → 401', () => {
    cy.request({
      method: 'POST',
      url: '/lessons/fake-lesson-id/flashcards',
      failOnStatusCode: false,
    }).its('status').should('eq', 401);
  });

  it('POST /lessons/fake-id/flashcards with invalid auth → 401', () => {
    cy.request({
      method: 'POST',
      url: '/lessons/fake-lesson-id/flashcards',
      headers: { Authorization: 'Bearer invalid-token' },
      failOnStatusCode: false,
    }).its('status').should('eq', 401);
  });
});

describe('Flashcard SM-2 rating API', () => {
  it('POST /lessons/fake-id/flashcards/rate without auth → 401', () => {
    cy.request({
      method: 'POST',
      url: '/lessons/fake-lesson-id/flashcards/rate',
      form: true,
      body: { card_id: 0, rating: 3 },
      failOnStatusCode: false,
    }).its('status').should('eq', 401);
  });
});
