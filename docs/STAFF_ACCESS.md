# Staff access

Staff sign in at `/staff/login/` with their existing Django username and password.
Successful sign-in opens `/dashboard/`, or the original same-host staff URL when
sign-in follows an expired session. External redirects and login/logout loops are
rejected. Staff must be active and already hold `workqueue.view_workitem`.
Nothing creates users or grants permissions.

Dashboard, queue, CRM, project management and supporting staff screens share a
navigation bar and a CSRF-protected POST sign-out button. Existing action-specific
permissions still apply. The website footer links to Staff sign in. Administration
at `/admin/` and verified-email client access are unchanged.

Login uses Django AuthenticationForm/LoginView and standard session rotation,
CSRF protection, safe redirects and password hashing. Authentication failures do
not disclose whether a user exists or has staff permission. Login pages and
logout responses are never cached; pages are excluded from indexing. Sign-in
attempts are throttled using the existing shared cache: 30 POSTs per IP and 10
per submitted username over five minutes. A limited request returns 429 and
Retry-After without authenticating. No new credentials or infrastructure needed.

If staff cannot sign in, confirm the existing username, active/staff flags and
queue-view permission through administration. No public staff registration or
password-reset feature is added by this change.
