# Slater Woods Design Company — local rebrand review

Review date: October 12, 2026. Local implementation only; not deployed.

## What changed

- Replaced the web-business identity with Slater Woods Design Company, including an SW lettermark, forest/cream visual system, favicon, social image, web navigation/footer, page titles, metadata, legal-page branding, and inquiry emails.
- Built the homepage around the requested headline, three service categories, four featured projects, development approach, and inquiry calls to action.
- Added reusable service and portfolio card templates. Kept existing URLs, redesign detail page, pricing calculator, analytics attributes, form validation, rate limiting, CSRF, reCAPTCHA, and email configuration.
- Expanded the existing case-study system to four projects. Provost Home Design and Boston Baseball Ledger are owner-operated; J. Fisk Construction and Heritage Village of Norton are client projects, as confirmed by Mike.
- Added Software & Digital Solutions at `/services/software-digital-solutions/`; inquiries use the existing Other project type, avoiding a database migration.
- Canonicals, social URLs, structured data and web crawler information use the configured WEB_DESIGN_URL, currently https://web.provosthomedesign.com. Query strings are excluded from canonical URLs.
- Corrected the internal inquiry-notification admin link to MAIN_SITE_URL, where the existing protected admin lives. Customer acknowledgments contain no admin link.
- Fixed narrow-screen grid overflow and added visible keyboard focus styles. No application dependencies added.

## Business boundary

The existing SubdomainURLRoutingMiddleware selects config.web_urls for WEB_DESIGN_HOST. That URL configuration exposes only the web marketing/inquiry pages and its SEO endpoints. The main website retains config.urls and its architectural pages, catalog, intake, work queue, CRM and administration.

Changes to templates/base.html are conditional on IS_WEB_DESIGN_SITE. Residential brand settings and SiteSettings data are not renamed. No models, migrations, production settings, DNS, deployment scripts, or workqueue code were changed. Existing contact addresses and email recipients remain configured as before. No production data was read or written for testing.

## Portfolio evidence and limitations

- https://www.provosthomedesign.com/ — public page inspected through web retrieval. Catalog search, project submission/upload, tracking and booking were visible; local routes additionally confirm favorites and comparisons. Browser screenshot capture failed with a connection reset, so the existing house-plan image remains, accurately described as illustrative imagery.
- https://www.jfiskconstruction.com/ — inspected in browser. Products, excavation/services, service-area content and call/contact actions verified. Genuine homepage screenshot captured October 12, 2026.
- https://www.heritagevillagenorton.com/ — inspected in browser. Available-home details, two floor-plan types, finish selections, community/site-plan presentation and listing-team inquiry path verified. Genuine homepage screenshot captured October 12, 2026.
- https://www.bostonbaseballledger.com/ — web retrieval and browser connection failed; focused search returned no indexed result. Included using Mike's confirmed ownership and supplied publishing-project description. The image explicitly says it is a project graphic, not a screenshot. No unverified interactive features, metrics or results are claimed. Revisit the live site and replace the graphic/expand the case study when reachable.

## Verification

- Django system check: no issues.
- 80 automated tests passed across pages and plans, including new brand-boundary, all-page canonical/schema/static-asset, four-project ownership, sitemap, inquiry email, delivery-failure, rate-limit, CSRF and reCAPTCHA checks.
- Production-style WhiteNoise manifest collection/compression passed with output isolated in output/slater-static-review.
- Browser review at 390, 768 and 1280 pixels across home, services, work, about, contact, pricing and the two new project pages: one main heading per page, no horizontal overflow, no broken loaded images, no captured console errors.
- Mobile navigation opened and routed to contact. Empty inquiry submission showed linked validation errors and labeled inputs.
- Email tests used the in-memory backend; real Graph delivery and live reCAPTCHA were not exercised. These require a post-approval deployment smoke test. This is a targeted accessibility review, not a formal conformance audit.
- Existing unrelated documentation edits were compared against the pre-edit backup and are unchanged.

