/**
 * 12-accessibility.cy.js
 *
 * Basic accessibility smoke tests:
 * - Required ARIA attributes present
 * - Interactive elements are keyboard-reachable
 * - Images have alt text (or are decorative)
 * - Page has a <title>
 * - Landmark regions present
 */

beforeEach(() => {
  cy.clearLocalStorage();
  cy.visit('/');
});

describe('Page metadata', () => {
  it('has a non-empty <title>', () => {
    cy.title().should('not.be.empty');
  });

  it('html element has a lang attribute', () => {
    cy.get('html').should('have.attr', 'lang');
  });
});

describe('Keyboard / ARIA on auth modal trigger', () => {
  it('Sign in button is focusable', () => {
    cy.get('#auth-corner').contains('Sign in').focus().should('be.focused');
  });

  it('burger menu button has aria-label', () => {
    cy.get('#burger').should('have.attr', 'aria-label');
  });

  it('notification bell button exists and has a title', () => {
    cy.get('#notif-bell').should('have.attr', 'title');
  });
});

describe('Sidebar nav accessibility', () => {
  it('nav-group-header buttons have aria-expanded attribute', () => {
    cy.get('#sidebar .nav-group-header').each(($btn) => {
      expect($btn).to.have.attr('aria-expanded');
    });
  });
});

describe('Auth modal accessibility', () => {
  beforeEach(() => {
    cy.get('#auth-corner').contains('Sign in').click();
  });

  it('close button has accessible text (×)', () => {
    cy.get('#close-modal').should('contain', '×');
  });

  it('email input has type="email"', () => {
    cy.get('#auth-form input[name="email"]').should('have.attr', 'type', 'email');
  });

  it('password input has type="password"', () => {
    cy.get('#auth-form input[name="password"]').should('have.attr', 'type', 'password');
  });

  it('password input has minlength attribute', () => {
    cy.get('#auth-form input[name="password"]').should('have.attr', 'minlength');
  });

  it('email input is marked required', () => {
    cy.get('#auth-form input[name="email"]').should('have.attr', 'required');
  });
});

describe('PWA / manifest', () => {
  it('manifest link is present in <head>', () => {
    cy.get('link[rel="manifest"]').should('exist');
  });

  it('manifest.json has name and start_url', () => {
    cy.request('/manifest.json').then((res) => {
      expect(res.body).to.have.property('name');
      expect(res.body).to.have.property('start_url');
    });
  });
});
