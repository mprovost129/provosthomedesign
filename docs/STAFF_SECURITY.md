# Staff sign-in and recovery

Staff security is available at `/staff/security/`. Enrollment is optional per account: merely opening the page does not create a device or change access. Existing staff permissions and client portal verification remain in place.

## Enrollment

The account holder enters their current website password, scans the locally generated QR code in an authenticator app, and confirms a fresh six-digit code. The pending setup expires after 15 minutes and belongs to the enrolling browser session. Successful confirmation enables the extra sign-in check and shows ten recovery codes once. Store them in a password manager or print and secure them. Do not email codes or send setup secrets to support.

Once enrolled, staff must verify after password sign-in before accessing protected pages, including Django administration and private attachment routes. Old password-only sessions are challenged on their next protected request. Confirmation rotates the session key. OTP time steps and recovery codes cannot be reused; row locks serialize competing attempts. Failed attempts have account and IP throttles. Other staff accounts are not enrolled automatically.

## Password and factor recovery

The staff login page links to `/staff/password-reset/`. A generic response avoids account disclosure. For an eligible active staff account, an email is queued through the existing durable sender to the email saved on the **Django user account**, not the CRM contact. Reset links use the configured canonical website address, expire after one hour, and are invalid after use. Resetting a password does not remove an authenticator or sign in automatically.

A recovery code substitutes for an authenticator code once. Generating replacement codes or disabling a factor requires the current password plus a fresh authenticator/recovery code. Disabling also requires an explicit checkbox. Security changes queue a notice to the account's existing email. Regenerating invalidates all older recovery codes. If both the authenticator and all recovery codes are lost, an administrator must verify identity through an established channel before any manual recovery; no unauthenticated bypass is provided.

## Storage and operation

`StaffOTPDevice` stores its setup secret encrypted with Fernet, using an HKDF-derived key from Django `SECRET_KEY`. Keep this secret securely backed up along with the database. For key rotation, retain the prior key in `SECRET_KEY_FALLBACKS` until all device secrets have been re-encrypted by saving the devices with the new primary key. Losing all applicable keys makes enrolled authenticators unreadable; restoring only the database is insufficient.

Recovery codes contain 96 random bits and only SHA-256 digests bound to the device are stored. Security pages are non-cacheable and have a no-referrer policy. No QR image service receives setup data. Do not include device keys, reset tokens, passwords, or recovery codes in operational logging, screenshots, or support messages. Database and mail-outbox backups remain sensitive, including short-lived reset links.

Dependencies are pinned to `django-otp==1.7.5` and `segno==1.6.6`. Migration `0045_staff_two_step_security` is additive. The scheduled job needs the same dependency deployment because it imports the application models. No new external service or hosting plan is required.
