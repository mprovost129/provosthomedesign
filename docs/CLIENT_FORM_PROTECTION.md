# Public client form protection

Project intake (new and update), upload reservations, tracking sign-in emails,
booking sign-in emails, and appointment/reschedule requests verify reCAPTCHA
before their write or email operation. The provider must approve validity,
score, hostname, and the server's fixed action. Existing CSRF, session-bound
tickets, honeypot and shared rate limits remain in place.

The existing website reCAPTCHA configuration is reused: RECAPTCHA_SITE_KEY
(or PUBLIC_KEY), Enterprise API key/project, or standard v3 secret fallback.
Never put server credentials in templates or logs. Missing credentials and
provider failures reject public writes. Only DEBUG=True together with
INTAKE_LOCAL_DEVELOPMENT=True and no server credentials allows a local preview
without reCAPTCHA. Workers do not need assessment credentials because they do
not accept public requests.

The browser obtains a fresh token immediately before each protected POST,
including each file reservation. It does not reuse a token after uploading a
large document. Failed verification keeps answers and ready uploads available
for retry. Google scripts blocked by a browser show a retry/contact message.

New work, updates, and appointment/reschedule forms require an initially
unchecked Terms & Conditions checkbox linked to the existing /terms/ page.
Intake answers retain acceptance, the terms URL and acceptance timestamp;
booking audit records retain the URL and timestamp. Historical records are
unchanged. These records do not constitute a versioned snapshot of the legal
text. Sign-in and cancellation do not ask clients to accept terms again.

Google guidance: https://docs.cloud.google.com/recaptcha/docs/instrument-web-pages
and https://docs.cloud.google.com/recaptcha/docs/create-assessment-website.

Validation: production-mode assessment doubles exercise missing/invalid tokens,
low scores, wrong actions/hosts, provider failure, blocked upload allocation,
blocked sign-in email, consent recording, retained files, and booking replay.
Run `python manage.py test workqueue --settings=config.settings_queue_dev` and
syntax-check `static/js/client-recaptcha.js` and `static/js/client-intake.js`.
