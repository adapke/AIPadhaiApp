/**
 * 03-auth-modal.cy.js
 *
 * Browser-level (DOM) tests for the auth modal:
 * open/close, tab switching, form field visibility, DPDP under-13 toggle.
 *
 * Does NOT submit the form (that's covered by 02-auth-api.cy.js via
 * cy.request — faster and more reliable).
 */

beforeEach(() => {
  // Clear any leftover auth state from previous tests
  cy.clearLocalStorage();
  cy.visit('/');
});

describe('Auth corner: unauthenticated state', () => {
  it('shows "Sign in" button when not logged in', () => {
    cy.get('#auth-corner').contains('Sign in').should('be.visible');
  });

  it('does NOT show user menu when not logged in', () => {
    cy.get('#auth-corner').find('#user-menu').should('not.exist');
  });
});

describe('Auth modal: open / close', () => {
  it('modal is closed on page load', () => {
    cy.get('#auth-modal').should('not.have.class', 'open');
  });

  it('clicking "Sign in" opens the modal', () => {
    cy.get('#auth-corner').contains('Sign in').click();
    cy.get('#auth-modal').should('have.class', 'open');
  });

  it('clicking × closes the modal', () => {
    cy.get('#auth-corner').contains('Sign in').click();
    cy.get('#auth-modal').should('have.class', 'open');
    cy.get('#close-modal').click();
    cy.get('#auth-modal').should('not.have.class', 'open');
  });
});

describe('Auth modal: tabs', () => {
  beforeEach(() => {
    cy.get('#auth-corner').contains('Sign in').click();
  });

  it('Sign up tab is active by default', () => {
    cy.get('#auth-modal .tab[data-mode="signup"]').should('have.class', 'active');
    cy.get('#auth-modal .tab[data-mode="login"]').should('not.have.class', 'active');
  });

  it('clicking Log in tab switches the active tab', () => {
    cy.get('#auth-modal .tab[data-mode="login"]').click();
    cy.get('#auth-modal .tab[data-mode="login"]').should('have.class', 'active');
    cy.get('#auth-modal .tab[data-mode="signup"]').should('not.have.class', 'active');
  });

  it('switching back to Sign up restores that tab as active', () => {
    cy.get('#auth-modal .tab[data-mode="login"]').click();
    cy.get('#auth-modal .tab[data-mode="signup"]').click();
    cy.get('#auth-modal .tab[data-mode="signup"]').should('have.class', 'active');
  });
});

describe('Auth modal: form fields', () => {
  beforeEach(() => {
    cy.get('#auth-corner').contains('Sign in').click();
  });

  it('email and password fields are always visible', () => {
    cy.get('#auth-form input[name="email"]').should('be.visible');
    cy.get('#auth-form input[name="password"]').should('be.visible');
  });

  it('DOB field is visible on Sign up tab', () => {
    cy.get('#auth-modal .tab[data-mode="signup"]').click();
    cy.get('#auth-dob').should('be.visible');
  });

  it('DOB field is hidden when Log in tab is selected', () => {
    cy.get('#auth-modal .tab[data-mode="login"]').click();
    // The .signup-only wrapper becomes display:none
    cy.get('#auth-form .signup-only').should('not.be.visible');
  });

  it('under-13 parent email block is hidden initially', () => {
    cy.get('#signup-dpdp').should('not.be.visible');
  });

  it('entering a DOB < 13 years ago shows the parent email block', () => {
    // Enter a DOB 10 years ago
    const dob = new Date();
    dob.setFullYear(dob.getFullYear() - 10);
    const dobStr = dob.toISOString().slice(0, 10);
    cy.get('#auth-dob').type(dobStr).trigger('change');
    cy.get('#signup-dpdp').should('be.visible');
    cy.get('#auth-parent-email').should('be.visible');
  });

  it('entering a DOB > 13 years ago hides the parent email block', () => {
    // First show it
    const youngDob = new Date();
    youngDob.setFullYear(youngDob.getFullYear() - 10);
    cy.get('#auth-dob').type(youngDob.toISOString().slice(0, 10)).trigger('change');
    cy.get('#signup-dpdp').should('be.visible');
    // Now enter an adult DOB
    const adultDob = new Date();
    adultDob.setFullYear(adultDob.getFullYear() - 18);
    cy.get('#auth-dob').clear().type(adultDob.toISOString().slice(0, 10)).trigger('change');
    cy.get('#signup-dpdp').should('not.be.visible');
  });

  it('submit button is labelled "Continue"', () => {
    cy.get('#auth-form button[type="submit"]').should('contain', 'Continue');
  });

  it('status div is empty before submission', () => {
    cy.get('#auth-status').should('have.text', '');
  });
});

describe('Auth modal: submission errors shown in UI', () => {
  beforeEach(() => {
    cy.get('#auth-corner').contains('Sign in').click();
    // Switch to Login mode so we can test login errors
    cy.get('#auth-modal .tab[data-mode="login"]').click();
  });

  it('shows an error message for unknown email', () => {
    cy.get('#auth-form input[name="email"]').type('nobody@nowhere.invalid');
    cy.get('#auth-form input[name="password"]').type('Test1234!');
    cy.get('#auth-form button[type="submit"]').click();
    // Either a 401 error message or a 503 "not configured" message is acceptable
    cy.get('#auth-status', { timeout: 8000 }).should('not.have.text', '').and('not.have.text', 'Working…');
  });
});
