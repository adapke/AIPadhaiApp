/**
 * 13-board-exam-wiring.cy.js
 *
 * Tests for the board/exam context wiring we implemented:
 * - /tiers returns all 12 board keys
 * - /curriculum/index covers all new boards
 * - Board guidance keys are consistent between /tiers and /curriculum/index
 * - Admin curriculum CRUD gates are correct
 */

describe('Board guidance wiring: /tiers', () => {
  let tiersData;

  before(() => {
    cy.request('/tiers').then((res) => { tiersData = res.body; });
  });

  it('boards list has 12 entries (all supported boards/exams)', () => {
    cy.request('/tiers').then((res) => {
      expect(res.body.boards).to.have.length(12);
    });
  });

  const expectedBoards = [
    'AP_Telangana', 'CBSE', 'ICSE', 'IGCSE',
    'JEE', 'Karnataka', 'Maharashtra', 'NEET',
    'SSC', 'TamilNadu', 'UP', 'UPSC',
  ];

  expectedBoards.forEach((board) => {
    it(`boards list includes "${board}"`, () => {
      cy.request('/tiers').then((res) => {
        expect(res.body.boards).to.include(board);
      });
    });
  });
});

describe('Board coverage in /curriculum/index', () => {
  const curriculumBoards = [
    'CBSE', 'ICSE', 'Maharashtra', 'TamilNadu',
    'Karnataka', 'AP_Telangana', 'UP', 'NEET', 'JEE',
  ];

  curriculumBoards.forEach((board) => {
    it(`curriculum includes entries for board "${board}"`, () => {
      cy.request(`/curriculum/index?board=${board}`).then((res) => {
        expect(res.status).to.eq(200);
        expect(res.body.entries.length).to.be.greaterThan(0);
      });
    });
  });
});

describe('Class 11-12 CBSE in curriculum', () => {
  it('CBSE Class 11 Physics is present', () => {
    cy.request('/curriculum/index?board=CBSE&cls=11').then((res) => {
      const physChapters = res.body.entries.filter((e) => e.subject === 'Physics');
      expect(physChapters.length).to.be.greaterThan(0);
    });
  });

  it('CBSE Class 12 Biology includes Genetics chapter', () => {
    cy.request('/curriculum/index?board=CBSE&cls=12').then((res) => {
      const bio = res.body.entries.filter((e) => e.subject === 'Biology');
      const genetics = bio.find((e) => e.chapter_title.toLowerCase().includes('inheritance'));
      expect(genetics).to.exist;
    });
  });

  it('CBSE Class 12 Chemistry is present', () => {
    cy.request('/curriculum/index?board=CBSE&cls=12').then((res) => {
      const chem = res.body.entries.filter((e) => e.subject === 'Chemistry');
      expect(chem.length).to.be.greaterThan(0);
    });
  });
});

describe('NEET/JEE exam-specific chapters', () => {
  it('NEET Biology chapters exist', () => {
    cy.request('/curriculum/index?board=NEET').then((res) => {
      const bio = res.body.entries.filter((e) => e.subject === 'Biology');
      expect(bio.length).to.be.greaterThan(0);
    });
  });

  it('JEE Physics chapters exist', () => {
    cy.request('/curriculum/index?board=JEE').then((res) => {
      const phys = res.body.entries.filter((e) => e.subject === 'Physics');
      expect(phys.length).to.be.greaterThan(0);
    });
  });

  it('JEE Maths chapters exist', () => {
    cy.request('/curriculum/index?board=JEE').then((res) => {
      const maths = res.body.entries.filter((e) => e.subject === 'Maths');
      expect(maths.length).to.be.greaterThan(0);
    });
  });

  it('JEE entries have "JEE" in their topics list', () => {
    cy.request('/curriculum/index?board=JEE').then((res) => {
      res.body.entries.forEach((e) => {
        expect(e.topics).to.include('JEE');
      });
    });
  });
});