Test log: output/slater-tests.txt. Review images: output/slater-desktop.jpg, output/slater-mobile.jpg, output/slater-home-full.jpg, output/slater-work.jpg.

## Local preview

Running at http://127.0.0.1:8766/ with config.settings_web_preview. This imports only the isolated development configuration, never .env. It uses output/slater-preview.sqlite3, an in-memory email backend, no analytics, and disabled operational feature flags. Database setup applied migrations only to this new disposable local preview database.

With a working project Python environment:

```powershell
New-Item -ItemType Directory -Force output
python manage.py migrate --settings=config.settings_web_preview
python manage.py runserver 127.0.0.1:8766 --settings=config.settings_web_preview
python manage.py test pages plans --settings=config.settings_queue_dev --noinput
```

The existing env/Scripts/python.exe launcher could not start in this session. Verification used the app's bundled Python 3.12 with existing project packages via output/slater_manage.py and UTF-8 mode. No packages or requirements files were changed. That local launcher issue is independent of the rebrand.

## Review items

1. Review the visual identity and copy in the local preview.
2. Verify Boston Baseball Ledger is publicly reachable, then capture its current layout and document only observed features. Its current portfolio entry is intentionally conservative.
3. Existing mike@provosthomedesign.com and 508-243-7912 remain the public contact details. Supply replacements only if desired; no new Slater Woods email address or legal entity was invented.
4. The web privacy/terms retain the existing provisions with branding/date updates. Confirm they reflect actual business practices and project agreements before launch.

## Deployment checklist — requires Mike's approval

- [ ] Approve the local design/content and the portfolio limitations above.
- [ ] Stage only the files listed below; exclude unrelated partner documentation, output/, tmp/, databases and secrets.
- [ ] Run the system check and pages/plans tests in the normal deployment environment.
- [ ] Review existing release scripts before running them; this rebrand needs no schema migration. Do not inadvertently include unrelated pending operational migrations.
- [ ] Keep WEB_DESIGN_HOST=web.provosthomedesign.com and WEB_DESIGN_URL=https://web.provosthomedesign.com. Leave the residential COMPANY_NAME, MAIN_SITE_HOST and MAIN_SITE_URL unchanged.
- [ ] Collect static assets with the existing production pipeline, then deploy the approved code and reload the application.
- [ ] Check both hostnames: architecture/catalog/intake/tracking on the main site; Slater branding, all four projects, services, pricing, privacy, terms, sitemap and errors on the web site.
- [ ] Submit one authorized inquiry with working reCAPTCHA; confirm storage, staff notification, reply-to, admin link and customer acknowledgment. Confirm configured analytics events.
- [ ] Check canonical and social previews; request social-cache refresh if the old image remains cached. Monitor error logs.

## Future dedicated domain — do not perform now

After a domain is purchased and approved: configure hosting/TLS; update WEB_DESIGN_HOST, WEB_DESIGN_URL, allowed hosts and CSRF origins; register the hostname with reCAPTCHA and analytics; preserve existing URL paths. Explicitly configure redirects from the old web subdomain at the proxy/hosting layer (changing WEB_DESIGN_HOST alone would otherwise send the old host into the residential URL configuration). Keep those redirects separate from www.provosthomedesign.com. Verify sitemaps, organization IDs, canonical/social URLs, legacy /web-design/ and /pricing/ redirects, and email links. Update search-console properties and monitor indexing. MAIN_SITE_URL must continue pointing to the architectural website/protected admin.

## Rollback

Pre-edit commit: cd72e61945c6f87a0f1427f1e6fe5b84bf892e17.

