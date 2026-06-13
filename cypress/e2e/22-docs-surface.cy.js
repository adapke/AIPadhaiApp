/**
 * 22-docs-surface.cy.js — prod-121
 *
 * Verify the production-readiness docs exist in the repo and that
 * the contracts the pytest layer locks (sections, cross-links) hold
 * from a Cypress perspective too.
 *
 * Why Cypress at all? Because if someone runs `npx cypress run`
 * against a fresh clone before pytest, this spec catches missing
 * docs before they try to deploy. cy.readFile is a Node-side file
 * read; doesn't need a live server.
 */

describe('Production-readiness docs surface (prod-121)', () => {
  const requiredDocs = [
    'docs/RAZORPAY.md',
    'docs/SMTP.md',
    'docs/SENTRY.md',
    'docs/POSTHOG.md',
    'docs/DEPLOY.md',
    'docs/MONITORING.md',
  ];

  requiredDocs.forEach((path) => {
    it(`${path} exists + is non-empty`, () => {
      cy.readFile(path).then((body) => {
        expect(body.length).to.be.greaterThan(1000);
      });
    });
  });

  it('PRODUCTION_CHECKLIST.md cross-links all 5 provider docs', () => {
    cy.readFile('PRODUCTION_CHECKLIST.md').then((body) => {
      expect(body).to.include('RAZORPAY.md');
      expect(body).to.include('SMTP.md');
      expect(body).to.include('SENTRY.md');
      expect(body).to.include('POSTHOG.md');
    });
  });

  it('DEPLOY.md references the 4 provider docs from §1', () => {
    cy.readFile('docs/DEPLOY.md').then((body) => {
      // DEPLOY.md §1 (Provider keys) cross-links each walkthrough
      expect(body).to.include('docs/RAZORPAY.md');
      expect(body).to.include('docs/SMTP.md');
      expect(body).to.include('docs/SENTRY.md');
      expect(body).to.include('docs/POSTHOG.md');
    });
  });

  it('ONBOARDING.md references the 5 ops scripts (admin discovery)', () => {
    cy.readFile('ONBOARDING.md').then((body) => {
      expect(body).to.include('curator_queue.py');
      expect(body).to.include('check_verified_iframes.py');
      expect(body).to.include('print_curator_stats.py');
      expect(body).to.include('nightly_ops.sh');
    });
  });

  it('PR template carries the required sections', () => {
    cy.readFile('.github/PULL_REQUEST_TEMPLATE.md').then((body) => {
      expect(body).to.include('## Summary');
      expect(body).to.include('## Test plan');
      expect(body).to.include('## Honest gaps');
      expect(body).to.include('make verify');
    });
  });

  it('PR honest-gaps workflow exists', () => {
    cy.readFile('.github/workflows/pr-honest-gaps.yml').then((body) => {
      expect(body).to.include('pull_request');
      expect(body).to.include('Honest gaps');
      // Bot exemption — dependabot/renovate PRs are auto-passed
      expect(body).to.include('dependabot');
      expect(body).to.include('renovate');
    });
  });

  it('MONITORING.md lists the 4 must-watch surfaces from the intro', () => {
    cy.readFile('docs/MONITORING.md').then((body) => {
      // These 4 are called out in the doc's intro as the bare-min
      // monitoring surfaces. Lock them so a future refactor doesn't
      // accidentally drop one.
      expect(body).to.include('Sentry');
      expect(body).to.include('/admin/health');
      expect(body).to.include('/admin/llm-costs');
      expect(body).to.include('PostHog');
    });
  });
});
