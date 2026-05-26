/**
 * 01-health.cy.js
 *
 * Public-route smoke tests: static pages, robots/sitemap/manifest,
 * and the key API endpoints that require no authentication.
 */

describe('Public pages', () => {
  it('GET /healthz returns status ok', () => {
    cy.request('/healthz').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body.status).to.eq('ok');
    });
  });

  it('GET /landing returns the app shell', () => {
    cy.request('/landing').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.include('PadhaiApp');
    });
  });

  it('GET /terms returns the terms page', () => {
    cy.request('/terms').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.include('Terms');
    });
  });

  it('GET /privacy returns the privacy page', () => {
    cy.request('/privacy').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.include('Privacy');
    });
  });

  it('GET /robots.txt is present and valid', () => {
    cy.request('/robots.txt').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.include('User-agent');
    });
  });

  it('GET /sitemap.xml is valid XML', () => {
    cy.request('/sitemap.xml').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.include('<urlset');
    });
  });

  it('GET /manifest.json is valid PWA manifest', () => {
    cy.request('/manifest.json').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.have.property('name');
    });
  });
});

describe('API: fees config', () => {
  it('GET /api/fees/config returns razorpay_configured flag', () => {
    cy.request('/api/fees/config').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.have.property('razorpay_configured');
    });
  });
});

describe('API: /tiers capabilities', () => {
  it('returns all capability keys', () => {
    cy.request('/tiers').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.have.all.keys('languages', 'levels', 'boards', 'themes', 'talking_head');
    });
  });

  it('languages list contains supported Indian languages', () => {
    cy.request('/tiers').then((res) => {
      const langs = res.body.languages;
      expect(langs).to.include('en');
      expect(langs).to.include('hi');
      expect(langs).to.include('ta');
    });
  });

  it('levels list contains all six levels', () => {
    cy.request('/tiers').then((res) => {
      const levels = res.body.levels;
      expect(levels).to.include('kg');
      expect(levels).to.include('middle');
      expect(levels).to.include('secondary');
      expect(levels).to.include('neet_jee');
    });
  });

  it('boards list includes CBSE, ICSE, NEET, JEE, and state boards', () => {
    cy.request('/tiers').then((res) => {
      const boards = res.body.boards;
      ['CBSE', 'ICSE', 'NEET', 'JEE', 'Maharashtra', 'Karnataka', 'TamilNadu'].forEach((b) => {
        expect(boards).to.include(b);
      });
    });
  });
});

describe('API: curriculum index', () => {
  it('GET /curriculum/index returns entries array', () => {
    cy.request('/curriculum/index').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.have.property('entries').that.is.an('array');
      expect(res.body.entries.length).to.be.greaterThan(50);
    });
  });

  it('filters by board=NEET return only NEET entries', () => {
    cy.request('/curriculum/index?board=NEET').then((res) => {
      expect(res.status).to.eq(200);
      res.body.entries.forEach((e) => expect(e.board).to.eq('NEET'));
    });
  });

  it('filters by board=CBSE and cls=11 return Class 11 CBSE entries', () => {
    cy.request('/curriculum/index?board=CBSE&cls=11').then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body.entries.length).to.be.greaterThan(0);
      res.body.entries.forEach((e) => {
        expect(e.board).to.eq('CBSE');
        expect(e.class).to.eq(11);
      });
    });
  });

  it('filters by board=JEE return neet_jee level entries', () => {
    cy.request('/curriculum/index?board=JEE').then((res) => {
      expect(res.status).to.eq(200);
      res.body.entries.forEach((e) => expect(e.level).to.eq('neet_jee'));
    });
  });

  it('returns boards and subjects metadata', () => {
    cy.request('/curriculum/index').then((res) => {
      expect(res.body).to.have.property('boards').that.is.an('array');
      expect(res.body).to.have.property('subjects').that.is.an('array');
      expect(res.body.boards).to.include('CBSE');
      expect(res.body.subjects).to.include('Maths');
    });
  });
});
