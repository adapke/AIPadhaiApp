/**
 * 10-curriculum-ui.cy.js
 *
 * Curriculum Map module UI: rendering, board filter chips,
 * and the lesson-curriculum matching API gate.
 */

describe('Curriculum module: navigation', () => {
  beforeEach(() => {
    cy.clearLocalStorage();
    cy.visit('/');
  });

  it('curriculum nav item exists in sidebar', () => {
    cy.get('#sidebar .nav-item[data-module="curriculum"]').should('exist');
  });

  it('navigating to curriculum shows the module section', () => {
    cy.get('#sidebar .nav-item[data-module="curriculum"]').click({ force: true });
    cy.get('#mod-curriculum').should('have.class', 'active');
  });

  it('?m=curriculum URL param opens the curriculum module', () => {
    cy.visit('/?m=curriculum');
    cy.get('#mod-curriculum').should('have.class', 'active');
  });
});

describe('Curriculum API: index filtering', () => {
  it('unfiltered index has > 100 entries (expanded catalogue)', () => {
    cy.request('/curriculum/index').then((res) => {
      expect(res.body.entries.length).to.be.greaterThan(100);
    });
  });

  it('CBSE filter returns only CBSE entries', () => {
    cy.request('/curriculum/index?board=CBSE').then((res) => {
      res.body.entries.forEach((e) => expect(e.board).to.eq('CBSE'));
    });
  });

  it('ICSE filter returns only ICSE entries', () => {
    cy.request('/curriculum/index?board=ICSE').then((res) => {
      res.body.entries.forEach((e) => expect(e.board).to.eq('ICSE'));
    });
  });

  it('Maharashtra filter returns state board entries', () => {
    cy.request('/curriculum/index?board=Maharashtra').then((res) => {
      expect(res.body.entries.length).to.be.greaterThan(0);
      res.body.entries.forEach((e) => expect(e.board).to.eq('Maharashtra'));
    });
  });

  it('NEET entries all have level neet_jee', () => {
    cy.request('/curriculum/index?board=NEET').then((res) => {
      expect(res.body.entries.length).to.be.greaterThan(0);
      res.body.entries.forEach((e) => expect(e.level).to.eq('neet_jee'));
    });
  });

  it('JEE entries all have level neet_jee', () => {
    cy.request('/curriculum/index?board=JEE').then((res) => {
      expect(res.body.entries.length).to.be.greaterThan(0);
      res.body.entries.forEach((e) => expect(e.level).to.eq('neet_jee'));
    });
  });

  it('each entry has required schema fields', () => {
    cy.request('/curriculum/index').then((res) => {
      const required = ['board', 'class', 'subject', 'chapter_no', 'chapter_title', 'level', 'summary', 'topics'];
      res.body.entries.slice(0, 10).forEach((entry) => {
        required.forEach((field) => expect(entry).to.have.property(field));
      });
    });
  });

  it('topics field is an array', () => {
    cy.request('/curriculum/index').then((res) => {
      res.body.entries.slice(0, 10).forEach((entry) => {
        expect(entry.topics).to.be.an('array');
      });
    });
  });

  it('count field matches entries array length', () => {
    cy.request('/curriculum/index').then((res) => {
      expect(res.body.count).to.eq(res.body.entries.length);
    });
  });
});

describe('Curriculum API: lesson matching gate', () => {
  it('POST /lessons/fake-id/curriculum without auth → 401', () => {
    cy.request({
      method: 'POST',
      url: '/lessons/fake-id/curriculum',
      failOnStatusCode: false,
    }).its('status').should('eq', 401);
  });
});

describe('Admin curriculum API: auth gate', () => {
  it('GET /api/admin/curriculum without auth → 401 or 403', () => {
    cy.request({
      url: '/api/admin/curriculum',
      failOnStatusCode: false,
    }).then((res) => {
      expect([401, 403]).to.include(res.status);
    });
  });

  it('POST /api/admin/curriculum without auth → 401 or 403', () => {
    cy.request({
      method: 'POST',
      url: '/api/admin/curriculum',
      form: true,
      body: { board: 'CBSE', class: 6, subject: 'Maths', chapter_no: 99, chapter_title: 'Test', level: 'middle' },
      failOnStatusCode: false,
    }).then((res) => {
      expect([401, 403]).to.include(res.status);
    });
  });
});
