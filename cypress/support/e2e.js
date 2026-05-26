// Support file — loaded before every spec.
// Import custom commands so they're available as cy.* everywhere.
import './commands';

// Silence uncaught exceptions from third-party scripts (e.g. service worker
// registration failures in dev). Test code exceptions will still fail tests.
Cypress.on('uncaught:exception', (err) => {
  // PanicException comes from the cryptography bindings in the test env.
  if (err.message.includes('PanicException') ||
      err.message.includes('SW registration') ||
      err.message.includes('serviceWorker')) {
    return false;
  }
  // Let all other errors propagate and fail the test.
});
