# Google reCAPTCHA v2 Setup Guide

This guide will help you set up Google reCAPTCHA v2 to protect your registration and contact forms from bots.

## Step 1: Get reCAPTCHA Keys

1. Go to [Google reCAPTCHA Admin Console](https://www.google.com/recaptcha/admin)
2. Click **"+"** to create a new site
3. Fill in the form:
   - **Label**: `Provost Home Design` (or your site name)
   - **reCAPTCHA type**: Select **"reCAPTCHA v2"** → **"I'm not a robot" Checkbox**
   - **Domains**: 
     - `provosthomedesign.com` (production)
     - `localhost` (for local development)
   - Accept the reCAPTCHA Terms of Service
4. Click **Submit**
5. Copy your keys:
   - **Site Key** (public key)
   - **Secret Key** (private key)

## Step 2: Add Keys to Environment Variables

### Local Development (.env file)
Add these lines to your `.env` file:
```bash
RECAPTCHA_PUBLIC_KEY=your-site-key-here
RECAPTCHA_PRIVATE_KEY=your-secret-key-here
```

### Production Server
Add to your production environment variables (Ubuntu):
```bash
# Edit your .env file on the server
nano /srv/phdapp/.env

# Add these lines:
RECAPTCHA_PUBLIC_KEY=your-site-key-here
RECAPTCHA_PRIVATE_KEY=your-secret-key-here
```

## Step 3: Install Dependencies

```bash
# Local development
pip install django-recaptcha==4.0.0

# Production server
cd /srv/phdapp
source .venv/bin/activate
pip install django-recaptcha==4.0.0
```

## Step 4: Deploy Changes

### Local Testing
```bash
# Run migrations (if needed)
python manage.py migrate

# Start dev server
python manage.py runserver

# Test registration: http://localhost:8000/portal/register/
# Test contact form: http://localhost:8000/contact/
```

### Production Deployment
```bash
# On production server
cd /srv/phdapp
source .venv/bin/activate

# Pull latest code
export GIT_SSH_COMMAND='ssh -i ~/.ssh/id_deploy_phd -o IdentitiesOnly=yes'
git fetch --prune
git reset --hard origin/main

# Install new package
pip install django-recaptcha==4.0.0

# Collect static files (reCAPTCHA widget)
python manage.py collectstatic --noinput

# Restart service
sudo systemctl restart phdapp
```

## What's Protected

After setup, the following forms will require reCAPTCHA verification:

1. **Client Registration** (`/portal/register/`)
   - Prevents automated bot account creation
   - Users must check "I'm not a robot" before registering

2. **Contact Form** (`/contact/`)
   - Prevents spam contact submissions
   - Already has honeypot field for extra protection
   - Users must verify reCAPTCHA before sending message

## Testing

### Test Successful Submission
1. Fill out the form correctly
2. Check the "I'm not a robot" checkbox
3. Complete the CAPTCHA challenge if prompted
4. Submit the form - should work normally

### Test Bot Prevention
1. Try to submit form without checking reCAPTCHA
2. Should see validation error: "This field is required"

### Test Honeypot (Contact Form Only)
1. The contact form has a hidden "website" field
2. Bots that auto-fill all fields will be rejected
3. Normal users won't see this field

## Troubleshooting

### "Invalid reCAPTCHA keys" Error
- Check that keys are in your `.env` file
- Verify keys match what's in Google reCAPTCHA admin
- Make sure domain is registered in reCAPTCHA admin
- Restart your Django server after adding keys

### reCAPTCHA Not Showing
- Check that `django_recaptcha` is in `INSTALLED_APPS`
- Run `python manage.py collectstatic`
- Check browser console for JavaScript errors
- Verify domain is allowed in reCAPTCHA admin

### Testing Keys (Development Only)
Google provides test keys that always pass:
```
Site key: 6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI
Secret key: 6LeIxAcTAAAAAGG-vFI1TnRWxMZNFuojJ4WifJWe
```
⚠️ **Never use test keys in production!**

## Additional Security

Beyond reCAPTCHA, these protections are also active:

1. **Honeypot Field** (Contact Form)
   - Hidden field that bots fill out
   - Rejects submissions with honeypot value

2. **CSRF Protection** (All Forms)
   - Django's built-in CSRF tokens
   - Prevents cross-site request forgery

3. **Rate Limiting** (Coming Soon)
   - Can add django-ratelimit to limit submission frequency
   - Prevents brute force attacks

## Support

For issues:
1. Check [django-recaptcha documentation](https://github.com/torchbox/django-recaptcha)
2. Review [Google reCAPTCHA docs](https://developers.google.com/recaptcha/docs/display)
3. Check Django logs: `sudo journalctl -u phdapp -n 100`
