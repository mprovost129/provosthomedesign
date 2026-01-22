# Production Deployment Checklist

## ✅ Pre-Deployment Configuration Complete

The `.env` file has been updated for production. Review the following:

### 1. Environment Settings
- [x] `DEBUG=False` (Production mode enabled)
- [x] `ALLOWED_HOSTS` (Only production domains)
- [x] `CSRF_TRUSTED_ORIGINS` (HTTPS only)
- [x] `SECURE_SSL_REDIRECT=True` (Force HTTPS)
- [x] `BEHIND_PROXY=True` (For Azure/production server)
- [x] Security headers enabled (HSTS, secure cookies)

### 2. ⚠️ CRITICAL: Stripe Keys - ACTION REQUIRED

**Current Status:** Still using TEST keys (pk_test_* and sk_test_*)

**You MUST replace with LIVE keys before accepting real payments:**

1. Go to https://dashboard.stripe.com/apikeys
2. Switch from "Test mode" to "Live mode" (toggle in top-right)
3. Copy your **Publishable key** (starts with `pk_live_`)
4. Copy your **Secret key** (starts with `sk_live_`)
5. Update `.env`:
   ```
   STRIPE_PUBLISHABLE_KEY=pk_live_YOUR_LIVE_KEY_HERE
   STRIPE_SECRET_KEY=sk_live_YOUR_LIVE_KEY_HERE
   ```

### 3. ⚠️ Stripe Webhook Configuration - ACTION REQUIRED

**Current webhook secret is for local development only!**

1. Go to https://dashboard.stripe.com/webhooks
2. Click "Add endpoint"
3. Set endpoint URL: `https://provosthomedesign.com/portal/webhook/stripe/`
4. Select events to listen for:
   - `payment_intent.succeeded`
   - `payment_intent.payment_failed`
   - `charge.succeeded`
   - `charge.failed`
5. Copy the **Signing secret** (starts with `whsec_`)
6. Update `.env`:
   ```
   STRIPE_WEBHOOK_SECRET=whsec_YOUR_PRODUCTION_SECRET_HERE
   ```

### 4. Database
- [ ] Run migrations: `python manage.py migrate`
- [ ] Collect static files: `python manage.py collectstatic --noinput`
- [ ] Create superuser if needed: `python manage.py createsuperuser`

### 5. Server Configuration
- [ ] Restart application server (Azure App Service)
- [ ] Verify environment variables are loaded
- [ ] Check logs for any startup errors

### 6. Testing After Deployment

**DO NOT test with real credit cards until live keys are configured!**

1. **Test Client Portal Access:**
   - [ ] Visit https://provosthomedesign.com/portal/login/
   - [ ] Can log in successfully
   - [ ] Dashboard displays correctly
   - [ ] Navigation works (invoices, profile)

2. **Test Invoice Management:**
   - [ ] Create test invoice via admin
   - [ ] View invoice in portal
   - [ ] Download PDF works

3. **Test Payment Flow (with live keys):**
   - [ ] Click "Pay Now" on invoice
   - [ ] Stripe payment form loads
   - [ ] Test payment with Stripe test card: `4242 4242 4242 4242`
   - [ ] Payment processes successfully
   - [ ] Invoice status updates to "Paid"
   - [ ] Webhook receives event (check Stripe Dashboard > Webhooks > Logs)

4. **Test Email Notifications:**
   - [ ] Password reset emails send
   - [ ] Invoice notifications send

### 7. Security Verification
- [ ] Force HTTPS working (HTTP redirects to HTTPS)
- [ ] Admin panel accessible only via `/admin/`
- [ ] Client portal requires authentication
- [ ] No debug information visible on errors
- [ ] Static files serving correctly

### 8. Monitoring
- [ ] Set up error monitoring (Sentry, etc.)
- [ ] Monitor Stripe webhook logs regularly
- [ ] Check Django logs for errors
- [ ] Monitor payment success rate

## 🔐 Security Notes

1. **Never commit `.env` to git** - It contains sensitive keys
2. **Rotate secrets periodically** - Especially after team member changes
3. **Keep Stripe keys secure** - Live keys have access to real payments
4. **Monitor for suspicious activity** - Check Stripe Dashboard regularly

## 📞 Support Resources

- **Stripe Dashboard:** https://dashboard.stripe.com
- **Stripe Testing:** https://stripe.com/docs/testing
- **Django Documentation:** https://docs.djangoproject.com/
- **Client Portal:** https://provosthomedesign.com/portal/login/

## 🎉 What's Live

✅ Client authentication (login, register, password reset)
✅ Invoice management and viewing
✅ PDF invoice downloads
✅ Stripe payment integration
✅ Automatic payment status updates via webhooks
✅ Client profile management
✅ Responsive design for mobile/tablet

---

**Last Updated:** January 22, 2026
**Deployment Status:** Ready for production (pending Stripe live keys)
