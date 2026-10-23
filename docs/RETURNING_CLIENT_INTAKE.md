# Returning-client intake

The public intake remains usable without sign-in. Returning clients have two optional conveniences:

- **Remember the contact and billing details I enter:** stores only the eight contact/billing fields in this browser's local storage for 90 days after the last opted-in edit. It does not retrieve CRM information or grant access. Leave it unchecked on a shared device. Browser/private-mode storage restrictions do not prevent normal intake.
- **Use my saved details:** emails a one-use verification link, with the existing generic response, reCAPTCHA, CSRF and address/IP rate limits. Confirmation returns to intake. An unchecked-by-default confirmation option remembers intake recognition for 90 days. It is separate from the existing seven-day portal/booking session and does not extend it.

After verification, the CRM projection contains only the exact email's active contact associations on unmerged clients. Each company stays separate; no other contact emails, project inventory, notes, rates, financial data or fuzzy matches are returned. Multiple associations require an explicit contact/company selection. A single association provides editable defaults. Submission edits never update the CRM or imply project access.

Project selectors still use `authorized_projects(verified_email(request))`. The intake device cookie cannot authorize project records, documents, receipts, booking or the portal. No project address or description is remembered by this feature, and terms are never checked automatically. Failed validation and explicitly recovered drafts take precedence over defaults. CRM fill is an explicit button when replacing current answers.

## Recognition and forgetting

`IntakeDevice` stores only the normalized verified email, a SHA-256 digest of a random 256-bit token and expiration/revocation timestamps. The browser receives the opaque token in a host-only HttpOnly, SameSite=Lax cookie (Secure in production). It expires after 90 days without sliding extension. Active contact association is checked on every request. Expired records older than seven days are pruned when issuing another device token.

Confirming another sign-in revokes the previous device token; remembering the new email is again opt-in. Client sign-out revokes the recognized intake device. **Forget this device and sign out** additionally clears this feature's browser storage, removes this browser's unfinished intake drafts and rotates its intake binding. Old unsubmitted upload bytes expire under the existing upload cleanup. Submitted work and CRM records are not deleted. Already-open tabs may still contain displayed information; close them on a shared device. Existing seven-day draft recovery is separate from this optional 90-day contact storage.

## Release

Migration `0048_remember_intake_devices` is additive: creates the device table and adds the intake email destination. No new service, external permission, mail provider or secret is required. The existing outbox delivers verification messages. No real customer messages or records are needed for testing.