output/slater-rebrand-rollback contains tracked-before.zip, the exact pre-existing tracked diff, a copy of the untracked partner guide, and the original status/commit files. Restore only the rebrand files from the archive after reviewing any later edits; remove only newly added rebrand files if reverting. Do not reset the whole checkout or overwrite the partner documents, output/, tmp/, or database files. Production rollback would redeploy the prior approved code/static build; there are no rebrand schema changes to undo.

## Files changed

- `pages/tests.py`
- `pages/views.py`
- `static/css/web-site.css`
- `static/images/web-mark.svg`
- `static/images/web-og-preview.png`
- `templates/base.html`
- `templates/includes/footer.html`
- `templates/includes/web_footer.html`
- `templates/includes/web_navbar.html`
- `templates/pages/emails/web_design_ack.html`
- `templates/pages/emails/web_design_ack.txt`
- `templates/pages/emails/web_design_notification.html`
- `templates/pages/emails/web_design_notification.txt`
- `templates/pages/pricing.html`
- `templates/pages/web_about.html`
- `templates/pages/web_case_study.html`
- `templates/pages/web_contact.html`
- `templates/pages/web_design.html`
- `templates/pages/web_error.html`
- `templates/pages/web_privacy.html`
- `templates/pages/web_region.html`
- `templates/pages/web_service_detail.html`
- `templates/pages/web_services.html`
- `templates/pages/web_terms.html`
- `templates/pages/web_thanks.html`
- `templates/pages/web_work.html`
- `config/settings_web_preview.py` (new)
- `pages/web_brand.py` (new)
- `pages/test_web_rebrand.py` (new)
- `templates/includes/web_project_card.html` (new)
- `templates/includes/web_service_cards.html` (new)
- `static/images/web-portfolio/j-fisk.jpg` (new)
- `static/images/web-portfolio/heritage.jpg` (new)
- `static/images/web-portfolio/ledger.svg` (new)
- `docs/SLATER_WOODS_REBRAND.md` (new)

Local output files include rollback archives, review screenshots, isolated preview/static-build data and verification logs; they are not production deliverables and should not be staged.

## Follow-up: portfolio image cropping

October 12, 2026: browser review confirmed that the fixed 16:10 card frames were cropping the wider Heritage and J. Fisk screenshots and the Provost illustration. Updated the shared image rule in static/css/web-site.css to contain the complete image, centered inside the existing frame. The Ledger graphic already matches the frame ratio. Checked all four cards on the homepage and Work page at 390, 768 and 1280 pixels; images retain their full bounds and neither page has horizontal overflow. This is a CSS-only correction; no backend behavior changed. Review screenshot: output/slater-portfolio-uncropped.jpg.

## Follow-up: residential footer sister company

October 12, 2026: Mike explicitly authorized a narrow change to the Provost Home Design architectural site's footer. Updated `templates/includes/footer.html` with a third navigation column headed “Sister Company,” linking “Slater Woods Design Company” to the existing configured web-services URL. Added the description “Websites, applications & software.” The referral analytics attributes remain intact. The wider navigation area uses three columns on desktop/tablet and stacks the sister-company section on phones; socials move below on tablet. Architectural branding remains unchanged elsewhere.

Updated existing brand-boundary assertions in `pages/tests.py` and `pages/test_web_rebrand.py` to allow the footer referral while keeping Slater Woods out of the residential page content and metadata. The 40 routing/rebrand tests pass. Visually checked the local residential homepage at 390, 768 and 1280 pixels, including the destination URL and overflow. Screenshot: `output/provost-sister-company-footer.jpg`. Local changes only; not deployed.

## Follow-up: website creator credit

Added a linked “Website by Slater Woods Design Company” credit below the existing Provost Home Design copyright in the residential footer. Kept copyright ownership wording intact and preserved referral tracking with a distinct credit label. Added mobile bottom spacing so the fixed project button clears the credit. Desktop and mobile browser checks passed; 40 routing/rebrand tests passed with the existing referral-count assertion updated for the second link. Screenshot: output/provost-website-credit.jpg. Local only, not deployed.
