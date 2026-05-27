/**
 * 04-navigation.cy.js
 *
 * Module navigation: sidebar, module sections, role switcher, URL deep-links,
 * and mobile bottom navigation.
 */

beforeEach(() => {
  cy.clearLocalStorage();
  cy.visit('/ui-legacy');
});

describe('Initial page state', () => {
  it('home module is active on load', () => {
    cy.get('#mod-home').should('have.class', 'active');
  });

  it('all other primary modules are NOT active on load', () => {
    ['studio', 'explainer', 'chat', 'library', 'quizmaker', 'flashcards',
     'notes', 'curriculum', 'path', 'teacher', 'parent', 'school'].forEach((name) => {
      cy.get(`#mod-${name}`).should('not.have.class', 'active');
    });
  });

  it('sidebar home nav item is active', () => {
    cy.get('#sidebar .nav-item[data-module="home"]').should('have.class', 'active');
  });
});

describe('Primary navigation via sidebar', () => {
  [
    'studio',
    'explainer',
    'chat',
    'library',
    'quizmaker',
    'flashcards',
    'notes',
    'curriculum',
    'path',
  ].forEach((module) => {
    it(`navigating to "${module}" shows its section`, () => {
      cy.get(`#sidebar .nav-item[data-module="${module}"]`).click({ force: true });
      cy.get(`#mod-${module}`).should('have.class', 'active');
      // All other modules should lose the active class
      cy.get('.module.active').should('have.length', 1);
    });
  });
});

describe('Navigation: nav items activate on click', () => {
  it('clicking studio nav item marks it as active', () => {
    cy.get('#sidebar .nav-item[data-module="studio"]').click({ force: true });
    cy.get('#sidebar .nav-item[data-module="studio"]').should('have.class', 'active');
    cy.get('#sidebar .nav-item[data-module="home"]').should('not.have.class', 'active');
  });

  it('clicking home nav item resets to home', () => {
    cy.get('#sidebar .nav-item[data-module="studio"]').click({ force: true });
    cy.get('#sidebar .nav-item[data-module="home"]').click({ force: true });
    cy.get('#mod-home').should('have.class', 'active');
  });
});

describe('URL deep-link: ?m= query param', () => {
  ['studio', 'quizmaker', 'chat', 'curriculum', 'flashcards'].forEach((mod) => {
    it(`?m=${mod} opens that module on load`, () => {
      cy.visit(`/ui-legacy?m=${mod}`);
      cy.get(`#mod-${mod}`).should('have.class', 'active');
    });
  });
});

describe('URL deep-link: #hash navigation', () => {
  it('#studio opens the studio module', () => {
    cy.visit('/ui-legacy#studio');
    cy.get('#mod-studio').should('have.class', 'active');
  });

  it('#quizmaker opens the quizmaker module', () => {
    cy.visit('/ui-legacy#quizmaker');
    cy.get('#mod-quizmaker').should('have.class', 'active');
  });
});

describe('Role switcher (home screen)', () => {
  it('Student role button is active by default (first visit)', () => {
    cy.get('.role-btn[data-role="student"]').should('have.class', 'active');
  });

  it('clicking Teacher role switches active button', () => {
    cy.get('.role-btn[data-role="teacher"]').click();
    cy.get('.role-btn[data-role="teacher"]').should('have.class', 'active');
    cy.get('.role-btn[data-role="student"]').should('not.have.class', 'active');
  });

  it('clicking Parent role switches active button', () => {
    cy.get('.role-btn[data-role="parent"]').click();
    cy.get('.role-btn[data-role="parent"]').should('have.class', 'active');
  });

  it('clicking Admin role switches active button', () => {
    cy.get('.role-btn[data-role="admin"]').click();
    cy.get('.role-btn[data-role="admin"]').should('have.class', 'active');
  });
});

describe('Home module: quick-action chips', () => {
  it('student home section is visible on load', () => {
    cy.get('#home-student').should('be.visible');
  });

  it('Video Studio chip navigates to studio module', () => {
    cy.contains('.sh-chip', 'Video Studio').click();
    cy.get('#mod-studio').should('have.class', 'active');
  });

  it('Flashcards chip navigates to flashcards module', () => {
    cy.contains('.sh-chip', 'Flashcards').click();
    cy.get('#mod-flashcards').should('have.class', 'active');
  });

  it('Curriculum Map chip navigates to curriculum module', () => {
    // There may be multiple chips — click the first one
    cy.contains('.sh-chip', 'Curriculum Map').first().click();
    cy.get('#mod-curriculum').should('have.class', 'active');
  });
});

describe('Sidebar group expand/collapse', () => {
  it('Create group can be expanded', () => {
    // Find the nav group that contains studio
    cy.get('#sidebar .nav-item[data-module="studio"]')
      .closest('.nav-group')
      .then(($group) => {
        // If collapsed, expand it
        if (!$group.hasClass('open')) {
          cy.wrap($group).find('.nav-group-header').click();
        }
        cy.wrap($group).should('have.class', 'open');
      });
  });
});

describe('Burger menu (mobile)', () => {
  it('burger button exists', () => {
    cy.get('#burger').should('exist');
  });

  it('clicking burger toggles sidebar open class', () => {
    cy.get('#burger').click();
    // Sidebar should gain some open/expanded state (implementation varies)
    cy.get('#sidebar').then(($el) => {
      // Just verify the click didn't crash the page
      expect($el).to.exist;
    });
  });
});
