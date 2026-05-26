/**
 * 05-video-studio.cy.js
 *
 * Video Studio UI: step rail, source selector, form fields,
 * board/exam selector, and form validation before generation.
 */

beforeEach(() => {
  cy.clearLocalStorage();
  cy.visit('/');
  // Navigate to the studio module
  cy.get('#sidebar .nav-item[data-module="studio"]').click({ force: true });
  cy.get('#mod-studio').should('have.class', 'active');
});

describe('Video Studio: overall layout', () => {
  it('shows the studio section hero', () => {
    cy.get('#mod-studio .hero').should('be.visible');
    cy.get('#mod-studio .hero h2').should('contain', 'Video Studio');
  });

  it('renders the 4-step rail', () => {
    cy.get('#vs-steps .vs-step').should('have.length', 4);
    cy.get('#vs-steps .vs-step[data-step="1"]').should('contain', 'Source');
    cy.get('#vs-steps .vs-step[data-step="2"]').should('contain', 'Customize');
    cy.get('#vs-steps .vs-step[data-step="3"]').should('contain', 'Generate');
    cy.get('#vs-steps .vs-step[data-step="4"]').should('contain', 'Learn');
  });

  it('step 1 panel is visible on load', () => {
    cy.get('#vs-step-1').should('be.visible');
  });

  it('step 2 panel is hidden on load (not yet customized)', () => {
    cy.get('#vs-step-2').should('not.be.visible');
  });

  it('step 1 is the active step', () => {
    cy.get('#vs-steps .vs-step[data-step="1"]').should('have.class', 'active');
  });
});

describe('Video Studio: source selector', () => {
  it('Topic source button is active by default', () => {
    cy.get('.vs-source-btn[data-source="topic"]').should('have.class', 'active');
  });

  it('topic input is visible by default', () => {
    cy.get('#vs-source-topic').should('be.visible');
    cy.get('#vs-topic').should('be.visible');
  });

  it('file input is hidden by default', () => {
    cy.get('#vs-source-file').should('not.be.visible');
  });

  it('clicking "Upload PDF / image" shows the file input', () => {
    cy.get('.vs-source-btn[data-source="file"]').click();
    cy.get('#vs-source-file').should('be.visible');
    cy.get('#vs-file').should('be.visible');
  });

  it('clicking "Upload PDF / image" hides the topic input', () => {
    cy.get('.vs-source-btn[data-source="file"]').click();
    cy.get('#vs-source-topic').should('not.be.visible');
  });

  it('switching back to topic source hides the file input again', () => {
    cy.get('.vs-source-btn[data-source="file"]').click();
    cy.get('.vs-source-btn[data-source="topic"]').click();
    cy.get('#vs-source-file').should('not.be.visible');
    cy.get('#vs-source-topic').should('be.visible');
  });

  it('topic input has the correct placeholder text', () => {
    cy.get('#vs-topic').should('have.attr', 'placeholder')
      .and('include', 'Photosynthesis');
  });

  it('file input accepts image and PDF types', () => {
    cy.get('#vs-file').should('have.attr', 'accept').and('include', 'image/*').and('include', '.pdf');
  });
});

describe('Video Studio: step 2 — customize options', () => {
  beforeEach(() => {
    // Fill in a topic and proceed to step 2
    cy.get('#vs-topic').type('Photosynthesis');
    cy.get('#vs-go-customize').click();
    cy.get('#vs-step-2').should('be.visible');
  });

  it('video mode selector is present', () => {
    cy.get('#vs-mode').should('be.visible');
  });

  it('audience selector contains standard options', () => {
    cy.get('#vs-user-type').should('be.visible');
    cy.get('#vs-user-type option[value="student"]').should('exist');
    cy.get('#vs-user-type option[value="teacher"]').should('exist');
    cy.get('#vs-user-type option[value="parent"]').should('exist');
  });

  it('age selector is present with KG option', () => {
    cy.get('#vs-age').should('be.visible');
    cy.get('#vs-age option[value="5"]').should('exist');
  });

  it('language selector contains English and Hindi', () => {
    cy.get('#vs-language').should('be.visible');
    cy.get('#vs-language option[value="en"]').should('exist');
    cy.get('#vs-language option[value="hi"]').should('exist');
  });

  it('language selector contains all 10 supported languages', () => {
    const langs = ['en', 'hi', 'ta', 'te', 'kn', 'mr', 'bn', 'gu', 'pa', 'ml'];
    cy.get('#vs-language').find('option').then(($opts) => {
      const values = [...$opts].map((o) => o.value);
      langs.forEach((l) => expect(values).to.include(l));
    });
  });

  it('tone override selector is present', () => {
    cy.get('#vs-tone').should('be.visible');
    cy.get('#vs-tone option[value="friendly"]').should('exist');
    cy.get('#vs-tone option[value="exam_focused"]').should('exist');
  });

  it('duration selector is present', () => {
    cy.get('#vs-duration').should('be.visible');
    cy.get('#vs-duration option[value="60"]').should('exist');
    cy.get('#vs-duration option[value="300"]').should('exist');
  });

  it('output format selector is present with 16:9 default', () => {
    cy.get('#vs-format').should('be.visible');
    cy.get('#vs-format option[value="16:9"]').should('have.attr', 'selected');
  });
});

describe('Video Studio: topic validation', () => {
  it('Next button is visible on step 1', () => {
    cy.get('#vs-go-customize').should('be.visible');
  });

  it('empty topic: Next button click does not advance to step 2 (field required)', () => {
    // Don't type anything
    cy.get('#vs-go-customize').click();
    // Step 2 should still be hidden because topic is empty
    cy.get('#vs-step-2').should('not.be.visible');
  });
});
